"""Entry point for the cookie refresh service."""
from __future__ import annotations

import asyncio
import logging
import os

import structlog

from refresh.config import load_config
from refresh.scheduler import run_scheduled_refresh

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, os.getenv("LOG_LEVEL", "INFO"))
    )
)

logger = structlog.get_logger(__name__)

MAX_CONCURRENT_BROWSERS = 3


async def main() -> None:
    """Load config and start one refresh scheduler per site."""
    config = load_config()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BROWSERS)
    logger.info(
        "refresh_service_starting",
        sites=[s.domain for s in config.sites],
        max_concurrent=MAX_CONCURRENT_BROWSERS,
    )

    tasks = [
        asyncio.create_task(
            run_scheduled_refresh(site, semaphore, config),
            name=f"refresh-{site.domain}",
        )
        for site in config.sites
    ]

    try:
        await asyncio.gather(*tasks)
    except Exception:
        logger.exception("fatal_error")
        raise


if __name__ == "__main__":
    asyncio.run(main())
