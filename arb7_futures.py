"""
arb7_futures.py — Futures CME avec levier et gestion du risque
==============================================================
Marchés :
  /GC  — Or (100 oz, ~$270,000 notionnel)
  /CL  — Pétrole WTI (1,000 barils, ~$70,000)
  /ES  — S&P 500 E-mini (50×, ~$260,000)
  /NQ  — Nasdaq E-mini (20×, ~$420,000)
  /ZN  — Bons du Trésor 10Y (100,000$)

Stratégies :
  1. Momentum trend following sur chaque contrat
  2. Pair trading Or vs ZN (corrélation négative taux/or)
  3. Mean reversion /ES intrajournalier

Levier max : 2x (notionnel / NAV)
Stop loss global : -10% NAV
Stop loss par position : -2% NAV
ClientId : 8
"""

import os, sys, time, logging, schedule
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, date
from ib_insync import IB, Future, util, LimitOrder, StopOrder
import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    handlers=[
        logging.FileHandler('futures_trading.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)
from alerts import alert_manager

# ── Config ────────────────────────────────────────────────────────────────────
HOST       = '127.0.0.1'
PORT       = 7497
CLIENT_ID  = 8
CAPITAL    = 1_090_000
MAX_LEVER  = 2.0
STOP_LOSS  = -0.10        # Stop global -10% NAV
POS_STOP   = -0.02        # Stop par position -2% NAV
CSV_FILE   = 'futures_positions.csv'

# Spécifications des contrats
FUTURES_SPEC = {
    '/GC': {'symbol': 'GC', 'exchange': 'COMEX', 'mult': 100,    'currency': 'USD', 'name': 'Or'},
    '/CL': {'symbol': 'CL', 'exchange': 'NYMEX', 'mult': 1000,   'currency': 'USD', 'name': 'Petrole'},
    '/ES': {'symbol': 'ES', 'exchange': 'CME',   'mult': 50,     'currency': 'USD', 'name': 'SP500'},
    '/NQ': {'symbol': 'NQ', 'exchange': 'CME',   'mult': 20,     'currency': 'USD', 'name': 'Nasdaq'},
    '/ZN': {'symbol': 'ZN', 'exchange': 'CBOT',  'mult': 1000,   'currency': 'USD', 'name': 'Tresor10Y'},
}

# Allocation max par contrat (% du capital)
MAX_ALLOC_PER_CONTRACT = 0.15  # 15% du capital par contrat

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
        alert_manager.notify_crash('futures_bot', str(e))
        raise

def get_nav(ib: IB) -> float:
    for v in ib.accountValues():
        if v.tag == 'NetLiquidation' and v.currency in ('USD', 'EUR', 'BASE'):
            return float(v.value)
    return CAPITAL

def get_leverage(ib: IB) -> float:
    nav = get_nav(ib)
    gross = sum(abs(item.marketValue) for item in ib.portfolio())
    return gross / nav if nav > 0 else 0.0

# ── Stop loss global ──────────────────────────────────────────────────────────

def check_global_stop(ib: IB, peak_nav: list) -> bool:
    nav = get_nav(ib)
    peak_nav[0] = max(peak_nav[0], nav)
    dd = (nav - peak_nav[0]) / peak_nav[0]
    if dd <= STOP_LOSS:
        log.warning(f"STOP LOSS GLOBAL FUTURES : {dd*100:.1f}%")
        alert_manager.notify_crash('futures_bot',
            f"Stop loss -10% déclenché — NAV ${nav:,.0f} — liquidation futures")
        close_all_futures(ib)
        return True
    return False

def close_all_futures(ib: IB):
    for pos in ib.positions():
        if pos.contract.secType == 'FUT' and pos.position != 0:
            action = 'SELL' if pos.position > 0 else 'BUY'
            contract = pos.contract
            ib.qualifyContracts(contract)
            order = LimitOrder(action, abs(int(pos.position)), 0)
            ib.placeOrder(contract, order)
            log.info(f"Urgence close futures : {action} {contract.symbol}")
            ib.sleep(1)

# ── Helpers futures ───────────────────────────────────────────────────────────

def get_front_contract(ib: IB, symbol: str, exchange: str) -> object:
    """Récupère le contrat front-month."""
    try:
        # Générer les prochains mois
        today = date.today()
        for months_ahead in range(0, 6):
            month = (today.month + months_ahead - 1) % 12 + 1
            year  = today.year + (today.month + months_ahead - 1) // 12
            expiry = f"{year}{month:02d}"
            contract = Future(symbol, expiry, exchange)
            try:
                qualified = ib.qualifyContracts(contract)
                if qualified:
                    return qualified[0]
            except Exception:
                continue
        return None
    except Exception as e:
        log.warning(f"Front contract error {symbol}: {e}")
        return None

def get_future_price(ib: IB, contract) -> float:
    try:
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
        log.warning(f"Future price error: {e}")
        return None

def place_future_order(ib: IB, contract, action: str, qty: int,
                        price: float, stop_price: float = None) -> bool:
    try:
        limit_order = LimitOrder(action, qty, round(price, 2))
        trade = ib.placeOrder(contract, limit_order)
        log.info(f"Futures {action} {qty}x {contract.symbol} @ ${price:.2f}")

        timeout = 60
        while not trade.isDone() and timeout > 0:
            ib.sleep(1); timeout -= 1

        if trade.orderStatus.status == 'Filled':
            fill_price = trade.orderStatus.avgFillPrice
            log.info(f"Rempli : {action} {contract.symbol} @ ${fill_price:.2f}")

            # Placer le stop loss automatiquement
            if stop_price:
                stop_action = 'SELL' if action == 'BUY' else 'BUY'
                stop_order = StopOrder(stop_action, qty, round(stop_price, 2))
                ib.placeOrder(contract, stop_order)
                log.info(f"Stop loss placé : {contract.symbol} @ ${stop_price:.2f}")

            save_future_csv(contract.symbol, action, qty, fill_price, stop_price)
            return True
        else:
            ib.cancelOrder(limit_order)
            return False
    except Exception as e:
        log.warning(f"Future order error: {e}")
        return False

def save_future_csv(symbol, action, qty, fill, stop):
    row = {'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
           'symbol': symbol, 'action': action, 'qty': qty,
           'fill': round(fill, 4), 'stop': round(stop, 4) if stop else None}
    df = pd.DataFrame([row])
    if os.path.exists(CSV_FILE):
        df = pd.concat([pd.read_csv(CSV_FILE), df], ignore_index=True)
    df.to_csv(CSV_FILE, index=False)

# ── Calcul notionnel et levier ────────────────────────────────────────────────

def calc_notional(symbol: str, price: float, qty: int) -> float:
    spec = FUTURES_SPEC.get(f'/{symbol}', {})
    mult = spec.get('mult', 1)
    return price * mult * qty

def max_contracts(ib: IB, symbol: str, price: float) -> int:
    """Calcule le nombre max de contrats selon levier et allocation."""
    nav = get_nav(ib)
    lev = get_leverage(ib)
    remaining_lever = (MAX_LEVER - lev) * nav
    max_alloc = nav * MAX_ALLOC_PER_CONTRACT
    budget = min(remaining_lever, max_alloc)
    spec = FUTURES_SPEC.get(f'/{symbol}', {})
    notional_per = price * spec.get('mult', 1)
    if notional_per <= 0:
        return 0
    return max(0, int(budget / notional_per))

# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 1 : MOMENTUM TREND FOLLOWING
# ══════════════════════════════════════════════════════════════════════════════

# Historique des prix pour signaux momentum (en mémoire)
price_history_futures: dict = {}

def update_price_history(symbol: str, price: float):
    if symbol not in price_history_futures:
        price_history_futures[symbol] = []
    price_history_futures[symbol].append(price)
    if len(price_history_futures[symbol]) > 200:
        price_history_futures[symbol].pop(0)

def get_momentum_signal(symbol: str) -> str:
    """
    Signal momentum :
    - MA20 > MA50 → LONG
    - MA20 < MA50 → SHORT
    - Sinon → FLAT
    """
    hist = price_history_futures.get(symbol, [])
    if len(hist) < 50:
        return 'FLAT'
    arr = np.array(hist)
    ma20 = arr[-20:].mean()
    ma50 = arr[-50:].mean()
    if ma20 > ma50 * 1.001:
        return 'LONG'
    elif ma20 < ma50 * 0.999:
        return 'SHORT'
    return 'FLAT'

def run_futures_momentum(ib: IB, peak_nav: list):
    """Trend following sur tous les futures configurés."""
    if check_global_stop(ib, peak_nav): return
    log.info("=== FUTURES MOMENTUM ===")
    lev = get_leverage(ib)
    log.info(f"Levier actuel : {lev:.2f}x")

    if lev >= MAX_LEVER:
        log.warning(f"Levier max {MAX_LEVER}x atteint — skip")
        return

    current_positions = {pos.contract.symbol: pos.position
                         for pos in ib.positions()
                         if pos.contract.secType == 'FUT'}

    for fut_key, spec in FUTURES_SPEC.items():
        symbol   = spec['symbol']
        exchange = spec['exchange']

        contract = get_front_contract(ib, symbol, exchange)
        if not contract:
            log.warning(f"Contrat introuvable : {symbol}")
            continue

        price = get_future_price(ib, contract)
        if not price:
            continue

        update_price_history(symbol, price)
        signal = get_momentum_signal(symbol)
        current_pos = current_positions.get(symbol, 0)

        log.info(f"{symbol} @ ${price:.2f} | Signal : {signal} | Pos actuelle : {current_pos}")

        if signal == 'FLAT':
            continue

        # Fermer position inverse si nécessaire
        if (signal == 'LONG' and current_pos < 0) or \
           (signal == 'SHORT' and current_pos > 0):
            close_action = 'BUY' if current_pos < 0 else 'SELL'
            place_future_order(ib, contract, close_action, abs(current_pos), price)
            ib.sleep(2)
            current_pos = 0

        # Ouvrir nouvelle position si pas déjà dedans
        if (signal == 'LONG' and current_pos <= 0) or \
           (signal == 'SHORT' and current_pos >= 0):

            qty = max_contracts(ib, symbol, price)
            if qty <= 0:
                log.info(f"{symbol} : pas de budget disponible")
                continue

            action = 'BUY' if signal == 'LONG' else 'SELL'
            # Stop loss à 2% du notionnel
            stop_pct = 0.02
            stop_price = price * (1 - stop_pct) if action == 'BUY' else price * (1 + stop_pct)

            notional = calc_notional(symbol, price, qty)
            log.info(f"{symbol} : {action} {qty} contrat(s) | notionnel ${notional:,.0f}")

            place_future_order(ib, contract, action, qty, price, stop_price)

# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 2 : PAIR TRADING OR vs ZN (TAUX)
# ══════════════════════════════════════════════════════════════════════════════

def run_gold_bonds_pair(ib: IB, peak_nav: list):
    """
    Or et bons du trésor ont une corrélation historique :
    Quand les taux montent (ZN baisse) → or tend à baisser aussi
    Quand les taux baissent (ZN monte) → or tend à monter
    
    On trade le spread : LONG GC / SHORT ZN si spread écarté
    """
    if check_global_stop(ib, peak_nav): return
    log.info("=== PAIR TRADING OR/ZN ===")

    gc_contract = get_front_contract(ib, 'GC', 'COMEX')
    zn_contract = get_front_contract(ib, 'ZN', 'CBOT')

    if not gc_contract or not zn_contract:
        log.warning("Contrats GC/ZN introuvables")
        return

    gc_price = get_future_price(ib, gc_contract)
    zn_price = get_future_price(ib, zn_contract)

    if not gc_price or not zn_price:
        return

    update_price_history('GC_pair', gc_price)
    update_price_history('ZN_pair', zn_price)

    hist_gc = price_history_futures.get('GC_pair', [])
    hist_zn = price_history_futures.get('ZN_pair', [])

    if len(hist_gc) < 60 or len(hist_zn) < 60:
        log.info("Pair GC/ZN : historique insuffisant")
        return

    # Calculer le ratio et ses bandes de Bollinger
    ratio = np.array(hist_gc[-60:]) / np.array(hist_zn[-60:])
    ratio_mean = ratio.mean()
    ratio_std  = ratio.std()
    current_ratio = gc_price / zn_price

    z_score = (current_ratio - ratio_mean) / ratio_std
    log.info(f"Or/ZN ratio : {current_ratio:.3f} | Z-score : {z_score:.2f}")

    nav = get_nav(ib)
    lev = get_leverage(ib)

    if abs(z_score) < 1.5 or lev >= MAX_LEVER:
        return

    if z_score > 2.0:
        # Or trop cher vs taux → SHORT GC
        log.info(f"Pair : SHORT GC (z={z_score:.2f})")
        qty_gc = max_contracts(ib, 'GC', gc_price)
        if qty_gc > 0:
            stop = gc_price * 1.02
            place_future_order(ib, gc_contract, 'SELL', qty_gc, gc_price, stop)

    elif z_score < -2.0:
        # Or trop bon marché vs taux → LONG GC
        log.info(f"Pair : LONG GC (z={z_score:.2f})")
        qty_gc = max_contracts(ib, 'GC', gc_price)
        if qty_gc > 0:
            stop = gc_price * 0.98
            place_future_order(ib, gc_contract, 'BUY', qty_gc, gc_price, stop)

# ══════════════════════════════════════════════════════════════════════════════
#  STRATÉGIE 3 : MEAN REVERSION /ES INTRAJOURNALIER
# ══════════════════════════════════════════════════════════════════════════════

es_intraday_prices: list = []

def run_es_mean_reversion(ib: IB, peak_nav: list):
    """
    S&P 500 E-mini : mean reversion intrajournalier.
    Si ES s'écarte de >1.5% de son ouverture → trade en sens inverse.
    Ferme en fin de journée.
    """
    if check_global_stop(ib, peak_nav): return

    now = datetime.now()
    # Ne trader qu'entre 10h et 15h
    if not (10 <= now.hour < 15):
        return

    log.info("=== ES MEAN REVERSION ===")

    es_contract = get_front_contract(ib, 'ES', 'CME')
    if not es_contract: return

    price = get_future_price(ib, es_contract)
    if not price: return

    es_intraday_prices.append(price)

    # Référence = première mesure de la journée
    if len(es_intraday_prices) < 3:
        return

    open_price = es_intraday_prices[0]
    deviation  = (price - open_price) / open_price

    log.info(f"ES @ ${price:.2f} | Open ${open_price:.2f} | Ecart {deviation*100:.2f}%")

    current_pos = next(
        (pos.position for pos in ib.positions()
         if pos.contract.secType == 'FUT' and pos.contract.symbol == 'ES'),
        0
    )

    lev = get_leverage(ib)
    if lev >= MAX_LEVER: return

    if deviation > 0.015 and current_pos >= 0:
        # ES monte trop → SHORT
        qty = max_contracts(ib, 'ES', price)
        qty = min(qty, 1)  # Max 1 contrat ES (notionnel ~$250,000)
        if qty > 0:
            stop = price * 1.01
            log.info(f"ES MR : SHORT 1 contrat (écart +{deviation*100:.2f}%)")
            place_future_order(ib, es_contract, 'SELL', qty, price, stop)

    elif deviation < -0.015 and current_pos <= 0:
        # ES baisse trop → LONG
        qty = max_contracts(ib, 'ES', price)
        qty = min(qty, 1)
        if qty > 0:
            stop = price * 0.99
            log.info(f"ES MR : LONG 1 contrat (écart {deviation*100:.2f}%)")
            place_future_order(ib, es_contract, 'BUY', qty, price, stop)

def close_es_eod(ib: IB):
    """Ferme toutes les positions ES en fin de journée."""
    log.info("=== FERMETURE EOD ES ===")
    es_intraday_prices.clear()
    for pos in ib.positions():
        if pos.contract.secType == 'FUT' and pos.contract.symbol == 'ES':
            if pos.position != 0:
                action = 'SELL' if pos.position > 0 else 'BUY'
                contract = pos.contract
                ib.qualifyContracts(contract)
                price = get_future_price(ib, contract)
                if price:
                    place_future_order(ib, contract, action,
                                       abs(int(pos.position)), price)
                    log.info(f"EOD close ES : {action} {abs(int(pos.position))}")

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("Démarrage Futures Bot")
    ib = connect()
    ib.sleep(2)

    nav_init = get_nav(ib)
    peak_nav = [nav_init]
    log.info(f"NAV : ${nav_init:,.0f} | Levier : {get_leverage(ib):.2f}x")
    log.info(f"Stop loss global : {STOP_LOSS*100:.0f}% | Max levier : {MAX_LEVER}x")

    def market_open():
        now = datetime.now()
        return now.weekday() < 5 and \
               now.replace(hour=9,minute=30) <= now <= now.replace(hour=15,minute=55)

    # Lundi + Mercredi 9h50 : Momentum trend following
    schedule.every().monday.at("09:50").do(
        lambda: run_futures_momentum(ib, peak_nav) if market_open() else None)
    schedule.every().wednesday.at("09:50").do(
        lambda: run_futures_momentum(ib, peak_nav) if market_open() else None)

    # Mardi + Jeudi 10h05 : Pair trading Or/ZN
    schedule.every().tuesday.at("10:05").do(
        lambda: run_gold_bonds_pair(ib, peak_nav) if market_open() else None)
    schedule.every().thursday.at("10:05").do(
        lambda: run_gold_bonds_pair(ib, peak_nav) if market_open() else None)

    # Toutes les 15 min entre 10h et 15h : ES mean reversion
    schedule.every(15).minutes.do(
        lambda: run_es_mean_reversion(ib, peak_nav) if market_open() else None)

    # 15h45 : Fermeture EOD ES
    schedule.every().day.at("15:45").do(
        lambda: close_es_eod(ib) if datetime.now().weekday() < 5 else None)

    # Toutes les heures : vérification stop loss + levier
    schedule.every().hour.do(lambda: check_global_stop(ib, peak_nav))

    log.info("Scheduler Futures actif :")
    log.info("  Lundi/Mercredi 9h50 — Momentum trend following")
    log.info("  Mardi/Jeudi 10h05 — Pair trading Or/ZN")
    log.info("  Toutes les 15min — ES mean reversion (10h-15h)")
    log.info("  15h45 — Fermeture EOD ES")

    try:
        while True:
            schedule.run_pending()
            ib.sleep(60)
    except KeyboardInterrupt:
        log.info("Arrêt manuel")
    except Exception as e:
        alert_manager.notify_crash('futures_bot', str(e))
        log.error(f"Erreur : {e}")
    finally:
        ib.disconnect()

if __name__ == '__main__':
    main()
