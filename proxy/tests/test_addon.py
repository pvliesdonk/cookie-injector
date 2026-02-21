"""Tests for the proxy cookie store and addon hybrid failure handling."""
from __future__ import annotations

import json
import sys
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


# --- CookieInjectorAddon integration tests ---

@pytest.fixture(autouse=True, scope="session")
def _addon_sys_path():
    """Add proxy/ to sys.path so addon.py's standalone imports resolve."""
    proxy_dir = str(Path(__file__).resolve().parent.parent)
    if proxy_dir not in sys.path:
        sys.path.insert(0, proxy_dir)


def _make_flow(host):
    """Create a real mitmproxy HTTPFlow for testing."""
    from mitmproxy.test import tflow

    flow = tflow.tflow()
    flow.request.host = host
    return flow


@pytest.fixture()
def addon(tmp_path, monkeypatch):
    """Create a CookieInjectorAddon with a temp cookie dir."""
    monkeypatch.setenv("COOKIE_DIR", str(tmp_path))
    from proxy.addon import CookieInjectorAddon

    a = CookieInjectorAddon()
    a.cookie_dir = tmp_path
    return a


class TestAddonRequest:
    """Tests for CookieInjectorAddon.request()."""

    def test_missing_cookie_file_returns_502(self, addon):
        flow = _make_flow("www.nrc.nl")
        addon.request(flow)
        assert flow.response is not None
        assert flow.response.status_code == 502
        body = json.loads(flow.response.get_text())
        assert body["status"] == "missing"
        assert flow.response.headers["X-Cookie-Injector-Status"] == "missing"

    def test_expired_cookies_returns_502(self, addon, tmp_path):
        _write_cookie_file(
            tmp_path,
            "nrc.nl",
            [{"name": "s", "value": "v", "expires": time.time() - 3600}],
        )
        flow = _make_flow("www.nrc.nl")
        addon.request(flow)
        assert flow.response is not None
        assert flow.response.status_code == 502
        body = json.loads(flow.response.get_text())
        assert body["status"] == "expired"

    def test_valid_cookies_injected(self, addon, tmp_path):
        _write_cookie_file(
            tmp_path,
            "nrc.nl",
            [{"name": "s", "value": "v", "expires": time.time() + 48 * 3600}],
        )
        flow = _make_flow("www.nrc.nl")
        addon.request(flow)
        assert flow.response is None
        assert flow.request.headers["Cookie"] == "s=v"

    def test_status_header_on_response_not_request(self, addon, tmp_path):
        from mitmproxy import http

        _write_cookie_file(
            tmp_path,
            "nrc.nl",
            [{"name": "s", "value": "v", "expires": time.time() + 48 * 3600}],
        )
        flow = _make_flow("www.nrc.nl")
        addon.request(flow)
        assert "X-Cookie-Injector-Status" not in flow.request.headers
        # Simulate upstream response arriving
        flow.response = http.Response.make(200, b"OK")
        addon.response(flow)
        assert flow.response.headers["X-Cookie-Injector-Status"] == "ok"

    def test_malformed_json_returns_502(self, addon, tmp_path):
        (tmp_path / "nrc.nl.json").write_text("NOT JSON{{{")
        flow = _make_flow("www.nrc.nl")
        addon.request(flow)
        assert flow.response is not None
        assert flow.response.status_code == 502
        body = json.loads(flow.response.get_text())
        assert body["status"] == "error"

    def test_unknown_domain_passes_through(self, addon):
        flow = _make_flow("localhost")
        addon.request(flow)
        assert flow.response is None
