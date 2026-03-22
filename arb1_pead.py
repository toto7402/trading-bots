import numpy as np
import pandas as pd
from ib_insync import IB, Stock, LimitOrder
import requests
import logging
import time
import os
from datetime import datetime, timedelta
import schedule

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    handlers=[logging.FileHandler('pead.log'), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

HOST      = '127.0.0.1'
PORT         = 4002
CLIENT_ID = 2
CAPITAL   = 1_090_000
POS_SIZE  = 0.04
HOLD_DAYS = 60
MIN_SURPRISE = 0.05
MAX_POSITIONS = 8

FINNHUB_KEY = "d6q4h6hr01qhcrmirjn0d6q4h6hr01qhcrmirjng"


def connect():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
    log.info(f"Connecte — {ib.wrapper.accounts}")
    return ib


def get_nav(ib):
    for v in ib.accountValues():
        if v.tag == 'NetLiquidation' and v.currency in ('USD', 'EUR', 'BASE'):
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


def fetch_earnings_surprises():
    """
    Récupère les earnings surprises via Finnhub (gratuit jusqu'à 60 req/min).
    Retourne les tickers avec surprise > MIN_SURPRISE dans les 5 derniers jours.
    """
    url = "https://finnhub.io/api/v1/calendar/earnings"
    today = datetime.now()
    from_date = (today - timedelta(days=5)).strftime('%Y-%m-%d')
    to_date   = today.strftime('%Y-%m-%d')

    try:
        r = requests.get(url, params={
            'from':   from_date,
            'to':     to_date,
            'token':  FINNHUB_KEY,
        }, timeout=10)
        data = r.json().get('earningsCalendar', [])
    except Exception as e:
        log.error(f"Finnhub fetch error : {e}")
        return []

    candidates = []
    for item in data:
        try:
            actual   = float(item.get('epsActual') or 0)
            estimate = float(item.get('epsEstimate') or 0)
            ticker   = item.get('symbol', '')

            if not ticker or estimate <= 0:
                continue

            surprise = (actual - estimate) / abs(estimate)

            if surprise >= MIN_SURPRISE:
                candidates.append({
                    'ticker':   ticker,
                    'surprise': round(surprise, 3),
                    'actual':   actual,
                    'estimate': estimate,
                    'date':     item.get('date', ''),
                })
        except Exception:
            continue

    candidates.sort(key=lambda x: x['surprise'], reverse=True)
    log.info(f"Earnings surprises trouvees : {len(candidates)}")
    return candidates[:MAX_POSITIONS]


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
    if os.path.exists('pead_positions.csv'):
        return pd.read_csv('pead_positions.csv', parse_dates=['entry_date'])
    return pd.DataFrame(columns=['ticker','shares','entry_price','entry_date','surprise'])


def save_positions(df):
    df.to_csv('pead_positions.csv', index=False)


def days_held(df):
    now = datetime.now()
    dates = pd.to_datetime(df["entry_date"], errors="coerce")
    return dates.apply(lambda x: int((now - x).days) if pd.notna(x) else 0)


def run(ib):
    log.info("=== PEAD SCAN ===")
    nav      = get_nav(ib)
    pos_cap  = nav * POS_SIZE
    existing = load_positions()

    if len(existing) > 0:
        held = days_held(existing)
        to_close = existing[held >= HOLD_DAYS]
        for _, row in to_close.iterrows():
            log.info(f"Cloture PEAD {row['ticker']} apres {HOLD_DAYS}j")
            place_order(ib, row['ticker'], 'SELL', int(row['shares']))
        existing = existing[held < HOLD_DAYS].reset_index(drop=True)

    if len(existing) >= MAX_POSITIONS:
        log.info("Positions max atteintes")
        save_positions(existing)
        return

    candidates = fetch_earnings_surprises()
    held = set(existing['ticker'].tolist())
    new_entries = []

    for c in candidates:
        if c['ticker'] in held:
            continue
        price = get_price(ib, c['ticker'])
        if not price or price < 3:
            continue
        shares = int(pos_cap / price)
        if shares == 0:
            continue
        ok = place_order(ib, c['ticker'], 'BUY', shares)
        if ok:
            new_entries.append({
                'ticker':     c['ticker'],
                'shares':     shares,
                'entry_price': price,
                'entry_date': datetime.now(),
                'surprise':   c['surprise'],
            })
            log.info(f"PEAD entry : {c['ticker']} surprise={c['surprise']*100:.1f}%")
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

    ib = connect()
    run(ib)

    schedule.every().day.at("15:00").do(lambda: run(ib))
    schedule.every().day.at("20:30").do(lambda: run(ib))

    log.info("PEAD actif — scan a 10h00 et 15h45 chaque jour")
    try:
        while True:
            schedule.run_pending()
            ib.sleep(60)
    except KeyboardInterrupt:
        log.info("Arret")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
