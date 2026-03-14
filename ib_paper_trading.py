import numpy as np
import pandas as pd
from ib_insync import IB, Stock, MarketOrder, LimitOrder, util
import logging
import time
import os
from datetime import datetime, timedelta
import schedule

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    handlers=[
        logging.FileHandler('ib_trading.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

HOST      = '127.0.0.1'
PORT         = 7497
CLIENT_ID    = 1
CAPITAL      = 50_000
MAX_POS_PCT  = 0.05
N_TOP        = 10
RISK_FREE    = 0.045
SLIPPAGE     = 0.003
CACHE_FILE   = 'equity_data_cache_full.parquet'
MAX_DD_STOP  = 0.15

UNIVERSE = list(dict.fromkeys([
    "AMBA", "APPF", "BAND", "BLFS", "CLFD", "CNXC", "CRDO", "DNLI",
    "EVTC", "EXPI", "FIVN", "FLNC", "FORM", "GTLB", "HIMS", "JAMF",
    "KLIC", "LBRT", "MGNI", "MGNX", "NTLA", "NVTS", "PAYO", "QNST",
    "RAMP", "RELY", "RMBS", "RVLV", "TBLA", "TCMD", "TMDX", "TNET",
    "UPST", "WKME", "XNCR", "YEXT",
    "ARRY", "CPRX", "GERN", "HALO", "HRMY", "HROW", "INVA", "MIRM",
    "MNKD", "NARI", "PCVX", "PDCO", "PHAT", "RCUS", "RDNT", "SAGE",
    "SANA", "VERA",
    "BOOT", "BMBL", "DSGN", "ENVA", "JOBY", "LCID", "MARA", "OSCR",
    "QDEL", "TDUP", "URBN", "USPH", "VCEL", "ZI",
    "ARIS", "CMPO", "PFIS",
]))



def kelly_fraction(win_rate: float = 0.655, avg_win: float = 0.08,
                   avg_loss: float = 0.04, fraction: float = 0.25) -> float:
    """Kelly fractionné — fraction=0.25 = Kelly/4 (conservateur)."""
    b = avg_win / avg_loss
    f = (win_rate * b - (1 - win_rate)) / b
    return max(0.0, min(f * fraction, 0.08))  # cap à 8% par position

def connect() -> IB:
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
    log.info(f"Connecte a IB Paper Trading — compte : {ib.wrapper.accounts}")
    return ib


def get_account_value(ib: IB) -> float:
    vals = ib.accountValues()
    for v in vals:
        if v.tag == 'NetLiquidation' and v.currency == 'USD':
            return float(v.value)
    return CAPITAL


def get_positions(ib: IB) -> dict:
    positions = {}
    for pos in ib.positions():
        if pos.position != 0:
            positions[pos.contract.symbol] = {
                'shares':    pos.position,
                'avg_cost':  pos.avgCost,
            }
    return positions


def qualify_contract(ib: IB, ticker: str):
    contract = Stock(ticker, 'SMART', 'USD')
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            return None
        return qualified[0]
    except Exception:
        return None


def get_price(ib: IB, ticker: str) -> float:
    contract = qualify_contract(ib, ticker)
    if not contract:
        log.warning(f"Contrat introuvable : {ticker}")
        return None

    ib.reqMarketDataType(3)
    ticker_obj = ib.reqMktData(contract, '', False, False)
    ib.sleep(3)

    price = None
    for attr in [ticker_obj.last, ticker_obj.close, ticker_obj.bid, ticker_obj.ask]:
        try:
            if attr and not np.isnan(float(attr)):
                price = float(attr)
                break
        except Exception:
            continue

    ib.cancelMktData(contract)
    if not price:
        log.warning(f"Prix introuvable : {ticker}")
    return price


def get_prices_batch(ib: IB, tickers: list) -> dict:
    prices = {}
    ib.reqMarketDataType(3)

    valid_contracts = {}
    for t in tickers:
        c = qualify_contract(ib, t)
        if c:
            valid_contracts[t] = c
        else:
            log.warning(f"Contrat ignore : {t}")

    ticker_objs = {t: ib.reqMktData(c, '', False, False)
                   for t, c in valid_contracts.items()}
    ib.sleep(4)

    for t, obj in ticker_objs.items():
        price = None
        for attr in [obj.last, obj.close, obj.bid, obj.ask]:
            try:
                if attr and not np.isnan(float(attr)):
                    price = float(attr)
                    break
            except Exception:
                continue
        if price:
            prices[t] = price
        else:
            log.warning(f"Prix introuvable : {t}")

    for c in valid_contracts.values():
        ib.cancelMktData(c)

    return prices


def compute_momentum_scores(prices_hist: dict) -> pd.Series:
    scores = {}
    for ticker, series in prices_hist.items():
        if len(series) < 130:
            continue
        s = pd.Series(series).sort_index()
        s = s[s > 3]
        if len(s) < 130:
            continue
        ret     = s.pct_change().clip(-0.5, 0.5)
        vol_21  = ret.rolling(21).std().iloc[-1] * np.sqrt(252)
        gain    = ret.clip(lower=0).rolling(14).mean()
        loss    = (-ret.clip(upper=0)).rolling(14).mean()
        rsi     = (100 - 100 / (1 + gain / (loss + 1e-9))).iloc[-1]

        if rsi >= 72 or vol_21 >= 0.90 or vol_21 <= 0.10:
            continue

        n        = len(s)
        mom_12_1 = s.pct_change(min(252, n-2)).iloc[-1] - s.pct_change(21).iloc[-1]
        mom_6_1  = s.pct_change(min(126, n-2)).iloc[-1] - s.pct_change(21).iloc[-1]
        score    = 0.6 * mom_12_1 + 0.4 * mom_6_1

        if score > 0:
            scores[ticker] = score

    return pd.Series(scores).nlargest(N_TOP)


def load_historical_prices() -> dict:
    if not os.path.exists(CACHE_FILE):
        log.warning(f"Cache introuvable : {CACHE_FILE}")
        return {}

    df = pd.read_parquet(CACHE_FILE)
    df.index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(d), t) for d, t in df.index],
        names=['date', 'ticker']
    )

    hist = {}
    for ticker in UNIVERSE:
        try:
            s = df.xs(ticker, level='ticker')['close'].sort_index()
            hist[ticker] = s
        except Exception:
            pass

    log.info(f"Historique charge : {len(hist)} tickers")
    return hist


