"""Core refresh logic: execute Playwright login flows."""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import structlog
from playwright.async_api import async_playwright

from refresh.config import SiteConfig
from refresh.cookie_store import save_cookies_with_metadata

logger = structlog.get_logger(__name__)

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 5


def _script_module_name(domain: str) -> str:
    """Convert domain to script module name (e.g. nrc.nl -> refresh.scripts.nrc_nl)."""
    return "refresh.scripts." + domain.replace(".", "_")


async def perform_refresh(
    site: SiteConfig,
    semaphore: asyncio.Semaphore,
    cookie_dir: str | Path,
) -> None:
    """Execute login flow with retry. Never overwrites valid cookies on failure.

    Args:
        site: Site configuration.
        semaphore: Concurrency limiter (max 3 browsers).
        cookie_dir: Directory for {domain}.json files.
    """
    log = logger.bind(domain=site.domain)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("refresh_attempt_starting", attempt=attempt)
            cookies = await _run_login_flow(site, semaphore)
            save_cookies_with_metadata(
                domain=site.domain,
                cookies=cookies,
                cookie_dir=cookie_dir,
                refresh_source="scheduled",
            )
            log.info("refresh_succeeded", attempt=attempt, cookies_count=len(cookies))
            return
        except Exception as exc:
            log.warning("refresh_attempt_failed", attempt=attempt, error=str(exc))
            if attempt < MAX_RETRIES:
                backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                log.info("backing_off", seconds=backoff)
                await asyncio.sleep(backoff)

    raise RuntimeError(f"All {MAX_RETRIES} refresh attempts failed for {site.domain}")


async def _run_login_flow(
    site: SiteConfig,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Launch browser, run site script, return cookies.

    Args:
        site: Site configuration.
        semaphore: Concurrency limiter.

    Returns:
        Cookie dicts from Playwright context.
    """
    module_name = _script_module_name(site.domain)
    try:
        script_module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            f"No login script for '{site.domain}'. Expected: {module_name}"
        ) from exc

    async with semaphore:
        logger.info("browser_acquired", domain=site.domain)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                cookies = await script_module.login(page, site)
                return cookies
            finally:
                await browser.close()
