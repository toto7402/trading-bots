from datetime import time
from dataclasses import dataclass, field
from typing import Optional
import logging

log = logging.getLogger(__name__)

FUTURES_YFINANCE_MAP = {
    'MCL': 'CL=F', 'MGC': 'GC=F', 'NG': 'NG=F', 'CL': 'CL=F', 'GC': 'GC=F',
    'ZC': 'ZC=F', 'ZW': 'ZW=F', 'ZS': 'ZS=F', 'SI': 'SI=F', 'HG': 'HG=F',
    'KC': 'KC=F', 'CC': 'CC=F', 'CT': 'CT=F', 'SB': 'SB=F', 'RB': 'RB=F',
    'HO': 'HO=F', 'PL': 'PL=F', 'MES': 'ES=F', 'MNQ': 'NQ=F', 'MYM': 'YM=F',
    'M2K': 'RTY=F', 'RTY': 'RTY=F', 'ES': 'ES=F', 'NQ': 'NQ=F',
}


@dataclass
class AssetClass:
    name: str
    broker: str                          # 'IB', 'ALPACA', 'CRYPTO'
    sessions: list[tuple[time, time]]    # list of (open_utc, close_utc) tuples
    tickers: list[str] = field(default_factory=list)

    def is_open_at(self, t: time) -> bool:
        """Return True if t falls within ANY session window."""
        for open_t, close_t in self.sessions:
            if open_t <= close_t:
                if open_t <= t <= close_t:
                    return True
            else:  # overnight session (e.g. sydney)
                if t >= open_t or t <= close_t:
                    return True
        return False


ASSET_CLASSES: dict[str, AssetClass] = {
    'asian_stocks': AssetClass(
        name='asian_stocks', broker='IB',
        sessions=[(time(0, 0), time(2, 30)), (time(3, 30), time(6, 30))],
        tickers=['7203.T', '6758.T', '9984.T', '005930.KS', '000660.KS', 'BABA', 'JD', 'PDD', 'BIDU', 'NIO'],
    ),
    'hong_kong': AssetClass(
        name='hong_kong', broker='IB',
        sessions=[(time(1, 30), time(4, 0)), (time(5, 0), time(8, 0))],
        tickers=['0700.HK', '0941.HK', '1299.HK', '2318.HK', '3690.HK', '9988.HK', '1810.HK', '0388.HK'],
    ),
    'china_stocks': AssetClass(
        name='china_stocks', broker='IB',
        sessions=[(time(1, 30), time(3, 30)), (time(5, 0), time(7, 0))],
        tickers=['600519.SS', '601318.SS', '000858.SZ', '000333.SZ', '002594.SZ'],
    ),
    'sydney': AssetClass(
        name='sydney', broker='IB',
        sessions=[(time(22, 0), time(5, 30))],   # overnight — crosses midnight
        tickers=['BHP.AX', 'CBA.AX', 'CSL.AX', 'NAB.AX', 'WBC.AX', 'ANZ.AX', 'RIO.AX'],
    ),
    'eu_stocks': AssetClass(
        name='eu_stocks', broker='IB',
        sessions=[(time(8, 0), time(16, 30))],
        tickers=['SAP.DE', 'ASML.AS', 'LVMH.PA', 'TTE.PA', 'SIE.DE', 'ALV.DE', 'MC.PA', 'OR.PA', 'BNP.PA', 'DTE.DE'],
    ),
    'us_stocks': AssetClass(
        name='us_stocks', broker='ALPACA',
        sessions=[(time(13, 0), time(21, 0))],
        tickers=['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B', 'JPM', 'V', 'MA', 'UNH', 'XOM', 'LLY', 'JNJ'],
    ),
    'futures': AssetClass(
        name='futures', broker='IB',
        sessions=[(time(13, 0), time(21, 30))],
        tickers=['MCL', 'MGC', 'NG', 'CL', 'GC', 'ZC', 'ZW', 'ZS', 'SI', 'HG', 'KC', 'CC', 'CT', 'SB', 'RB', 'HO', 'PL', 'MES', 'MNQ', 'MYM', 'M2K', 'RTY', 'ES', 'NQ'],
    ),
    'crypto': AssetClass(
        name='crypto', broker='CRYPTO',
        sessions=[(time(0, 0), time(23, 59))],
        tickers=['BTC-USD', 'ETH-USD', 'BNB-USD', 'SOL-USD', 'XRP-USD', 'ADA-USD', 'AVAX-USD', 'DOT-USD'],
    ),
}

YFINANCE_SOURCE = {'us_stocks', 'eu_stocks', 'asian_stocks', 'hong_kong', 'china_stocks', 'sydney', 'futures', 'crypto'}


def get_asset_class(ticker: str) -> Optional[str]:
    """Return asset class name for a given ticker, or None."""
    for name, ac in ASSET_CLASSES.items():
        if ticker in ac.tickers:
            return name
    return None
