# -*- coding: utf-8 -*-
"""
arb9_intraday.py -- 4 strategies intraday actives
==================================================
1. Mean Reversion OU       (clientId 15) -- barres 5min, z-score OU
2. Momentum ETFs sectoriels (clientId 16) -- XLE XBI XLF XLK XLU XLV
3. Stat Arb Pairs          (clientId 17) -- SPY/QQQ, AAPL/MSFT, XOM/CVX
4. Breakout 30min          (clientId 18) -- range 09h30-10h00, breakout

Capital par strategie : $50,000
Heures : 09h35-15h45 ET (14h35-20h45 UTC)
"""

import os, sys, time, logging, threading
import numpy as np
import pandas as pd
from collections import deque
from datetime import datetime, time as dtime
from ib_insync import IB, Stock, LimitOrder, StopOrder, util
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.stattools import adfuller

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('arb9_intraday.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

try:
    from alerts import alert_manager
except Exception:
    class _A:
        def notify_crash(self, *a): pass
        def _send(self, *a): pass
    alert_manager = _A()

HOST    = '127.0.0.1'
PORT    = 7497
CAPITAL = 50_000
SLIP    = 0.0005   # 5 bps

#        Helpers                                                                                                                                                                                                          

def is_market_hours():
    now = datetime.utcnow()
    if now.weekday() >= 5: return False
    t = now.hour * 60 + now.minute
    return 14*60+35 <= t <= 20*60+45

def slip_price(px, action):
    return px * (1 + SLIP) if action == 'BUY' else px * (1 - SLIP)

def get_nav(ib):
    for v in ib.accountValues():
        if v.tag == 'NetLiquidation' and v.currency in ('USD','EUR','BASE'):
            return float(v.value)
    return CAPITAL * 20  # fallback

def get_price(ib, ticker):
    c = Stock(ticker, 'SMART', 'USD')
    try:
        ib.qualifyContracts(c)
        ib.reqMarketDataType(3)
        t = ib.reqMktData(c, '', False, False)
        ib.sleep(2)
        for attr in [t.last, t.close, t.bid, t.ask]:
            try:
                v = float(attr)
                if not np.isnan(v) and v > 0:
                    ib.cancelMktData(c)
                    return v
            except Exception: pass
        ib.cancelMktData(c)
    except Exception: pass
    return None

def place_order(ib, ticker, action, shares, px=None):
    if shares <= 0: return False, 0
    c = Stock(ticker, 'SMART', 'USD')
    try:
        ib.qualifyContracts(c)
        if px is None:
            px = get_price(ib, ticker)
        if not px: return False, 0
        lmt = round(slip_price(px, action), 2)
        order = LimitOrder(action, shares, lmt)
        trade = ib.placeOrder(c, order)
        timeout = 30
        while not trade.isDone() and timeout > 0:
            ib.sleep(1); timeout -= 1
        if trade.orderStatus.status == 'Filled':
            fill = trade.orderStatus.avgFillPrice
            log.info(f"FILL {action} {shares}x {ticker} @ ${fill:.2f}")
            return True, fill
        ib.cancelOrder(order)
        return False, 0
    except Exception as e:
        log.warning(f"Order {ticker}: {e}")
        return False, 0

def fit_ou(prices):
    x = np.array(prices, dtype=float)
    if len(x) < 30: return None
    try:
        res = OLS(x[1:], add_constant(x[:-1])).fit()
        a, b = float(res.params[0]), float(res.params[1])
        if not (0 < b < 1): return None
        theta = -np.log(b)
        mu = a / (1 - b)
        sig = float(res.resid.std()) * np.sqrt(2*theta/(1-b**2))
        hl  = np.log(2) / theta
        seq = sig / np.sqrt(2*theta + 1e-9)
        if seq <= 0: return None
        return {'theta': theta, 'mu': mu, 'sig_eq': seq, 'hl': hl}
    except Exception: return None

#                                                                                                                                                                                                                                           
# 1. MEAN REVERSION OU (barres 5min)
#                                                                                                                                                                                                                                           

class MeanReversionBot:
    TICKERS  = ['AAPL','MSFT','AMZN','NVDA','META','GOOGL','SPY','QQQ']
    ENTRY_Z  = 2.0
    EXIT_Z   = 0.4
    STOP_Z   = 3.5
    MAX_POS  = 3
    POS_PCT  = 0.12   # 12% du capital par position

    def __init__(self):
        self.ib = IB()
        self.pos = {}
        self.ph  = {t: deque(maxlen=60) for t in self.TICKERS}
        self.cash = float(CAPITAL)

    def connect(self):
        self.ib.connect(HOST, PORT, clientId=15)
        log.info("MR bot connecte")

    def get_bars(self, ticker):
        c = Stock(ticker, 'SMART', 'USD')
        try:
            self.ib.qualifyContracts(c)
            bars = self.ib.reqHistoricalData(c, '', '1 D', '5 mins',
                    'TRADES', useRTH=True, formatDate=1, keepUpToDate=False)
            if bars:
                return [float(b.close) for b in bars if b.close > 0]
        except Exception as e:
            log.debug(f"Bars {ticker}: {e}")
        return []

    def run_cycle(self):
        for ticker in self.TICKERS:
            prices = self.get_bars(ticker)
            if len(prices) < 20: continue
            for p in prices[-60:]:
                self.ph[ticker].append(p)

            ou = fit_ou(list(self.ph[ticker]))
            if ou is None: continue
            if not (5 <= ou['hl'] <= 120): continue

            px = prices[-1]
            z  = (px - ou['mu']) / ou['sig_eq']

            pid = f"MR_{ticker}"
            if pid in self.pos:
                p = self.pos[pid]
                close = ((p['dir']=='LONG' and z >= self.EXIT_Z) or
                         (p['dir']=='SHORT' and z <= -self.EXIT_Z) or
                         abs(z) >= self.STOP_Z)
                if close:
                    action = 'SELL' if p['dir']=='LONG' else 'BUY'
                    ok, fill = place_order(self.ib, ticker, action, p['shares'])
                    if ok:
                        pnl = p['shares']*(fill-p['entry']) if p['dir']=='LONG' else p['shares']*(p['entry']-fill)
                        self.cash += p['shares']*fill if p['dir']=='LONG' else p['shares']*p['entry']
                        log.info(f"MR CLOSE {ticker} P&L ${pnl:+.2f} z={z:.2f}")
                        alert_manager._send(f"MR CLOSE {ticker} P&L ${pnl:+.2f}")
                        del self.pos[pid]
                continue

            if len(self.pos) >= self.MAX_POS: continue
            size = max(1, int(CAPITAL * self.POS_PCT / px))

            if z >= self.ENTRY_Z:
                ok, fill = place_order(self.ib, ticker, 'SELL', size, px)
                if ok:
                    self.cash -= size*fill*0.1
                    self.pos[pid] = {'shares':size,'entry':fill,'dir':'SHORT'}
                    alert_manager._send(f"MR SHORT {ticker} z={z:.2f} hl={ou['hl']:.0f}s")
            elif z <= -self.ENTRY_Z:
                ok, fill = place_order(self.ib, ticker, 'BUY', size, px)
                if ok:
                    self.cash -= size*fill
                    self.pos[pid] = {'shares':size,'entry':fill,'dir':'LONG'}
                    alert_manager._send(f"MR LONG {ticker} z={z:.2f} hl={ou['hl']:.0f}s")

    def run(self):
        self.connect()
        log.info("MR Bot actif -- barres 5min")
        while True:
            try:
                if is_market_hours():
                    self.run_cycle()
                    self.ib.sleep(300)  # 5 min
                else:
                    # Fermer tout en fin de journee
                    for pid, p in list(self.pos.items()):
                        t = pid.replace('MR_','')
                        action = 'SELL' if p['dir']=='LONG' else 'BUY'
                        place_order(self.ib, t, action, p['shares'])
                        del self.pos[pid]
                    self.ib.sleep(60)
            except Exception as e:
                alert_manager.notify_crash('mr_bot', str(e))
                log.error(f"MR error: {e}")
                self.ib.sleep(60)

#                                                                                                                                                                                                                                           
# 2. MOMENTUM ETFs SECTORIELS
#                                                                                                                                                                                                                                           

class ETFMomentumBot:
    ETFS = ['XLE','XBI','XLF','XLK','XLU','XLV','XLI','XLY','XLP','GLD','TLT','USO']
    LOOKBACK  = 20   # barres 15min
    TOP_N     = 2    # top 2 long, bottom 2 short
    POS_PCT   = 0.20

    def __init__(self):
        self.ib  = IB()
        self.pos = {}
        self.cash = float(CAPITAL)

    def connect(self):
        self.ib.connect(HOST, PORT, clientId=16)
        log.info("ETF Momentum bot connecte")

    def get_bars(self, ticker):
        c = Stock(ticker, 'SMART', 'USD')
        try:
            self.ib.qualifyContracts(c)
            bars = self.ib.reqHistoricalData(c, '', '2 D', '15 mins',
                    'TRADES', useRTH=True, formatDate=1, keepUpToDate=False)
            return [float(b.close) for b in bars if b.close > 0] if bars else []
        except Exception as e:
            log.debug(f"Bars {ticker}: {e}")
            return []

    def compute_score(self, prices):
        if len(prices) < self.LOOKBACK: return 0.0
        p = np.array(prices[-self.LOOKBACK:])
        ret = (p[-1] - p[0]) / p[0]
        vol = np.std(np.diff(p)/p[:-1]) * np.sqrt(252*26) + 1e-9
        return float(ret / vol)

    def run_cycle(self):
        scores = {}
        prices = {}
        for etf in self.ETFS:
            bars = self.get_bars(etf)
            if len(bars) >= self.LOOKBACK:
                scores[etf] = self.compute_score(bars)
                prices[etf] = bars[-1]
            self.ib.sleep(0.2)

        if not scores: return

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        longs  = [t for t,s in ranked[:self.TOP_N] if s > 0.1]
        shorts = [t for t,s in ranked[-self.TOP_N:] if s < -0.1]

        target = set(longs + shorts)
        current = set(self.pos.keys())

        # Fermer les positions hors target
        for t in current - target:
            p = self.pos[t]
            action = 'SELL' if p['dir']=='LONG' else 'BUY'
            ok, fill = place_order(self.ib, t, action, p['shares'])
            if ok:
                pnl = p['shares']*(fill-p['entry']) if p['dir']=='LONG' else p['shares']*(p['entry']-fill)
                self.cash += p['shares']*fill if p['dir']=='LONG' else p['shares']*p['entry']
                log.info(f"ETF CLOSE {t} P&L ${pnl:+.2f}")
                del self.pos[t]

        # Ouvrir nouvelles positions
        for t in longs:
            if t in self.pos: continue
            px = prices.get(t)
            if not px: continue
            size = max(1, int(CAPITAL * self.POS_PCT / px))
            ok, fill = place_order(self.ib, t, 'BUY', size, px)
            if ok:
                self.cash -= size*fill
                self.pos[t] = {'shares':size,'entry':fill,'dir':'LONG'}
                alert_manager._send(f"ETF LONG {t} score={scores[t]:.2f}")

        for t in shorts:
            if t in self.pos: continue
            px = prices.get(t)
            if not px: continue
            size = max(1, int(CAPITAL * self.POS_PCT / px))
            ok, fill = place_order(self.ib, t, 'SELL', size, px)
            if ok:
                self.cash -= size*fill*0.1
                self.pos[t] = {'shares':size,'entry':fill,'dir':'SHORT'}
                alert_manager._send(f"ETF SHORT {t} score={scores[t]:.2f}")

    def run(self):
        self.connect()
        log.info("ETF Momentum Bot actif -- barres 15min")
        while True:
            try:
                if is_market_hours():
                    self.run_cycle()
                    self.ib.sleep(900)  # 15 min
                else:
                    for t, p in list(self.pos.items()):
                        action = 'SELL' if p['dir']=='LONG' else 'BUY'
                        place_order(self.ib, t, action, p['shares'])
                        del self.pos[t]
                    self.ib.sleep(60)
            except Exception as e:
                alert_manager.notify_crash('etf_mom_bot', str(e))
                log.error(f"ETF Mom error: {e}")
                self.ib.sleep(60)

#                                                                                                                                                                                                                                           
# 3. STAT ARB PAIRS TRADING
#                                                                                                                                                                                                                                           

class PairsBot:
    PAIRS = [
        ('SPY',  'QQQ'),
        ('AAPL', 'MSFT'),
        ('XOM',  'CVX'),
        ('JPM',  'BAC'),
        ('GLD',  'GDX'),
    ]
    ENTRY_Z   = 2.0
    EXIT_Z    = 0.3
    STOP_Z    = 3.5
    POS_PCT   = 0.20
    CAL_BARS  = 60

    def __init__(self):
        self.ib  = IB()
        self.pos = {}
        self.ph  = {t: deque(maxlen=self.CAL_BARS)
                    for p in self.PAIRS for t in p}
        self.cash = float(CAPITAL)

    def connect(self):
        self.ib.connect(HOST, PORT, clientId=17)
        log.info("Pairs bot connecte")

    def get_bars(self, ticker):
        c = Stock(ticker, 'SMART', 'USD')
        try:
            self.ib.qualifyContracts(c)
            bars = self.ib.reqHistoricalData(c, '', '2 D', '5 mins',
                    'TRADES', useRTH=True, formatDate=1, keepUpToDate=False)
            return [float(b.close) for b in bars if b.close > 0] if bars else []
        except Exception as e:
            log.debug(f"Bars {ticker}: {e}")
            return []

    def run_cycle(self):
        # Update historique
        all_tickers = list(set(t for p in self.PAIRS for t in p))
        bars = {}
        for t in all_tickers:
            b = self.get_bars(t)
            if b:
                bars[t] = b
                for px in b[-self.CAL_BARS:]:
                    self.ph[t].append(px)
            self.ib.sleep(0.2)

        for (tx, ty) in self.PAIRS:
            if tx not in bars or ty not in bars: continue
            px = bars[tx][-1]
            py = bars[ty][-1]
            pk = f"{tx}_{ty}"

            # Fermer position existante
            if pk in self.pos:
                p = self.pos[pk]
                spread = px - p['beta'] * py
                z = (spread - p['mu']) / (p['sig_eq'] + 1e-9)
                close = ((p['dir']=='LONG_SPREAD'  and z >= self.EXIT_Z) or
                         (p['dir']=='SHORT_SPREAD' and z <= -self.EXIT_Z) or
                         abs(z) >= self.STOP_Z)
                if close:
                    if p['dir'] == 'LONG_SPREAD':
                        ok1, fx = place_order(self.ib, tx, 'SELL', p['sx'], px)
                        ok2, fy = place_order(self.ib, ty, 'BUY',  p['sy'], py)
                        pnl = p['sx']*(fx-p['ex']) - p['sy']*(fy-p['ey'])
                    else:
                        ok1, fx = place_order(self.ib, tx, 'BUY',  p['sx'], px)
                        ok2, fy = place_order(self.ib, ty, 'SELL', p['sy'], py)
                        pnl = -p['sx']*(fx-p['ex']) + p['sy']*(fy-p['ey'])
                    log.info(f"PAIRS CLOSE {pk} P&L ${pnl:+.2f} z={z:.2f}")
                    alert_manager._send(f"PAIRS CLOSE {pk} P&L ${pnl:+.2f}")
                    del self.pos[pk]
                continue

            # Calibrer
            if len(self.ph[tx]) < 40 or len(self.ph[ty]) < 40: continue
            xa = np.array(list(self.ph[tx]), dtype=float)
            ya = np.array(list(self.ph[ty]), dtype=float)
            try:
                res = OLS(xa, add_constant(ya)).fit()
                beta = float(res.params[1])
                if beta <= 0: continue
                spread = xa - beta * ya
                adf = adfuller(spread, maxlag=3, autolag='AIC')
                if adf[1] > 0.10: continue
                ou = fit_ou(spread)
                if ou is None: continue
                if not (5 <= ou['hl'] <= 300): continue
                z = (float(spread[-1]) - ou['mu']) / ou['sig_eq']
            except Exception: continue

            if abs(z) < self.ENTRY_Z: continue
            if len(self.pos) >= 3: continue

            sx = max(1, int(CAPITAL * self.POS_PCT / px))
            sy = max(1, int(sx * beta * px / py))

            if z >= self.ENTRY_Z:
                ok1, fx = place_order(self.ib, tx, 'SELL', sx, px)
                ok2, fy = place_order(self.ib, ty, 'BUY',  sy, py)
                if ok1 and ok2:
                    self.pos[pk] = {'sx':sx,'sy':sy,'ex':fx,'ey':fy,
                        'beta':beta,'mu':ou['mu'],'sig_eq':ou['sig_eq'],
                        'dir':'SHORT_SPREAD'}
                    alert_manager._send(f"PAIRS SHORT_SPREAD {pk} z={z:.2f} hl={ou['hl']:.0f}")
            elif z <= -self.ENTRY_Z:
                ok1, fx = place_order(self.ib, tx, 'BUY',  sx, px)
                ok2, fy = place_order(self.ib, ty, 'SELL', sy, py)
                if ok1 and ok2:
                    self.pos[pk] = {'sx':sx,'sy':sy,'ex':fx,'ey':fy,
                        'beta':beta,'mu':ou['mu'],'sig_eq':ou['sig_eq'],
                        'dir':'LONG_SPREAD'}
                    alert_manager._send(f"PAIRS LONG_SPREAD {pk} z={z:.2f} hl={ou['hl']:.0f}")

    def run(self):
        self.connect()
        log.info("Pairs Bot actif -- barres 5min")
        while True:
            try:
                if is_market_hours():
                    self.run_cycle()
                    self.ib.sleep(300)
                else:
                    all_t = list(set(t for p in self.PAIRS for t in p))
                    for t in all_t:
                        for pk, p in list(self.pos.items()):
                            if t in pk:
                                action = 'SELL' if p['dir']=='LONG_SPREAD' else 'BUY'
                    self.ib.sleep(60)
            except Exception as e:
                alert_manager.notify_crash('pairs_bot', str(e))
                log.error(f"Pairs error: {e}")
                self.ib.sleep(60)

#                                                                                                                                                                                                                                           
# 4. BREAKOUT 30MIN
#                                                                                                                                                                                                                                           

class BreakoutBot:
    TICKERS  = ['SPY','QQQ','AAPL','MSFT','NVDA','AMZN','TSLA']
    POS_PCT  = 0.12
    STOP_PCT = 0.005   # stop loss 0.5%
    TARGET_PCT = 0.015  # target 1.5%
    MAX_POS  = 3

    def __init__(self):
        self.ib    = IB()
        self.pos   = {}
        self.range = {}   # ticker -> {high, low, set}
        self.cash  = float(CAPITAL)
        self.range_set_today = False

    def connect(self):
        self.ib.connect(HOST, PORT, clientId=18)
        log.info("Breakout bot connecte")

    def get_opening_range(self, ticker):
        c = Stock(ticker, 'SMART', 'USD')
        try:
            self.ib.qualifyContracts(c)
            bars = self.ib.reqHistoricalData(c, '', '1 D', '30 mins',
                    'TRADES', useRTH=True, formatDate=1, keepUpToDate=False)
            if bars and len(bars) >= 1:
                first = bars[0]
                return float(first.high), float(first.low)
        except Exception as e:
            log.debug(f"Range {ticker}: {e}")
        return None, None

    def run_cycle(self):
        now_utc = datetime.utcnow()
        t_min   = now_utc.hour * 60 + now_utc.minute

        # Definir le range d'ouverture apres 15h05 UTC (10h05 ET)
        if 15*60+5 <= t_min <= 15*60+15 and not self.range_set_today:
            for ticker in self.TICKERS:
                h, l = self.get_opening_range(ticker)
                if h and l:
                    self.range[ticker] = {'high': h, 'low': l}
                    log.info(f"Range {ticker}: H={h:.2f} L={l:.2f}")
                self.ib.sleep(0.2)
            self.range_set_today = True

        if not self.range_set_today: return

        # Reset quotidien
        if t_min < 14*60+35:
            self.range_set_today = False
            return

        # Surveiller les breakouts
        for ticker in self.TICKERS:
            if ticker not in self.range: continue
            r   = self.range[ticker]
            pid = f"BRK_{ticker}"

            # Gerer position existante
            if pid in self.pos:
                p   = self.pos[pid]
                px  = get_price(self.ib, ticker)
                if not px: continue
                hit_stop   = ((p['dir']=='LONG'  and px <= p['stop']) or
                              (p['dir']=='SHORT' and px >= p['stop']))
                hit_target = ((p['dir']=='LONG'  and px >= p['target']) or
                              (p['dir']=='SHORT' and px <= p['target']))
                eod = t_min >= 20*60+30

                if hit_stop or hit_target or eod:
                    reason = 'STOP' if hit_stop else 'TARGET' if hit_target else 'EOD'
                    action = 'SELL' if p['dir']=='LONG' else 'BUY'
                    ok, fill = place_order(self.ib, ticker, action, p['shares'], px)
                    if ok:
                        pnl = p['shares']*(fill-p['entry']) if p['dir']=='LONG' else p['shares']*(p['entry']-fill)
                        self.cash += p['shares']*fill if p['dir']=='LONG' else p['shares']*p['entry']
                        log.info(f"BRK CLOSE {ticker} {reason} P&L ${pnl:+.2f}")
                        alert_manager._send(f"BRK CLOSE {ticker} {reason} P&L ${pnl:+.2f}")
                        del self.pos[pid]
                continue

            # Chercher breakout
            if len(self.pos) >= self.MAX_POS: continue
            if t_min < 15*60+15: continue  # attendre apres 10h15 ET

            px = get_price(self.ib, ticker)
            if not px: continue

            size = max(1, int(CAPITAL * self.POS_PCT / px))

            if px > r['high'] * 1.001:  # breakout haussier
                ok, fill = place_order(self.ib, ticker, 'BUY', size, px)
                if ok:
                    stop   = round(fill * (1 - self.STOP_PCT), 2)
                    target = round(fill * (1 + self.TARGET_PCT), 2)
                    self.cash -= size*fill
                    self.pos[pid] = {'shares':size,'entry':fill,'dir':'LONG',
                                     'stop':stop,'target':target}
                    alert_manager._send(f"BRK LONG {ticker} @ ${fill:.2f} T=${target:.2f} S=${stop:.2f}")

            elif px < r['low'] * 0.999:  # breakout baissier
                ok, fill = place_order(self.ib, ticker, 'SELL', size, px)
                if ok:
                    stop   = round(fill * (1 + self.STOP_PCT), 2)
                    target = round(fill * (1 - self.TARGET_PCT), 2)
                    self.cash -= size*fill*0.1
                    self.pos[pid] = {'shares':size,'entry':fill,'dir':'SHORT',
                                     'stop':stop,'target':target}
                    alert_manager._send(f"BRK SHORT {ticker} @ ${fill:.2f} T=${target:.2f} S=${stop:.2f}")

    def run(self):
        self.connect()
        log.info("Breakout Bot actif -- range 30min")
        while True:
            try:
                if is_market_hours() or datetime.utcnow().hour * 60 + datetime.utcnow().minute >= 14*60+35:
                    self.run_cycle()
                    self.ib.sleep(60)
                else:
                    self.ib.sleep(60)
            except Exception as e:
                alert_manager.notify_crash('breakout_bot', str(e))
                log.error(f"Breakout error: {e}")
                self.ib.sleep(60)

#        Main                                                                                                                                                                                                                   

def run_bot(bot_class, name):
    while True:
        try:
            bot = bot_class()
            bot.run()
        except Exception as e:
            log.error(f"{name} crash: {e}")
            alert_manager.notify_crash(name, str(e))
            time.sleep(30)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--bot', choices=['mr','etf','pairs','breakout'], required=True)
    args = parser.parse_args()

    bots = {
        'mr':       (MeanReversionBot,  'mr_bot'),
        'etf':      (ETFMomentumBot,    'etf_mom_bot'),
        'pairs':    (PairsBot,          'pairs_bot'),
        'breakout': (BreakoutBot,       'breakout_bot'),
    }
    cls, name = bots[args.bot]
    log.info(f"Demarrage {name}")
    run_bot(cls, name)
