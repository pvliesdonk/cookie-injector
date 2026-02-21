"""Tests for ADR-0002: session cookie persistence workaround."""
from __future__ import annotations

import time

from refresh.cookie_store import (
    SESSION_COOKIE_TTL_SECONDS,
    apply_session_cookie_workaround,
)


def test_session_cookie_gets_explicit_expiry():
    before = int(time.time())
    cookies = [
        {"name": "session_id", "value": "abc", "domain": ".nrc.nl", "expires": -1}
    ]
    result = apply_session_cookie_workaround(cookies)
    assert len(result) == 1
    assert result[0]["expires"] > before
    assert result[0]["expires"] <= before + SESSION_COOKIE_TTL_SECONDS + 5


def test_persistent_cookie_unchanged():
    original_expiry = int(time.time()) + 7 * 24 * 3600
    cookies = [{"name": "pref", "value": "xyz", "expires": original_expiry}]
    result = apply_session_cookie_workaround(cookies)
    assert result[0]["expires"] == original_expiry


def test_mixed_cookies_only_session_modified():
    original_expiry = int(time.time()) + 86400
    cookies = [
        {"name": "session", "value": "s", "expires": -1},
        {"name": "pref", "value": "p", "expires": original_expiry},
    ]
    result = apply_session_cookie_workaround(cookies)
    session = next(c for c in result if c["name"] == "session")
    pref = next(c for c in result if c["name"] == "pref")
    assert session["expires"] > time.time()
    assert pref["expires"] == original_expiry


def test_original_list_not_mutated():
    cookies = [{"name": "s", "value": "v", "expires": -1}]
    original_expires = cookies[0]["expires"]
    apply_session_cookie_workaround(cookies)
    assert cookies[0]["expires"] == original_expires
