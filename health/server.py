"""Health check HTTP endpoint for the cookie-injector system.

Endpoints:
    GET /health     - JSON per-site status + overall system status
    GET /           - Same as /health
    GET /index.html - Static status dashboard

Environment variables:
    COOKIE_DIR: Path to cookie files. Default: /cookies
    HEALTH_PORT: Port to listen on. Default: 8081
    LOG_LEVEL: Logging verbosity. Default: INFO
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import structlog

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, os.getenv("LOG_LEVEL", "INFO"))
    )
)

logger = structlog.get_logger(__name__)

EXPIRING_THRESHOLD_SECONDS = 24 * 3600

_STATIC_DIR = Path(__file__).parent / "static"


def get_site_status(cookie_file: Path) -> dict:
    """Calculate status for a single site from its cookie file.

    Args:
        cookie_file: Path to {domain}.json.

    Returns:
        Status dict with keys: status, cookies_count, cookies_valid_until, etc.
    """
    try:
        with cookie_file.open() as f:
            data = json.load(f)

        cookies = data.get("cookies", [])
        metadata = data.get("metadata", {})
        now = time.time()
        valid = [c for c in cookies if c.get("expires", -1) > now]

        if not valid:
            return {
                "status": "expired",
                "cookies_count": 0,
                "cookies_valid_until": None,
                "time_remaining_hours": 0.0,
                "last_refresh": metadata.get("refreshed_at"),
                "next_refresh": metadata.get("next_refresh"),
                "session_cookie_workaround": metadata.get(
                    "session_cookie_workaround", False
                ),
            }

        min_expiry = min(c["expires"] for c in valid)
        time_remaining = min_expiry - now
        valid_until = (
            datetime.fromtimestamp(min_expiry, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        status = "expiring" if time_remaining < EXPIRING_THRESHOLD_SECONDS else "ok"

        return {
            "status": status,
            "cookies_count": len(valid),
            "cookies_valid_until": valid_until,
            "time_remaining_hours": round(time_remaining / 3600, 1),
            "last_refresh": metadata.get("refreshed_at"),
            "next_refresh": metadata.get("next_refresh"),
            "session_cookie_workaround": metadata.get(
                "session_cookie_workaround", False
            ),
        }
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.error("site_status_error", cookie_file=str(cookie_file), error=str(exc))
        return {"status": "error", "error": str(exc)}


def get_health_status(cookie_dir: Path) -> dict:
    """Build complete health response from all cookie files.

    Args:
        cookie_dir: Directory containing {domain}.json files.

    Returns:
        Dict with overall status and per-site details.
    """
    sites: dict[str, dict] = {}

    for cookie_file in sorted(cookie_dir.glob("*.json")):
        domain = cookie_file.stem
        sites[domain] = get_site_status(cookie_file)

    statuses = {s["status"] for s in sites.values()}
    if not sites or all(s == "error" for s in statuses):
        overall = "error"
    elif all(s == "ok" for s in statuses):
        overall = "ok"
    else:
        overall = "degraded"

    return {
        "status": overall,
        "timestamp": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "sites": sites,
    }


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the health endpoint."""

    cookie_dir: Path = Path(os.getenv("COOKIE_DIR", "/cookies"))

    def log_message(self, format: str, *args: object) -> None:
        """Route access logs through structlog."""
        logger.debug("http_access", message=format % args)

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path in ("/", "/health"):
            self._serve_health_json()
        elif self.path == "/index.html":
            self._serve_static("index.html", "text/html")
        else:
            self.send_error(404, "Not Found")

    def _serve_health_json(self) -> None:
        health_data = get_health_status(self.cookie_dir)
        body = json.dumps(health_data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        logger.info("health_served", status=health_data["status"])

    def _serve_static(self, filename: str, content_type: str) -> None:
        static_file = _STATIC_DIR / filename
        if not static_file.exists():
            self.send_error(404, "Static file not found")
            return
        body = static_file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(port: int | None = None) -> None:
    """Start the health HTTP server.

    Args:
        port: Port to listen on. Defaults to HEALTH_PORT env, then 8081.
    """
    effective_port = port or int(os.getenv("HEALTH_PORT", "8081"))
    server = ThreadingHTTPServer(("0.0.0.0", effective_port), HealthHandler)
    logger.info("health_server_starting", port=effective_port)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
