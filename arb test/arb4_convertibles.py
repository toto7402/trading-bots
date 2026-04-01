"""
Arbitrage Obligations Convertibles Small Cap
=============================================
Principe : une obligation convertible vaut au minimum
  max(valeur obligataire, valeur de conversion)
  + prime d'option (Black-Scholes implicite)

Quand le prix de marché < valeur théorique => signal long convertible
On achète la convertible sous-évaluée et on hedge le delta equity en shortant l'action.
Sur small cap, ce mispricing persiste car personne ne couvre ces titres.

Sources données :
- FINRA TRACE (gratuit) : prix OTC des obligations
- Finnhub : données equity pour le hedge
- IB : exécution (convertibles via Fixed Income desk)

Note : IB paper trading supporte les obligations via SMART/BOND
"""

import numpy as np
import pandas as pd
from ib_insync import IB, Stock, Bond, LimitOrder
import requests
import logging
import os
from datetime import datetime, timedelta
import schedule
import math

import sys
_file_handler   = logging.FileHandler('convertibles.log', encoding='utf-8')
_stream_handler = logging.StreamHandler(sys.stdout)
_fmt = logging.Formatter('%(asctime)s  %(levelname)s  %(message)s')
_file_handler.setFormatter(_fmt)
_stream_handler.setFormatter(_fmt)
_stream_handler.stream.reconfigure(encoding='utf-8', errors='replace')
logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _stream_handler])
log = logging.getLogger(__name__)

HOST      = '127.0.0.1'
PORT      = 7497
CLIENT_ID = 5
CAPITAL   = 50_000
POS_SIZE  = 0.06        # 6% par position (plus concentré, moins de liquidité)
MAX_POSITIONS = 5
MIN_MISPRICING = 0.03   # 3% de décote minimum pour entrer

FINNHUB_KEY = "d6q4h6hr01qhcrmirjn0d6q4h6hr01qhcrmirjng"
RISK_FREE   = 0.05      # taux sans risque actuel (~5%)


# ─── Black-Scholes ────────────────────────────────────────────────────────────

