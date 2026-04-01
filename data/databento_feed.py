#!/usr/bin/env python3
"""
databento_feed.py -- Databento live market data feed with Redis tick publishing
"""
import logging
import asyncio
import json
import math
from typing import Optional

try:
    import databento as db
    DATABENTO_AVAILABLE = True
except ImportError:
    DATABENTO_AVAILABLE = False

try:
    import redis.asyncio as aioredis
except ImportError:
    import aioredis

from config.settings import settings

log = logging.getLogger(__name__)


class DatabentoFeed:
    """Simple historical Databento feed."""

    def connect(self):
        log.info("Databento historical feed ready")

    def get_bars(self, symbol, start, end):
        """Placeholder — returns empty list."""
        return []


class DatabentoLiveFeed:
    """Live Databento feed that publishes ticks to Redis."""

    def __init__(self, redis_url: str = None):
        self._redis_url = redis_url or settings.redis_url
        self._redis = None
        self._client = None
        self._running = False
        self._degraded = False  # True if no key or unsupported plan

    async def start(self):
        """Connect to Redis then start the live feed (degrades gracefully)."""
        self._redis = await aioredis.from_url(self._redis_url, decode_responses=True)

        if not DATABENTO_AVAILABLE:
            log.warning("databento package not installed — degraded mode")
            self._degraded = True
            return

        if not settings.databento.api_key:
            log.warning("DATABENTO_API_KEY not set — degraded mode")
            self._degraded = True
            return

        self._running = True
        await self._connect_with_retry()

    async def _connect_with_retry(self):
        """Attempt to connect to the Databento live feed with exponential backoff."""
        MAX_RETRIES = 10
        backoff = 2
        attempt = 0

        while attempt < MAX_RETRIES:
            try:
                client = db.Live(
                    key=settings.databento.api_key,
                    dataset='XNAS.ITCH',
                )
                client.subscribe(
                    dataset='XNAS.ITCH',
                    schema='trades',
                    symbols=[
                        'AAPL', 'MSFT', 'SPY', 'QQQ',
                        'NVDA', 'TSLA', 'AMZN', 'META',
                        'AMD', 'NVDA',
                    ],
                )
                self._client = client
                async for record in client:
                    await self._publish_tick(record)
            except Exception as e:
                err_str = str(e).lower()
                if any(kw in err_str for kw in ('permission', 'not supported', '403')):
                    self._degraded = True
                    msg = f"Databento live feed degraded (plan/permission issue): {e}"
                    log.warning(msg)
                    await self._send_telegram(msg)
                    return

                attempt += 1
                capped = min(backoff, 60)
                log.warning(
                    "Databento connection attempt %d/%d failed: %s — retrying in %ds",
                    attempt, MAX_RETRIES, e, capped,
                )
                await asyncio.sleep(capped)
                backoff = min(backoff * 2, 60)

        log.warning("Databento live feed: max retries reached, entering degraded mode")
        self._degraded = True

    async def _publish_tick(self, record):
        """Publish a single trade record to Redis."""
        try:
            symbol = record.symbol if hasattr(record, 'symbol') else str(record.hd.instrument_id)
            price = float(record.price) / 1e9
            size = int(record.size) if hasattr(record, 'size') else 0
            ts = str(record.ts_event)
            await self._redis.setex(
                f'tick:{symbol}',
                60,
                json.dumps({'price': price, 'size': size, 'ts': ts}),
            )
        except Exception as e:
            log.warning("Failed to publish tick: %s", e)

    async def get_latest_tick(self, symbol: str) -> Optional[dict]:
        """Return the most recent tick for a symbol from Redis, or None."""
        if self._redis is None:
            return None
        raw = await self._redis.get(f'tick:{symbol}')
        if raw:
            return json.loads(raw)
        return None

    async def _send_telegram(self, msg: str):
        """Send a plain-text Telegram alert (fire-and-forget, sync requests)."""
        try:
            import requests
            if not settings.telegram_token:
                return
            requests.post(
                f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage",
                json={'chat_id': settings.telegram_chat_id, 'text': msg},
                timeout=5,
            )
        except Exception as e:
            log.warning("Telegram alert failed: %s", e)

    def is_live(self) -> bool:
        """Return True only when the feed is running and not in degraded mode."""
        return self._running and not self._degraded


# Module-level singletons
databento_feed = DatabentoFeed()
databento_live = DatabentoLiveFeed()
