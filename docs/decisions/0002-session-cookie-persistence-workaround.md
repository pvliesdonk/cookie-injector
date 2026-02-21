# ADR-0002: Session Cookie Persistence Workaround

**Status:** Accepted

**Date:** 2026-02-21

**Decided by:** Multi-agent architectural deliberation (Discussion #2)

**Decision makers:** Gemini 3 Pro, Claude Opus 4.6, GPT-5.2, pvliesdonk

---

## Context

Playwright's `launch_persistent_context()` does **not** persist session cookies (cookies without an explicit `expires` field) across browser restarts due to upstream Chromium behavior. This is a known issue tracked as [Playwright #36139](https://github.com/microsoft/playwright/issues/36139) (opened May 2025, still open as of Feb 2026).

### Impact on Cookie-Injector

Many paywalled sites use **session cookies** for authentication:

```javascript
// Example session cookie from nrc.nl login
Set-Cookie: session_id=abc123; Path=/; HttpOnly; Secure; SameSite=Lax
// Note: No Expires or Max-Age attribute
```

**Problem:** When the cookie refresh service restarts (Docker restart, deployment, crash recovery), Playwright's persistent context does NOT restore these session cookies. The saved `cookies/*.json` files contain session cookies with `expires: -1`, which are invalid and will not be injected by the proxy.

**Severity:** **CRITICAL** - Without mitigation, authentication silently fails after any service restart, breaking core functionality.

---

## Decision

**Explicitly set `expires` timestamp on all cookies when saving to the cookie store**, treating session cookies as persistent cookies with a 30-day default lifetime.

### Implementation

```python
import time

def save_cookies_with_expiry_workaround(cookies: list[dict], output_path: Path) -> None:
    """
    Save cookies to JSON file with explicit expiry timestamps.

    Workaround for Playwright issue #36139: session cookies (expires=-1)
    are not persisted across browser restarts. We explicitly set expiry
    to ensure all cookies are persistent.
    """
    processed_cookies = []

    for cookie in cookies:
        cookie_copy = cookie.copy()

        # Check if this is a session cookie
        if cookie_copy.get('expires', -1) == -1:
            # Set explicit expiry: 30 days from now
            cookie_copy['expires'] = int(time.time()) + (30 * 24 * 3600)

            # Log for observability
            logger.info(
                f"Set explicit expiry for session cookie",
                cookie_name=cookie_copy['name'],
                domain=cookie_copy['domain'],
                expires_timestamp=cookie_copy['expires']
            )

        processed_cookies.append(cookie_copy)

    # Write to temporary file, then atomic rename
    tmp_path = output_path.with_suffix('.json.tmp')

    cookie_data = {
        "cookies": processed_cookies,
        "metadata": {
            "refreshed_at": datetime.utcnow().isoformat() + "Z",
            "session_cookie_workaround": True,  # Flag for future reference
            "playwright_version": playwright.__version__
        }
    }

    with open(tmp_path, 'w') as f:
        json.dump(cookie_data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    tmp_path.rename(output_path)
```

### Default Expiry: 30 Days

**Rationale for 30-day default:**
- Longer than typical cookie refresh intervals (12-24h)
- Shorter than most persistent cookies (90+ days)
- Provides safety margin for refresh failures (manual intervention window)
- Matches common session duration expectations for paid subscriptions

**Tunable per site** if needed:

```yaml
# config/sites.yaml
sites:
  - domain: nrc.nl
    refresh_interval: 12h
    session_cookie_ttl: 30d  # Default

  - domain: shortlived.com
    refresh_interval: 6h
    session_cookie_ttl: 7d  # Shorter for high-security site
```

---

## Rationale

### Alternative Approaches Considered

#### 1. Use `browser.new_context()` Without Persistence
**Approach:** Don't use `launch_persistent_context()`, save cookies manually on every login.

**Rejected because:**
- Still requires explicit expiry handling (same problem)
- Loses browser profile persistence (may trigger anti-bot detection)
- More complex code (manual cookie save on every login vs automatic)

#### 2. File-Based Cookie Cleanup on Startup
**Approach:** Accept that session cookies are lost, rely on refresh service to re-login on startup.

**Rejected because:**
- Increases startup time (must wait for all sites to re-login)
- Creates unnecessary load on paywalled sites (login on every restart)
- Doesn't fix the root issue (cookies still missing between refresh and proxy startup)

#### 3. Use Playwright's Storage State API
**Approach:** Use `context.storage_state()` instead of `context.cookies()`.

**Result:** Still returns `expires: -1` for session cookies (same issue).

#### 4. Patch Playwright/Chromium
**Approach:** Submit upstream fix to Playwright or Chromium.

**Rejected because:**
- Timeline unknown (issue open since May 2025, no resolution)
- Out of our control
- Need immediate solution for MVP
- Workaround is simple and reliable

### Why This Workaround is Safe

**Session cookies are semantically "valid until browser restart"**, but in our architecture:
- The "browser" is the Playwright-controlled headless Chrome
- Browser lifetime = refresh service container lifetime
- Container restarts are infrequent (deployments, crashes)
- Setting explicit expiry aligns with intended behavior ("cookie valid for service lifetime")

**Cookie refresh frequency** (12-24h) is much shorter than workaround expiry (30 days), so cookies are replaced long before artificial expiry:

```
Timeline:
T+0h:   Login, save cookies with expires=T+720h (30 days)
T+12h:  Scheduled refresh, save new cookies with expires=T+732h
T+24h:  Scheduled refresh, save new cookies with expires=T+744h
...
Artificial expiry at T+720h is never reached.
```

---

## Consequences

### Positive

- **Critical bug mitigated:** Authentication works across service restarts
- **Simple implementation:** One-line fix in cookie save logic
- **Reliable:** No dependency on upstream Playwright fix
- **Observable:** Logged for debugging, flagged in metadata

### Negative

- **Conceptual mismatch:** Session cookies become persistent (violates HTTP cookie semantics)
- **Manual cleanup needed:** If refresh service is disabled long-term (>30 days), stale cookies accumulate
- **Security consideration:** Session cookies intended to expire on "browser close" now persist for 30 days (acceptable for homelab, may need tuning for multi-user deployments)

### Operational Impact

**Cookie cleanup not critical for MVP** because:
- Refresh service replaces cookies every 12-24h
- Old cookie files are overwritten (atomic rename)
- No accumulation of stale data

**Post-MVP consideration:** Add cleanup task to delete cookie files older than 60 days (2x workaround expiry) if refresh service is disabled.

---

## Validation

### Test Case: Session Cookie Persistence

```python
# tests/test_session_cookie_workaround.py
import pytest
import time
from pathlib import Path

def test_session_cookies_get_explicit_expiry():
    """Verify session cookies are given explicit expiry timestamps."""
    # Simulate cookies returned by Playwright
    playwright_cookies = [
        {
            "name": "session_id",
            "value": "abc123",
            "domain": ".nrc.nl",
            "path": "/",
            "expires": -1,  # Session cookie
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax"
        },
        {
            "name": "persistent_cookie",
            "value": "xyz789",
            "domain": ".nrc.nl",
            "path": "/",
            "expires": int(time.time()) + 7 * 24 * 3600,  # 7 days
            "httpOnly": False,
            "secure": True
        }
    ]

    output_path = Path("/tmp/test_cookies.json")
    save_cookies_with_expiry_workaround(playwright_cookies, output_path)

    # Read back and verify
    with open(output_path) as f:
        saved_data = json.load(f)

    cookies = saved_data['cookies']

    # Session cookie should now have explicit expiry
    session_cookie = next(c for c in cookies if c['name'] == 'session_id')
    assert session_cookie['expires'] > time.time()
    assert session_cookie['expires'] < time.time() + 31 * 24 * 3600  # ~30 days

    # Persistent cookie should be unchanged
    persistent_cookie = next(c for c in cookies if c['name'] == 'persistent_cookie')
    assert persistent_cookie['expires'] == playwright_cookies[1]['expires']

    # Metadata should flag workaround
    assert saved_data['metadata']['session_cookie_workaround'] is True


def test_cookies_survive_service_restart():
    """Integration test: verify cookies persist across container restart."""
    # This test requires Docker and is run in CI only
    pass  # Implemented in integration test suite
```

---

## Monitoring

Add structured logging to track session cookie conversions:

```python
logger.info(
    "Session cookie workaround applied",
    domain=domain,
    session_cookies_count=sum(1 for c in cookies if c.get('expires', -1) == -1),
    total_cookies_count=len(cookies),
    artificial_expiry_hours=30 * 24
)
```

Health endpoint should expose whether session cookie workaround is active:

```json
{
  "sites": {
    "nrc.nl": {
      "status": "ok",
      "cookies_count": 5,
      "session_cookies_converted": 2,
      "workaround_active": true
    }
  }
}
```

---

## Links

- [Playwright Issue #36139](https://github.com/microsoft/playwright/issues/36139) - Session cookies not persisted in persistent context
- [Discussion #2: Multi-agent architectural deliberation](https://github.com/pvliesdonk/cookie-injector/discussions/2)
- [Chromium Issue 1453813](https://bugs.chromium.org/p/chromium/issues/detail?id=1453813) - Upstream Chromium behavior
- [RFC 6265 Section 4.1.2.2](https://datatracker.ietf.org/doc/html/rfc6265#section-4.1.2.2) - Session cookie specification

---

## Future Considerations

**If Playwright #36139 is fixed upstream:**
1. Add feature flag to disable workaround for newer Playwright versions
2. Keep workaround for backward compatibility with older deployments
3. Document migration path in upgrade guide

**If session cookie security becomes a concern:**
1. Make default TTL configurable globally (env var)
2. Support per-site TTL overrides in config
3. Consider encryption at rest for cookie files (separate ADR)

---

## Supersedes

None (initial decision)

---

## Related Decisions

- ADR-0001: Hybrid Failure Handling (uses expiry timestamps from this workaround)
- ADR-0003: Adaptive Scheduled Refresh (complements by ensuring fresh cookies before artificial expiry)
- ADR-0004: Embedded Cookie Metadata (metadata flag documents workaround application)
