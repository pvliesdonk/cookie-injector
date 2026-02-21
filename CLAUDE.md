# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Cookie-injecting proxy for authenticated access to paywalled sites from server-side applications (e.g., Wallabag). Three separate Docker services share a `cookies/` directory:

- **proxy/** — mitmproxy addon that intercepts HTTP(S) requests and injects cookies from file storage. Returns 502 when cookies are missing/expired (hybrid failure handling per ADR-0001).
- **refresh/** — Playwright-based service that runs site-specific login scripts on an adaptive schedule (75% of cookie lifetime, 6h–24h bounds per ADR-0003). Applies session cookie persistence workaround (ADR-0002).
- **health/** — Simple HTTP server (port 8081) exposing `GET /health` with per-site cookie status JSON.

## Build and Development Commands

```bash
# Install dependencies (uv workspace)
uv sync --all-extras

# Run all tests
uv run pytest

# Run tests for a single component
uv run pytest proxy/tests/
uv run pytest refresh/tests/
uv run pytest health/tests/

# Run a single test
uv run pytest proxy/tests/test_addon.py::test_hybrid_failure_missing_cookies

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Docker
docker compose up --build
docker compose up proxy      # single service
```

## Architecture

### Cookie Flow

1. **refresh** service runs login scripts → extracts cookies from Playwright → applies session cookie workaround → writes `cookies/{domain}.json` atomically (temp file + rename)
2. **proxy** service reads `cookies/{domain}.json` on each request → injects `Cookie` header → adds `X-Cookie-Injector-Status` response header (ok/expiring/expired/missing)
3. **health** service reads `cookies/*.json` → reports per-site status via HTTP

### Cookie File Format (ADR-0004)

```json
{
  "cookies": [{"name": "...", "value": "...", "domain": "...", "expires": 1234567890}],
  "metadata": {
    "refreshed_at": "2026-02-21T10:30:00Z",
    "refresh_source": "scheduled",
    "next_refresh": "2026-02-22T04:30:00Z",
    "session_cookie_workaround": true
  }
}
```

### Hybrid Failure Handling (ADR-0001)

| Cookie State | Proxy Behavior | X-Cookie-Injector-Status |
|---|---|---|
| File missing | 502 Bad Gateway | `missing` |
| All expired | 502 Bad Gateway | `expired` |
| Valid, <24h left | Inject (fail-open) | `expiring` |
| Valid, >24h left | Inject normally | `ok` |

### Session Cookie Workaround (ADR-0002)

Playwright does NOT persist session cookies (`expires=-1`) across browser restarts (Chromium bug, Playwright issue #36139). The refresh service must set explicit 30-day expiry on all session cookies before saving.

### Domain Matching

Uses `tldextract` to extract canonical registered domains (e.g., `www.nrc.nl` → `nrc.nl`). Cookie files named `{domain}.json`. RFC 6265 Section 5.1.3 domain matching for subdomains.

## Key Dependencies

- **proxy:** mitmproxy, tldextract, structlog
- **refresh:** playwright, pydantic, PyYAML, tldextract, structlog, httpx
- **health:** structlog (stdlib http.server for HTTP)

## Configuration

- `config/sites.yaml` — site definitions (domain, login URL, auth env var names, refresh interval)
- `.env` — credentials and alerting URLs (never committed)
- Environment variables: `COOKIE_DIR`, `LOG_LEVEL`, `CONFIG_PATH`, `HEALTH_PORT`

## Adding a New Site

Create `refresh/scripts/{domain}.py` exporting `async def login(page, config)` that performs the Playwright login flow and returns `await page.context.cookies()`. Add the site entry to `config/sites.yaml`.

## References

- [Issue #1: Architectural Design Plan](https://github.com/pvliesdonk/cookie-injector/issues/1)
- [Discussion #2: Multi-agent Deliberation](https://github.com/pvliesdonk/cookie-injector/discussions/2)
- ADRs in `docs/decisions/`: 0001-hybrid-failure-handling, 0002-session-cookie-persistence-workaround, 0003-adaptive-scheduled-refresh, 0004-embedded-cookie-metadata
- Implementation issues: #6 (project setup) → #3 (proxy) → #4 (refresh) → #5 (health)
