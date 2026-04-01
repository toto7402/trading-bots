import numpy as np
import pandas as pd
from ib_insync import IB, Stock, LimitOrder
import requests
import logging
import os
from datetime import datetime, timedelta
import schedule

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    handlers=[logging.FileHandler('spinoff.log'), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

HOST      = '127.0.0.1'
PORT      = 7497
CLIENT_ID = 3
CAPITAL   = 50_000
POS_SIZE  = 0.05
HOLD_DAYS_MIN = 5
HOLD_DAYS_MAX = 90
MAX_POSITIONS = 6

FINNHUB_KEY = "d6q4h6hr01qhcrmirjn0d6q4h6hr01qhcrmirjng"




def days_held(df, col='entry_date'):
    now = datetime.now()
    return df[col].apply(lambda x: (now - pd.to_datetime(x)).days if pd.notna(x) else 0)

def connect():
    ib = IB()
    retry_delay = 5
    max_delay = 300
    while True:
        try:
            ib.connect(HOST, PORT, clientId=CLIENT_ID)
            log.info(f"Connecte — {ib.wrapper.accounts}")
            retry_delay = 5  # reset on success
            return ib
        except Exception as e:
            log.error(f"API connection failed: {e}. Retrying in {retry_delay}s")
            import time as _time
            _time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)


def get_nav(ib):
    for v in ib.accountValues():
        if v.tag == 'NetLiquidation' and v.currency == 'USD':
            return float(v.value)
    return CAPITAL


def qualify(ib, ticker):
    from ib_insync import Stock
    c = Stock(ticker, 'SMART', 'USD')
    try:
        q = ib.qualifyContracts(c)
        return q[0] if q else None
    except Exception:
        return None


def get_price(ib, ticker):
    c = qualify(ib, ticker)
    if not c:
        return None
    ib.reqMarketDataType(3)
    t = ib.reqMktData(c, '', False, False)
    ib.sleep(3)
    price = None
    for attr in [t.last, t.close, t.bid]:
        try:
            if attr and not np.isnan(float(attr)):
                price = float(attr)
                break
        except Exception:
            continue
    ib.cancelMktData(c)
    return price


def fetch_recent_spinoffs():
    """
    Détecte les spin-offs récents via Finnhub IPO calendar
    filtré sur les opérations de type spin-off.
    Les spin-offs apparaissent comme des nouvelles cotations
    avec un parent identifiable.
    """
    url   = "https://finnhub.io/api/v1/stock/ipo-calendar"
    today = datetime.now()
    from_ = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    to_   = today.strftime('%Y-%m-%d')

    try:
        r    = requests.get(url, params={'from': from_, 'to': to_, 'token': FINNHUB_KEY}, timeout=10)
        data = r.json().get('ipoCalendar', [])
    except Exception as e:
        log.error(f"Finnhub IPO error : {e}")
        return []

    spinoffs = []
    for item in data:
        name   = (item.get('name') or '').lower()
        status = (item.get('status') or '').lower()
        ticker = item.get('symbol', '')

        is_spinoff = any(kw in name for kw in
                         ['spin', 'spinoff', 'spin-off', 'separation',
                          'carve', 'divestiture', 'split-off'])

        if is_spinoff and status == 'priced' and ticker:
            spinoffs.append({
                'ticker': ticker,
                'name':   item.get('name', ''),
                'date':   item.get('date', ''),
                'price':  float(item.get('price') or 0),
            })

    # Fallback : liste statique de spin-offs connus récents (2024-2025)
    # A mettre a jour manuellement ou via un flux RSS
    known_recent = [
        {'ticker': 'VSNT',  'name': 'Versant Media (Comcast spinoff)',       'date': '2026-01-02', 'price': 0},
        {'ticker': 'LLYVA', 'name': 'Liberty Live Holdings A (Liberty Media)', 'date': '2025-12-16', 'price': 0},
        {'ticker': 'LLYVK', 'name': 'Liberty Live Holdings K (Liberty Media)', 'date': '2025-12-16', 'price': 0},
        {'ticker': 'SOLAM', 'name': 'Solstice Advanced Materials (Honeywell)', 'date': '2025-10-30', 'price': 0},
        {'ticker': 'Q',     'name': 'Qnity Electronics (DuPont)',              'date': '2025-11-01', 'price': 0},
        {'ticker': 'GLIBA', 'name': 'GCI Liberty A (Liberty Broadband)',       'date': '2025-07-15', 'price': 0},
        {'ticker': 'GLIBK', 'name': 'GCI Liberty K (Liberty Broadband)',       'date': '2025-07-15', 'price': 0},
    ]

    for k in known_recent:
        if k['ticker'] not in [s['ticker'] for s in spinoffs]:
            spinoffs.append(k)

    log.info(f"Spin-offs detectes : {len(spinoffs)}")
    return spinoffs


