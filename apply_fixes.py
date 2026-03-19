"""
apply_fixes.py — Applique toutes les corrections aux bots de trading
Lancer depuis le dossier C:\\Users\\thoml\\trading-bots\\
Usage: python apply_fixes.py
"""

import re
import os
import sys

fixes_applied = []
fixes_failed  = []

def fix_file(filename, replacements):
    if not os.path.exists(filename):
        print(f"  ⚠️  Fichier introuvable : {filename}")
        fixes_failed.append(filename)
        return

    with open(filename, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    original = content
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
        else:
            print(f"  ⚠️  Pattern non trouvé dans {filename} : {old[:60]}...")

    if content != original:
        with open(filename, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
        print(f"  ✅ {filename}")
        fixes_applied.append(filename)
    else:
        print(f"  ℹ️  {filename} — déjà à jour ou pattern introuvable")

print("=" * 60)
print("  APPLY FIXES — Trading Bots")
print("=" * 60)
print()

# ── 1. risk_dashboard.py ─────────────────────────────────────────
print("[1/7] risk_dashboard.py — ClientId + CAPITAL")
fix_file('risk_dashboard.py', [
    (
        'CLIENT_ID  = 9          # clientId dédié au dashboard',
        'CLIENT_ID  = 20         # clientId dédié au risk dashboard (dashboard_server utilise 9)'
    ),
    (
        'CAPITAL    = 50_000',
        'CAPITAL    = 1_090_000'
    ),
])

# ── 2. arb2_spinoff.py — CRLF → LF ──────────────────────────────
print("[2/7] arb2_spinoff.py — CRLF line endings")
if os.path.exists('arb2_spinoff.py'):
    with open('arb2_spinoff.py', 'rb') as f:
        raw = f.read()
    fixed = raw.replace(b'\r\n', b'\n')
    if fixed != raw:
        with open('arb2_spinoff.py', 'wb') as f:
            f.write(fixed)
        print("  ✅ arb2_spinoff.py")
        fixes_applied.append('arb2_spinoff.py')
    else:
        print("  ℹ️  arb2_spinoff.py — déjà en LF")

# ── 3. alerts.py — check token ───────────────────────────────────
print("[3/7] alerts.py — vérification token")
fix_file('alerts.py', [
    (
        '        if TELEGRAM_TOKEN.startswith("VOTRE"):\n'
        '            log.warning("AlertManager : TELEGRAM_TOKEN non configuré, alerte ignorée.")\n'
        '            print(f"  [ALERTE NON ENVOYÉE — Telegram non configuré]\\n  {text}")\n'
        '            return',
        '        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:\n'
        '            log.warning("AlertManager : TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID manquant — alerte ignorée.")\n'
        '            print(f"  [ALERTE NON ENVOYÉE — Telegram non configuré]\\n  {text}")\n'
        '            return'
    ),
])

# ── 4. arb8_news_trading.py — heure ET ──────────────────────────
print("[4/7] arb8_news_trading.py — calcul heure ET")
fix_file('arb8_news_trading.py', [
    (
        '            now = datetime.now()\n'
        '            weekday = now.weekday()\n'
        '            hour    = (now.hour - 5) + now.minute / 60  # UTC -> ET\n'
        '\n'
        '            market_open = (weekday < 5 and 9.5 <= hour <= 16.0)',
        '            now = datetime.now()\n'
        '            weekday = now.weekday()\n'
        '            # Heure locale machine (pas de conversion UTC hardcodée)\n'
        '            hour_local = now.hour + now.minute / 60\n'
        '\n'
        '            market_open = (weekday < 5 and 9.5 <= hour_local <= 16.0)'
    ),
])

# ── 5. arb5_options.py — warning FINNHUB_KEY ────────────────────
print("[5/7] arb5_options.py — warning FINNHUB_KEY manquante")
fix_file('arb5_options.py', [
    (
        "FINNHUB_KEY = os.environ.get('FINNHUB_KEY', '')\n",
        "FINNHUB_KEY = os.environ.get('FINNHUB_KEY', '')\n"
        "if not FINNHUB_KEY:\n"
        "    print(\"WARNING: Variable d'environnement FINNHUB_KEY manquante. \"\n"
        "          \"Les stratégies dépendant de Finnhub seront désactivées.\")\n"
    ),
])

# ── 6. arb9_intraday.py — PairsBot EOD close ────────────────────
print("[6/7] arb9_intraday.py — PairsBot EOD close")
fix_file('arb9_intraday.py', [
    (
        '                else:\n'
        '                    all_t = list(set(t for p in self.PAIRS for t in p))\n'
        '                    for t in all_t:\n'
        '                        for pk, p in list(self.pos.items()):\n'
        '                            if t in pk:\n'
        '                                action = \'SELL\' if p[\'dir\']==\'LONG_SPREAD\' else \'BUY\'\n'
        '                    self.ib.sleep(60)',
        '                else:\n'
        '                    for pk, p in list(self.pos.items()):\n'
        '                        parts = pk.split(\'_\')\n'
        '                        tx, ty = parts[0], parts[1]\n'
        '                        if p[\'dir\'] == \'LONG_SPREAD\':\n'
        '                            place_order(self.ib, tx, \'SELL\', p[\'sx\'])\n'
        '                            place_order(self.ib, ty, \'BUY\',  p[\'sy\'])\n'
        '                        else:\n'
        '                            place_order(self.ib, tx, \'BUY\',  p[\'sx\'])\n'
        '                            place_order(self.ib, ty, \'SELL\', p[\'sy\'])\n'
        '                        del self.pos[pk]\n'
        '                    self.ib.sleep(60)'
    ),
])

# ── 7. dashboard_server_v2.py — indentation STRATEGIES ──────────
print("[7/7] dashboard_server_v2.py — indentation STRATEGIES dict")
fix_file('dashboard_server_v2.py', [
    (
        "'MR_Intraday':  {'color': '#06b6d4', 'csv': 'mr_positions.csv'},\n"
        " 'ETF_Mom':      {'color': '#f59e0b', 'csv': 'etf_mom_positions.csv'},\n"
        " 'Pairs':        {'color': '#ec4899', 'csv': 'pairs_positions.csv'},\n"
        " 'Breakout':     {'color': '#84cc16', 'csv': 'breakout_positions.csv'},\n"
        " 'News':         {'color': '#f97316', 'csv': 'news_trading_positions.csv'},",
        "    'MR_Intraday':  {'color': '#06b6d4', 'csv': 'mr_positions.csv'},\n"
        "    'ETF_Mom':      {'color': '#f59e0b', 'csv': 'etf_mom_positions.csv'},\n"
        "    'Pairs':        {'color': '#ec4899', 'csv': 'pairs_positions.csv'},\n"
        "    'Breakout':     {'color': '#84cc16', 'csv': 'breakout_positions.csv'},\n"
        "    'News':         {'color': '#f97316', 'csv': 'news_trading_positions.csv'},"
    ),
])

# ── Résumé ────────────────────────────────────────────────────────
print()
print("=" * 60)
print(f"  {len(fixes_applied)} correction(s) appliquée(s)")
if fixes_failed:
    print(f"  {len(fixes_failed)} fichier(s) introuvable(s) : {fixes_failed}")
print("=" * 60)
print()
print("Prochaine étape :")
print("  git add .")
print('  git commit -m "Fix: clientId, PairsBot EOD, ET timezone, CRLF, CAPITAL"')
print("  git push origin master")
