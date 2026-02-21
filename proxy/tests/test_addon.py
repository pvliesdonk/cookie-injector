"""Tests for the proxy cookie store and hybrid failure handling logic."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from proxy.cookie_store import (
    format_cookies,
    get_canonical_domain,
    get_cookie_status,
    load_cookies,
)


def _write_cookie_file(cookie_dir: Path, domain: str, cookies: list[dict]) -> None:
    data = {
        "cookies": cookies,
        "metadata": {
            "refreshed_at": "2026-02-21T10:00:00Z",
            "refresh_source": "scheduled",
        },
    }
    (cookie_dir / f"{domain}.json").write_text(json.dumps(data))


# --- get_canonical_domain ---

def test_canonical_domain_from_subdomain():
    assert get_canonical_domain("www.nrc.nl") == "nrc.nl"

def test_canonical_domain_already_canonical():
    assert get_canonical_domain("nrc.nl") == "nrc.nl"

def test_canonical_domain_deep_subdomain():
    assert get_canonical_domain("a.b.c.nrc.nl") == "nrc.nl"

def test_canonical_domain_invalid_raises():
    with pytest.raises(ValueError):
        get_canonical_domain("localhost")


# --- load_cookies ---

def test_load_cookies_valid(tmp_path):
    _write_cookie_file(
        tmp_path, "nrc.nl", [{"name": "s", "value": "v", "expires": 9999999999}]
    )
    cookies, metadata = load_cookies(tmp_path / "nrc.nl.json")
    assert len(cookies) == 1
    assert cookies[0]["name"] == "s"
    assert "refreshed_at" in metadata

def test_load_cookies_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_cookies(tmp_path / "missing.json")

def test_load_cookies_invalid_json(tmp_path):
    (tmp_path / "bad.json").write_text("NOT JSON{{{")
    with pytest.raises(json.JSONDecodeError):
        load_cookies(tmp_path / "bad.json")

def test_load_cookies_missing_key(tmp_path):
    (tmp_path / "bad.json").write_text('{"metadata": {}}')
    with pytest.raises(ValueError, match="missing 'cookies' key"):
        load_cookies(tmp_path / "bad.json")


# --- format_cookies ---

def test_format_single():
    assert format_cookies([{"name": "a", "value": "1"}]) == "a=1"

def test_format_multiple():
    cookies = [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]
    assert format_cookies(cookies) == "a=1; b=2"

def test_format_empty():
    assert format_cookies([]) == ""


# --- get_cookie_status (ADR-0001) ---

def test_status_expired():
    status, valid = get_cookie_status([{"name": "s", "expires": time.time() - 3600}])
    assert status == "expired"
    assert valid == []

def test_status_expiring():
    status, valid = get_cookie_status(
        [{"name": "s", "expires": time.time() + 12 * 3600}]
    )
    assert status == "expiring"
    assert len(valid) == 1

def test_status_ok():
    status, valid = get_cookie_status(
        [{"name": "s", "expires": time.time() + 48 * 3600}]
    )
    assert status == "ok"
    assert len(valid) == 1

def test_status_mixed():
    cookies = [
        {"name": "expired", "expires": time.time() - 3600},
        {"name": "valid", "expires": time.time() + 48 * 3600},
    ]
    status, valid = get_cookie_status(cookies)
    assert status == "ok"
    assert len(valid) == 1
    assert valid[0]["name"] == "valid"

def test_status_uses_earliest_expiry():
    cookies = [
        {"name": "a", "expires": time.time() + 12 * 3600},
        {"name": "b", "expires": time.time() + 48 * 3600},
    ]
    status, valid = get_cookie_status(cookies)
    assert status == "expiring"
    assert len(valid) == 2
