#!/usr/bin/env python3
"""
alpaca_bot.py -- Alpaca paper trading bot with live WebSocket streams
"""
import asyncio
import json
import logging
import os
from typing import Optional
import aioredis  # or redis.asyncio

log = logging.getLogger(__name__)

# Scalping universe
SCALP_UNIVERSE = ['AAPL','MSFT','NVDA','TSLA','AMD','META','GOOGL','AMZN','SPY','QQQ',
                   'COIN','MSTR','PLTR','RIVN','LCID','SOFI','HOOD','RBLX','SNAP','UBER']


class AlpacaBot:
    def __init__(self, api_key: str, secret_key: str, redis_url: str = 'redis://localhost:6379'):
        self._api_key = api_key
        self._secret_key = secret_key
        self._redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None
        self._running = False

    async def start(self):
        self._redis = await aioredis.from_url(self._redis_url)
        self._running = True

        data_task = asyncio.create_task(self._start_data_stream())
        trading_task = asyncio.create_task(self._start_trading_stream())

        await asyncio.gather(data_task, trading_task)

    async def _start_data_stream(self):
        while self._running:
            try:
                from alpaca.data.live import StockDataStream
                stream = StockDataStream(self._api_key, self._secret_key, feed='iex')
                stream.subscribe_trades(self._on_live_trade, *SCALP_UNIVERSE)
                await stream._run_forever()  # or stream.run()
            except Exception as exc:
                log.error("Data stream error, reconnecting in 5s: %s", exc)
                await asyncio.sleep(5)

    async def _on_live_trade(self, trade):
        await self._redis.setex(
            f'tick:{trade.symbol}',
            30,
            json.dumps({
                'price': float(trade.price),
                'size': int(trade.size),
                'ts': str(trade.timestamp),
            })
        )
        log.debug("Tick received: %s @ %s", trade.symbol, trade.price)

    async def _start_trading_stream(self):
        while self._running:
            try:
                from alpaca.trading.stream import TradingStream
                stream = TradingStream(self._api_key, self._secret_key, paper=True)
                stream.subscribe_trade_updates(self._on_trade_update)
                await stream._run_forever()
            except Exception as exc:
                log.error("Trading stream error, reconnecting in 5s: %s", exc)
                await asyncio.sleep(5)

    async def _on_trade_update(self, update):
        if update.event == 'fill':
            await self._redis.setex(
                f'order:{update.order.id}',
                300,
                json.dumps({
                    'status': 'FILLED',
                    'symbol': update.order.symbol,
                    'qty': float(update.order.filled_qty),
                    'price': float(update.order.filled_avg_price or 0),
                    'event': update.event,
                })
            )
            log.info(
                "Order filled: %s %s qty=%s price=%s",
                update.order.id,
                update.order.symbol,
                update.order.filled_qty,
                update.order.filled_avg_price,
            )

    async def stop(self):
        self._running = False
