# -*- coding: utf-8 -*-
"""
news_signals.py -- News Signal Engine v2
==========================================
NLP avance par regles combinees + contexte geopolitique
Sources : IB TWS News Bulletins + Finnhub
ClientId : 10
"""

import os, sys, re, time, json, logging, requests
from datetime import datetime, timedelta, date
import numpy as np
import pandas as pd
from ib_insync import IB, Stock, util

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

HOST        = '127.0.0.1'
PORT         = 4002
CLIENT_ID   = 10
FINNHUB_KEY = os.environ.get('FINNHUB_KEY', '')
REFRESH_S   = 300
NEWS_FILE   = 'news_signals_log.json'

SIGNAL_THRESHOLD = 0.35

#        Regles NLP combinees                                                                                                                                                                   
# Format : (mots_requis, mots_exclus, signal, score, description)
# mots_requis : TOUS doivent etre presents (AND)
# mots_exclus : AUCUN ne doit etre present

COMBINED_RULES = [

    #        FDA / BIOTECH                                                                                                                                                                            
    (['fda', 'approv'],          ['reject','deny','refuse','fail'],   'FDA_APPROVAL',   0.85, 'FDA approval'),
    (['fda', 'approved'],        ['not','reject','deny'],             'FDA_APPROVAL',   0.90, 'FDA approved'),
    (['phase 3', 'success'],     ['fail','miss','disappoint'],        'FDA_APPROVAL',   0.70, 'Phase 3 success'),
    (['phase 3', 'met', 'endpoint'], ['not','fail','miss'],          'FDA_APPROVAL',   0.75, 'Phase 3 endpoint met'),
    (['breakthrough', 'therapy'], [],                                 'FDA_APPROVAL',   0.60, 'Breakthrough therapy'),
    (['fda', 'reject'],          [],                                  'FDA_REJECTION', -0.85, 'FDA rejection'),
    (['complete response letter'], [],                                'FDA_REJECTION', -0.80, 'FDA CRL'),
    (['clinical hold'],          [],                                  'FDA_REJECTION', -0.75, 'Clinical hold'),
    (['phase 3', 'fail'],        [],                                  'FDA_REJECTION', -0.80, 'Phase 3 failure'),
    (['missed', 'primary endpoint'], [],                             'FDA_REJECTION', -0.80, 'Missed endpoint'),

    #        MACRO RISK OFF                                                                                                                                                                         
    (['cpi', 'higher', 'expected'],   ['lower','cool','soft'],  'MACRO_RISK_OFF', -0.70, 'CPI hot'),
    (['cpi', 'beat'],                 ['miss'],                 'MACRO_RISK_OFF', -0.70, 'CPI beat'),
    (['inflation', 'accelerat'],      ['slow','cool'],          'MACRO_RISK_OFF', -0.65, 'Inflation accelerating'),
    (['fed', 'hike'],                 ['pause','cut'],          'MACRO_RISK_OFF', -0.65, 'Fed hike'),
    (['fed', 'hawkish'],              [],                       'MACRO_RISK_OFF', -0.70, 'Fed hawkish'),
    (['rate', 'hike'],                ['cut','pause'],          'MACRO_RISK_OFF', -0.60, 'Rate hike'),
    (['nfp', 'beat'],                 ['miss'],                 'MACRO_RISK_OFF', -0.55, 'NFP beat'),
    (['jobs', 'above', 'expected'],   [],                       'MACRO_RISK_OFF', -0.55, 'Jobs above expected'),
    (['recession', 'fear'],           [],                       'MACRO_RISK_OFF', -0.60, 'Recession fears'),
    (['yield curve', 'invert'],       [],                       'MACRO_RISK_OFF', -0.65, 'Yield curve inversion'),

    #        MACRO RISK ON                                                                                                                                                                            
    (['cpi', 'cool'],             ['hot','beat','higher'],      'MACRO_RISK_ON',  0.70, 'CPI cooling'),
    (['cpi', 'miss'],             ['beat'],                     'MACRO_RISK_ON',  0.70, 'CPI miss'),
    (['inflation', 'cool'],       [],                           'MACRO_RISK_ON',  0.65, 'Inflation cooling'),
    (['fed', 'cut'],              ['hike'],                     'MACRO_RISK_ON',  0.70, 'Fed cut'),
    (['fed', 'dovish'],           [],                           'MACRO_RISK_ON',  0.70, 'Fed dovish'),
    (['fed', 'pivot'],            [],                           'MACRO_RISK_ON',  0.75, 'Fed pivot'),
    (['fed', 'pause'],            [],                           'MACRO_RISK_ON',  0.60, 'Fed pause'),
    (['rate', 'cut'],             ['hike'],                     'MACRO_RISK_ON',  0.65, 'Rate cut'),
    (['nfp', 'miss'],             ['beat'],                     'MACRO_RISK_ON',  0.55, 'NFP miss'),
    (['soft landing'],            [],                           'MACRO_RISK_ON',  0.60, 'Soft landing'),
    (['disinflation'],            [],                           'MACRO_RISK_ON',  0.60, 'Disinflation'),

    #        GEOPOLITIQUE PETROLE HAUSSE                                                                                                                                  
    (['iran', 'sanction'],        ['lift','remove','end','deal'], 'GEO_OIL_UP',  0.70, 'Iran sanctions'),
    (['russia', 'sanction', 'oil'], ['lift','remove'],           'GEO_OIL_UP',  0.70, 'Russia oil sanctions'),
    (['opec', 'cut'],             ['increas','rais'],             'GEO_OIL_UP',  0.75, 'OPEC cut'),
    (['opec+', 'reduc'],         [],                              'GEO_OIL_UP',  0.70, 'OPEC+ reduction'),
    (['pipeline', 'disrupt'],    [],                              'GEO_OIL_UP',  0.65, 'Pipeline disruption'),
    (['middle east', 'escal'],   [],                              'GEO_OIL_UP',  0.65, 'Middle East escalation'),
    (['oil', 'supply', 'cut'],   [],                              'GEO_OIL_UP',  0.70, 'Oil supply cut'),
    (['gulf', 'tension'],        ['ease','resolv'],               'GEO_OIL_UP',  0.60, 'Gulf tensions'),
    (['houthi', 'attack'],       [],                              'GEO_OIL_UP',  0.65, 'Houthi attack shipping'),
    (['strait', 'hormuz'],       ['open','safe'],                 'GEO_OIL_UP',  0.70, 'Strait of Hormuz risk'),

    #        GEOPOLITIQUE PETROLE BAISSE                                                                                                                                  
    (['trump', 'iran', 'deal'],       [],  'GEO_OIL_DOWN', -0.75, 'Trump Iran deal'),
    (['iran', 'nuclear', 'deal'],     [],  'GEO_OIL_DOWN', -0.75, 'Iran nuclear deal'),
    (['iran', 'sanction', 'lift'],    [],  'GEO_OIL_DOWN', -0.80, 'Iran sanctions lifted'),
    (['iran', 'sanction', 'remov'],   [],  'GEO_OIL_DOWN', -0.80, 'Iran sanctions removed'),
    (['ceasefire', 'ukraine'],        [],  'GEO_OIL_DOWN', -0.70, 'Ukraine ceasefire'),
    (['peace', 'russia', 'ukraine'],  [],  'GEO_OIL_DOWN', -0.70, 'Russia Ukraine peace'),
    (['trump', 'peace', 'ukraine'],   [],  'GEO_OIL_DOWN', -0.70, 'Trump Ukraine peace'),
    (['opec', 'increas'],             [],  'GEO_OIL_DOWN', -0.70, 'OPEC increase'),
    (['opec+', 'rais', 'output'],     [],  'GEO_OIL_DOWN', -0.70, 'OPEC+ raise output'),
    (['ceasefire', 'middle east'],    [],  'GEO_OIL_DOWN', -0.65, 'Middle East ceasefire'),
    (['peace deal', 'israel'],        [],  'GEO_OIL_DOWN', -0.65, 'Israel peace deal'),

    #        GEOPOLITIQUE OR HAUSSE                                                                                                                                                 
    (['war', 'declar'],           ['end','over','peace'],  'GEO_GOLD_UP',  0.70, 'War declared'),
    (['nuclear', 'threat'],       [],                      'GEO_GOLD_UP',  0.75, 'Nuclear threat'),
    (['bank', 'fail'],            [],                      'GEO_GOLD_UP',  0.70, 'Bank failure'),
    (['financial', 'crisis'],     [],                      'GEO_GOLD_UP',  0.75, 'Financial crisis'),
    (['debt ceiling'],            [],                      'GEO_GOLD_UP',  0.65, 'Debt ceiling'),
    (['dollar', 'weak'],          [],                      'GEO_GOLD_UP',  0.60, 'Dollar weakness'),
    (['safe haven', 'demand'],    [],                      'GEO_GOLD_UP',  0.65, 'Safe haven demand'),
    (['flight to quality'],       [],                      'GEO_GOLD_UP',  0.70, 'Flight to quality'),
    (['trump', 'tariff', 'china'], ['remov','lift'],       'GEO_GOLD_UP',  0.65, 'Trump China tariffs'),
    (['trade war', 'escalat'],    [],                      'GEO_GOLD_UP',  0.65, 'Trade war escalation'),

    #        GEOPOLITIQUE OR BAISSE                                                                                                                                                 
    (['peace', 'deal'],           ['fail','break'],        'GEO_GOLD_DOWN', -0.55, 'Peace deal'),
    (['risk', 'on'],              [],                      'GEO_GOLD_DOWN', -0.50, 'Risk on'),
    (['dollar', 'strong'],        [],                      'GEO_GOLD_DOWN', -0.55, 'Dollar strong'),
    (['trade deal', 'sign'],      [],                      'GEO_GOLD_DOWN', -0.60, 'Trade deal signed'),
    (['trump', 'china', 'deal'],  [],                      'GEO_GOLD_DOWN', -0.65, 'Trump China deal'),
    (['tariff', 'remov'],         [],                      'GEO_GOLD_DOWN', -0.60, 'Tariff removed'),

    #        M&A                                                                                                                                                                                                          
    (['acquir', 'billion'],       ['not','fail','block'],  'MA_DEAL',  0.80, 'Acquisition announced'),
    (['merger', 'agree'],         ['terminat','block'],    'MA_DEAL',  0.80, 'Merger agreement'),
    (['takeover', 'bid'],         ['reject','fail'],       'MA_DEAL',  0.80, 'Takeover bid'),
    (['tender offer'],            [],                      'MA_DEAL',  0.80, 'Tender offer'),
    (['buyout', 'agree'],         [],                      'MA_DEAL',  0.75, 'Buyout agreement'),
    (['to be acquir'],            [],                      'MA_DEAL',  0.85, 'To be acquired'),
    (['all-cash', 'deal'],        [],                      'MA_DEAL',  0.85, 'All-cash deal'),
    (['premium', 'offer', 'share'], [],                   'MA_DEAL',  0.80, 'Premium offer'),

    #        EARNINGS                                                                                                                                                                                        
    (['earnings', 'beat'],        ['miss','fail'],         'EARNINGS_BEAT',  0.75, 'Earnings beat'),
    (['eps', 'beat'],             ['miss'],                'EARNINGS_BEAT',  0.75, 'EPS beat'),
    (['raised', 'guidance'],      ['lower','cut'],         'EARNINGS_BEAT',  0.70, 'Raised guidance'),
    (['record', 'revenue'],       [],                      'EARNINGS_BEAT',  0.65, 'Record revenue'),
    (['above', 'consensus'],      [],                      'EARNINGS_BEAT',  0.70, 'Above consensus'),
    (['earnings', 'miss'],        ['beat'],                'EARNINGS_MISS', -0.75, 'Earnings miss'),
    (['eps', 'miss'],             ['beat'],                'EARNINGS_MISS', -0.75, 'EPS miss'),
    (['cut', 'guidance'],         ['rais'],                'EARNINGS_MISS', -0.75, 'Cut guidance'),
    (['profit', 'warning'],       [],                      'EARNINGS_MISS', -0.80, 'Profit warning'),
    (['below', 'consensus'],      [],                      'EARNINGS_MISS', -0.70, 'Below consensus'),

    #        MACRO BLOQUANT                                                                                                                                                                         
    (['fomc', 'decision'],        [],   'MACRO_BLOCK',  0.0, 'FOMC decision'),
    (['federal reserve', 'rate'], [],   'MACRO_BLOCK',  0.0, 'Fed rate decision'),
    (['nonfarm payroll'],         [],   'MACRO_BLOCK',  0.0, 'NFP release'),
    (['consumer price index'],    [],   'MACRO_BLOCK',  0.0, 'CPI release'),
    (['cpi', 'release'],          [],   'MACRO_BLOCK',  0.0, 'CPI release'),
    (['pce', 'inflation'],        [],   'MACRO_BLOCK',  0.0, 'PCE inflation'),
    (['gdp', 'release'],          [],   'MACRO_BLOCK',  0.0, 'GDP release'),
]

