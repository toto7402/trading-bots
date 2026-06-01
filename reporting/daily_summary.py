#!/usr/bin/env python3
"""
daily_summary.py - Résumé quotidien performance + signaux via Claude API
Cron: 15 22 * * * python3 /opt/hedge-fund-agents/reporting/daily_summary.py
"""

import os
import sys
import sqlite3
import json
from datetime import datetime, date
from pathlib import Path

TRACK_RECORD_DB = "/var/data/trading/track_record.db"
AGENT_MEMORY_DB = "/opt/hedge-fund-agents/agent_memory.db"
ENV_FILE        = "/opt/hedge-fund/.env"
SUMMARIES_DIR   = Path("/opt/hedge-fund-agents/reporting/daily_summaries")
TODAY           = date.today().isoformat()


def load_api_key():
    """Lit ANTHROPIC_API_KEY depuis .env ou l'environnement."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY"):
                    _, _, val = line.partition("=")
                    val = val.strip().strip('"').strip("'")
                    if val:
                        return val
    except FileNotFoundError:
        pass
    raise RuntimeError(f"ANTHROPIC_API_KEY introuvable dans {ENV_FILE} et les variables d'environnement")


def _tables(db_path):
    try:
        con = sqlite3.connect(db_path)
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = [r[0] for r in cur.fetchall()]
        con.close()
        return names
    except Exception as e:
        print(f"  [WARN] Impossible de lister les tables de {db_path}: {e}")
        return []


def _query(db_path, sql, params=()):
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        con.close()
        return rows
    except Exception as e:
        return [{"error": str(e)}]


def _columns(db_path, table):
    rows = _query(db_path, f"PRAGMA table_info({table})")
    return [r["name"] for r in rows if "name" in r]


def _date_filter_clause(cols):
    """Retourne une clause WHERE date si une colonne date/timestamp existe."""
    for c in cols:
        if c.lower() in ("date", "trade_date", "created_at", "timestamp", "ts"):
            return f"WHERE date({c}) = date('now')"
    return ""


def fetch_db_snapshot(db_path, row_limit=60):
    """Récupère les données récentes de toutes les tables d'une DB."""
    snapshot = {}
    for table in _tables(db_path):
        cols = _columns(db_path, table)
        where = _date_filter_clause(cols)
        rows = _query(db_path, f"SELECT * FROM {table} {where} ORDER BY rowid DESC LIMIT {row_limit}")
        if not rows or (len(rows) == 1 and "error" in rows[0]):
            # Fallback sans filtre date
            rows = _query(db_path, f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT {row_limit}")
        if rows:
            snapshot[table] = rows
    return snapshot


def truncate(obj, max_chars=5000):
    s = json.dumps(obj, indent=2, default=str)
    if len(s) > max_chars:
        s = s[:max_chars] + "\n  ... [tronqué]"
    return s


def generate_summary(api_key, track_data, signals_data):
    try:
        import anthropic
    except ImportError:
        sys.exit("Paquet 'anthropic' manquant. Exécute: pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""Tu es l'analyste principal d'un fonds quantitatif algorithmique. \
Rédige le résumé quotidien de gestion du {TODAY} en exactement 250 mots.

=== DONNÉES PERFORMANCE — track_record.db ===
{truncate(track_data)}

=== SIGNAUX MARCHÉ — agent_memory.db ===
{truncate(signals_data)}

Structure obligatoire (4 sections):

**PERFORMANCE DU JOUR**
P&L réalisé, positions clés, rendement journalier, drawdown éventuel.

**SIGNAUX MARCHÉ**
Principaux signaux déclenchés aujourd'hui: stratégie, direction, conviction, actifs.

**POINTS D'ATTENTION DEMAIN**
Risques identifiés, catalyseurs macro/micro à surveiller, ajustements à envisager.

**VERDICT GLOBAL**
Une seule ligne finale en majuscules:
VERT — journée positive, cap maintenu
ORANGE — vigilance, ajustements nécessaires
ROUGE — pertes significatives ou risques élevés, réduire l'exposition

Sois factuel, précis, sans remplissage. Exactement 250 mots."""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def add_cron_if_missing():
    """Ajoute la tâche cron si elle n'est pas déjà présente."""
    import subprocess
    entry = "15 22 * * * python3 /opt/hedge-fund-agents/reporting/daily_summary.py"
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""
    if entry in existing:
        return False
    new_crontab = existing.rstrip("\n") + "\n" + entry + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    return True


def main():
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SUMMARIES_DIR / f"summary_{TODAY}.txt"

    print(f"[{datetime.now():%H:%M:%S}] Lecture track_record.db  → {TRACK_RECORD_DB}")
    track_data = fetch_db_snapshot(TRACK_RECORD_DB)
    print(f"  Tables trouvées : {list(track_data.keys()) or '(aucune)'}")

    print(f"[{datetime.now():%H:%M:%S}] Lecture agent_memory.db  → {AGENT_MEMORY_DB}")
    signals_data = fetch_db_snapshot(AGENT_MEMORY_DB)
    print(f"  Tables trouvées : {list(signals_data.keys()) or '(aucune)'}")

    print(f"[{datetime.now():%H:%M:%S}] Appel Claude API (claude-opus-4-8)...")
    api_key = load_api_key()
    summary = generate_summary(api_key, track_data, signals_data)

    header = (
        f"RÉSUMÉ QUOTIDIEN — {TODAY}\n"
        f"Généré le {datetime.now():%d/%m/%Y à %H:%M:%S}\n"
        f"{'=' * 60}\n\n"
    )
    full_text = header + summary + "\n"

    output_path.write_text(full_text, encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(full_text)
    print(f"{'=' * 60}")
    print(f"Sauvegardé → {output_path}")

    # Cron
    try:
        added = add_cron_if_missing()
        if added:
            print("Cron ajouté : 15 22 * * *")
        else:
            print("Cron déjà présent.")
    except Exception as e:
        print(f"[WARN] Cron non ajouté automatiquement : {e}")
        print("  Ajoute manuellement : crontab -e")
        print("  15 22 * * * python3 /opt/hedge-fund-agents/reporting/daily_summary.py")


if __name__ == "__main__":
    main()
