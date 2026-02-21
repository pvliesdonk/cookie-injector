"""Alerting integration: ntfy push notifications and healthchecks.io pings."""
from __future__ import annotations

import os

import httpx
import structlog

logger = structlog.get_logger(__name__)


async def send_ntfy_alert(domain: str, error: str, ntfy_url: str | None = None) -> None:
    """Send push notification via ntfy on refresh failure.

    Args:
        domain: Domain that failed to refresh.
        error: Human-readable error description.
        ntfy_url: ntfy topic URL. Defaults to NTFY_URL env var.
    """
    url = ntfy_url or os.getenv("NTFY_URL")
    if not url:
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                content=f"Cookie refresh FAILED for {domain}: {error}",
                headers={
                    "Title": f"cookie-injector: {domain} failed",
                    "Priority": "high",
                    "Tags": "warning,cookie-injector",
                },
            )
            response.raise_for_status()
        logger.info("ntfy_alert_sent", domain=domain)
    except Exception as exc:
        logger.error("ntfy_alert_failed", domain=domain, error=str(exc))


async def ping_healthcheck(
    domain: str,
    success: bool,
    healthcheck_url: str | None = None,
) -> None:
    """Ping healthchecks.io endpoint after refresh attempt.

    Args:
        domain: Domain that was refreshed.
        success: True for success, False appends /fail.
        healthcheck_url: Base URL. Defaults to HEALTHCHECK_URL env var.
    """
    url = healthcheck_url or os.getenv("HEALTHCHECK_URL")
    if not url:
        return

    ping_url = url if success else f"{url}/fail"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(ping_url)
            response.raise_for_status()
        logger.info("healthcheck_pinged", domain=domain, success=success)
    except Exception as exc:
        logger.error("healthcheck_ping_failed", domain=domain, error=str(exc))