# Instruments par signal
SIGNAL_INSTRUMENTS = {
    'FDA_APPROVAL':   {'buy': ['XBI'], 'sell': []},
    'FDA_REJECTION':  {'buy': [], 'sell': ['XBI']},
    'MACRO_RISK_OFF': {'buy': ['TLT', 'GLD', 'VXX'], 'sell': ['SPY', 'QQQ']},
    'MACRO_RISK_ON':  {'buy': ['SPY', 'QQQ', 'IWM'], 'sell': ['TLT', 'GLD']},
    'GEO_OIL_UP':     {'buy': ['USO', 'XLE'], 'sell': []},
    'GEO_OIL_DOWN':   {'buy': [], 'sell': ['USO', 'XLE']},
    'GEO_GOLD_UP':    {'buy': ['GLD', 'GDX'], 'sell': []},
    'GEO_GOLD_DOWN':  {'buy': [], 'sell': ['GLD']},
    'EARNINGS_BEAT':  {'buy': ['TICKER'], 'sell': []},
    'EARNINGS_MISS':  {'buy': [], 'sell': ['TICKER']},
    'MA_DEAL':        {'buy': ['TARGET'], 'sell': []},
}

def analyze(headline, body=''):
    text = (headline + ' ' + body).lower()
    signals = []
    for required, excluded, signal_type, score, desc in COMBINED_RULES:
        if all(kw in text for kw in required):
            if not any(kw in text for kw in excluded):
                signals.append((signal_type, score, desc))
    # Deduplication : garder signal avec score max par type
    best = {}
    for sig, score, desc in signals:
        if sig not in best or abs(score) > abs(best[sig][0]):
            best[sig] = (score, desc)
    return [(sig, score, desc) for sig, (score, desc) in best.items()]

