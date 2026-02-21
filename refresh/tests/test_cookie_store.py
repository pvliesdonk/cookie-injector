"""Tests for atomic cookie storage and ADR-0004 format."""
from __future__ import annotations

import json
import time

import pytest

from refresh.cookie_store import load_cookies, save_cookies_with_metadata


@pytest.fixture()
def cookie_dir(tmp_path):
    return tmp_path


def test_atomic_write_creates_file(cookie_dir):
    cookies = [{"name": "s", "value": "v", "domain": ".nrc.nl", "expires": -1}]
    save_cookies_with_metadata("nrc.nl", cookies, cookie_dir)
    assert (cookie_dir / "nrc.nl.json").exists()
    assert not (cookie_dir / "nrc.nl.json.tmp").exists()


def test_adr004_format(cookie_dir):
    cookies = [{"name": "s", "value": "v", "domain": ".nrc.nl", "expires": -1}]
    save_cookies_with_metadata("nrc.nl", cookies, cookie_dir, refresh_source="manual")
    data = json.loads((cookie_dir / "nrc.nl.json").read_text())
    assert "cookies" in data
    assert "metadata" in data
    assert data["metadata"]["refresh_source"] == "manual"
    assert "refreshed_at" in data["metadata"]
    assert data["metadata"]["session_cookie_workaround"] is True


def test_session_workaround_on_save(cookie_dir):
    cookies = [{"name": "s", "value": "v", "domain": ".nrc.nl", "expires": -1}]
    save_cookies_with_metadata("nrc.nl", cookies, cookie_dir)
    data = json.loads((cookie_dir / "nrc.nl.json").read_text())
    assert data["cookies"][0]["expires"] > time.time()


def test_next_refresh_in_metadata(cookie_dir):
    cookies = [{"name": "s", "value": "v", "expires": time.time() + 86400}]
    save_cookies_with_metadata(
        "nrc.nl", cookies, cookie_dir, next_refresh_at="2026-02-22T10:00:00Z"
    )
    data = json.loads((cookie_dir / "nrc.nl.json").read_text())
    assert data["metadata"]["next_refresh"] == "2026-02-22T10:00:00Z"


def test_load_round_trip(cookie_dir):
    cookies = [
        {"name": "a", "value": "b", "domain": ".nrc.nl", "expires": time.time() + 3600}
    ]
    save_cookies_with_metadata("nrc.nl", cookies, cookie_dir)
    loaded, metadata = load_cookies(cookie_dir / "nrc.nl.json")
    assert len(loaded) == 1
    assert loaded[0]["name"] == "a"
    assert "refreshed_at" in metadata
