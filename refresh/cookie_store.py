"""Atomic cookie persistence with ADR-0002 session cookie workaround."""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

SESSION_COOKIE_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def apply_session_cookie_workaround(cookies: list[dict]) -> list[dict]:
    """Set explicit expiry on session cookies (ADR-0002).

    Playwright does not persist session cookies (expires=-1) across browser
    restarts due to Chromium bug (Playwright issue #36139).

    Args:
        cookies: Cookie dicts from Playwright context.cookies().

    Returns:
        New list with session cookies given 30-day expiry. Input not mutated.
    """
    now = int(time.time())
    processed: list[dict] = []

    for cookie in cookies:
        c = cookie.copy()
        if c.get("expires", -1) == -1:
            c["expires"] = now + SESSION_COOKIE_TTL_SECONDS
            logger.info(
                "session_cookie_workaround_applied",
                cookie_name=c.get("name"),
                domain=c.get("domain"),
                expires=c["expires"],
            )
        processed.append(c)

    return processed


def save_cookies_with_metadata(
    domain: str,
    cookies: list[dict],
    cookie_dir: str | Path,
    refresh_source: str = "scheduled",
    next_refresh_at: str | None = None,
) -> None:
    """Atomically save cookies and metadata in ADR-0004 format.

    Applies session cookie workaround, writes to .json.tmp, then renames.

    Args:
        domain: Canonical domain, e.g. "nrc.nl".
        cookies: Raw cookie list from Playwright.
        cookie_dir: Directory for {domain}.json files.
        refresh_source: One of "scheduled", "manual", "startup".
        next_refresh_at: ISO 8601 next refresh time, or None.
    """
    cookie_dir = Path(cookie_dir)
    cookie_file = cookie_dir / f"{domain}.json"
    tmp_file = cookie_dir / f"{domain}.json.tmp"

    processed_cookies = apply_session_cookie_workaround(cookies)

    session_cookie_count = sum(1 for c in cookies if c.get("expires", -1) == -1)

    metadata: dict = {
        "refreshed_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "refresh_source": refresh_source,
        "site_config": domain,
        "cookies_count": len(processed_cookies),
        "session_cookie_workaround": session_cookie_count > 0,
        "session_cookies_converted": session_cookie_count,
    }
    if next_refresh_at is not None:
        metadata["next_refresh"] = next_refresh_at

    data = {"cookies": processed_cookies, "metadata": metadata}

    with tmp_file.open("w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    tmp_file.rename(cookie_file)
    logger.info("cookies_saved", domain=domain, cookies_count=len(processed_cookies))


def load_cookies(path: Path) -> tuple[list[dict], dict]:
    """Load cookies and metadata from ADR-0004 JSON file.

    Args:
        path: Path to the cookie JSON file.

    Returns:
        Tuple of (cookies, metadata).
    """
    if not path.exists():
        raise FileNotFoundError(f"Cookie file not found: {path}")
    with path.open() as f:
        data = json.load(f)
    if "cookies" not in data:
        raise ValueError(f"Invalid cookie file format: {path}")
    return data["cookies"], data.get("metadata", {})