def extract_tickers(text):
    tickers = re.findall(r'\b([A-Z]{2,5})\b', text)
    known = {
        'MRNA','BNTX','NVAX','VRTX','REGN','BIIB','ALNY','RCUS','SAGE',
        'SANA','VERA','MIRM','NTLA','GERN','HALO','HRMY','INVA','MNKD',
        'PCVX','DNLI','SPY','QQQ','IWM','TLT','GLD','GDX','USO','VXX',
        'XLE','XLF','XLK','XLV','XBI','XLU','XLB','ITA',
    }
    return [t for t in set(tickers) if t in known]

def extract_ma_target(text):
    patterns = [
        r'acquires? ([A-Z]{2,5})',
        r'buys? ([A-Z]{2,5})',
        r'([A-Z]{2,5}) to be acquired',
        r'takeover of ([A-Z]{2,5})',
        r'merger with ([A-Z]{2,5})',
        r'tender offer for ([A-Z]{2,5})',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return None

#        IB et Finnhub                                                                                                                                                                                           

def fetch_ib_news(ib):
    news = []
    try:
        ib.reqNewsBulletins(allMessages=True)
        ib.sleep(2)
        for b in ib.newsBulletins():
            news.append({'id': f"ib_{abs(hash(b.message))}", 'headline': b.message[:300], 'body': '', 'source': 'IB'})
    except Exception as e:
        log.debug(f"IB bulletins: {e}")
    key_tickers = ['SPY', 'GLD', 'USO', 'XBI', 'TLT', 'QQQ']
    for ticker in key_tickers:
        try:
            c = Stock(ticker, 'SMART', 'USD')
            qualified = ib.qualifyContracts(c)
            if not qualified: continue
            end = datetime.now().strftime('%Y%m%d %H:%M:%S')
            headlines = ib.reqHistoricalNews(qualified[0].conId, 'BRFG+DJNL+RTRS', '', end, 3)
            ib.sleep(0.5)
            for h in headlines:
                news.append({'id': f"ibh_{h.articleId}", 'headline': h.headline, 'body': '', 'source': h.providerCode, 'ticker': ticker})
        except Exception as e:
            log.debug(f"IB hist news {ticker}: {e}")
    return news

def fetch_finnhub_news(tickers):
    if not FINNHUB_KEY:
        return []
    news = []
    today = date.today()
    yest  = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    for ticker in tickers[:5]:
        try:
            r = requests.get('https://finnhub.io/api/v1/company-news',
                params={'symbol': ticker, 'from': yest, 'to': today.strftime('%Y-%m-%d'), 'token': FINNHUB_KEY},
                timeout=8)
            for art in r.json()[:3]:
                news.append({'id': f"fh_{art.get('id','')}", 'headline': art.get('headline',''), 'body': art.get('summary','')[:300], 'source': 'Finnhub'})
            time.sleep(0.1)
        except Exception:
            pass
    return news

def fetch_macro_calendar():
    if not FINNHUB_KEY:
        return []
    try:
        today = date.today()
        r = requests.get('https://finnhub.io/api/v1/calendar/economic',
            params={'from': today.strftime('%Y-%m-%d'), 'to': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'token': FINNHUB_KEY},
            timeout=8)
        return [e for e in r.json().get('economicCalendar', []) if e.get('impact') == 'high']
    except Exception:
        return []

#        Engine                                                                                                                                                                                                             

class NewsEngine:
    def __init__(self):
        self.processed   = set()
        self.signals_log = []
        self.macro_events = []
        self.blocked_until = None

    def is_blocked(self):
        return self.blocked_until and datetime.now() < self.blocked_until

    def block(self, event, minutes=30):
        self.blocked_until = datetime.now() + timedelta(minutes=minutes)
        log.warning(f"MACRO BLOCK: {event} -- {minutes} min")
        alert_manager._send(f"MACRO BLOCK: {event}\nSuspension {minutes} min\nReprise: {self.blocked_until.strftime('%H:%M')}")

    def process(self, item):
        item_id  = item.get('id', '')
        if item_id in self.processed: return
        self.processed.add(item_id)

        headline = item.get('headline', '').strip()
        body     = item.get('body', '')
        if len(headline) < 10: return

        signals  = analyze(headline, body)
        tickers  = extract_tickers(headline + ' ' + body)
        ma_target = extract_ma_target(headline + ' ' + body)

        for sig, score, desc in signals:
            if sig == 'MACRO_BLOCK':
                self.block(desc)
                continue

            if abs(score) < SIGNAL_THRESHOLD:
                continue

            log.info(f"SIGNAL: {sig} ({score:+.2f}) -- {desc} -- '{headline[:60]}'")

            record = {
                'time':    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'signal':  sig,
                'score':   score,
                'desc':    desc,
                'headline': headline[:120],
                'tickers': tickers,
                'ma_target': ma_target,
                'action':  'BULLISH' if score > 0 else 'BEARISH',
                'source':  item.get('source', ''),
            }
            self.signals_log.append(record)
            if len(self.signals_log) > 200:
                self.signals_log = self.signals_log[-200:]

            # Alerte Telegram
            direction = "BULLISH" if score > 0 else "BEARISH"
            instruments = SIGNAL_INSTRUMENTS.get(sig, {})
            buys  = [t for t in instruments.get('buy', []) if t not in ('TICKER','TARGET')]
            sells = [t for t in instruments.get('sell', []) if t not in ('TICKER','TARGET')]
            if 'TICKER' in instruments.get('buy', []) and tickers:
                buys += tickers[:2]
            if 'TARGET' in instruments.get('buy', []) and ma_target:
                buys.append(ma_target)

            alert_manager._send(
                f"NEWS SIGNAL -- {sig}\n"
                f"{direction} (score {score:+.2f})\n"
                f"Raison: {desc}\n"
                f"BUY: {', '.join(buys) if buys else 'aucun'}\n"
                f"SELL: {', '.join(sells) if sells else 'aucun'}\n"
                f"News: {headline[:100]}\n"
                f"{datetime.now().strftime('%H:%M:%S')}"
            )

    def save(self):
        try:
            with open(NEWS_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'signals': self.signals_log[-50:],
                    'blocked': self.is_blocked(),
                    'blocking_until': self.blocked_until.strftime('%H:%M') if self.blocked_until else None,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"Save: {e}")

    def get_summary(self):
        bullish = [s for s in self.signals_log if s['action'] == 'BULLISH']
        bearish = [s for s in self.signals_log if s['action'] == 'BEARISH']
        return {
            'signals_today':   len(self.signals_log),
            'bullish':         len(bullish),
            'bearish':         len(bearish),
            'blocked':         self.is_blocked(),
            'blocking_until':  self.blocked_until.strftime('%H:%M') if self.blocked_until else None,
            'macro_upcoming':  self.macro_events,
            'recent_signals':  self.signals_log[-10:],
        }

engine = NewsEngine()

def get_engine():
    return engine

#        Main                                                                                                                                                                                                                   

def main():
    log.info("News Signal Engine v2 -- Regles combinees + contexte geo")

    ib = IB()
    while datetime.now().weekday() >= 5:
        log.info("Weekend -- attente...")
        time.sleep(3600)

    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID)
        log.info(f"Connecte: {ib.wrapper.accounts}")
    except Exception as e:
        alert_manager.notify_crash('news_bot', str(e))
        raise

    log.info(f"Regles NLP: {len(COMBINED_RULES)} regles combinees")
    log.info("Contexte geopol: Trump/Iran/Ukraine/OPEC/Fed/FDA/M&A")

    try:
        while True:
            try:
                # Macro calendar
                events = fetch_macro_calendar()
                engine.macro_events = events
                for ev in events:
                    try:
                        from datetime import datetime as dt
                        ev_time = dt.fromtimestamp(ev.get('time', 0))
                        delta = (ev_time - datetime.now()).total_seconds() / 60
                        if 0 <= delta <= 30:
                            engine.block(ev.get('event', 'MACRO'))
                    except Exception:
                        pass

                if not engine.is_blocked():
                    # IB news
                    news = fetch_ib_news(ib)
                    if len(news) < 3:
                        news += fetch_finnhub_news(['SPY','GLD','USO','XBI','TLT','QQQ'])

                    for item in news:
                        engine.process(item)

                    engine.save()
                    s = engine.get_summary()
                    log.info(f"Signaux: {s['signals_today']} total ({s['bullish']} bull / {s['bearish']} bear)")
                else:
                    log.info(f"Bloque jusqu'a {engine.blocked_until.strftime('%H:%M')}")

            except Exception as e:
                log.error(f"Cycle error: {e}")
                alert_manager.notify_crash('news_bot', str(e))

            time.sleep(REFRESH_S)

    except KeyboardInterrupt:
        log.info("Arret")
    finally:
        ib.disconnect()

if __name__ == '__main__':
    main()
