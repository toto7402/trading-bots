"""
dashboard_server.py — Serveur web Flask pour le dashboard de trading
Tourne sur le VPS, accessible via navigateur sur http://VPS_IP:8080
Se connecte à TWS via le tunnel SSH existant (127.0.0.1:7497)
"""

from flask import Flask, jsonify, render_template_string
from ib_insync import IB, util
import threading
import time
import json
import os
from datetime import datetime
import numpy as np

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
HOST      = '127.0.0.1'
PORT      = 7497
CLIENT_ID = 9
CAPITAL   = 1_090_000
REFRESH_S = 30

# ── State global ─────────────────────────────────────────────────────────────
state = {
    'positions':    [],
    'metrics':      {},
    'nav_history':  [],
    'alerts':       [],
    'last_update':  None,
    'connected':    False,
}
state_lock = threading.Lock()

# ── IB Connection ─────────────────────────────────────────────────────────────
ib = IB()

def connect_ib():
    global ib
    try:
        if ib.isConnected():
            return True
        ib.connect(HOST, PORT, clientId=CLIENT_ID)
        return True
    except Exception as e:
        print(f"IB connection error: {e}")
        return False

def fetch_data():
    global state
    while True:
        try:
            if not ib.isConnected():
                connect_ib()
                time.sleep(5)
                continue

            positions = []
            total_upnl = 0
            total_long = 0
            total_short = 0

            portfolio = {item.contract.symbol: item for item in ib.portfolio()}
            for pos in ib.positions():
                t = pos.contract.symbol
                item = portfolio.get(t)
                if not item:
                    continue
                mkt_v = item.marketValue
                upnl  = item.unrealizedPNL
                cost  = abs(pos.avgCost * pos.position)
                ret   = upnl / cost * 100 if cost > 0 else 0
                direction = 'LONG' if pos.position > 0 else 'SHORT'

                positions.append({
                    'ticker':    t,
                    'shares':    int(pos.position),
                    'avg_cost':  round(pos.avgCost, 3),
                    'mkt_price': round(item.marketPrice, 3),
                    'mkt_value': round(mkt_v, 2),
                    'upnl':      round(upnl, 2),
                    'return_pct':round(ret, 2),
                    'weight':    round(abs(mkt_v) / CAPITAL * 100, 2),
                    'direction': direction,
                })

                total_upnl += upnl
                if direction == 'LONG':
                    total_long += mkt_v
                else:
                    total_short += abs(mkt_v)

            nav = CAPITAL + total_upnl
            nav_history = state['nav_history'] + [{'t': datetime.now().strftime('%H:%M'), 'v': round(nav, 2)}]
            nav_history = nav_history[-100:]  # garder 100 points

            # Drawdown
            peak = max([x['v'] for x in nav_history]) if nav_history else CAPITAL
            dd   = (nav - peak) / peak * 100

            # Alertes
            alerts = list(state['alerts'])
            if dd < -5:
                alerts.insert(0, {
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'type': 'danger',
                    'msg':  f'Drawdown {dd:.1f}% — seuil -5% dépassé'
                })
                alerts = alerts[:20]

            metrics = {
                'nav':         round(nav, 2),
                'upnl':        round(total_upnl, 2),
                'upnl_pct':    round(total_upnl / CAPITAL * 100, 2),
                'cash':        round(CAPITAL - total_long + total_short, 2),
                'gross_exp':   round(total_long + total_short, 2),
                'net_exp':     round(total_long - total_short, 2),
                'drawdown':    round(dd, 2),
                'n_positions': len(positions),
                'n_long':      sum(1 for p in positions if p['direction'] == 'LONG'),
                'n_short':     sum(1 for p in positions if p['direction'] == 'SHORT'),
            }

            with state_lock:
                state['positions']   = sorted(positions, key=lambda x: -abs(x['mkt_value']))
                state['metrics']     = metrics
                state['nav_history'] = nav_history
                state['alerts']      = alerts
                state['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                state['connected']   = True

        except Exception as e:
            print(f"Fetch error: {e}")
            with state_lock:
                state['connected'] = False
        
        time.sleep(REFRESH_S)

# ── API Routes ────────────────────────────────────────────────────────────────
@app.route('/api/data')
def api_data():
    with state_lock:
        return jsonify(state)

@app.route('/api/alert', methods=['POST'])
def add_alert():
    from flask import request
    data = request.json
    with state_lock:
        state['alerts'].insert(0, {
            'time': datetime.now().strftime('%H:%M:%S'),
            'type': data.get('type', 'info'),
            'msg':  data.get('msg', '')
        })
        state['alerts'] = state['alerts'][:20]
    return jsonify({'ok': True})

# ── HTML Dashboard ────────────────────────────────────────────────────────────
HTML = '''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {
    --bg:       #0a0a0a;
    --surface:  #111111;
    --border:   #222222;
    --text:     #e8e8e8;
    --muted:    #666666;
    --accent:   #f0f0f0;
    --green:    #4ade80;
    --red:      #f87171;
    --orange:   #fb923c;
    --blue:     #60a5fa;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    font-size: 13px;
    min-height: 100vh;
  }
  
  /* Header */
  .header {
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    background: var(--bg);
    z-index: 100;
  }
  .header-left { display: flex; align-items: center; gap: 16px; }
  .logo {
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--green);
    animation: pulse 2s infinite;
  }
  .dot.offline { background: var(--red); animation: none; }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }
  .last-update { font-family: 'DM Mono', monospace; font-size: 11px; color: var(--muted); }
  .refresh-btn {
    background: none; border: 1px solid var(--border); color: var(--muted);
    padding: 6px 14px; border-radius: 4px; cursor: pointer;
    font-family: 'DM Mono', monospace; font-size: 11px; letter-spacing: 1px;
    transition: all 0.2s;
  }
  .refresh-btn:hover { border-color: var(--text); color: var(--text); }

  /* Layout */
  .main { padding: 24px; display: grid; gap: 16px; }
  
  /* KPI Row */
  .kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
  .kpi {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px 18px;
  }
  .kpi-label { font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }
  .kpi-value { font-family: 'DM Mono', monospace; font-size: 20px; font-weight: 500; }
  .kpi-sub { font-size: 11px; color: var(--muted); margin-top: 4px; }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .neu { color: var(--text); }

  /* Grid 2 cols */
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 900px) { .grid-2 { grid-template-columns: 1fr; } }

  /* Card */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .card-header {
    padding: 12px 18px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .card-title { font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); }
  .card-body { padding: 18px; }

  /* Table */
  table { width: 100%; border-collapse: collapse; }
  th {
    font-size: 10px; letter-spacing: 1.5px; text-transform: uppercase;
    color: var(--muted); padding: 8px 10px; text-align: right;
    border-bottom: 1px solid var(--border); font-weight: 400;
  }
  th:first-child { text-align: left; }
  td {
    padding: 9px 10px; text-align: right;
    font-family: 'DM Mono', monospace; font-size: 12px;
    border-bottom: 1px solid #1a1a1a;
  }
  td:first-child { text-align: left; font-family: 'DM Sans', sans-serif; font-weight: 500; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #161616; }
  .badge {
    display: inline-block; padding: 2px 7px; border-radius: 3px;
    font-size: 10px; letter-spacing: 1px; font-family: 'DM Mono', monospace;
  }
  .badge-long  { background: rgba(74,222,128,0.1); color: var(--green); }
  .badge-short { background: rgba(248,113,113,0.1); color: var(--red); }

  /* Chart */
  .chart-wrap { position: relative; height: 200px; }

  /* Alerts */
  .alert-list { display: flex; flex-direction: column; gap: 8px; max-height: 280px; overflow-y: auto; }
  .alert-item {
    padding: 10px 14px; border-radius: 4px; border-left: 3px solid;
    font-size: 12px;
  }
  .alert-danger  { background: rgba(248,113,113,0.05); border-color: var(--red); }
  .alert-warning { background: rgba(251,146,60,0.05);  border-color: var(--orange); }
  .alert-info    { background: rgba(96,165,250,0.05);  border-color: var(--blue); }
  .alert-time { font-family: 'DM Mono', monospace; font-size: 10px; color: var(--muted); margin-right: 8px; }
  .no-alerts { color: var(--muted); font-size: 12px; text-align: center; padding: 24px; }

  /* Metrics grid */
  .metrics-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .metric-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #1a1a1a; }
  .metric-row:last-child { border-bottom: none; }
  .metric-label { color: var(--muted); font-size: 12px; }
  .metric-value { font-family: 'DM Mono', monospace; font-size: 12px; }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <span class="logo">Trading / DUP091760</span>
    <div class="dot" id="status-dot"></div>
  </div>
  <div class="header-left">
    <span class="last-update" id="last-update">—</span>
    <button class="refresh-btn" onclick="loadData()">↻ REFRESH</button>
  </div>
</div>

<div class="main">

  <!-- KPIs -->
  <div class="kpi-row" id="kpi-row">
    <div class="kpi"><div class="kpi-label">NAV</div><div class="kpi-value" id="kpi-nav">—</div></div>
    <div class="kpi"><div class="kpi-label">P&L non réalisé</div><div class="kpi-value" id="kpi-upnl">—</div><div class="kpi-sub" id="kpi-upnl-pct">—</div></div>
    <div class="kpi"><div class="kpi-label">Cash</div><div class="kpi-value" id="kpi-cash">—</div></div>
    <div class="kpi"><div class="kpi-label">Drawdown</div><div class="kpi-value" id="kpi-dd">—</div></div>
    <div class="kpi"><div class="kpi-label">Positions</div><div class="kpi-value" id="kpi-pos">—</div><div class="kpi-sub" id="kpi-pos-sub">—</div></div>
    <div class="kpi"><div class="kpi-label">Exposition nette</div><div class="kpi-value" id="kpi-net">—</div></div>
  </div>

  <!-- NAV Chart + Alerts -->
  <div class="grid-2">
    <div class="card">
      <div class="card-header"><span class="card-title">NAV History</span></div>
      <div class="card-body"><div class="chart-wrap"><canvas id="nav-chart"></canvas></div></div>
    </div>
    <div class="card">
      <div class="card-header"><span class="card-title">Alertes</span><span id="alert-count" style="font-family:DM Mono;font-size:11px;color:var(--muted)">0</span></div>
      <div class="card-body">
        <div class="alert-list" id="alert-list">
          <div class="no-alerts">Aucune alerte</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Positions -->
  <div class="card">
    <div class="card-header"><span class="card-title">Positions</span><span id="pos-count" style="font-family:DM Mono;font-size:11px;color:var(--muted)">0 positions</span></div>
    <div class="card-body" style="padding:0;">
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Dir</th>
            <th>Shares</th>
            <th>Avg Cost</th>
            <th>Mkt Price</th>
            <th>Mkt Value</th>
            <th>P&L $</th>
            <th>Return</th>
            <th>Weight</th>
          </tr>
        </thead>
        <tbody id="positions-table"></tbody>
      </table>
    </div>
  </div>

</div>

<script>
let navChart = null;

function fmt(n, decimals=2) {
  if (n === null || n === undefined) return '—';
  return new Intl.NumberFormat('fr-FR', {minimumFractionDigits: decimals, maximumFractionDigits: decimals}).format(n);
}

function fmtUSD(n) {
  if (n === null || n === undefined) return '—';
  const sign = n >= 0 ? '+' : '';
  return sign + '$' + fmt(Math.abs(n));
}

function colorClass(n) {
  if (n > 0) return 'pos';
  if (n < 0) return 'neg';
  return 'neu';
}

function loadData() {
  fetch('/api/data')
    .then(r => r.json())
    .then(data => {
      const m = data.metrics || {};
      const dot = document.getElementById('status-dot');
      dot.className = 'dot' + (data.connected ? '' : ' offline');
      document.getElementById('last-update').textContent = data.last_update || '—';

      // KPIs
      const nav = m.nav || 0;
      document.getElementById('kpi-nav').textContent = '$' + fmt(nav);
      const upnlEl = document.getElementById('kpi-upnl');
      upnlEl.textContent = fmtUSD(m.upnl);
      upnlEl.className = 'kpi-value ' + colorClass(m.upnl);
      document.getElementById('kpi-upnl-pct').textContent = (m.upnl_pct >= 0 ? '+' : '') + fmt(m.upnl_pct) + '%';
      document.getElementById('kpi-cash').textContent = '$' + fmt(m.cash);
      const ddEl = document.getElementById('kpi-dd');
      ddEl.textContent = fmt(m.drawdown) + '%';
      ddEl.className = 'kpi-value ' + (m.drawdown < -5 ? 'neg' : m.drawdown < -2 ? 'orange' : 'pos');
      document.getElementById('kpi-pos').textContent = m.n_positions || 0;
      document.getElementById('kpi-pos-sub').textContent = (m.n_long || 0) + 'L / ' + (m.n_short || 0) + 'S';
      document.getElementById('kpi-net').textContent = '$' + fmt(m.net_exp);

      // NAV Chart
      const hist = data.nav_history || [];
      if (hist.length > 1) {
        const labels = hist.map(x => x.t);
        const values = hist.map(x => x.v);
        const isUp = values[values.length-1] >= values[0];
        const color = isUp ? '#4ade80' : '#f87171';
        if (!navChart) {
          const ctx = document.getElementById('nav-chart').getContext('2d');
          navChart = new Chart(ctx, {
            type: 'line',
            data: {
              labels,
              datasets: [{
                data: values,
                borderColor: color,
                backgroundColor: color + '15',
                borderWidth: 1.5,
                pointRadius: 0,
                fill: true,
                tension: 0.3,
              }]
            },
            options: {
              responsive: true, maintainAspectRatio: false,
              plugins: { legend: { display: false } },
              scales: {
                x: { grid: { color: '#1a1a1a' }, ticks: { color: '#666', font: { family: 'DM Mono', size: 10 }, maxTicksLimit: 6 } },
                y: { grid: { color: '#1a1a1a' }, ticks: { color: '#666', font: { family: 'DM Mono', size: 10 }, callback: v => '$' + (v/1000).toFixed(0) + 'k' } }
              }
            }
          });
        } else {
          navChart.data.labels = labels;
          navChart.data.datasets[0].data = values;
          navChart.data.datasets[0].borderColor = color;
          navChart.data.datasets[0].backgroundColor = color + '15';
          navChart.update();
        }
      }

      // Positions table
      const tbody = document.getElementById('positions-table');
      const positions = data.positions || [];
      document.getElementById('pos-count').textContent = positions.length + ' position' + (positions.length > 1 ? 's' : '');
      if (positions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#666;padding:24px;">Aucune position</td></tr>';
      } else {
        tbody.innerHTML = positions.map(p => `
          <tr>
            <td><strong>${p.ticker}</strong></td>
            <td><span class="badge badge-${p.direction.toLowerCase()}">${p.direction}</span></td>
            <td>${p.shares}</td>
            <td>$${fmt(p.avg_cost, 3)}</td>
            <td>$${fmt(p.mkt_price, 3)}</td>
            <td>$${fmt(Math.abs(p.mkt_value))}</td>
            <td class="${colorClass(p.upnl)}">${fmtUSD(p.upnl)}</td>
            <td class="${colorClass(p.return_pct)}">${(p.return_pct >= 0 ? '+' : '') + fmt(p.return_pct)}%</td>
            <td>${fmt(p.weight)}%</td>
          </tr>
        `).join('');
      }

      // Alerts
      const alerts = data.alerts || [];
      document.getElementById('alert-count').textContent = alerts.length;
      const alertList = document.getElementById('alert-list');
      if (alerts.length === 0) {
        alertList.innerHTML = '<div class="no-alerts">Aucune alerte</div>';
      } else {
        alertList.innerHTML = alerts.map(a => `
          <div class="alert-item alert-${a.type}">
            <span class="alert-time">${a.time}</span>${a.msg}
          </div>
        `).join('');
      }
    })
    .catch(e => console.error('Fetch error:', e));
}

// Auto-refresh toutes les 30s
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>'''

@app.route('/')
def index():
    return HTML

if __name__ == '__main__':
    # Thread de fetch IB en arrière-plan
    if connect_ib():
        t = threading.Thread(target=fetch_data, daemon=True)
        t.start()
    else:
        print("IB non connecté — données simulées")
        t = threading.Thread(target=fetch_data, daemon=True)
        t.start()

    app.run(host='0.0.0.0', port=8080, debug=False)
