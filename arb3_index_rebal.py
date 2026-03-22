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
    handlers=[logging.FileHandler('index_rebal.log'), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

HOST      = '127.0.0.1'
PORT         = 4002
CLIENT_ID = 4
CAPITAL   = 50_000
POS_SIZE  = 0.04
MAX_POSITIONS = 8

FINNHUB_KEY = "d6q4h6hr01qhcrmirjn0d6q4h6hr01qhcrmirjng"

# Russell 2000 rebalance : dernier vendredi de juin
# S&P 500 rebalance : troisième vendredi de mars, juin, sept, déc
# On shorten les nouveaux entrants avant le rebalancement
# et on achète les sortants (oversold après expulsion)



def days_held(df, col='entry_date'):
    now = datetime.now()
    return df[col].apply(lambda x: (now - pd.to_datetime(x)).days if pd.notna(x) else 0)

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


def fetch_index_changes():
    """
    Récupère les changements d'indice annoncés via Finnhub.
    Les annonces Russell sont faites ~1 semaine avant le rebalancement.
    Strategy : acheter les futurs EXCLUS (mean reversion après la vente forcée)
    """
    url = "https://finnhub.io/api/v1/index/constituents"
    candidates = []

    # Russell 2000 exclusions — source Finnhub index changes
    # Annoncé fin mai/début juin pour effet fin juin
    try:
        r    = requests.get(url, params={'symbol': '^RUT', 'token': FINNHUB_KEY}, timeout=10)
        data = r.json()
        if 'constituents' in data:
            log.info(f"Russell 2000 : {len(data['constituents'])} constituants charges")
    except Exception as e:
        log.error(f"Index fetch error : {e}")

    # Fallback : surveiller les annonces via news Finnhub
    try:
        news_url = "https://finnhub.io/api/v1/news"
        today    = datetime.now().strftime('%Y-%m-%d')
        from_    = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        r = requests.get(news_url, params={
            'category': 'general',
            'from':     from_,
            'to':       today,
            'token':    FINNHUB_KEY,
        }, timeout=10)
        news = r.json()

        index_keywords = ['russell', 'index addition', 'index removal',
                         's&p 500 adds', 'removed from', 'added to index']

        for article in news[:50]:
            headline = (article.get('headline') or '').lower()
            if any(kw in headline for kw in index_keywords):
                log.info(f"News indice detectee : {article.get('headline')}")

    except Exception as e:
        log.error(f"News fetch error : {e}")

    return candidates


def is_near_rebalance_date():
    """
    Vérifie si on est dans la fenêtre de trading autour d'un rebalancement d'indice.
    Russell 2000 : dernier vendredi de juin (annonce ~J-7)
    S&P 500      : troisième vendredi de mars, juin, sept, déc
    Retourne (True, 'buy' ou 'sell', jours_avant_rebal)
    """
    today = datetime.now()
    month = today.month
    day   = today.day

    def last_friday_of_month(year, month):
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        d = datetime(year, month, last_day)
        while d.weekday() != 4:
            d -= timedelta(days=1)
        return d

    def third_friday_of_month(year, month):
        d = datetime(year, month, 1)
        fridays = 0
        while fridays < 3:
            if d.weekday() == 4:
                fridays += 1
            if fridays < 3:
                d += timedelta(days=1)
        return d

    windows = []

    # Russell 2000 — dernier vendredi juin
    russell_date = last_friday_of_month(today.year, 6)
    windows.append(('RUSSELL', russell_date))

    # S&P 500 — troisième vendredi mars/juin/sept/déc
    for m in [3, 6, 9, 12]:
        sp_date = third_friday_of_month(today.year, m)
        windows.append((f'SP500_{m}', sp_date))

    for name, rebal_date in windows:
        days_to = (rebal_date - today).days
        if -5 <= days_to <= 10:
            log.info(f"Fenetre rebalancement {name} : J{days_to:+d}")
            return True, days_to, name

    return False, None, None


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
        log.info(f"Rempli {action} {shares} {ticker}")
        return True
    log.warning(f"Non rempli : {ticker}")
    ib.cancelOrder(trade.order)
    return False


def load_positions():
    if os.path.exists('index_rebal_positions.csv'):
        return pd.read_csv('index_rebal_positions.csv', parse_dates=['entry_date'])
    return pd.DataFrame(columns=['ticker','shares','entry_price','entry_date','index','days_to_rebal'])


def save_positions(df):
    df.to_csv('index_rebal_positions.csv', index=False)


def run(ib):
    log.info("=== INDEX REBAL SCAN ===")
    nav      = get_nav(ib)
    pos_cap  = nav * POS_SIZE
    existing = load_positions()

    in_window, days_to, index_name = is_near_rebalance_date()

    # Clôturer si on est J+5 après le rebalancement
    if not in_window or (days_to is not None and days_to < -3):
        for _, row in existing.iterrows():
            log.info(f"Cloture post-rebal : {row['ticker']}")
            place_order(ib, row['ticker'], 'SELL', int(row['shares']))
        save_positions(pd.DataFrame(columns=existing.columns))
        return

    if not in_window:
        log.info("Hors fenetre rebalancement — attente")
        save_positions(existing)
        return

    log.info(f"Fenetre active : {index_name} J{days_to:+d}")

    # Récupérer les candidats (exclusions d'indice = mean reversion play)
    candidates = fetch_index_changes()
    held       = set(existing['ticker'].tolist())
    new_entries = []

    for ticker in candidates:
        if ticker in held or len(existing) + len(new_entries) >= MAX_POSITIONS:
            break
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
                'index':        index_name,
                'days_to_rebal': days_to,
            })

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

    schedule.every().day.at("09:40").do(lambda: run(ib))

    log.info("Index Rebal actif — scan quotidien a 09h40")
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