def place_order(ib: IB, ticker: str, action: str, shares: int) -> bool:
    if shares <= 0:
        return False

    contract = Stock(ticker, 'SMART', 'USD')
    ib.qualifyContracts(contract)

    price = get_price(ib, ticker)
    if not price:
        return False

    limit_price = round(price * (1.005 if action == 'BUY' else 0.995), 2)
    order       = LimitOrder(action, shares, limit_price)
    trade       = ib.placeOrder(contract, order)

    log.info(f"Ordre {action} {shares} {ticker} @ ${limit_price:.2f}")

    timeout = 30
    while not trade.isDone() and timeout > 0:
        ib.sleep(1)
        timeout -= 1

    if trade.orderStatus.status == 'Filled':
        log.info(f"Rempli : {action} {shares} {ticker} @ ${trade.orderStatus.avgFillPrice:.2f}")
        return True
    else:
        log.warning(f"Non rempli : {ticker} — statut {trade.orderStatus.status}")
        ib.cancelOrder(order)
        return False


def check_drawdown_stop(ib: IB, peak_capital: float) -> bool:
    current = get_account_value(ib)
    dd      = (current - peak_capital) / peak_capital
    if dd < -MAX_DD_STOP:
        log.warning(f"STOP DRAWDOWN DECLENCHE : {dd*100:.1f}% < -{MAX_DD_STOP*100:.0f}%")
        return True
    return False


