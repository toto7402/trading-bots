#!/usr/bin/env python3
"""
main.py -- Trading AI System entry point
"""
import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/root/trading-bots/trading_main.log'),
    ]
)
log = logging.getLogger(__name__)

from config.settings import settings
from data.databento_feed import databento_feed, databento_live
from core.ai_supervisor import supervisor
from agents.autogen_orchestrator import orchestrator
from core.market_scheduler import scheduler


async def main():
    log.info("=== Trading AI System starting ===")

    # Start historical feed
    databento_feed.connect()

    # Start live feed as background task (degrades gracefully if unavailable)
    asyncio.create_task(databento_live.start())

    # Log active session info
    log.info("Redis URL: %s", settings.redis_url)
    log.info(
        "Alpaca: %s (paper=%s)",
        "configured" if settings.alpaca_api_key else "not configured",
        settings.alpaca_paper,
    )
    log.info(
        "IB Gateway: %s:%s (client_id=%s)",
        settings.ib_host, settings.ib_port, settings.ib_client_id,
    )

    # Log AI component status
    gemini_active = bool(settings.google_api_key)
    log.info("Gemini active: %s", gemini_active)
    log.info(
        "AutoGen orchestrator enabled: %s",
        getattr(orchestrator, 'enabled', True),
    )

    # Run indefinitely
    while True:
        await asyncio.sleep(3600)


if __name__ == '__main__':
    asyncio.run(main())