def bs_call(S, K, T, r, sigma):
    """Prix d'un call européen."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    from scipy.stats import norm
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def bs_delta(S, K, T, r, sigma):
    """Delta du call (pour hedging)."""
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    from scipy.stats import norm
    return norm.cdf(d1)


def implied_vol_equity(ticker, finnhub_key, lookback=252):
    """
    Calcule la vol historique sur lookback jours comme proxy de vol implicite.
    En l'absence d'options cotées sur small caps, c'est le meilleur proxy.
    """
    url = "https://finnhub.io/api/v1/stock/candle"
    to_   = int(datetime.now().timestamp())
    from_ = int((datetime.now() - timedelta(days=lookback + 30)).timestamp())
    try:
        r = requests.get(url, params={
            'symbol':     ticker,
            'resolution': 'D',
            'from':       from_,
            'to':         to_,
            'token':      finnhub_key,
        }, timeout=10)
        data = r.json()
        if data.get('s') != 'ok' or not data.get('c'):
            return 0.35  # vol par défaut
        closes = np.array(data['c'])
        returns = np.diff(np.log(closes))
        vol = float(np.std(returns) * np.sqrt(252))
        return max(vol, 0.15)
    except Exception:
        return 0.35


# ─── Valorisation convertible ─────────────────────────────────────────────────

def theoretical_value(face, coupon, ytm, years_to_maturity,
                      conversion_ratio, stock_price, sigma, r=RISK_FREE):
    """
    Valorisation d'une obligation convertible :
      = valeur obligataire (PV des flux) + valeur option de conversion (B-S)

    face             : valeur nominale (typiquement 1000)
    coupon           : coupon annuel en %
    ytm              : yield to maturity du marché (proxy : spread + rf)
    years_to_maturity: maturité résiduelle en années
    conversion_ratio : nombre d'actions par obligation (face / prix de conversion)
    stock_price      : prix actuel de l'action
    sigma            : vol implicite de l'action
    """
    # 1. Valeur obligataire (PV des coupons + remboursement)
    bond_value = 0
    c = face * coupon / 100
    for t in range(1, int(years_to_maturity * 2) + 1):
        bond_value += (c / 2) / (1 + ytm / 2) ** t
    bond_value += face / (1 + ytm / 2) ** (int(years_to_maturity * 2))

    # 2. Valeur de conversion = nb actions × prix action
    conversion_value = conversion_ratio * stock_price

    # 3. Valeur option (B-S) sur la conversion
    K     = face / conversion_ratio  # prix de conversion = strike implicite
    T     = max(years_to_maturity, 0.01)
    call  = bs_call(stock_price, K, T, r, sigma)
    option_value = conversion_ratio * call

    # 4. Valeur théorique = max(bond floor, conversion value) + prime option
    bond_floor    = max(bond_value, conversion_value)
    theo          = bond_floor + option_value * 0.5  # 0.5 = haircut liquidité small cap

    return {
        'bond_value':       round(bond_value, 2),
        'conversion_value': round(conversion_value, 2),
        'option_value':     round(option_value, 2),
        'theoretical':      round(theo, 2),
        'delta':            bs_delta(stock_price, K, T, r, sigma),
    }


# ─── Univers convertibles small cap ───────────────────────────────────────────

CONVERTIBLE_UNIVERSE = [
    # (ticker_equity, cusip_bond, face, coupon%, maturity_date, conversion_ratio)
    # Liste manuelle — les ETF ICVT / CWB contiennent les grands émetteurs
    # Pour les small caps on surveille ces émissions connues
    ('MSTR',  'MSTR4.25',  1000, 0.00,  '2027-02-15', 6.9),
    ('RIOT',  'RIOT3.75',  1000, 0.00,  '2026-12-15', 28.5),
    ('NOVA',  'NOVA1.00',  1000, 1.00,  '2028-06-01', 20.0),
    ('BLNK',  'BLNK3.25',  1000, 3.25,  '2028-05-01', 35.0),
    ('CLOV',  'CLOV3.50',  1000, 3.50,  '2027-09-01', 55.0),
    ('SFIX',  'SFIX8.625', 1000, 8.625, '2029-01-15', 12.0),
]


def fetch_bond_price_finra(ticker):
    """
    Récupère le dernier prix OTC d'une obligation via FINRA TRACE
    (API publique, pas d'authentification requise).
    """
    url = f"https://services-dynarep.ddwa.finra.org/pub/FINRA/Data/fixed-income/otc/trade/search"
    try:
        r = requests.get(url, params={
            'issuerName': ticker,
            'limit':      5,
        }, timeout=10, headers={'Accept': 'application/json'})
        data = r.json()
        if data and isinstance(data, list):
            price = float(data[0].get('lastSalePrice', 0))
            return price if price > 0 else None
    except Exception:
        pass
    return None


# ─── Connexion & utilitaires IB ───────────────────────────────────────────────


def days_held(df, col='entry_date'):
    now = datetime.now()
    return df[col].apply(lambda x: (now - pd.to_datetime(x, errors="coerce")).days if pd.notna(x) else 0)

def connect():
    ib = IB()
    retry_delay = 5
    max_delay = 300
    while True:
        try:
            ib.connect(HOST, PORT, clientId=CLIENT_ID)
            log.info(f"Connecte — {ib.wrapper.accounts}")
            retry_delay = 5  # reset on success
            return ib
        except Exception as e:
            log.error(f"API connection failed: {e}. Retrying in {retry_delay}s")
            import time as _time
            _time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)


def get_nav(ib):
    for v in ib.accountValues():
        if v.tag == 'NetLiquidation' and v.currency == 'USD':
            return float(v.value)
    return CAPITAL


def qualify_stock(ib, ticker):
    c = Stock(ticker, 'SMART', 'USD')
    try:
        q = ib.qualifyContracts(c)
        return q[0] if q else None
    except Exception:
        return None


def get_stock_price(ib, ticker):
    c = qualify_stock(ib, ticker)
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


def place_stock_order(ib, ticker, action, shares):
    if shares <= 0:
        return False
    c = qualify_stock(ib, ticker)
    if not c:
        return False
    price = get_stock_price(ib, ticker)
    if not price:
        return False
    lmt   = round(price * (1.005 if action == 'BUY' else 0.995), 2)
    order = LimitOrder(action, shares, lmt, tif='DAY')
    trade = ib.placeOrder(c, order)
    timeout = 60
    while not trade.isDone() and timeout > 0:
        ib.sleep(1)
        timeout -= 1
    if trade.orderStatus.status == 'Filled':
        log.info(f"Rempli {action} {shares} {ticker} equity @ ${trade.orderStatus.avgFillPrice:.2f}")
        return True
    ib.cancelOrder(trade.order)
    return False


# ─── Positions ────────────────────────────────────────────────────────────────

def load_positions():
    if os.path.exists('convertibles_positions.csv'):
        return pd.read_csv('convertibles_positions.csv', parse_dates=['entry_date'])
    return pd.DataFrame(columns=[
        'ticker','bond_cusip','bond_price','theo_value','mispricing',
        'delta','hedge_shares','entry_date','pos_usd'
    ])


def save_positions(df):
    df.to_csv('convertibles_positions.csv', index=False)


# ─── Logique principale ───────────────────────────────────────────────────────

def run(ib):
    log.info("=== CONVERTIBLES SCAN ===")
    nav      = get_nav(ib)
    pos_cap  = nav * POS_SIZE
    existing = load_positions()
    held     = set(existing['ticker'].tolist())
    new_entries = []

    today = datetime.now()

    for (ticker, cusip, face, coupon, maturity_str, conv_ratio) in CONVERTIBLE_UNIVERSE:
        if ticker in held:
            continue
        if len(existing) + len(new_entries) >= MAX_POSITIONS:
            break

        maturity = datetime.strptime(maturity_str, '%Y-%m-%d')
        years_to_maturity = max((maturity - today).days / 365, 0.1)

        # Prix action
        stock_price = get_stock_price(ib, ticker)
        if not stock_price or stock_price < 1:
            log.warning(f"{ticker} : prix action introuvable")
            continue

        # Vol historique comme proxy de vol implicite
        sigma = implied_vol_equity(ticker, FINNHUB_KEY)

        # Prix marché de l'obligation (FINRA TRACE)
        bond_mkt_price = fetch_bond_price_finra(ticker)
        if not bond_mkt_price:
            log.warning(f"{ticker} : prix obligation introuvable via FINRA")
            # Fallback : estimer à partir du prix de conversion
            conversion_value = conv_ratio * stock_price
            bond_mkt_price = conversion_value * 0.95  # hypothèse 5% de décote
            log.info(f"{ticker} : prix obligation estimé à {bond_mkt_price:.2f}")

        # YTM proxy (high yield small cap ~8-12%)
        ytm = 0.10

        # Valeur théorique
        val = theoretical_value(
            face=face, coupon=coupon, ytm=ytm,
            years_to_maturity=years_to_maturity,
            conversion_ratio=conv_ratio,
            stock_price=stock_price,
            sigma=sigma,
        )

        # Mispricing = (théorique - marché) / théorique
        mispricing = (val['theoretical'] - bond_mkt_price) / val['theoretical']

        log.info(
            f"{ticker} | Stock=${stock_price:.2f} | σ={sigma:.0%} | "
            f"Bond mkt={bond_mkt_price:.2f} | Theo={val['theoretical']:.2f} | "
            f"Mispricing={mispricing:.1%} | Delta={val['delta']:.2f}"
        )

        if mispricing < MIN_MISPRICING:
            log.info(f"{ticker} : décote insuffisante ({mispricing:.1%} < {MIN_MISPRICING:.0%})")
            continue

        # Filtre distressed : stock trop loin du strike => artefact modèle, pas vrai arb
        implied_conversion_price = face / conv_ratio if conv_ratio > 0 else float('inf')
        if stock_price < 0.20 * implied_conversion_price:
            log.info(f"{ticker} : stock trop loin du strike ({stock_price:.2f} < 20% de {implied_conversion_price:.2f}) -- skip")
            continue

        # Nombre d'obligations à acheter
        n_bonds  = max(1, int(pos_cap / bond_mkt_price))
        pos_size = n_bonds * bond_mkt_price

        # Hedge delta : shorter l'action sous-jacente
        delta        = val['delta']
        hedge_shares = int(n_bonds * conv_ratio * delta)

        log.info(
            f"{ticker} : ENTREE — {n_bonds} obligations @ {bond_mkt_price:.2f} "
            f"+ short {hedge_shares} actions (delta hedge)"
        )

        # Note : les obligations IB nécessitent un compte avec accès Fixed Income
        # En paper trading on simule via le suivi CSV uniquement
        # Le hedge equity est exécuté normalement
        hedge_ok = True
        if hedge_shares > 0:
            hedge_ok = place_stock_order(ib, ticker, 'SELL', hedge_shares)

        if hedge_ok or hedge_shares == 0:
            new_entries.append({
                'ticker':      ticker,
                'bond_cusip':  cusip,
                'bond_price':  bond_mkt_price,
                'theo_value':  val['theoretical'],
                'mispricing':  round(mispricing, 4),
                'delta':       round(delta, 3),
                'hedge_shares': hedge_shares,
                'entry_date':  datetime.now(),
                'pos_usd':     pos_size,
            })

    if new_entries:
        existing = pd.concat([existing, pd.DataFrame(new_entries)], ignore_index=True)

    save_positions(existing)
    log.info(f"Positions actives : {len(existing)}")

    # Monitoring des positions existantes — rehedge si delta dérive > 10%
    for i, row in existing.iterrows():
        ticker      = row['ticker']
        stock_price = get_stock_price(ib, ticker)
        if not stock_price:
            continue

        match = [(t, f, c, cr) for t, _, f, c, _, cr in
                 [(t, cu, f, co, m, cr) for t, cu, f, co, m, cr in CONVERTIBLE_UNIVERSE]
                 if t == ticker]
        if not match:
            continue

        _, face, coupon, conv_ratio = match[0]
        maturity   = [datetime.strptime(m, '%Y-%m-%d') for t, _, f, c, m, cr
                      in CONVERTIBLE_UNIVERSE if t == ticker][0]
        ytm_res    = max((maturity - today).days / 365, 0.1)
        sigma      = implied_vol_equity(ticker, FINNHUB_KEY)
        val_new    = theoretical_value(face, coupon, 0.10, ytm_res,
                                       conv_ratio, stock_price, sigma)
        delta_new  = val_new['delta']
        delta_old  = row['delta']

        if abs(delta_new - delta_old) > 0.10:
            delta_diff   = delta_new - delta_old
            n_bonds      = int(row['pos_usd'] / row['bond_price'])
            hedge_change = int(abs(delta_diff) * n_bonds * conv_ratio)
            action       = 'SELL' if delta_diff > 0 else 'BUY'
            log.info(f"Rehedge {ticker} : delta {delta_old:.2f} → {delta_new:.2f}, "
                     f"{action} {hedge_change} actions")
            place_stock_order(ib, ticker, action, hedge_change)
            existing.at[i, 'delta'] = delta_new

    save_positions(existing)


def main():
    try:
        from scipy.stats import norm
    except ImportError:
        print("pip install scipy --break-system-packages")
        return

    if FINNHUB_KEY == "VOTRE_CLE_FINNHUB":
        print("Obtenir une cle gratuite sur finnhub.io et remplacer FINNHUB_KEY")
        return

    retry_delay = 5
    max_delay = 300
    while True:
        try:
            ib = connect()
            run(ib)
            schedule.every().day.at("10:30").do(lambda: run(ib))
            schedule.every().day.at("14:00").do(lambda: run(ib))
            log.info("Convertibles actif — scan a 10h30 et 14h00")
            retry_delay = 5  # reset on successful connect
            while True:
                schedule.run_pending()
                if not ib.isConnected():
                    log.warning("IB connection lost — reconnecting")
                    break
                ib.sleep(60)
        except KeyboardInterrupt:
            log.info("Arret")
            break
        except Exception as e:
            log.error(f"Connection failed: {e}. Retrying in {retry_delay}s")
            import time as _time
            _time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)
    try:
        ib.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    main()
