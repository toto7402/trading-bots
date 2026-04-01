#!/usr/bin/env python3
"""
main.py -- Trading AI System entry point
"""
import asyncio
import logging
import os
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/root/bots/trading_main.log"),
    ],
)
log = logging.getLogger(__name__)

from config.settings import settings
from data.databento_feed import databento_feed, databento_live
from core.ai_supervisor import supervisor
from agents.autogen_orchestrator import orchestrator
from core.market_scheduler import scheduler

# Global shutdown event — set by SIGTERM/SIGINT handler
_shutdown = asyncio.Event() if False else None  # created inside main()


def _handle_signal(loop: asyncio.AbstractEventLoop, shutdown_event: asyncio.Event) -> None:
    """Handle SIGTERM/SIGINT gracefully so systemd does not SIGKILL us."""
    log.info("Shutdown signal received — stopping Trading AI System cleanly")
    loop.call_soon_threadsafe(shutdown_event.set)


async def main() -> None:
    global _shutdown
    _shutdown = asyncio.Event()

    # Register signal handlers for clean systemd stop (prevents SIGKILL timeout)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, loop, _shutdown)

    log.info("=== Trading AI System starting ===")

    # Start historical feed
    try:
        databento_feed.connect()
    except Exception as exc:
        log.warning("Databento historical feed init failed (non-fatal): %s", exc)

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
        settings.ib_host,
        settings.ib_port,
        settings.ib_client_id,
    )

    # Log AI component status
    gemini_active = bool(settings.google_api_key)
    log.info("Gemini active: %s", gemini_active)
    log.info(
        "AutoGen orchestrator enabled: %s",
        getattr(orchestrator, "enabled", True),
    )

    # Wait until a shutdown signal arrives (SIGTERM from systemd)
    log.info("Trading AI System running — waiting for shutdown signal")
    while not _shutdown.is_set():
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=3600)
        except asyncio.TimeoutError:
            # Periodic heartbeat log every hour
            log.info("Trading AI System heartbeat — still running")

    log.info("=== Trading AI System stopped cleanly ===")


if __name__ == "__main__":
    asyncio.run(main())
