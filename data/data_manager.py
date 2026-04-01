import logging
from typing import Optional

import pandas as pd
import yfinance as yf

try:
    import fredapi
    _FREDAPI_AVAILABLE = True
except ImportError:
    _FREDAPI_AVAILABLE = False

from config.settings import settings

log = logging.getLogger(__name__)

FUTURES_YFINANCE_MAP = {
    'MCL': 'CL=F', 'MGC': 'GC=F', 'NG': 'NG=F', 'CL': 'CL=F', 'GC': 'GC=F',
    'ZC': 'ZC=F', 'ZW': 'ZW=F', 'ZS': 'ZS=F', 'SI': 'SI=F', 'HG': 'HG=F',
    'KC': 'KC=F', 'CC': 'CC=F', 'CT': 'CT=F', 'SB': 'SB=F', 'RB': 'RB=F',
    'HO': 'HO=F', 'PL': 'PL=F', 'MES': 'ES=F', 'MNQ': 'NQ=F', 'MYM': 'YM=F',
    'M2K': 'RTY=F', 'RTY': 'RTY=F', 'ES': 'ES=F', 'NQ': 'NQ=F',
}


class DataManager:

    def get_ohlcv(
        self,
        ticker: str,
        period: str = '1mo',
        interval: str = '1d',
        asset_class: str = 'stocks',
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data for a ticker via yfinance.

        If asset_class is 'futures' or the ticker is in FUTURES_YFINANCE_MAP,
        the ticker is remapped to its yfinance futures symbol before downloading.
        Returns None on empty result or exception.
        """
        try:
            yf_ticker = ticker
            if asset_class == 'futures' or ticker in FUTURES_YFINANCE_MAP:
                yf_ticker = FUTURES_YFINANCE_MAP.get(ticker, ticker)

            df = yf.download(yf_ticker, period=period, interval=interval, progress=False)

            if df is None or df.empty:
                log.warning('get_ohlcv: empty result for ticker=%s (yf_ticker=%s)', ticker, yf_ticker)
                return None

            return df

        except Exception as exc:
            log.warning('get_ohlcv: exception fetching ticker=%s: %s', ticker, exc)
            return None

    def get_fundamental(self, ticker: str) -> dict:
        """Fetch fundamental info for a ticker via yfinance. Returns {} on error."""
        try:
            info = yf.Ticker(ticker).info
            return info if isinstance(info, dict) else {}
        except Exception as exc:
            log.warning('get_fundamental: exception fetching ticker=%s: %s', ticker, exc)
            return {}

    def get_fred_series(self, series_id: str) -> Optional[pd.Series]:
        """Fetch a FRED data series. Requires fredapi and a configured fred_api_key.

        Returns None (with a warning) if fredapi is unavailable or the API key
        is not configured.
        """
        if not _FREDAPI_AVAILABLE:
            log.warning('get_fred_series: fredapi is not installed; cannot fetch series=%s', series_id)
            return None

        fred_api_key = getattr(settings, 'fred_api_key', '')
        if not fred_api_key:
            log.warning('get_fred_series: fred_api_key is not configured; cannot fetch series=%s', series_id)
            return None

        try:
            fred = fredapi.Fred(api_key=fred_api_key)
            series = fred.get_series(series_id)
            return series
        except Exception as exc:
            log.warning('get_fred_series: exception fetching series=%s: %s', series_id, exc)
            return None


data_manager = DataManager()
