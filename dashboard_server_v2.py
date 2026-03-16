"""
dashboard_server_v2.py — Dashboard institutionnel niveau hedge fund
===================================================================
- Vue globale portefeuille consolidé
- Performance par stratégie avec P&L, Sharpe, drawdown
- Greeks agrégés (delta, gamma, theta, vega)
- Risk manager : VaR cross, stress tests, corrélations, levier
- Flux positions en temps réel
- API REST pour le frontend
ClientId : 9
"""

from flask import Flask, jsonify, request
try:
    from news_signals import get_engine as get_news_engine
    NEWS_ENABLED = True
except ImportError:
    NEWS_ENABLED = False
from ib_insync import IB, util
import threading, time, os, json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from scipy.stats import norm

app = Flask(__name__)

HOST, PORT, CLIENT_ID = '127.0.0.1', 7497, 9
CAPITAL = 1_090_000
REFRESH_S = 30

STRATEGIES = {
    'Momentum':    {'color': '#3266ad', 'csv': None},
    'PEAD':        {'color': '#1d9e75', 'csv': 'pead_positions.csv'},
    'Spinoff':     {'color': '#ba7517', 'csv': 'spinoff_positions.csv'},
    'IndexRebal':  {'color': '#7f77dd', 'csv': 'index_rebal_positions.csv'},
    'Convertibles':{'color': '#d85a30', 'csv': 'convertibles_positions.csv'},
    'Options':     {'color': '#d4537e', 'csv': 'options_positions.csv'},
    'ETF_Options': {'color': '#639922', 'csv': 'etf_options_positions.csv'},
    'Futures':     {'color': '#888780', 'csv': 'futures_positions.csv'},
}

state = {
    'positions': [], 'metrics': {}, 'nav_history': [],
    'strategy_perf': {}, 'greeks': {}, 'risk': {},
    'alerts': [], 'news': {}, 'last_update': None, 'connected': False,
}
state_lock = threading.Lock()
ib = IB()
pnl_history_by_strat = {s: [] for s in STRATEGIES}
nav_history_global = []

def connect_ib():
    try:
        if ib.isConnected(): return True
        ib.connect(HOST, PORT, clientId=CLIENT_ID)
        return True
    except Exception as e:
        print(f"IB error: {e}")
        return False

def get_nav():
    for v in ib.accountValues():
        if v.tag == 'NetLiquidation' and v.currency in ('USD', 'EUR', 'BASE'):
            return float(v.value)
    return CAPITAL

def get_strat_for_ticker(ticker, position):
    csvs = {
        'pead_positions.csv': 'PEAD',
        'spinoff_positions.csv': 'Spinoff',
        'index_rebal_positions.csv': 'IndexRebal',
        'convertibles_positions.csv': 'Convertibles',
        'options_positions.csv': 'Options',
        'etf_options_positions.csv': 'ETF_Options',
        'futures_positions.csv': 'Futures',
    }
    for csv_file, strat in csvs.items():
        if os.path.exists(csv_file):
            try:
                df = pd.read_csv(csv_file)
                col = 'ticker' if 'ticker' in df.columns else 'symbol'
                if col in df.columns and ticker in df[col].values:
                    return strat
            except Exception:
                pass
    if ticker == 'MSTR' and position < 0:
        return 'Convertibles'
    return 'Momentum'

def compute_var(returns, confidence=0.95):
    if len(returns) < 5: return 0.0
    return float(-np.percentile(returns, (1-confidence)*100))

def compute_cvar(returns, confidence=0.95):
    if len(returns) < 5: return 0.0
    threshold = np.percentile(returns, (1-confidence)*100)
    tail = returns[returns <= threshold]
    return float(-tail.mean()) if len(tail) > 0 else 0.0

def compute_sharpe(returns, rf=0.045/252):
    if len(returns) < 5 or returns.std() == 0: return 0.0
    return float((returns.mean() - rf) / returns.std() * np.sqrt(252))

def compute_max_dd(nav_series):
    if len(nav_series) < 2: return 0.0
    arr = np.array(nav_series)
    peaks = np.maximum.accumulate(arr)
    return float(((arr - peaks) / peaks).min())

