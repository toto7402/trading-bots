"""
arb5_options.py — Stratégies d'options automatisées
=====================================================
4 stratégies :
  1. Covered Calls   : vente de calls sur positions longues momentum (lundi 10h)
  2. Cash-Secured Puts : vente de puts sur cash disponible (lundi + jeudi 10h30)
  3. Straddle pre-earnings : achat straddle 2j avant résultats (scan quotidien 9h45)
  4. Bull Put Spread : spread haussier sur signaux momentum (lundi 11h)

Connexion IB : HOST 127.0.0.1:7497, clientId=6
CSV positions : options_positions.csv
Alertes : Telegram via alerts.py
"""

import sys
import os
import time
import logging
import schedule
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, date
from ib_insync import IB, Stock, Option, util, LimitOrder, MarketOrder
import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    handlers=[
        logging.FileHandler('options_trading.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

from alerts import alert_manager

# ── Config ────────────────────────────────────────────────────────────────────
HOST        = '127.0.0.1'
PORT         = 4002
CLIENT_ID   = 6
CAPITAL     = 1_090_000
FINNHUB_KEY = os.environ.get('FINNHUB_KEY', '')
if not FINNHUB_KEY:
    print("WARNING: Variable d'environnement FINNHUB_KEY manquante. "
          "Les stratégies dépendant de Finnhub seront désactivées.")
if not FINNHUB_KEY:
    print("WARNING: Variable d'environnement FINNHUB_KEY manquante. "
          "Les stratégies dépendant de Finnhub seront désactivées.")

# Paramètres options
CC_DELTA_TARGET   = 0.30   # Covered Call : delta ~0.30 (OTM)
CC_DTE_TARGET     = 30     # Covered Call : 30 jours à expiration
CSP_DTE_TARGET    = 30     # Cash-Secured Put : 30 jours
CSP_DELTA_TARGET  = 0.25   # Put delta ~0.25
STRADDLE_DTE      = 14     # Straddle : 14 jours à expiration
SPREAD_WIDTH      = 5      # Bull Put Spread : écart entre strikes ($5)
MIN_PREMIUM       = 0.20   # Prime minimum pour qu'un trade soit intéressant ($)
MAX_OPTIONS_PCT   = 0.10   # Max 10% du capital en options
CSV_FILE          = 'options_positions.csv'

# ── Connexion IB ──────────────────────────────────────────────────────────────

def connect() -> IB:
    # Attendre ouverture marché si weekend
    import datetime
    while datetime.datetime.now().weekday() >= 5:
        log.info("Weekend — attente ouverture marché lundi...")
        time.sleep(3600)
    ib = IB()
    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID)
        log.info(f"Connecté — compte : {ib.wrapper.accounts}")
        return ib
    except Exception as e:
        alert_manager.notify_crash('options_bot', str(e))
        raise


def get_account_value(ib: IB) -> float:
    for v in ib.accountValues():
        if v.tag == 'NetLiquidation' and v.currency in ('USD', 'EUR', 'BASE'):
            return float(v.value)
    return CAPITAL


def get_cash(ib: IB) -> float:
    for v in ib.accountValues():
        if v.tag == 'AvailableFunds' and v.currency in ('USD', 'EUR', 'BASE'):
            return float(v.value)
    return 0.0


def get_positions(ib: IB) -> dict:
    positions = {}
    for pos in ib.positions():
        if pos.position != 0:
            positions[pos.contract.symbol] = {
                'shares':   pos.position,
                'avg_cost': pos.avgCost,
                'secType':  pos.contract.secType,
            }
    return positions


# ── Helpers options ───────────────────────────────────────────────────────────

def next_expiry(ib: IB, ticker: str, dte_target: int) -> str:
    """Trouve la date d'expiration la plus proche du DTE cible."""
    contract = Stock(ticker, 'SMART', 'USD')
    try:
        chains = ib.reqSecDefOptParams(ticker, '', 'STK', 0)
        if not chains:
            return None
        chain = chains[0]
        target_date = date.today() + timedelta(days=dte_target)
        expirations = sorted([
            datetime.strptime(e, '%Y%m%d').date()
            for e in chain.expirations
            if datetime.strptime(e, '%Y%m%d').date() > date.today()
        ])
        if not expirations:
            return None
        closest = min(expirations, key=lambda d: abs((d - target_date).days))
        return closest.strftime('%Y%m%d')
    except Exception as e:
        log.warning(f"Expiry error {ticker}: {e}")
        return None


def get_stock_price(ib: IB, ticker: str) -> float:
    contract = Stock(ticker, 'SMART', 'USD')
    try:
        ib.qualifyContracts(contract)
        ib.reqMarketDataType(3)
        t = ib.reqMktData(contract, '', False, False)
        ib.sleep(2)
        price = None
        for attr in [t.last, t.close, t.bid, t.ask]:
            try:
                if attr and not np.isnan(float(attr)):
                    price = float(attr)
                    break
            except Exception:
                continue
        ib.cancelMktData(contract)
        return price
    except Exception as e:
        log.warning(f"Price error {ticker}: {e}")
        return None


def get_option_price(ib: IB, ticker: str, expiry: str,
                     strike: float, right: str) -> tuple:
    """Retourne (mid_price, delta) d'une option."""
    contract = Option(ticker, expiry, strike, right, 'SMART')
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            return None, None
        contract = qualified[0]
        ib.reqMarketDataType(3)
        t = ib.reqMktData(contract, '100', False, False)
        ib.sleep(3)
        mid = None
        if t.bid and t.ask and not np.isnan(t.bid) and not np.isnan(t.ask):
            mid = (t.bid + t.ask) / 2
        delta = None
        if t.modelGreeks:
            delta = t.modelGreeks.delta
        ib.cancelMktData(contract)
        return mid, delta
    except Exception as e:
        log.warning(f"Option price error {ticker} {right} {strike}: {e}")
        return None, None


def find_strike_by_delta(ib: IB, ticker: str, expiry: str,
                          right: str, target_delta: float,
                          spot: float) -> float:
    """Trouve le strike le plus proche du delta cible."""
    try:
        chains = ib.reqSecDefOptParams(ticker, '', 'STK', 0)
        if not chains:
            return None
        strikes = sorted([s for s in chains[0].strikes if s > 0])

        # Filtrer les strikes pertinents
        if right == 'C':
            candidates = [s for s in strikes if spot * 0.95 < s < spot * 1.20]
        else:
            candidates = [s for s in strikes if spot * 0.80 < s < spot * 1.05]

        if not candidates:
            return None

        best_strike = None
        best_diff   = float('inf')

        for strike in candidates[:10]:  # Limiter les appels API
            _, delta = get_option_price(ib, ticker, expiry, strike, right)
            if delta is None:
                continue
            delta_abs = abs(delta)
            diff = abs(delta_abs - target_delta)
            if diff < best_diff:
                best_diff   = diff
                best_strike = strike

        return best_strike
    except Exception as e:
        log.warning(f"Strike search error {ticker}: {e}")
        return None


def place_option_order(ib: IB, ticker: str, expiry: str,
                       strike: float, right: str, action: str,
                       quantity: int, limit_price: float) -> bool:
    contract = Option(ticker, expiry, strike, right, 'SMART')
    try:
        ib.qualifyContracts(contract)
        order = LimitOrder(action, quantity, round(limit_price, 2))
        trade = ib.placeOrder(contract, order)
        log.info(f"Option ordre : {action} {quantity}x {ticker} {right}{strike} {expiry} @ ${limit_price:.2f}")

        timeout = 45
        while not trade.isDone() and timeout > 0:
            ib.sleep(1)
            timeout -= 1

        if trade.orderStatus.status == 'Filled':
            log.info(f"Rempli : {action} {ticker} {right}{strike}")
            save_option_position(ticker, expiry, strike, right, action, quantity,
                                 trade.orderStatus.avgFillPrice)
            return True
        else:
            log.warning(f"Non rempli : {ticker} {right}{strike} — {trade.orderStatus.status}")
            ib.cancelOrder(order)
            return False
    except Exception as e:
        log.warning(f"Option order error: {e}")
        return False


def save_option_position(ticker, expiry, strike, right, action,
                          quantity, fill_price):
    row = {
        'date':       datetime.now().strftime('%Y-%m-%d %H:%M'),
        'ticker':     ticker,
        'strategy':   'unknown',
        'expiry':     expiry,
        'strike':     strike,
        'right':      right,
        'action':     action,
        'quantity':   quantity,
        'fill_price': round(fill_price, 4),
        'premium':    round(fill_price * quantity * 100, 2),
    }
    df = pd.DataFrame([row])
    if os.path.exists(CSV_FILE):
        existing = pd.read_csv(CSV_FILE)
        df = pd.concat([existing, df], ignore_index=True)
    df.to_csv(CSV_FILE, index=False)


# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 1 : COVERED CALLS
# ══════════════════════════════════════════════════════════════════════════════

def run_covered_calls(ib: IB):
    """
    Vend des calls OTM (delta ~0.30) sur chaque position longue du momentum bot.
    Un contrat = 100 actions. Seulement si on a >= 100 actions du ticker.
    """
    log.info("=== COVERED CALLS ===")
    positions = get_positions(ib)
    total_premium = 0.0

    for ticker, pos in positions.items():
        if pos['secType'] != 'STK' or pos['shares'] < 100:
            continue

        contracts_to_sell = int(pos['shares'] / 100)
        spot = get_stock_price(ib, ticker)
        if not spot:
            continue

        expiry = next_expiry(ib, ticker, CC_DTE_TARGET)
        if not expiry:
            continue

        strike = find_strike_by_delta(ib, ticker, expiry, 'C', CC_DELTA_TARGET, spot)
        if not strike:
            continue

        mid, delta = get_option_price(ib, ticker, expiry, strike, 'C')
        if not mid or mid < MIN_PREMIUM:
            log.info(f"CC {ticker} : prime trop faible (${mid:.2f}) — skip")
            continue

        log.info(f"CC {ticker} : SELL {contracts_to_sell}x C{strike} {expiry} @ ${mid:.2f} (delta {delta:.2f})")
        success = place_option_order(ib, ticker, expiry, strike, 'C',
                                     'SELL', contracts_to_sell, mid * 0.95)
        if success:
            premium = mid * contracts_to_sell * 100
            total_premium += premium
            log.info(f"CC {ticker} : prime encaissée ${premium:.2f}")

    log.info(f"Covered Calls terminé — prime totale ${total_premium:.2f}")
    if total_premium > 0:
        alert_manager.notify_fill('options_bot', 'COVERED_CALLS', 'SELL',
                                  0, total_premium)


# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 2 : CASH-SECURED PUTS
# ══════════════════════════════════════════════════════════════════════════════

# Univers de tickers pour les CSP — actions de qualité qu'on accepte de détenir
CSP_UNIVERSE = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA',
    'META', 'JPM', 'V', 'UNH', 'JNJ',
    'CRDO', 'TMDX', 'RMBS', 'RCUS', 'TCMD',
]

