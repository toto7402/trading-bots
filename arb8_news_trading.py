# -*- coding: utf-8 -*-
"""
arb8_news_trading.py -- News Trading Bot avec LLM (Claude API)
================================================================
NLP remplace par Claude claude-sonnet-4-20250514 pour comprendre
le contexte reel des news (geopolitique, macro, M&A, FDA...)

Workflow :
  1. Reception news (IB TWS + Finnhub)
  2. Envoi a Claude API -> analyse contexte + signal structure
  3. Execution automatique selon le signal
  4. Alerte Telegram
"""

import os, sys, re, time, json, logging, requests
from datetime import datetime, timedelta, date
import numpy as np
import pandas as pd
from ib_insync import IB, Stock, LimitOrder

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    handlers=[
        logging.FileHandler('news_trading.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)
from alerts import alert_manager

#        Config                                                                                                                                                                                                             
HOST        = '127.0.0.1'
PORT         = 4002
CLIENT_ID   = 11
CAPITAL     = 1_090_000
FINNHUB_KEY = os.environ.get('FINNHUB_KEY', '')
MAX_LEVER   = 2.0
STOP_LOSS   = -0.10
SCAN_INTERVAL = 300  # 5 minutes
CSV_FILE    = 'news_trading_positions.csv'

SIZING = {
    'FDA_APPROVAL':   0.03,
    'FDA_REJECTION':  0.03,
    'EARNINGS_BEAT':  0.02,
    'EARNINGS_MISS':  0.02,
    'MA_DEAL':        0.025,
    'MACRO_RISK_OFF': 0.04,
    'MACRO_RISK_ON':  0.04,
    'GEO_OIL_UP':     0.03,
    'GEO_OIL_DOWN':   0.03,
    'GEO_GOLD_UP':    0.03,
    'GEO_GOLD_DOWN':  0.02,
    'SECTOR_BULLISH': 0.02,
    'SECTOR_BEARISH': 0.02,
}

HOLD_DAYS = {
    'FDA_APPROVAL':   30,
    'FDA_REJECTION':  10,
    'EARNINGS_BEAT':  60,
    'EARNINGS_MISS':  30,
    'MA_DEAL':        90,
    'MACRO_RISK_OFF': 5,
    'MACRO_RISK_ON':  5,
    'GEO_OIL_UP':     7,
    'GEO_OIL_DOWN':   7,
    'GEO_GOLD_UP':    7,
    'GEO_GOLD_DOWN':  5,
    'SECTOR_BULLISH': 10,
    'SECTOR_BEARISH': 10,
}

# Mapping signal -> instruments a trader
SIGNAL_TO_INSTRUMENTS = {
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
    'SECTOR_BULLISH': {'buy': ['SECTOR_ETF'], 'sell': []},
    'SECTOR_BEARISH': {'buy': [], 'sell': ['SECTOR_ETF']},
}

SECTOR_ETFS = {
    'biotech': 'XBI', 'energy': 'XLE', 'financials': 'XLF',
    'tech': 'XLK', 'healthcare': 'XLV', 'utilities': 'XLU',
    'materials': 'XLB', 'defense': 'ITA', 'gold_miners': 'GDX',
}

#        Claude LLM NLP                                                                                                                                                                                     

CLAUDE_SYSTEM = """Tu es un analyste quantitatif senior specialise dans le news trading pour un hedge fund.
Ton role : analyser une news financiere et determiner son impact precis sur les marches.

Tu dois repondre UNIQUEMENT en JSON valide avec cette structure exacte :
{
  "signal": "NOM_DU_SIGNAL ou null",
  "confidence": 0.0 a 1.0,
  "reasoning": "explication courte en 1 phrase",
  "tickers": ["TICKER1", "TICKER2"],
  "sector_etf": "XBI ou XLE ou XLF etc ou null",
  "ma_target": "TICKER cible M&A ou null",
  "hold_days_override": null ou nombre de jours
}

Signaux disponibles :
- FDA_APPROVAL : approbation FDA, fin d'essai positif -> BUY biotech
- FDA_REJECTION : rejet FDA, echec essai -> SHORT biotech
- EARNINGS_BEAT : resultats > consensus -> BUY
- EARNINGS_MISS : resultats < consensus -> SHORT
- MA_DEAL : fusion/acquisition annoncee -> BUY cible
- MACRO_RISK_OFF : CPI chaud, Fed hawkish, recession -> BUY TLT/GLD, SHORT SPY
- MACRO_RISK_ON : Fed dovish, bonne macro, stimulus -> BUY SPY/QQQ, SHORT TLT
- GEO_OIL_UP : guerre, sanctions, OPEC cut -> BUY USO/XLE
- GEO_OIL_DOWN : paix, accord nucleaire, OPEC+ increase -> SHORT USO/XLE
- GEO_GOLD_UP : crise, guerre, dollar faible -> BUY GLD/GDX
- GEO_GOLD_DOWN : risk-on, dollar fort -> SHORT GLD
- SECTOR_BULLISH : news positive pour un secteur
- SECTOR_BEARISH : news negative pour un secteur
- null : news sans impact trading clair

IMPORTANT : 
- Si Trump annonce la fin d'une guerre -> GEO_OIL_DOWN (moins de prime de risque) + GEO_GOLD_DOWN
- Si sanctions levees sur Iran -> GEO_OIL_DOWN (plus d'offre)
- Si Fed plus hawkish que prevu -> MACRO_RISK_OFF
- Si accord commercial -> MACRO_RISK_ON
- Confidence < 0.5 = retourner null (ne pas trader si incertain)
- Repondre UNIQUEMENT en JSON, zero texte avant ou apres"""

def analyze_with_claude(headline, body=''):
    """Envoie la news a Claude API et recupere le signal structure."""
    try:
        prompt = f"News headline: {headline}"
        if body:
            prompt += f"\n\nBody: {body[:500]}"

        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'Content-Type': 'application/json',
                'anthropic-version': '2023-06-01',
            },
            json={
                'model': 'claude-sonnet-4-20250514',
                'max_tokens': 300,
                'system': CLAUDE_SYSTEM,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=15
        )

        if resp.status_code != 200:
            log.warning(f"Claude API error {resp.status_code}: {resp.text[:100]}")
            return None

        content = resp.json()['content'][0]['text'].strip()
        # Nettoyer si besoin
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        elif '```' in content:
            content = content.split('```')[1].split('```')[0].strip()

        result = json.loads(content)

        # Valider
        if result.get('confidence', 0) < 0.5:
            return None
        if not result.get('signal'):
            return None

        log.info(f"Claude signal: {result['signal']} "
                 f"(confidence {result['confidence']:.2f}) -- {result['reasoning']}")
        return result

    except json.JSONDecodeError as e:
        log.warning(f"Claude JSON parse error: {e}")
        return None
    except Exception as e:
        log.warning(f"Claude API call error: {e}")
        return None

#        IB connexion et ordres                                                                                                                                                             

def connect():
    ib = IB()
    while datetime.now().weekday() >= 5:
        log.info("Weekend -- attente...")
        time.sleep(3600)
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
    log.info(f"Connecte : {ib.wrapper.accounts}")
    return ib

def get_nav(ib):
    for v in ib.accountValues():
        if v.tag == 'NetLiquidation' and v.currency in ('USD', 'EUR', 'BASE'):
            return float(v.value)
    return CAPITAL

def get_leverage(ib):
    nav = get_nav(ib)
    gross = sum(abs(item.marketValue) for item in ib.portfolio())
    return gross / nav if nav > 0 else 0

def get_price(ib, ticker):
    try:
        c = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(c)
        ib.reqMarketDataType(3)
        t = ib.reqMktData(c, '', False, False)
        ib.sleep(2)
        for attr in [t.last, t.close, t.bid, t.ask]:
            try:
                val = float(attr)
                if not np.isnan(val) and val > 0:
                    ib.cancelMktData(c)
                    return val
            except Exception:
                continue
        ib.cancelMktData(c)
        return None
    except Exception as e:
        log.warning(f"Price {ticker}: {e}")
        return None

def place_order(ib, ticker, action, shares):
    try:
        c = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(c)
        px = get_price(ib, ticker)
        if not px:
            return False, 0
        limit_px = round(px * (1.005 if action == 'BUY' else 0.995), 2)
        order = LimitOrder(action, shares, limit_px)
        trade = ib.placeOrder(c, order)
        timeout = 30
        while not trade.isDone() and timeout > 0:
            ib.sleep(1); timeout -= 1
        if trade.orderStatus.status == 'Filled':
            fill = trade.orderStatus.avgFillPrice
            log.info(f"FILL: {action} {shares}x {ticker} @ ${fill:.2f}")
            row = {'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
                   'ticker': ticker, 'action': action,
                   'shares': shares, 'price': round(fill, 3)}
            df_new = pd.DataFrame([row])
            if os.path.exists(CSV_FILE):
                df_new = pd.concat([pd.read_csv(CSV_FILE), df_new], ignore_index=True)
            df_new.to_csv(CSV_FILE, index=False)
            return True, fill
        ib.cancelOrder(order)
        return False, 0
    except Exception as e:
        log.warning(f"Order {ticker}: {e}")
        return False, 0

#        Sources de news                                                                                                                                                                                  

def fetch_ib_news(ib):
    news = []
    try:
        ib.reqNewsBulletins(allMessages=True)
        ib.sleep(2)
        for b in ib.newsBulletins():
            news.append({
                'id': f"ib_{abs(hash(b.message))}",
                'headline': b.message[:300],
                'body': '',
                'source': 'IB',
            })
    except Exception as e:
        log.debug(f"IB bulletins: {e}")

    # Historical news sur tickers cles via TWS
    key_tickers = ['SPY', 'GLD', 'USO', 'XBI', 'TLT']
    for ticker in key_tickers:
        try:
            c = Stock(ticker, 'SMART', 'USD')
            qualified = ib.qualifyContracts(c)
            if not qualified:
                continue
            end = datetime.now().strftime('%Y%m%d %H:%M:%S')
            headlines = ib.reqHistoricalNews(
                qualified[0].conId, 'BRFG+DJNL+RTRS', '', end, 2
            )
            ib.sleep(0.5)
            for h in headlines:
                news.append({
                    'id': f"ibh_{h.articleId}",
                    'headline': h.headline,
                    'body': '',
                    'source': h.providerCode,
                    'ticker': ticker,
                })
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
                params={'symbol': ticker, 'from': yest,
                        'to': today.strftime('%Y-%m-%d'), 'token': FINNHUB_KEY},
                timeout=8)
            for art in r.json()[:2]:
                news.append({
                    'id': f"fh_{art.get('id','')}",
                    'headline': art.get('headline', ''),
                    'body': art.get('summary', '')[:300],
                    'source': 'Finnhub',
                    'ticker': ticker,
                })
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
            params={'from': today.strftime('%Y-%m-%d'),
                    'to': (today + timedelta(days=2)).strftime('%Y-%m-%d'),
                    'token': FINNHUB_KEY}, timeout=8)
        return [e for e in r.json().get('economicCalendar', [])
                if e.get('impact') == 'high']
    except Exception:
        return []

