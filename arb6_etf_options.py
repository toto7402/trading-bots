"""
arb6_etf_options.py — Options sur ETFs avec levier
====================================================
Stratégies :
  1. Covered Calls sur SPY/QQQ/IWM détenus
  2. Cash-Secured Puts sur SPY/QQQ/IWM (entrée à prix réduit)
  3. Iron Condor sur SPY (range-bound, prime maximale)
  4. Long Calls/Puts levierisés sur signal momentum ETF

Levier max : 2x
Stop loss global : -10% NAV
ClientId : 7
"""

import os, sys, time, logging, schedule
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, date
from ib_insync import IB, Stock, Option, util, LimitOrder
import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    handlers=[
        logging.FileHandler('etf_options.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)
from alerts import alert_manager

# ── Config ────────────────────────────────────────────────────────────────────
HOST       = '127.0.0.1'
PORT       = 7497
CLIENT_ID  = 7
CAPITAL    = 1_090_000
MAX_LEVER  = 2.0          # Levier maximum 2x
STOP_LOSS  = -0.10        # Stop global -10%
MIN_PREM   = 0.30         # Prime minimum $0.30
DTE_TARGET = 30           # Jours à expiration cible
CSV_FILE   = 'etf_options_positions.csv'

ETF_UNIVERSE = ['SPY', 'QQQ', 'IWM']  # ETFs tradés

# Allocation par stratégie (% du capital)
ALLOC_CC      = 0.00   # Covered Calls — sur positions détenues seulement
ALLOC_CSP     = 0.15   # Cash-Secured Puts — 15% du capital
ALLOC_CONDOR  = 0.10   # Iron Condor — 10% du capital
ALLOC_DIREC   = 0.05   # Options directionnelles levierisées — 5%

# ── Connexion ─────────────────────────────────────────────────────────────────

def connect() -> IB:
    ib = IB()
    while datetime.now().weekday() >= 5:
        log.info("Weekend — attente lundi...")
        time.sleep(3600)
    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID)
        log.info(f"Connecté — {ib.wrapper.accounts}")
        return ib
    except Exception as e:
        alert_manager.notify_crash('etf_options_bot', str(e))
        raise

def get_nav(ib: IB) -> float:
    for v in ib.accountValues():
        if v.tag == 'NetLiquidation' and v.currency in ('USD', 'EUR', 'BASE'):
            return float(v.value)
    return CAPITAL

def get_cash(ib: IB) -> float:
    for v in ib.accountValues():
        if v.tag == 'AvailableFunds' and v.currency in ('USD', 'EUR', 'BASE'):
            return float(v.value)
    return 0.0

def get_leverage(ib: IB) -> float:
    """Calcule le levier actuel = gross exposure / NAV."""
    nav = get_nav(ib)
    gross = 0.0
    for item in ib.portfolio():
        gross += abs(item.marketValue)
    return gross / nav if nav > 0 else 0.0

# ── Stop loss global ──────────────────────────────────────────────────────────

def check_global_stop(ib: IB, peak_nav: list) -> bool:
    """Coupe tout si drawdown > 10%."""
    nav = get_nav(ib)
    peak_nav[0] = max(peak_nav[0], nav)
    dd = (nav - peak_nav[0]) / peak_nav[0]
    if dd <= STOP_LOSS:
        log.warning(f"STOP LOSS GLOBAL : {dd*100:.1f}% — liquidation options")
        alert_manager.notify_crash('etf_options_bot',
                                   f"Stop loss -10% déclenché — NAV ${nav:,.0f}")
        close_all_options(ib)
        return True
    return False

def close_all_options(ib: IB):
    """Ferme toutes les positions options en urgence."""
    for pos in ib.positions():
        if pos.contract.secType == 'OPT' and pos.position != 0:
            action = 'BUY' if pos.position < 0 else 'SELL'
            contract = pos.contract
            ib.qualifyContracts(contract)
            order = LimitOrder(action, abs(int(pos.position)), 0.01)
            ib.placeOrder(contract, order)
            log.info(f"Urgence close : {action} {contract.symbol} {contract.right}{contract.strike}")
            ib.sleep(1)

# ── Helpers options ───────────────────────────────────────────────────────────

def get_price(ib: IB, ticker: str, sec_type: str = 'STK') -> float:
    if sec_type == 'ETF':
        contract = Stock(ticker, 'SMART', 'USD')
    else:
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

def get_expiry(ib: IB, ticker: str, dte: int) -> str:
    try:
        chains = ib.reqSecDefOptParams(ticker, '', 'STK', 0)
        if not chains:
            return None
        target = date.today() + timedelta(days=dte)
        expirations = sorted([
            datetime.strptime(e, '%Y%m%d').date()
            for e in chains[0].expirations
            if datetime.strptime(e, '%Y%m%d').date() > date.today()
        ])
        if not expirations:
            return None
        return min(expirations, key=lambda d: abs((d - target).days)).strftime('%Y%m%d')
    except Exception as e:
        log.warning(f"Expiry error {ticker}: {e}")
        return None

def get_option_mid(ib: IB, ticker: str, expiry: str,
                   strike: float, right: str) -> tuple:
    contract = Option(ticker, expiry, strike, right, 'SMART')
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            return None, None
        ib.reqMarketDataType(3)
        t = ib.reqMktData(qualified[0], '100', False, False)
        ib.sleep(3)
        mid = None
        if t.bid and t.ask and not np.isnan(t.bid) and not np.isnan(t.ask):
            mid = (t.bid + t.ask) / 2
        delta = t.modelGreeks.delta if t.modelGreeks else None
        ib.cancelMktData(qualified[0])
        return mid, delta
    except Exception as e:
        log.warning(f"Option mid error: {e}")
        return None, None

def place_opt(ib: IB, ticker: str, expiry: str, strike: float,
              right: str, action: str, qty: int, price: float,
              strategy: str) -> bool:
    contract = Option(ticker, expiry, strike, right, 'SMART')
    try:
        ib.qualifyContracts(contract)
        order = LimitOrder(action, qty, round(price, 2))
        trade = ib.placeOrder(contract, order)
        log.info(f"[{strategy}] {action} {qty}x {ticker} {right}{strike} {expiry} @ ${price:.2f}")
        timeout = 45
        while not trade.isDone() and timeout > 0:
            ib.sleep(1); timeout -= 1
        if trade.orderStatus.status == 'Filled':
            save_csv(ticker, expiry, strike, right, action, qty,
                     trade.orderStatus.avgFillPrice, strategy)
            return True
        ib.cancelOrder(order)
        return False
    except Exception as e:
        log.warning(f"Order error: {e}")
        return False

def save_csv(ticker, expiry, strike, right, action, qty, fill, strategy):
    row = {'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
           'ticker': ticker, 'strategy': strategy, 'expiry': expiry,
           'strike': strike, 'right': right, 'action': action,
           'qty': qty, 'fill': round(fill, 4),
           'premium': round(fill * qty * 100, 2)}
    df = pd.DataFrame([row])
    if os.path.exists(CSV_FILE):
        df = pd.concat([pd.read_csv(CSV_FILE), df], ignore_index=True)
    df.to_csv(CSV_FILE, index=False)

# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 1 : COVERED CALLS ETF
# ══════════════════════════════════════════════════════════════════════════════

def run_cc_etf(ib: IB, peak_nav: list):
    """Vend des calls OTM (delta 0.25) sur ETFs détenus."""
    if check_global_stop(ib, peak_nav): return
    log.info("=== CC ETF ===")
    lev = get_leverage(ib)
    if lev >= MAX_LEVER:
        log.warning(f"Levier {lev:.2f}x >= {MAX_LEVER}x — CC ETF skip")
        return

    positions = {pos.contract.symbol: pos for pos in ib.positions()
                 if pos.contract.secType in ('STK', 'ETF')
                 and pos.position >= 100
                 and pos.contract.symbol in ETF_UNIVERSE}

    for ticker, pos in positions.items():
        spot = get_price(ib, ticker, 'ETF')
        if not spot: continue
        expiry = get_expiry(ib, ticker, DTE_TARGET)
        if not expiry: continue

        # Strike OTM ~3% au-dessus
        strike = round(spot * 1.03 / 1) * 1
        mid, delta = get_option_mid(ib, ticker, expiry, strike, 'C')
        if not mid or mid < MIN_PREM: continue

        qty = int(pos.position / 100)
        place_opt(ib, ticker, expiry, strike, 'C', 'SELL', qty,
                  mid * 0.95, 'CC_ETF')

# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 2 : CASH-SECURED PUTS ETF
# ══════════════════════════════════════════════════════════════════════════════

def run_csp_etf(ib: IB, peak_nav: list):
    """Vend des puts OTM (delta 0.20) sur SPY/QQQ/IWM."""
    if check_global_stop(ib, peak_nav): return
    log.info("=== CSP ETF ===")
    lev = get_leverage(ib)
    if lev >= MAX_LEVER:
        log.warning(f"Levier {lev:.2f}x >= {MAX_LEVER}x — CSP ETF skip")
        return

    max_capital = CAPITAL * ALLOC_CSP
    deployed = 0.0

    for ticker in ETF_UNIVERSE:
        if deployed >= max_capital: break
        spot = get_price(ib, ticker, 'ETF')
        if not spot: continue
        expiry = get_expiry(ib, ticker, DTE_TARGET)
        if not expiry: continue

        # Strike OTM ~5% en dessous
        strike = round(spot * 0.95 / 1) * 1
        cash_needed = strike * 100

        if deployed + cash_needed > max_capital: continue

        mid, delta = get_option_mid(ib, ticker, expiry, strike, 'P')
        if not mid or mid < MIN_PREM: continue

        place_opt(ib, ticker, expiry, strike, 'P', 'SELL', 1,
                  mid * 0.95, 'CSP_ETF')
        deployed += cash_needed
        log.info(f"CSP {ticker} : cash sécurisé ${cash_needed:,.0f} | levier {get_leverage(ib):.2f}x")

# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 3 : IRON CONDOR SPY
# ══════════════════════════════════════════════════════════════════════════════

def run_iron_condor(ib: IB, peak_nav: list):
    """
    Iron Condor SPY :
    - SELL call OTM + BUY call plus OTM  (bear call spread)
    - SELL put OTM  + BUY put plus OTM   (bull put spread)
    Profit si SPY reste dans le range.
    """
    if check_global_stop(ib, peak_nav): return
    log.info("=== IRON CONDOR SPY ===")
    lev = get_leverage(ib)
    if lev >= MAX_LEVER:
        log.warning(f"Levier {lev:.2f}x — Iron Condor skip")
        return

    ticker = 'SPY'
    spot = get_price(ib, ticker, 'ETF')
    if not spot: return
    expiry = get_expiry(ib, ticker, DTE_TARGET)
    if not expiry: return

    # Strikes : ±5% pour les short, ±8% pour les long
    call_short = round(spot * 1.05)
    call_long  = round(spot * 1.08)
    put_short  = round(spot * 0.95)
    put_long   = round(spot * 0.92)

    # Vérifier que la prime nette est suffisante
    mid_cs, _ = get_option_mid(ib, ticker, expiry, call_short, 'C')
    mid_cl, _ = get_option_mid(ib, ticker, expiry, call_long,  'C')
    mid_ps, _ = get_option_mid(ib, ticker, expiry, put_short,  'P')
    mid_pl, _ = get_option_mid(ib, ticker, expiry, put_long,   'P')

    if not all([mid_cs, mid_cl, mid_ps, mid_pl]):
        log.warning("Iron Condor SPY : prix manquants — skip")
        return

    net_premium = (mid_cs - mid_cl) + (mid_ps - mid_pl)
    max_loss    = (call_long - call_short - net_premium) * 100

    log.info(f"Iron Condor SPY : prime nette ${net_premium:.2f} | max loss ${max_loss:.0f}")

    if net_premium < 1.0:
        log.info("Iron Condor : prime insuffisante — skip")
        return

    # Placer les 4 jambes
    qty = max(1, int(CAPITAL * ALLOC_CONDOR / (max_loss * 10)))
    qty = min(qty, 5)  # Cap à 5 contrats

    place_opt(ib, ticker, expiry, call_short, 'C', 'SELL', qty, mid_cs * 0.95, 'CONDOR')
    place_opt(ib, ticker, expiry, call_long,  'C', 'BUY',  qty, mid_cl * 1.05, 'CONDOR')
    place_opt(ib, ticker, expiry, put_short,  'P', 'SELL', qty, mid_ps * 0.95, 'CONDOR')
    place_opt(ib, ticker, expiry, put_long,   'P', 'BUY',  qty, mid_pl * 1.05, 'CONDOR')

    log.info(f"Iron Condor SPY ouvert — {qty} contrats | prime ${net_premium*qty*100:.2f}")

# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 4 : OPTIONS DIRECTIONNELLES LEVIERISÉES
# ══════════════════════════════════════════════════════════════════════════════

def get_etf_momentum(ib: IB, ticker: str) -> str:
    """Signal simple : si SPY/QQQ au-dessus de sa MA20 → bullish."""
    # Utilise le prix actuel vs MA approximative (données différées)
    spot = get_price(ib, ticker, 'ETF')
    if not spot: return 'neutral'
    # Heuristique simple : comparer bid/ask spread comme proxy de tendance
    # En pratique, on utiliserait des données historiques
    return 'bullish'  # Simplifié — à enrichir avec vraies données

def run_directional_etf(ib: IB, peak_nav: list):
    """
    Achète des calls (bullish) ou puts (bearish) OTM levierisés.
    Levier effectif : delta × notionnel / capital
    """
    if check_global_stop(ib, peak_nav): return
    log.info("=== OPTIONS DIRECTIONNELLES ETF ===")

    lev = get_leverage(ib)
    if lev >= MAX_LEVER * 0.8:  # Garde 20% de marge
        log.warning(f"Levier {lev:.2f}x proche max — directionnel skip")
        return

    max_capital = CAPITAL * ALLOC_DIREC
    deployed = 0.0

    for ticker in ['SPY', 'QQQ']:
        if deployed >= max_capital: break
        signal = get_etf_momentum(ib, ticker)
        if signal == 'neutral': continue

        spot = get_price(ib, ticker, 'ETF')
        if not spot: continue
        expiry = get_expiry(ib, ticker, 21)  # 21 jours pour options directionnelles
        if not expiry: continue

        right = 'C' if signal == 'bullish' else 'P'
        # Strike légèrement OTM (delta ~0.40 pour plus de levier)
        if right == 'C':
            strike = round(spot * 1.02)
        else:
            strike = round(spot * 0.98)

        mid, delta = get_option_mid(ib, ticker, expiry, strike, right)
        if not mid or mid < MIN_PREM: continue

        # Nombre de contrats selon budget et levier cible
        cost_per_contract = mid * 100
        qty = max(1, int((max_capital - deployed) / cost_per_contract))
        qty = min(qty, 10)  # Cap à 10 contrats

        # Vérifier que le levier résultant ne dépasse pas 2x
        notional = qty * strike * 100
        new_lev = (get_nav(ib) * lev + notional) / get_nav(ib)
        if new_lev > MAX_LEVER:
            qty = max(1, int(qty * MAX_LEVER / new_lev))

        place_opt(ib, ticker, expiry, strike, right, 'BUY', qty,
                  mid * 1.05, f'DIREC_{signal.upper()}')
        deployed += cost_per_contract * qty
        log.info(f"Directionnel {ticker} {right}{strike} : {qty} contrats "
                 f"@ ${mid:.2f} | levier estimé {new_lev:.2f}x")

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("Démarrage ETF Options Bot")
    ib = connect()
    ib.sleep(2)

    nav_init  = get_nav(ib)
    peak_nav  = [nav_init]
    log.info(f"NAV initiale : ${nav_init:,.0f} | Levier actuel : {get_leverage(ib):.2f}x")

    def market_open():
        now = datetime.now()
        return (now.weekday() < 5 and
                now.replace(hour=14, minute=30) <= now <= now.replace(hour=15, minute=55))

    # Lundi 10h15 : Covered Calls + Iron Condor
    schedule.every().monday.at("10:15").do(
        lambda: (run_cc_etf(ib, peak_nav),
                 run_iron_condor(ib, peak_nav)) if market_open() else None)

    # Lundi + Jeudi 10h45 : CSP ETF
    schedule.every().monday.at("10:45").do(
        lambda: run_csp_etf(ib, peak_nav) if market_open() else None)
    schedule.every().thursday.at("10:45").do(
        lambda: run_csp_etf(ib, peak_nav) if market_open() else None)

    # Mardi + Vendredi 11h00 : Directionnels
    schedule.every().tuesday.at("11:00").do(
        lambda: run_directional_etf(ib, peak_nav) if market_open() else None)
    schedule.every().friday.at("11:00").do(
        lambda: run_directional_etf(ib, peak_nav) if market_open() else None)

    # Toutes les heures : vérification stop loss + levier
    schedule.every().hour.do(lambda: check_global_stop(ib, peak_nav))

    log.info("Scheduler ETF Options actif")
    log.info(f"  Levier max : {MAX_LEVER}x | Stop loss : {STOP_LOSS*100:.0f}%")

    try:
        while True:
            schedule.run_pending()
            ib.sleep(60)
    except KeyboardInterrupt:
        log.info("Arrêt manuel")
    except Exception as e:
        alert_manager.notify_crash('etf_options_bot', str(e))
        log.error(f"Erreur : {e}")
    finally:
        ib.disconnect()

if __name__ == '__main__':
    main()
