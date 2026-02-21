# ADR-0001: Hybrid Failure Handling Strategy

**Status:** Accepted

**Date:** 2026-02-21

**Decided by:** Multi-agent architectural deliberation (Discussion #2)

**Decision makers:** Claude Opus 4.6, Gemini 3 Pro, GPT-5.2, pvliesdonk

---

## Context

The cookie-injecting proxy must decide how to handle missing or expired cookies when Wallabag makes requests to paywalled sites. Three approaches were evaluated:

### Option 1: Fail-Closed
**Behavior:** Return HTTP 502 Bad Gateway if cookies are missing or stale.

**Pros:**
- Explicit errors prevent mystery failures
- Clear signal to Wallabag that authentication is broken
- Easier debugging (immediate feedback)

**Cons:**
- Breaks Wallabag's ability to fetch non-paywalled content from domains that also host paywalled content (e.g., free articles on nrc.nl)
- Proxy cannot distinguish which specific URLs require authentication
- Requires maintaining a site allowlist

### Option 2: Fail-Open
**Behavior:** Always inject available cookies (if any), let origin server decide validity.

**Pros:**
- Works for mixed paywalled/free content domains
- Proxy remains transparent
- Origin server is authoritative on cookie validity

**Cons:**
- Silent failures harder to debug
- Wallabag sees mysterious fetch failures without obvious cause
- No visibility into cookie staleness

### Option 3: Hybrid (Selected)
**Behavior:** Fail-closed when cookies completely missing or all expired, fail-open when valid but approaching expiry.

**Pros:**
- Balances explicit error visibility with pragmatism
- Supports mixed content domains
- Respects cookie grace periods (many sites allow 24-48h beyond stated expiry)
- Observable via headers for debugging

**Cons:**
- More complex implementation than pure strategies
- Subjective threshold (24h) for "approaching expiry"

---

## Decision

Implement **hybrid failure handling** based on cookie state:

| Cookie State | Proxy Behavior | HTTP Status | Header | Response Body |
|-------------|----------------|-------------|---------|---------------|
| **Missing** (no file exists) | Fail-closed | `502 Bad Gateway` | `X-Cookie-Injector-Status: missing` | JSON error |
| **All Expired** (all cookies past expiry) | Fail-closed | `502 Bad Gateway` | `X-Cookie-Injector-Status: expired` | JSON error |
| **Valid but Expiring Soon** (<24h remain) | Fail-open (inject) | Pass-through | `X-Cookie-Injector-Status: expiring` | N/A |
| **Valid** (>24h remain) | Inject normally | Pass-through | `X-Cookie-Injector-Status: ok` | N/A |

### 502 Response Format

```json
{
  "error": "cookie_injector_no_valid_cookies",
  "domain": "nrc.nl",
  "message": "No valid authentication cookies available for this domain. Cookie refresh may be failing.",
  "last_refresh_attempt": "2026-02-21T10:30:00Z",
  "status": "missing",
  "debug_info": {
    "cookie_file": "/cookies/nrc.nl.json",
    "file_exists": false
  }
}
```

### Header Usage

All proxied requests include `X-Cookie-Injector-Status` header for observability:
- `ok` - Cookies valid, >24h remaining
- `expiring` - Cookies valid but <24h remaining (refresh should happen soon)
- `missing` - No cookie file exists for domain (502 returned)
- `expired` - All cookies past expiry (502 returned)

This enables Wallabag (or monitoring tools) to detect degraded states before complete failure.

---

## Rationale

### Why Hybrid Over Pure Fail-Closed?

**Critical finding from GPT-5.2 agent:** Fail-closed breaks Wallabag for mixed content domains. Many news sites (nrc.nl, fd.nl) have both free and paywalled articles on the same domain. The proxy cannot distinguish which specific URLs require authentication, so fail-closed would return 502 for **all** requests to the domain, including free content.

Example scenario:
- Domain: `nrc.nl`
- Cookie refresh fails
- User tries to fetch free article from nrc.nl
- Pure fail-closed: 502 error (broken)
- Hybrid: Injects stale/missing cookies, origin server returns content for free articles, 401/403 for paywalled (correct behavior)

### Why Hybrid Over Pure Fail-Open?

**Critical finding from Claude Opus 4.6 agent:** Pure fail-open creates silent failures. When cookie refresh fails for days, Wallabag experiences mysterious fetch failures with no indication that authentication is the root cause. This creates poor operational visibility.

Hybrid provides:
- Explicit 502 errors when cookies are **definitely** invalid (missing or expired)
- Graceful degradation when cookies **might** still work (approaching expiry, grace periods)
- Observable state via headers for monitoring

### Why 24-Hour Threshold?

**Evidence from Gemini 3 Pro agent:** Many paywalled sites have cookie grace periods of 24-48 hours beyond the stated `expires` timestamp. Aggressive fail-closed on expiry creates false positives.

The 24-hour threshold balances:
- Cookie refresh has time to succeed before hard expiry
- Grace periods respected (most sites still accept cookies for 1-2 days after nominal expiry)
- Early warning via `expiring` status for monitoring

---

## Consequences

### Positive

- **Better UX for mixed content:** Wallabag can fetch free articles even when cookie refresh fails
- **Explicit error visibility:** 502 errors clearly indicate authentication failures (not network issues, site downtime, etc.)
- **Observable state:** Headers enable monitoring and debugging without log access
- **Respects grace periods:** Doesn't prematurely fail on cookies that might still work

### Negative

- **Implementation complexity:** More complex than pure fail-open or fail-closed (requires expiry parsing, state machine)
- **Subjective threshold:** 24h is a reasonable default but may need tuning per site
- **Edge cases:** Sites with very short-lived cookies (<24h) may hit fail-open path too frequently

### Operational Impact

- Monitoring tools should alert on `X-Cookie-Injector-Status: expiring` to trigger investigation before hard failure
- Health endpoint (ADR-0004) provides per-site cookie status for operators
- 502 responses indicate urgent action needed (cookie refresh broken)

---

## Implementation Notes

### mitmproxy Addon Logic

```python
def request(self, flow: http.HTTPFlow) -> None:
    domain = get_canonical_domain(flow.request.pretty_host)
    cookie_file = Path(COOKIE_DIR) / f"{domain}.json"
    
    if not cookie_file.exists():
        # Fail-closed: missing
        return_502(flow, "missing", domain)
        return
    
    cookies, metadata = load_cookies(cookie_file)
    
    if all_cookies_expired(cookies):
        # Fail-closed: expired
        return_502(flow, "expired", domain)
        return
    
    # Inject cookies (fail-open)
    min_expiry = min(c['expires'] for c in cookies if c['expires'] > time.time())
    time_remaining = min_expiry - time.time()
    
    if time_remaining < 24 * 3600:
        flow.request.headers["X-Cookie-Injector-Status"] = "expiring"
    else:
        flow.request.headers["X-Cookie-Injector-Status"] = "ok"
    
    flow.request.headers["Cookie"] = format_cookies(cookies)
```

### Tuning Threshold

For sites with atypical cookie lifetimes, threshold can be made configurable:

```yaml
# config/sites.yaml
sites:
  - domain: nrc.nl
    refresh_interval: 12h
    fail_open_threshold: 24h  # Default
  
  - domain: shortlived.com
    refresh_interval: 2h
    fail_open_threshold: 6h  # Shorter threshold for short-lived cookies
```

---

## Links

- [Discussion #2: Multi-agent architectural deliberation](https://github.com/pvliesdonk/cookie-injector/discussions/2)
- [Issue #1: Architectural Design Plan](https://github.com/pvliesdonk/cookie-injector/issues/1)
- Original requirement: Prevent Wallabag mystery failures while supporting mixed content domains

---

## Supersedes

None (initial decision)

---

## Related Decisions

- ADR-0003: Adaptive Scheduled Refresh Strategy (complements failure handling with proactive refresh)
- ADR-0002: Session Cookie Persistence Workaround (ensures cookies have valid expiry timestamps for this logic)