#        Bot principal                                                                                                                                                                                        

class NewsTradingBot:
    def __init__(self, ib):
        self.ib           = ib
        self.positions    = {}
        self.processed    = set()
        self.peak_nav     = get_nav(ib)
        self.blocked_until = None

    def is_blocked(self):
        return self.blocked_until and datetime.now() < self.blocked_until

    def block(self, event, minutes=30):
        self.blocked_until = datetime.now() + timedelta(minutes=minutes)
        log.warning(f"BLOCK: {event} -- {minutes} min")
        alert_manager._send(
            f"MACRO BLOCK: {event}\n"
            f"Bots suspendus {minutes} min\n"
            f"Reprise: {self.blocked_until.strftime('%H:%M')}"
        )

    def check_stop_loss(self):
        nav = get_nav(self.ib)
        self.peak_nav = max(self.peak_nav, nav)
        dd = (nav - self.peak_nav) / self.peak_nav
        if dd <= STOP_LOSS:
            log.warning(f"STOP LOSS: {dd*100:.1f}%")
            alert_manager.notify_crash('news_trading', f"Stop loss {dd*100:.1f}%")
            self.close_all()
            return True
        return False

    def close_all(self):
        for ticker, pos in list(self.positions.items()):
            action = 'SELL' if pos['shares'] > 0 else 'BUY'
            place_order(self.ib, ticker, action, abs(pos['shares']))
        self.positions = {}

    def close_expired(self):
        to_close = []
        for ticker, pos in self.positions.items():
            days = (datetime.now() - pos['entry_date']).days
            if days >= pos['hold_days']:
                action = 'SELL' if pos['shares'] > 0 else 'BUY'
                ok, px = place_order(self.ib, ticker, action, abs(pos['shares']))
                if ok:
                    pnl = pos['shares'] * (px - pos['entry'])
                    log.info(f"Close {ticker}: P&L ${pnl:+.0f} ({days}j)")
                    alert_manager._send(
                        f"CLOSE {ticker}\n"
                        f"P&L: ${pnl:+.0f}\n"
                        f"Signal: {pos['signal']}\n"
                        f"Duree: {days}j"
                    )
                to_close.append(ticker)
        for t in to_close:
            del self.positions[t]

    def execute_signal(self, result, headline):
        """Execute le signal retourne par Claude."""
        signal      = result.get('signal')
        confidence  = result.get('confidence', 0)
        tickers     = result.get('tickers', [])
        sector_etf  = result.get('sector_etf')
        ma_target   = result.get('ma_target')
        hold_override = result.get('hold_days_override')

        if not signal or confidence < 0.5:
            return

        nav = get_nav(self.ib)
        if get_leverage(self.ib) >= MAX_LEVER:
            log.warning("Levier max atteint -- skip signal")
            return

        pos_size  = nav * SIZING.get(signal, 0.02)
        hold_days = hold_override or HOLD_DAYS.get(signal, 10)

        instruments = SIGNAL_TO_INSTRUMENTS.get(signal, {'buy': [], 'sell': []})

        # Remplacer les placeholders par les vrais tickers
        buys  = []
        sells = []

        for t in instruments.get('buy', []):
            if t == 'TICKER':
                buys.extend(tickers[:2])
            elif t == 'TARGET':
                if ma_target: buys.append(ma_target)
                elif tickers: buys.append(tickers[0])
            elif t == 'SECTOR_ETF':
                if sector_etf: buys.append(sector_etf)
            else:
                buys.append(t)

        for t in instruments.get('sell', []):
            if t == 'TICKER':
                sells.extend(tickers[:2])
            elif t == 'SECTOR_ETF':
                if sector_etf: sells.append(sector_etf)
            else:
                sells.append(t)

        # Ajouter les tickers specifiques mentionnes
        for ticker in tickers[:2]:
            if ticker not in buys + sells and ticker not in ('SPY','QQQ','IWM','TLT','GLD'):
                if signal in ('FDA_APPROVAL', 'EARNINGS_BEAT', 'MA_DEAL'):
                    buys.append(ticker)
                elif signal in ('FDA_REJECTION', 'EARNINGS_MISS'):
                    sells.append(ticker)

        # Executer les achats
        for ticker in buys:
            if ticker in self.positions:
                continue
            px = get_price(self.ib, ticker)
            if not px:
                continue
            shares = max(1, int(pos_size / px))
            ok, fill = place_order(self.ib, ticker, 'BUY', shares)
            if ok:
                self.positions[ticker] = {
                    'shares': shares, 'entry': fill,
                    'signal': signal, 'entry_date': datetime.now(),
                    'hold_days': hold_days,
                }
                alert_manager._send(
                    f"NEWS TRADE -- {signal}\n"
                    f"BUY {shares}x {ticker} @ ${fill:.2f}\n"
                    f"Confidence: {confidence:.0%}\n"
                    f"Hold: {hold_days}j\n"
                    f"News: {headline[:80]}\n"
                    f"{result.get('reasoning', '')}"
                )

        # Executer les ventes/shorts
        for ticker in sells:
            if ticker in self.positions:
                continue
            px = get_price(self.ib, ticker)
            if not px:
                continue
            shares = max(1, int(pos_size / px))
            ok, fill = place_order(self.ib, ticker, 'SELL', shares)
            if ok:
                self.positions[ticker] = {
                    'shares': -shares, 'entry': fill,
                    'signal': signal, 'entry_date': datetime.now(),
                    'hold_days': hold_days,
                }
                alert_manager._send(
                    f"NEWS TRADE -- {signal}\n"
                    f"SHORT {shares}x {ticker} @ ${fill:.2f}\n"
                    f"Confidence: {confidence:.0%}\n"
                    f"Hold: {hold_days}j\n"
                    f"News: {headline[:80]}\n"
                    f"{result.get('reasoning', '')}"
                )

    def run_cycle(self):
        if self.check_stop_loss():
            return

        self.close_expired()

        # Bloquer sur macro imminente
        for ev in fetch_macro_calendar():
            try:
                ev_time = datetime.fromtimestamp(ev.get('time', 0))
                delta   = (ev_time - datetime.now()).total_seconds() / 60
                if 0 <= delta <= 30:
                    self.block(ev.get('event', 'MACRO'))
            except Exception:
                pass

        if self.is_blocked():
            log.info(f"Bloque jusqu'a {self.blocked_until.strftime('%H:%M')}")
            return

        # Collecter news
        news_items = fetch_ib_news(self.ib)
        if len(news_items) < 3:
            news_items += fetch_finnhub_news(
                ['SPY', 'GLD', 'USO', 'XBI', 'TLT', 'QQQ']
            )

        # Analyser avec Claude
        for item in news_items:
            item_id = item.get('id', '')
            if item_id in self.processed:
                continue
            self.processed.add(item_id)

            headline = item.get('headline', '').strip()
            if len(headline) < 15:
                continue

            # Analyse Claude LLM
            result = analyze_with_claude(headline, item.get('body', ''))
            if result:
                self.execute_signal(result, headline)

        log.info(
            f"Cycle OK | {len(self.positions)} positions | "
            f"NAV ${get_nav(self.ib):,.0f} | "
            f"Levier {get_leverage(self.ib):.2f}x"
        )

#        Main                                                                                                                                                                                                                   

def main():
    log.info("=" * 60)
    log.info("NEWS TRADING BOT v2 -- LLM (Claude API)")
    log.info("=" * 60)

    ib  = connect()
    bot = NewsTradingBot(ib)
    ib.sleep(2)

    log.info(f"NAV : ${get_nav(ib):,.0f}")
    log.info("Strategies : FDA | Macro | Geo | M&A | Earnings")
    log.info("NLP : Claude claude-sonnet-4-20250514 (contextuel)")

    try:
        while True:
            now = datetime.now()
            weekday = now.weekday()
            # Heure locale machine (pas de conversion UTC hardcodée)
            hour_local = now.hour + now.minute / 60

            market_open = (weekday < 5 and 9.5 <= hour_local <= 16.0)

            if market_open:
                bot.run_cycle()
            else:
                log.info("Marche ferme")

            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        log.info("Arret")
    except Exception as e:
        alert_manager.notify_crash('news_trading', str(e))
        log.error(f"Erreur: {e}")
    finally:
        ib.disconnect()

if __name__ == '__main__':
    main()
