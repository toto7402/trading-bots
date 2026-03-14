"""
news_signals.py — Flux actualités IB + NLP pour signaux de trading
===================================================================
Sources :
  - IB News Bulletins (temps réel via API)
  - Finnhub News (par ticker)
  - Finnhub Economic Calendar (annonces macro)

Analyse NLP :
  - Sentiment (positif/négatif/neutre) par ticker
  - Détection d'événements clés : earnings, M&A, upgrade/downgrade,
    FDA approval, Fed, inflation, recession
  - Score de signal : combine sentiment + type d'événement + magnitude

Actions automatiques :
  - Alerte Telegram si événement majeur sur position détenue
  - Blocage des bots pendant annonces macro (NFP, CPI, FOMC)
  - Signal d'opportunité si news très positive sur ticker suivi

ClientId : 10
"""

import os, sys, time, logging, re, json
import threading
import requests
import numpy as np
from datetime import datetime, timedelta, date
from ib_insync import IB, util
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    handlers=[
        logging.FileHandler('news_signals.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)
from alerts import alert_manager

# ── Config ────────────────────────────────────────────────────────────────────
HOST        = '127.0.0.1'
PORT        = 7497
CLIENT_ID   = 10
FINNHUB_KEY = os.environ.get('FINNHUB_KEY', '')
REFRESH_S   = 300   # Scan toutes les 5 min
NEWS_FILE   = 'news_signals_log.json'

# Seuils de signal
SENTIMENT_THRESHOLD_POS =  0.35   # Score > 0.35 → signal positif
SENTIMENT_THRESHOLD_NEG = -0.35   # Score < -0.35 → signal négatif
MAJOR_EVENT_BOOST       =  0.40   # Bonus pour événements majeurs

# ── NLP — Dictionnaires de sentiment ─────────────────────────────────────────

POSITIVE_WORDS = {
    # Résultats financiers
    'beat': 0.6, 'beats': 0.6, 'exceeded': 0.5, 'surpassed': 0.5,
    'record': 0.5, 'strong': 0.4, 'growth': 0.4, 'profit': 0.4,
    'revenue': 0.2, 'earnings': 0.2, 'raised': 0.4, 'upgraded': 0.7,
    'outperform': 0.6, 'overweight': 0.5, 'buy': 0.5, 'bullish': 0.6,
    # M&A / Corporate
    'acquisition': 0.4, 'merger': 0.3, 'deal': 0.3, 'partnership': 0.3,
    'contract': 0.3, 'agreement': 0.3, 'approved': 0.5, 'approval': 0.5,
    # FDA / Biotech
    'fda approval': 0.8, 'approved drug': 0.7, 'positive trial': 0.7,
    'phase 3': 0.5, 'breakthrough': 0.6, 'efficacy': 0.4,
    # Macro positif
    'rate cut': 0.6, 'dovish': 0.5, 'stimulus': 0.4, 'recovery': 0.4,
    'jobs added': 0.4, 'unemployment fell': 0.5,
    # General
    'increase': 0.3, 'higher': 0.2, 'gain': 0.3, 'surge': 0.5,
    'rally': 0.4, 'soar': 0.5, 'jump': 0.4, 'rise': 0.2,
}

NEGATIVE_WORDS = {
    # Résultats financiers
    'missed': -0.6, 'miss': -0.5, 'below': -0.3, 'disappointed': -0.5,
    'loss': -0.5, 'losses': -0.5, 'decline': -0.4, 'downgrade': -0.7,
    'underperform': -0.6, 'underweight': -0.5, 'sell': -0.5, 'bearish': -0.6,
    'cut guidance': -0.7, 'lowered': -0.4, 'reduced': -0.3,
    # Corporate négatif
    'lawsuit': -0.5, 'investigation': -0.5, 'fraud': -0.8, 'scandal': -0.7,
    'bankruptcy': -0.9, 'default': -0.8, 'layoffs': -0.4, 'restructuring': -0.3,
    # FDA / Biotech négatif
    'fda rejection': -0.8, 'clinical hold': -0.7, 'failed trial': -0.8,
    'safety concern': -0.6, 'adverse event': -0.5,
    # Macro négatif
    'rate hike': -0.5, 'hawkish': -0.4, 'inflation': -0.3, 'recession': -0.7,
    'jobs lost': -0.5, 'unemployment rose': -0.4,
    # General
    'decrease': -0.3, 'lower': -0.2, 'fall': -0.3, 'drop': -0.4,
    'crash': -0.7, 'plunge': -0.6, 'tumble': -0.5, 'slump': -0.4,
}

# Événements clés qui déclenchent une alerte immédiate
MAJOR_EVENTS = {
    'fda approval':     ('FDA_APPROVAL',    0.9),
    'fda rejection':    ('FDA_REJECTION',  -0.9),
    'earnings beat':    ('EARNINGS_BEAT',   0.7),
    'earnings miss':    ('EARNINGS_MISS',  -0.7),
    'acquisition':      ('MA_DEAL',         0.5),
    'merger':           ('MA_DEAL',         0.5),
    'takeover':         ('TAKEOVER',        0.8),
    'bankruptcy':       ('BANKRUPTCY',     -0.9),
    'fraud':            ('FRAUD',          -0.9),
    'investigation':    ('INVESTIGATION',  -0.6),
    'downgrade':        ('DOWNGRADE',      -0.6),
    'upgraded':         ('UPGRADE',         0.6),
    'rate cut':         ('RATE_CUT',        0.6),
    'rate hike':        ('RATE_HIKE',      -0.5),
    'fomc':             ('FOMC',            0.0),
    'nonfarm payroll':  ('NFP',             0.0),
    'cpi':              ('CPI',             0.0),
}

# Annonces macro bloquantes (suspendent les bots 30 min avant/après)
MACRO_BLOCKING = ['fomc', 'nonfarm payroll', 'cpi', 'pce', 'gdp', 'fed decision']

# ── Analyse NLP ───────────────────────────────────────────────────────────────

def compute_sentiment(text: str) -> tuple:
    """
    Calcule un score de sentiment entre -1 et +1.
    Retourne (score, events_detected, is_blocking)
    """
    text_lower = text.lower()
    score = 0.0
    events = []
    is_blocking = False
    word_count = max(len(text.split()), 1)

    # Mots positifs
    for phrase, weight in POSITIVE_WORDS.items():
        if phrase in text_lower:
            score += weight

    # Mots négatifs
    for phrase, weight in NEGATIVE_WORDS.items():
        if phrase in text_lower:
            score += weight  # Négatif

    # Événements majeurs
    for phrase, (event_type, boost) in MAJOR_EVENTS.items():
        if phrase in text_lower:
            events.append(event_type)
            score += boost * MAJOR_EVENT_BOOST

    # Vérifier si annonce macro bloquante
    for macro in MACRO_BLOCKING:
        if macro in text_lower:
            is_blocking = True
            break

    # Normaliser
    score = max(-1.0, min(1.0, score / max(word_count / 10, 1)))

    return round(score, 3), events, is_blocking

def extract_tickers_from_text(text: str, known_tickers: set) -> list:
    """Extrait les tickers mentionnés dans le texte."""
    found = []
    words = re.findall(r'\b[A-Z]{2,5}\b', text)
    for w in words:
        if w in known_tickers:
            found.append(w)
    return list(set(found))

# ── Sources de données ────────────────────────────────────────────────────────

def get_positions_tickers(ib: IB) -> set:
    """Retourne les tickers des positions actuelles."""
    tickers = set()
    for pos in ib.positions():
        tickers.add(pos.contract.symbol)
    return tickers

def fetch_finnhub_news(ticker: str, days_back: int = 1) -> list:
    """Récupère les news Finnhub pour un ticker."""
    if not FINNHUB_KEY:
        return []
    try:
        today = date.today()
        from_date = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
        to_date   = today.strftime('%Y-%m-%d')
        url = f"https://finnhub.io/api/v1/company-news"
        params = {'symbol': ticker, 'from': from_date, 'to': to_date, 'token': FINNHUB_KEY}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning(f"Finnhub news error {ticker}: {e}")
        return []

def fetch_finnhub_macro_calendar() -> list:
    """Récupère le calendrier économique des 3 prochains jours."""
    if not FINNHUB_KEY:
        return []
    try:
        today = date.today()
        to    = (today + timedelta(days=3)).strftime('%Y-%m-%d')
        url   = "https://finnhub.io/api/v1/calendar/economic"
        params = {'from': today.strftime('%Y-%m-%d'), 'to': to, 'token': FINNHUB_KEY}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        return data.get('economicCalendar', [])
    except Exception as e:
        log.warning(f"Macro calendar error: {e}")
        return []

def fetch_ib_news_bulletins(ib: IB) -> list:
    """Récupère les bulletins d'actualité IB."""
    try:
        bulletins = []
        ib.reqNewsBulletins(allMessages=True)
        ib.sleep(2)
        for b in ib.newsBulletins():
            bulletins.append({
                'source': 'IB',
                'text':   b.message,
                'time':   datetime.now().strftime('%H:%M:%S'),
            })
        return bulletins
    except Exception as e:
        log.warning(f"IB bulletins error: {e}")
        return []

# ── Moteur de signaux ─────────────────────────────────────────────────────────

class NewsSignalEngine:
    def __init__(self):
        self.processed_ids = set()
        self.blocking_until: datetime = None
        self.signals_today = []
        self.macro_events_today = []

    def is_blocked(self) -> bool:
        """Retourne True si annonce macro en cours (bots suspendus)."""
        if self.blocking_until and datetime.now() < self.blocking_until:
            return True
        return False

    def block_for_macro(self, event_name: str):
        """Bloque les bots 30 min pour une annonce macro."""
        self.blocking_until = datetime.now() + timedelta(minutes=30)
        log.warning(f"MACRO BLOCK : {event_name} — bots suspendus jusqu'à {self.blocking_until.strftime('%H:%M')}")
        alert_manager._send(
            f"⏸️ *MACRO EVENT DÉTECTÉ*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Événement : *{event_name}*\n"
            f"⏰ Bots suspendus 30 min\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}\n"
            f"_Reprise automatique à {self.blocking_until.strftime('%H:%M')}_"
        )

    def process_article(self, ticker: str, headline: str, summary: str = '',
                         source: str = '') -> dict:
        """Analyse un article et génère un signal si pertinent."""
        text = f"{headline} {summary}"
        score, events, is_blocking = compute_sentiment(text)

        signal = {
            'ticker':    ticker,
            'headline':  headline[:120],
            'source':    source,
            'score':     score,
            'events':    events,
            'blocking':  is_blocking,
            'time':      datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'action':    'NONE',
        }

        if is_blocking:
            event_name = next((e for e in events if e in
                              ['FOMC','NFP','CPI']), 'MACRO')
            self.block_for_macro(event_name)

        if score >= SENTIMENT_THRESHOLD_POS:
            signal['action'] = 'BULLISH'
            log.info(f"SIGNAL BULLISH {ticker} (score {score:+.2f}) : {headline[:80]}")
        elif score <= SENTIMENT_THRESHOLD_NEG:
            signal['action'] = 'BEARISH'
            log.info(f"SIGNAL BEARISH {ticker} (score {score:+.2f}) : {headline[:80]}")

        # Alertes pour événements majeurs sur positions détenues
        if events and (abs(score) > 0.4):
            event_str = ', '.join(events)
            self.send_news_alert(ticker, headline, score, events)

        return signal

    def send_news_alert(self, ticker: str, headline: str,
                         score: float, events: list):
        """Envoie une alerte Telegram pour un événement majeur."""
        direction = "🟢 BULLISH" if score > 0 else "🔴 BEARISH"
        events_str = ' | '.join(events)
        msg = (
            f"📰 *NEWS SIGNAL — {ticker}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{direction} (score {score:+.2f})\n"
            f"📌 {headline[:100]}\n"
            f"🏷️ {events_str}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        alert_manager._send(msg)

    def scan_all_positions(self, ib: IB):
        """Scan les news pour toutes les positions détenues."""
        tickers = get_positions_tickers(ib)
        if not tickers:
            log.info("Aucune position — skip news scan")
            return

        log.info(f"News scan : {len(tickers)} tickers")
        new_signals = []

        for ticker in tickers:
            articles = fetch_finnhub_news(ticker, days_back=1)
            for art in articles[:5]:  # Max 5 articles par ticker
                art_id = f"{ticker}_{art.get('id','')}"
                if art_id in self.processed_ids:
                    continue
                self.processed_ids.add(art_id)

                signal = self.process_article(
                    ticker     = ticker,
                    headline   = art.get('headline', ''),
                    summary    = art.get('summary', ''),
                    source     = art.get('source', 'Finnhub'),
                )
                if signal['action'] != 'NONE':
                    new_signals.append(signal)

            time.sleep(0.5)  # Rate limit Finnhub

        self.signals_today.extend(new_signals)
        if len(self.signals_today) > 100:
            self.signals_today = self.signals_today[-100:]

        # Sauvegarder
        self._save_signals()
        return new_signals

    def scan_macro_calendar(self):
        """Vérifie les annonces macro à venir."""
        events = fetch_finnhub_macro_calendar()
        upcoming = []
        now = datetime.now()

        for ev in events:
            try:
                ev_time = datetime.fromtimestamp(ev.get('time', 0))
                delta_min = (ev_time - now).total_seconds() / 60
                impact = ev.get('impact', '')

                if 0 <= delta_min <= 60 and impact == 'high':
                    ev_name = ev.get('event', '')
                    upcoming.append({
                        'event': ev_name,
                        'time':  ev_time.strftime('%H:%M'),
                        'in_min': int(delta_min),
                        'actual': ev.get('actual', ''),
                        'estimate': ev.get('estimate', ''),
                    })
                    log.info(f"Macro event dans {int(delta_min)} min : {ev_name}")

                    if delta_min <= 30:
                        self.block_for_macro(ev_name)
            except Exception:
                continue

        self.macro_events_today = upcoming
        return upcoming

    def get_summary(self) -> dict:
        """Retourne un résumé pour le dashboard."""
        bullish = [s for s in self.signals_today if s['action'] == 'BULLISH']
        bearish = [s for s in self.signals_today if s['action'] == 'BEARISH']
        return {
            'signals_today':   len(self.signals_today),
            'bullish':         len(bullish),
            'bearish':         len(bearish),
            'blocked':         self.is_blocked(),
            'blocking_until':  self.blocking_until.strftime('%H:%M') if self.blocking_until else None,
            'macro_upcoming':  self.macro_events_today,
            'recent_signals':  self.signals_today[-10:],
        }

    def _save_signals(self):
        try:
            with open(NEWS_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'signals': self.signals_today[-50:],
                    'macro':   self.macro_events_today,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"Save signals error: {e}")


# ── Instance globale partageable ──────────────────────────────────────────────
engine = NewsSignalEngine()

def get_engine() -> NewsSignalEngine:
    return engine

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("Démarrage News Signal Engine")

    ib = IB()
    while datetime.now().weekday() >= 5:
        log.info("Weekend — attente lundi...")
        time.sleep(3600)

    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID)
        log.info(f"Connecté — {ib.wrapper.accounts}")
    except Exception as e:
        alert_manager.notify_crash('news_bot', str(e))
        raise

    log.info(f"Scan toutes les {REFRESH_S}s")
    log.info("Événements bloquants : NFP, CPI, FOMC, PCE")

    try:
        while True:
            try:
                # Scan macro calendar
                engine.scan_macro_calendar()

                # Scan news positions
                if not engine.is_blocked():
                    signals = engine.scan_all_positions(ib)
                    if signals:
                        log.info(f"{len(signals)} nouveaux signaux")
                else:
                    log.info(f"Bloqué jusqu'à {engine.blocking_until.strftime('%H:%M')}")

                summary = engine.get_summary()
                log.info(f"Résumé : {summary['signals_today']} signaux "
                         f"({summary['bullish']} bull / {summary['bearish']} bear)")

            except Exception as e:
                log.error(f"Scan error: {e}")
                alert_manager.notify_crash('news_bot', str(e))

            time.sleep(REFRESH_S)

    except KeyboardInterrupt:
        log.info("Arrêt manuel")
    finally:
        ib.disconnect()


if __name__ == '__main__':
    main()
