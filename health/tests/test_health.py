"""Tests for the health endpoint status calculation."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from health.server import get_health_status, get_site_status


def _write_cookie_file(
    cookie_dir: Path, domain: str, cookies: list[dict], metadata: dict | None = None,
) -> None:
    data = {
        "cookies": cookies,
        "metadata": metadata or {
            "refreshed_at": "2026-02-21T10:00:00Z",
            "session_cookie_workaround": True,
        },
    }
    (cookie_dir / f"{domain}.json").write_text(json.dumps(data))


@pytest.fixture()
def cookie_dir(tmp_path):
    return tmp_path


def test_site_status_ok(cookie_dir):
    _write_cookie_file(
        cookie_dir,
        "nrc.nl",
        [{"name": "s", "value": "v", "expires": time.time() + 48 * 3600}],
    )
    result = get_site_status(cookie_dir / "nrc.nl.json")
    assert result["status"] == "ok"
    assert result["cookies_count"] == 1
    assert result["time_remaining_hours"] > 24


def test_site_status_expiring(cookie_dir):
    _write_cookie_file(
        cookie_dir,
        "nrc.nl",
        [{"name": "s", "value": "v", "expires": time.time() + 12 * 3600}],
    )
    result = get_site_status(cookie_dir / "nrc.nl.json")
    assert result["status"] == "expiring"
    assert result["time_remaining_hours"] < 24


def test_site_status_expired(cookie_dir):
    _write_cookie_file(
        cookie_dir,
        "nrc.nl",
        [{"name": "s", "value": "v", "expires": time.time() - 3600}],
    )
    result = get_site_status(cookie_dir / "nrc.nl.json")
    assert result["status"] == "expired"
    assert result["cookies_count"] == 0


def test_site_status_error_on_bad_file(cookie_dir):
    (cookie_dir / "nrc.nl.json").write_text("NOT JSON{{{")
    result = get_site_status(cookie_dir / "nrc.nl.json")
    assert result["status"] == "error"
    assert "error" in result


def test_overall_status_ok(cookie_dir):
    _write_cookie_file(
        cookie_dir, "nrc.nl", [{"name": "s", "expires": time.time() + 48 * 3600}]
    )
    _write_cookie_file(
        cookie_dir, "fd.nl", [{"name": "s", "expires": time.time() + 48 * 3600}]
    )
    result = get_health_status(cookie_dir)
    assert result["status"] == "ok"
    assert "nrc.nl" in result["sites"]
    assert "fd.nl" in result["sites"]


def test_overall_status_degraded(cookie_dir):
    _write_cookie_file(
        cookie_dir, "nrc.nl", [{"name": "s", "expires": time.time() + 48 * 3600}]
    )
    _write_cookie_file(
        cookie_dir, "fd.nl", [{"name": "s", "expires": time.time() + 12 * 3600}]
    )
    result = get_health_status(cookie_dir)
    assert result["status"] == "degraded"


def test_overall_status_error_empty(cookie_dir):
    result = get_health_status(cookie_dir)
    assert result["status"] == "error"
    assert result["sites"] == {}


def test_tmp_files_excluded(cookie_dir):
    (cookie_dir / "nrc.nl.json.tmp").write_text("{}")
    result = get_health_status(cookie_dir)
    assert "nrc.nl" not in result["sites"]


def test_metadata_propagated(cookie_dir):
    _write_cookie_file(
        cookie_dir, "nrc.nl",
        [{"name": "s", "expires": time.time() + 48 * 3600}],
        metadata={
            "refreshed_at": "2026-02-21T10:00:00Z",
            "next_refresh": "2026-02-22T04:00:00Z",
            "session_cookie_workaround": True,
        },
    )
    result = get_health_status(cookie_dir)
    site = result["sites"]["nrc.nl"]
    assert site["last_refresh"] == "2026-02-21T10:00:00Z"
    assert site["next_refresh"] == "2026-02-22T04:00:00Z"
    assert site["session_cookie_workaround"] is True
