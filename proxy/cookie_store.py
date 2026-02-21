"""Cookie loading and formatting utilities for the proxy.

No mitmproxy dependency — independently testable.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import structlog
import tldextract

logger = structlog.get_logger(__name__)


def get_canonical_domain(host: str) -> str:
    """Extract the canonical registered domain from a hostname.

    Args:
        host: Raw hostname, e.g. "www.nrc.nl".

    Returns:
        Canonical registered domain, e.g. "nrc.nl".

    Raises:
        ValueError: If the host cannot be parsed.
    """
    extracted = tldextract.extract(host)
    if not extracted.domain or not extracted.suffix:
        raise ValueError(f"Cannot extract canonical domain from host: {host!r}")
    return f"{extracted.domain}.{extracted.suffix}"


def load_cookies(path: Path) -> tuple[list[dict], dict]:
    """Load cookies and metadata from an ADR-0004 JSON file.

    Args:
        path: Path to the .json cookie file.

    Returns:
        Tuple of (cookies, metadata).

    Raises:
        FileNotFoundError: If path does not exist.
        ValueError: If missing 'cookies' key.
        json.JSONDecodeError: If invalid JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"Cookie file not found: {path}")
    with path.open() as f:
        data = json.load(f)
    if "cookies" not in data:
        raise ValueError(f"Invalid cookie file format (missing 'cookies' key): {path}")
    return data["cookies"], data.get("metadata", {})


def format_cookies(cookies: list[dict]) -> str:
    """Format cookie dicts into a Cookie header value.

    Args:
        cookies: List of dicts with 'name' and 'value' keys.

    Returns:
        Semicolon-separated name=value string.
    """
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def get_cookie_status(cookies: list[dict]) -> tuple[str, list[dict]]:
    """Determine hybrid-failure status of cookies (ADR-0001).

    Args:
        cookies: Raw cookie list from JSON store.

    Returns:
        Tuple of (status, valid_cookies) where status is one of
        "expired", "expiring", or "ok".
    """
    now = time.time()
    valid = [c for c in cookies if c.get("expires", -1) > now]
    if not valid:
        return "expired", []
    min_expiry = min(c["expires"] for c in valid)
    time_remaining = min_expiry - now
    if time_remaining < 24 * 3600:
        return "expiring", valid
    return "ok", valid
