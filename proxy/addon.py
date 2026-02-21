"""Cookie-injecting mitmproxy addon with hybrid failure handling (ADR-0001).

Usage:
    mitmdump -s addon.py
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import structlog
from cookie_store import (
    format_cookies,
    get_canonical_domain,
    get_cookie_status,
    load_cookies,
)
from mitmproxy import http

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, os.getenv("LOG_LEVEL", "INFO"))
    )
)

logger = structlog.get_logger(__name__)


class CookieInjectorAddon:
    """mitmproxy addon that injects cookies into HTTP requests.

    Reads cookies from {COOKIE_DIR}/{domain}.json and applies
    hybrid failure handling (ADR-0001).
    """

    def __init__(self) -> None:
        self.cookie_dir = Path(os.getenv("COOKIE_DIR", "/cookies"))
        logger.info("addon_initialized", cookie_dir=str(self.cookie_dir))

    def request(self, flow: http.HTTPFlow) -> None:
        """Intercept request and inject cookies or return 502."""
        host = flow.request.pretty_host
        log = logger.bind(host=host)

        try:
            domain = get_canonical_domain(host)
        except ValueError:
            log.warning("cannot_extract_domain_skipping")
            return

        cookie_file = self.cookie_dir / f"{domain}.json"
        log = log.bind(domain=domain)

        if not cookie_file.exists():
            log.warning("cookie_file_missing")
            self._return_502(flow, "missing", domain)
            return

        try:
            cookies, metadata = load_cookies(cookie_file)
        except Exception as exc:
            log.error("cookie_load_error", error=str(exc))
            self._return_502(flow, "error", domain)
            return

        status, valid_cookies = get_cookie_status(cookies)

        if status == "expired":
            log.warning("all_cookies_expired")
            self._return_502(flow, "expired", domain)
            return

        flow.request.headers["Cookie"] = format_cookies(valid_cookies)
        flow.request.headers["X-Cookie-Injector-Status"] = status
        log.info("cookies_injected", status=status, count=len(valid_cookies))

    def _return_502(
        self,
        flow: http.HTTPFlow,
        reason: str,
        domain: str,
    ) -> None:
        """Short-circuit flow with 502 Bad Gateway JSON response."""
        error_body = {
            "error": "cookie_injector_no_valid_cookies",
            "domain": domain,
            "message": f"No valid authentication cookies available. Reason: {reason}",
            "status": reason,
        }
        flow.response = http.Response.make(
            502,
            json.dumps(error_body, indent=2).encode(),
            {
                "Content-Type": "application/json",
                "X-Cookie-Injector-Status": reason,
            },
        )
        logger.warning("returned_502", domain=domain, reason=reason)


addons = [CookieInjectorAddon()]