def fetch_data():
    global state, pnl_history_by_strat, nav_history_global
    while True:
        try:
            if not ib.isConnected():
                connect_ib()
                time.sleep(5)
                continue

            portfolio_map = {item.contract.symbol: item for item in ib.portfolio()}
            positions_raw = list(ib.positions())

            positions = []
            strat_pnl = {s: 0.0 for s in STRATEGIES}
            strat_value = {s: 0.0 for s in STRATEGIES}
            strat_trades = {s: 0 for s in STRATEGIES}

            total_upnl = 0.0
            total_long = 0.0
            total_short = 0.0

            # Greeks agrégés
            agg_delta = 0.0
            agg_gamma = 0.0
            agg_theta = 0.0
            agg_vega  = 0.0

            for pos in positions_raw:
                t = pos.contract.symbol
                sec_type = pos.contract.secType
                item = portfolio_map.get(t)
                if not item: continue

                mkt_v = item.marketValue
                upnl  = item.unrealizedPNL
                cost  = abs(pos.avgCost * pos.position)
                ret   = upnl / cost * 100 if cost > 0 else 0.0
                direction = 'LONG' if pos.position > 0 else 'SHORT'
                strat = get_strat_for_ticker(t, pos.position)

                strat_pnl[strat]   = strat_pnl.get(strat, 0) + upnl
                strat_value[strat] = strat_value.get(strat, 0) + abs(mkt_v)
                strat_trades[strat] = strat_trades.get(strat, 0) + 1

                total_upnl += upnl
                if direction == 'LONG':
                    total_long += mkt_v
                else:
                    total_short += abs(mkt_v)

                # Greeks pour options
                if sec_type == 'OPT' and item.modelGreeks:
                    g = item.modelGreeks
                    mult = abs(pos.position) * 100
                    sign = 1 if pos.position > 0 else -1
                    agg_delta += (g.delta or 0) * mult * sign
                    agg_gamma += (g.gamma or 0) * mult * sign
                    agg_theta += (g.theta or 0) * mult * sign
                    agg_vega  += (g.vega  or 0) * mult * sign

                positions.append({
                    'ticker':    t,
                    'sec_type':  sec_type,
                    'strategy':  strat,
                    'direction': direction,
                    'shares':    int(pos.position),
                    'avg_cost':  round(pos.avgCost, 3),
                    'mkt_price': round(item.marketPrice, 3),
                    'mkt_value': round(mkt_v, 2),
                    'upnl':      round(upnl, 2),
                    'return_pct':round(ret, 2),
                    'weight':    round(abs(mkt_v) / CAPITAL * 100, 2),
                })

            nav = CAPITAL + total_upnl
            nav_history_global.append(nav)
            if len(nav_history_global) > 500:
                nav_history_global.pop(0)

            nav_hist_display = [
                {'t': datetime.now().strftime('%H:%M'), 'v': round(nav, 2)}
            ]

            # Performance par stratégie
            strategy_perf = {}
            for strat in STRATEGIES:
                hist = pnl_history_by_strat[strat]
                hist.append(strat_pnl.get(strat, 0))
                if len(hist) > 200: hist.pop(0)
                arr = np.array(hist)
                returns = np.diff(arr) if len(arr) > 1 else np.array([0.0])
                cum_pnl = strat_pnl.get(strat, 0)
                strategy_perf[strat] = {
                    'pnl':       round(cum_pnl, 2),
                    'pnl_pct':   round(cum_pnl / CAPITAL * 100, 3),
                    'n_pos':     strat_trades.get(strat, 0),
                    'exposure':  round(strat_value.get(strat, 0), 2),
                    'sharpe':    round(compute_sharpe(returns), 3),
                    'color':     STRATEGIES[strat]['color'],
                    'history':   [round(x, 2) for x in hist[-50:]],
                }

            # Métriques globales
            nav_arr = np.array(nav_history_global)
            returns_global = np.diff(nav_arr) / nav_arr[:-1] if len(nav_arr) > 1 else np.array([0.0])
            peak = float(nav_arr.max()) if len(nav_arr) > 0 else CAPITAL
            dd   = (nav - peak) / peak * 100

            leverage = (total_long + total_short) / CAPITAL

            metrics = {
                'nav':          round(nav, 2),
                'upnl':         round(total_upnl, 2),
                'upnl_pct':     round(total_upnl / CAPITAL * 100, 3),
                'cash':         round(CAPITAL - total_long + total_short, 2),
                'gross_exp':    round(total_long + total_short, 2),
                'net_exp':      round(total_long - total_short, 2),
                'leverage':     round(leverage, 3),
                'drawdown':     round(dd, 2),
                'n_pos':        len(positions),
                'n_long':       sum(1 for p in positions if p['direction'] == 'LONG'),
                'n_short':      sum(1 for p in positions if p['direction'] == 'SHORT'),
                'sharpe':       round(compute_sharpe(returns_global), 3),
                'max_dd':       round(compute_max_dd(nav_history_global) * 100, 2),
            }

            # Greeks
            greeks = {
                'delta': round(agg_delta, 2),
                'gamma': round(agg_gamma, 4),
                'theta': round(agg_theta, 2),
                'vega':  round(agg_vega, 2),
            }

            # Risk manager
            var95  = compute_var(returns_global, 0.95)
            var99  = compute_var(returns_global, 0.99)
            cvar95 = compute_cvar(returns_global, 0.95)

            # Stress tests
            stress = {
                'choc_-30%': round((total_long * -0.30) + (total_short * 0.30), 2),
                'choc_-20%': round((total_long * -0.20) + (total_short * 0.20), 2),
                'choc_-10%': round((total_long * -0.10) + (total_short * 0.10), 2),
                'choc_+10%': round((total_long * 0.10)  + (total_short * -0.10), 2),
                'choc_+20%': round((total_long * 0.20)  + (total_short * -0.20), 2),
            }

            # Corrélations inter-stratégies (historique P&L)
            corr_matrix = {}
            strats_with_data = [s for s in STRATEGIES
                                 if len(pnl_history_by_strat[s]) > 10]
            if len(strats_with_data) >= 2:
                df_pnl = pd.DataFrame({s: pnl_history_by_strat[s][-50:]
                                        for s in strats_with_data})
                corr = df_pnl.corr().round(2)
                corr_matrix = corr.to_dict()

            risk = {
                'var_95':     round(var95 * nav, 2),
                'var_99':     round(var99 * nav, 2),
                'cvar_95':    round(cvar95 * nav, 2),
                'var_95_pct': round(var95 * 100, 3),
                'stress':     stress,
                'corr':       corr_matrix,
                'leverage':   round(leverage, 3),
                'max_leverage': 2.0,
            }

            # Alertes
            alerts = list(state['alerts'])
            if dd < -5:
                alerts.insert(0, {
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'type': 'danger',
                    'msg': f'Drawdown {dd:.1f}% — seuil -5% dépassé'
                })
            if leverage > 1.8:
                alerts.insert(0, {
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'type': 'warning',
                    'msg': f'Levier {leverage:.2f}x proche du max (2x)'
                })
            alerts = alerts[:30]

            # News signals
            news_summary = {}
            if NEWS_ENABLED:
                try:
                    news_summary = get_news_engine().get_summary()
                    if news_summary.get('blocked'):
                        alerts.insert(0, {
                            'time': datetime.now().strftime('%H:%M:%S'),
                            'type': 'warning',
                            'msg': f'MACRO BLOCK actif — bots suspendus jusqu\'à {news_summary["blocking_until"]}'
                        })
                except Exception:
                    pass

            with state_lock:
                state.update({
                    'positions':     sorted(positions, key=lambda x: -abs(x['mkt_value'])),
                    'metrics':       metrics,
                    'nav_history':   nav_hist_display,
                    'strategy_perf': strategy_perf,
                    'greeks':        greeks,
                    'risk':          risk,
                    'alerts':        alerts,
                    'news':          news_summary,
                    'last_update':   datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'connected':     True,
                })

        except Exception as e:
            print(f"Fetch error: {e}")
            with state_lock:
                state['connected'] = False

        time.sleep(REFRESH_S)

@app.route('/api/data')
def api_data():
    with state_lock:
        return jsonify(state)

@app.route('/')
def index():
    return open('/root/bots/dashboard_v2.html').read()

if __name__ == '__main__':
    connect_ib()
    t = threading.Thread(target=fetch_data, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=8080, debug=False)