def place_order(ib, ticker, action, shares):
    if shares <= 0:
        return False
    c = qualify(ib, ticker)
    if not c:
        return False
    price = get_price(ib, ticker)
    if not price:
        return False
    lmt   = round(price * (1.005 if action == 'BUY' else 0.995), 2)
    order = LimitOrder(action, shares, lmt, tif='DAY')
    trade = ib.placeOrder(c, order)
    log.info(f"Ordre {action} {shares} {ticker} @ ${lmt:.2f}")
    timeout = 60
    while not trade.isDone() and timeout > 0:
        ib.sleep(1)
        timeout -= 1
    if trade.orderStatus.status == 'Filled':
        log.info(f"Rempli {action} {shares} {ticker} @ ${trade.orderStatus.avgFillPrice:.2f}")
        return True
    log.warning(f"Non rempli : {ticker}")
    ib.cancelOrder(trade.order)
    return False


def load_positions():
    if os.path.exists('spinoff_positions.csv'):
        return pd.read_csv('spinoff_positions.csv', parse_dates=['entry_date'])
    return pd.DataFrame(columns=['ticker','shares','entry_price','entry_date','spinoff_name'])


def save_positions(df):
    df.to_csv('spinoff_positions.csv', index=False)


def run(ib):
    log.info("=== SPINOFF SCAN ===")
    nav     = get_nav(ib)
    pos_cap = nav * POS_SIZE
    existing = load_positions()

    # Clôturer les positions après HOLD_DAYS_MAX
    to_close = existing[
        days_held(existing) >= HOLD_DAYS_MAX
    ]
    for _, row in to_close.iterrows():
        log.info(f"Cloture spinoff {row['ticker']} apres {HOLD_DAYS_MAX}j")
        place_order(ib, row['ticker'], 'SELL', int(row['shares']))

    existing = existing[
        days_held(existing) < HOLD_DAYS_MAX
    ]

    if len(existing) >= MAX_POSITIONS:
        log.info("Positions max atteintes")
        save_positions(existing)
        return

    spinoffs = fetch_recent_spinoffs()
    held     = set(existing['ticker'].tolist())
    new_entries = []

    for s in spinoffs:
        ticker = s['ticker']
        if ticker in held:
            continue

        spinoff_date = pd.to_datetime(s.get('date', ''), errors='coerce')
        if pd.isna(spinoff_date):
            continue

        days_since = (datetime.now() - spinoff_date).days

        # On entre entre J+5 et J+30 (après la vente mécanique des institutionnels)
        if not (HOLD_DAYS_MIN <= days_since <= 30):
            log.info(f"{ticker} : {days_since}j depuis spinoff — hors fenetre entry")
            continue

        price = get_price(ib, ticker)
        if not price or price < 3:
            continue

        shares = int(pos_cap / price)
        if shares == 0:
            continue

        ok = place_order(ib, ticker, 'BUY', shares)
        if ok:
            new_entries.append({
                'ticker':       ticker,
                'shares':       shares,
                'entry_price':  price,
                'entry_date':   datetime.now(),
                'spinoff_name': s.get('name', ''),
            })
            log.info(f"Spinoff entry : {ticker} J+{days_since}")

        if len(existing) + len(new_entries) >= MAX_POSITIONS:
            break

    if new_entries:
        existing = pd.concat([existing, pd.DataFrame(new_entries)], ignore_index=True)

    save_positions(existing)
    log.info(f"Positions actives : {len(existing)}")


def main():
    if FINNHUB_KEY == "VOTRE_CLE_FINNHUB":
        print("Obtenir une cle gratuite sur finnhub.io et remplacer FINNHUB_KEY")
        return

    retry_delay = 5
    max_delay = 300
    while True:
        try:
            ib = connect()
            run(ib)
            schedule.every().monday.at("09:45").do(lambda: run(ib))
            schedule.every().thursday.at("09:45").do(lambda: run(ib))
            log.info("Spinoff actif — scan lundi et jeudi a 09h45")
            retry_delay = 5  # reset on successful connect
            while True:
                schedule.run_pending()
                if not ib.isConnected():
                    log.warning("IB connection lost — reconnecting")
                    break
                ib.sleep(60)
        except KeyboardInterrupt:
            log.info("Arret")
            break
        except Exception as e:
            log.error(f"Connection failed: {e}. Retrying in {retry_delay}s")
            import time as _time
            _time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)
    try:
        ib.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    main()