def run_cash_secured_puts(ib: IB):
    """
    Vend des puts OTM (delta ~0.25) sur le cash disponible.
    Cash sécurisé = strike × 100 par contrat.
    """
    log.info("=== CASH-SECURED PUTS ===")
    cash = get_cash(ib)
    max_csp_capital = CAPITAL * MAX_OPTIONS_PCT
    deployed = 0.0

    for ticker in CSP_UNIVERSE:
        if deployed >= max_csp_capital:
            break

        spot = get_stock_price(ib, ticker)
        if not spot or spot > 500:  # Éviter les titres trop chers
            continue

        expiry = next_expiry(ib, ticker, CSP_DTE_TARGET)
        if not expiry:
            continue

        strike = find_strike_by_delta(ib, ticker, expiry, 'P', CSP_DELTA_TARGET, spot)
        if not strike:
            continue

        # Cash nécessaire par contrat
        cash_needed = strike * 100
        if deployed + cash_needed > max_csp_capital:
            continue

        mid, delta = get_option_price(ib, ticker, expiry, strike, 'P')
        if not mid or mid < MIN_PREMIUM:
            log.info(f"CSP {ticker} : prime trop faible — skip")
            continue

        log.info(f"CSP {ticker} : SELL 1x P{strike} {expiry} @ ${mid:.2f} (delta {delta:.2f})")
        success = place_option_order(ib, ticker, expiry, strike, 'P',
                                     'SELL', 1, mid * 0.95)
        if success:
            deployed += cash_needed
            log.info(f"CSP {ticker} : cash sécurisé ${cash_needed:.0f}")

    log.info(f"Cash-Secured Puts terminé — capital déployé ${deployed:.0f}")


# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 3 : STRADDLE PRE-EARNINGS
# ══════════════════════════════════════════════════════════════════════════════

def get_upcoming_earnings() -> list:
    """Récupère les résultats dans les 2 prochains jours via Finnhub."""
    if not FINNHUB_KEY:
        return []
    try:
        today = date.today()
        end   = today + timedelta(days=3)
        url   = f"https://finnhub.io/api/v1/calendar/earnings"
        params = {
            'from':  today.strftime('%Y-%m-%d'),
            'to':    end.strftime('%Y-%m-%d'),
            'token': FINNHUB_KEY,
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        earnings = data.get('earningsCalendar', [])
        # Filtrer les tickers de notre univers + momentum positions
        our_tickers = set(CSP_UNIVERSE)
        return [e['symbol'] for e in earnings if e['symbol'] in our_tickers]
    except Exception as e:
        log.warning(f"Finnhub earnings error: {e}")
        return []


def run_straddle_earnings(ib: IB):
    """
    Achète un straddle (call + put ATM) 2 jours avant les résultats.
    Ferme la position le lendemain des résultats.
    """
    log.info("=== STRADDLE PRE-EARNINGS ===")
    tickers = get_upcoming_earnings()
    if not tickers:
        log.info("Aucun résultat proche — skip")
        return

    for ticker in tickers:
        spot = get_stock_price(ib, ticker)
        if not spot:
            continue

        expiry = next_expiry(ib, ticker, STRADDLE_DTE)
        if not expiry:
            continue

        # Strike ATM (arrondi au $1 près)
        strike = round(spot)

        mid_call, _ = get_option_price(ib, ticker, expiry, strike, 'C')
        mid_put,  _ = get_option_price(ib, ticker, expiry, strike, 'P')

        if not mid_call or not mid_put:
            continue

        straddle_cost = (mid_call + mid_put) * 100
        log.info(f"Straddle {ticker} : coût ${straddle_cost:.2f} "
                 f"(C${mid_call:.2f} + P${mid_put:.2f})")

        # Acheter call
        ok_call = place_option_order(ib, ticker, expiry, strike, 'C',
                                     'BUY', 1, mid_call * 1.05)
        # Acheter put
        ok_put  = place_option_order(ib, ticker, expiry, strike, 'P',
                                     'BUY', 1, mid_put * 1.05)

        if ok_call and ok_put:
            log.info(f"Straddle {ticker} ouvert — coût ${straddle_cost:.2f}")


# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 4 : BULL PUT SPREAD
# ══════════════════════════════════════════════════════════════════════════════

# Tickers avec signal haussier (top momentum)
BULL_UNIVERSE = [
    'CRDO', 'TMDX', 'RMBS', 'RCUS', 'TCMD',
    'NVTS', 'DSGN', 'TDUP', 'LCID', 'GBTG',
]

def run_bull_put_spread(ib: IB):
    """
    Bull Put Spread : vend un put OTM + achète un put plus OTM.
    Profit max = prime nette. Perte max = écart strikes - prime.
    """
    log.info("=== BULL PUT SPREAD ===")
    deployed = 0.0
    max_capital = CAPITAL * 0.03  # Max 3% du capital

    for ticker in BULL_UNIVERSE:
        if deployed >= max_capital:
            break

        spot = get_stock_price(ib, ticker)
        if not spot:
            continue

        expiry = next_expiry(ib, ticker, 30)
        if not expiry:
            continue

        # Strike short put : ~5% OTM
        strike_short = round(spot * 0.95 / 5) * 5  # Arrondi au $5
        # Strike long put : SPREAD_WIDTH en dessous
        strike_long  = strike_short - SPREAD_WIDTH

        mid_short, _ = get_option_price(ib, ticker, expiry, strike_short, 'P')
        mid_long,  _ = get_option_price(ib, ticker, expiry, strike_long,  'P')

        if not mid_short or not mid_long:
            continue

        net_premium = mid_short - mid_long
        if net_premium < MIN_PREMIUM:
            log.info(f"BPS {ticker} : prime nette trop faible (${net_premium:.2f}) — skip")
            continue

        max_loss    = (SPREAD_WIDTH - net_premium) * 100
        risk_reward = net_premium / (SPREAD_WIDTH - net_premium)

        if risk_reward < 0.25:  # Minimum 1:4 risk/reward
            log.info(f"BPS {ticker} : ratio risque/rendement insuffisant — skip")
            continue

        log.info(f"BPS {ticker} : SELL P{strike_short} / BUY P{strike_long} "
                 f"@ net ${net_premium:.2f} (max loss ${max_loss:.0f})")

        ok_short = place_option_order(ib, ticker, expiry, strike_short, 'P',
                                      'SELL', 1, mid_short * 0.95)
        ok_long  = place_option_order(ib, ticker, expiry, strike_long,  'P',
                                      'BUY',  1, mid_long  * 1.05)

        if ok_short and ok_long:
            deployed += max_loss
            log.info(f"BPS {ticker} ouvert — prime ${net_premium*100:.2f}")

    log.info(f"Bull Put Spreads terminé — capital à risque ${deployed:.0f}")


# ══════════════════════════════════════════════════════════════════════════════
#  FERMETURE POSITIONS
# ══════════════════════════════════════════════════════════════════════════════

def close_expiring_options(ib: IB):
    """Ferme les options à moins de 5 jours d'expiration."""
    log.info("=== CLÔTURE OPTIONS PROCHES EXPIRATION ===")
    for pos in ib.positions():
        if pos.contract.secType != 'OPT':
            continue
        expiry = datetime.strptime(pos.contract.lastTradeDateOrContractMonth, '%Y%m%d').date()
        days_left = (expiry - date.today()).days
        if days_left <= 5 and pos.position != 0:
            action = 'BUY' if pos.position < 0 else 'SELL'
            price = get_option_price(ib, pos.contract.symbol,
                                     pos.contract.lastTradeDateOrContractMonth,
                                     pos.contract.strike,
                                     pos.contract.right)[0]
            if price:
                place_option_order(ib, pos.contract.symbol,
                                   pos.contract.lastTradeDateOrContractMonth,
                                   pos.contract.strike, pos.contract.right,
                                   action, abs(int(pos.position)), price * 1.05)
                log.info(f"Clôture {pos.contract.symbol} {pos.contract.right}"
                         f"{pos.contract.strike} — {days_left}j restants")


# ══════════════════════════════════════════════════════════════════════════════
#  SCHEDULER
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("Démarrage Options Bot")
    ib = connect()
    ib.sleep(2)

    def is_market_open():
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        market_open  = now.replace(hour=14, minute=30, second=0)
        market_close = now.replace(hour=21, minute=0, second=0)
        return market_open <= now <= market_close

    # Lundi 10h00 : Covered Calls + Bull Put Spreads
    schedule.every().monday.at("10:00").do(
        lambda: (run_covered_calls(ib), run_bull_put_spread(ib)) if is_market_open() else None
    )

    # Lundi + Jeudi 10h30 : Cash-Secured Puts
    schedule.every().monday.at("10:30").do(
        lambda: run_cash_secured_puts(ib) if is_market_open() else None
    )
    schedule.every().thursday.at("10:30").do(
        lambda: run_cash_secured_puts(ib) if is_market_open() else None
    )

    # Tous les jours 9h45 : Straddle pre-earnings
    schedule.every().day.at("09:45").do(
        lambda: run_straddle_earnings(ib) if is_market_open() else None
    )

    # Tous les jours 15h30 : Clôture options proches expiration
    schedule.every().day.at("15:30").do(
        lambda: close_expiring_options(ib) if is_market_open() else None
    )

    log.info("Scheduler actif :")
    log.info("  Lundi 10h00 — Covered Calls + Bull Put Spreads")
    log.info("  Lundi/Jeudi 10h30 — Cash-Secured Puts")
    log.info("  Quotidien 9h45 — Straddle pre-earnings")
    log.info("  Quotidien 15h30 — Clôture positions proches expiration")

    try:
        while True:
            schedule.run_pending()
            ib.sleep(60)
    except KeyboardInterrupt:
        log.info("Arrêt manuel")
    except Exception as e:
        alert_manager.notify_crash('options_bot', str(e))
        log.error(f"Erreur critique: {e}")
    finally:
        ib.disconnect()
        log.info("Déconnecté")


if __name__ == '__main__':
    main()
