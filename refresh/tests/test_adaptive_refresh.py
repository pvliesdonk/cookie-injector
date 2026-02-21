"""Tests for ADR-0003: adaptive scheduled refresh."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from refresh.scheduler import MAX_INTERVAL, MIN_INTERVAL, calculate_next_refresh


def _write_cookie_file(cookie_dir: Path, domain: str, cookies: list[dict]) -> None:
    data = {"cookies": cookies, "metadata": {}}
    (cookie_dir / f"{domain}.json").write_text(json.dumps(data))


@pytest.fixture()
def cookie_dir(tmp_path):
    return tmp_path


def test_missing_file_returns_zero(cookie_dir):
    assert calculate_next_refresh("nrc.nl", cookie_dir) == 0.0


def test_all_expired_returns_zero(cookie_dir):
    _write_cookie_file(
        cookie_dir, "nrc.nl", [{"name": "s", "expires": time.time() - 3600}]
    )
    assert calculate_next_refresh("nrc.nl", cookie_dir) == 0.0


def test_24h_cookie_returns_18h(cookie_dir):
    _write_cookie_file(
        cookie_dir, "nrc.nl", [{"name": "s", "expires": time.time() + 24 * 3600}]
    )
    result = calculate_next_refresh("nrc.nl", cookie_dir)
    assert 17.9 * 3600 < result < 18.1 * 3600


def test_short_clamped_to_min(cookie_dir):
    _write_cookie_file(
        cookie_dir, "nrc.nl", [{"name": "s", "expires": time.time() + 4 * 3600}]
    )
    assert calculate_next_refresh("nrc.nl", cookie_dir) == MIN_INTERVAL


def test_long_clamped_to_max(cookie_dir):
    _write_cookie_file(
        cookie_dir, "nrc.nl", [{"name": "s", "expires": time.time() + 30 * 24 * 3600}]
    )
    assert calculate_next_refresh("nrc.nl", cookie_dir) == MAX_INTERVAL


def test_earliest_expiry_used(cookie_dir):
    _write_cookie_file(cookie_dir, "nrc.nl", [
        {"name": "a", "expires": time.time() + 8 * 3600},
        {"name": "b", "expires": time.time() + 48 * 3600},
    ])
    assert calculate_next_refresh("nrc.nl", cookie_dir) == MIN_INTERVAL