def rebalance(ib: IB, peak_capital: float) -> float:
    log.info("="*50)
    log.info("REBALANCEMENT MENSUEL")
    log.info("="*50)

    if check_drawdown_stop(ib, peak_capital):
        log.warning("Drawdown stop — liquidation totale")
        close_all_positions(ib)
        return get_account_value(ib)

    nav        = get_account_value(ib)
    pos_target = nav * kelly_fraction()  # Kelly fractionné
    log.info(f"NAV : ${nav:,.0f} | Position cible : ${pos_target:,.0f}")

    hist_prices = load_historical_prices()
    if not hist_prices:
        log.error("Pas de donnees historiques")
        return nav

    top_scores  = compute_momentum_scores(hist_prices)
    target_port = set(top_scores.index.tolist())
    log.info(f"Portefeuille cible : {sorted(target_port)}")

    current_pos = get_positions(ib)
    current_tickers = set(current_pos.keys())

    to_sell = current_tickers - target_port
    to_buy  = target_port - current_tickers

    for ticker in to_sell:
        shares = int(abs(current_pos[ticker]['shares']))
        log.info(f"Vente : {ticker} ({shares} actions)")
        place_order(ib, ticker, 'SELL', shares)
        ib.sleep(1)

    prices = get_prices_batch(ib, list(to_buy))
    for ticker in to_buy:
        price = prices.get(ticker)
        if not price:
            continue
        shares = int(pos_target / price)
        if shares > 0:
            log.info(f"Achat : {ticker} ({shares} actions @ ~${price:.2f})")
            place_order(ib, ticker, 'BUY', shares)
            ib.sleep(1)

    nav_after = get_account_value(ib)
    log.info(f"Rebalancement termine — NAV : ${nav_after:,.0f}")
    log.info("="*50)

    save_snapshot(ib, nav_after, top_scores)
    return nav_after


def close_all_positions(ib: IB):
    log.info("Liquidation totale du portefeuille")
    positions = get_positions(ib)
    for ticker, pos in positions.items():
        shares = int(abs(pos['shares']))
        place_order(ib, ticker, 'SELL', shares)
        ib.sleep(1)


def save_snapshot(ib: IB, nav: float, scores: pd.Series):
    snap = {
        'date':    datetime.now().strftime('%Y-%m-%d'),
        'nav':     nav,
        'tickers': ','.join(scores.index.tolist()),
    }
    df = pd.DataFrame([snap])

    if os.path.exists('snapshots.csv'):
        existing = pd.read_csv('snapshots.csv')
        df = pd.concat([existing, df], ignore_index=True)

    df.to_csv('snapshots.csv', index=False)
    log.info(f"Snapshot sauvegarde : {snap}")


def run_monthly_job(ib: IB, peak_capital: list):
    today = datetime.now()
    if today.weekday() >= 5:
        log.info("Weekend — pas de rebalancement")
        return

    new_nav        = rebalance(ib, peak_capital[0])
    peak_capital[0] = max(peak_capital[0], new_nav)


def main():
    log.info("Demarrage IB Paper Trading — Small Cap Momentum")

    ib            = connect()
    nav_init      = get_account_value(ib)
    peak_capital  = [nav_init]

    log.info(f"NAV initiale : ${nav_init:,.0f}")

    last_biz_day_of_month = lambda: (
        datetime.now().replace(day=1) +
        timedelta(days=32)
    ).replace(day=1) - timedelta(days=1)

    rebalance(ib, peak_capital[0])

    schedule.every().day.at("09:35").do(
        lambda: run_monthly_job(ib, peak_capital)
        if datetime.now().date() == last_biz_day_of_month().date()
        else None
    )

    log.info("Scheduler actif — rebalancement le dernier jour ouvre du mois a 09h35")
    log.info("Ctrl+C pour arreter")

    try:
        while True:
            schedule.run_pending()
            ib.sleep(60)
    except KeyboardInterrupt:
        log.info("Arret manuel")
    finally:
        ib.disconnect()
        log.info("Deconnecte de IB")


if __name__ == "__main__":
    main()
