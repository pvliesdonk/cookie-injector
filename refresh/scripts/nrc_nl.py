"""Site-specific login script for nrc.nl."""
from __future__ import annotations

import os

import structlog
from playwright.async_api import Page

from refresh.config import SiteConfig

logger = structlog.get_logger(__name__)


async def login(page: Page, config: SiteConfig) -> list[dict]:
    """Perform nrc.nl login and return cookies.

    Args:
        page: Playwright Page in a fresh browser context.
        config: Site configuration with auth env var names.

    Returns:
        Cookie dicts from page.context.cookies().
    """
    username = os.getenv(config.auth.username_env or "NRC_USER")
    password = os.getenv(config.auth.password_env or "NRC_PASS")

    if not username or not password:
        raise ValueError(
            "Missing credentials: "
            f"{config.auth.username_env} / {config.auth.password_env}"
        )

    logger.info("login_starting", domain=config.domain, url=config.login_url)

    await page.goto(config.login_url, wait_until="networkidle", timeout=30_000)
    await page.fill('input[name="username"]', username)
    await page.fill('input[name="password"]', password)
    await page.click('button[type="submit"]')
    await page.wait_for_url("**/home**", timeout=30_000)

    logger.info("login_succeeded", domain=config.domain)
    return await page.context.cookies()
