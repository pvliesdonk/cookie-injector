"""Adaptive scheduled refresh (ADR-0003)."""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog

from refresh.alerting import ping_healthcheck, send_ntfy_alert
from refresh.config import Config, SiteConfig
from refresh.cookie_store import load_cookies
from refresh.refresh import perform_refresh

logger = structlog.get_logger(__name__)

MIN_INTERVAL = 6 * 3600    # 6 hours
MAX_INTERVAL = 24 * 3600   # 24 hours
STARTUP_SKIP_THRESHOLD = 6 * 3600


def calculate_next_refresh(domain: str, cookie_dir: Path) -> float:
    """Calculate seconds until next refresh (ADR-0003).

    Returns 0 if cookies missing or all expired.
    Otherwise clamp(lifetime * 0.75, 6h, 24h).

    Args:
        domain: Canonical domain.
        cookie_dir: Directory with {domain}.json files.

    Returns:
        Seconds to sleep before next refresh.
    """
    cookie_file = cookie_dir / f"{domain}.json"

    if not cookie_file.exists():
        logger.info("no_cookie_file_refresh_immediately", domain=domain)
        return 0.0

    try:
        cookies, _ = load_cookies(cookie_file)
    except Exception as exc:
        logger.warning("cannot_load_cookies", domain=domain, error=str(exc))
        return 0.0

    now = time.time()
    valid = [c for c in cookies if c.get("expires", -1) > now]

    if not valid:
        logger.info("all_expired_refresh_immediately", domain=domain)
        return 0.0

    min_expiry = min(c["expires"] for c in valid)
    cookie_lifetime = min_expiry - now
    raw_interval = cookie_lifetime * 0.75
    interval = max(MIN_INTERVAL, min(MAX_INTERVAL, raw_interval))

    logger.info(
        "next_refresh_calculated",
        domain=domain,
        lifetime_hours=round(cookie_lifetime / 3600, 2),
        interval_hours=round(interval / 3600, 2),
    )
    return interval


async def run_scheduled_refresh(
    site: SiteConfig,
    semaphore: asyncio.Semaphore,
    config: Config,
) -> None:
    """Adaptive refresh loop for a single site. Runs forever.

    Args:
        site: Site configuration.
        semaphore: Shared concurrency limiter.
        config: Top-level config for cookie_dir and alerting URLs.
    """
    log = logger.bind(domain=site.domain)
    cookie_dir = Path(config.cookie_dir)

    # Startup: skip immediate refresh if cookies are fresh
    initial_interval = calculate_next_refresh(site.domain, cookie_dir)
    if initial_interval >= STARTUP_SKIP_THRESHOLD:
        log.info(
            "startup_skip_cookies_fresh",
            sleep_hours=round(initial_interval / 3600, 2),
        )
        await asyncio.sleep(initial_interval)

    while True:
        try:
            await perform_refresh(site, semaphore, cookie_dir)
            await ping_healthcheck(
                site.domain, success=True, healthcheck_url=config.healthcheck_url
            )
        except Exception as exc:
            log.error("scheduled_refresh_failed", error=str(exc))
            await send_ntfy_alert(site.domain, str(exc), ntfy_url=config.ntfy_url)
            await ping_healthcheck(
                site.domain, success=False, healthcheck_url=config.healthcheck_url
            )

        interval = calculate_next_refresh(site.domain, cookie_dir)
        if interval == 0:
            interval = MIN_INTERVAL

        next_at = datetime.fromtimestamp(time.time() + interval, tz=UTC).isoformat()
        log.info(
            "next_refresh_scheduled", next_at=next_at, hours=round(interval / 3600, 2)
        )
        await asyncio.sleep(interval)
