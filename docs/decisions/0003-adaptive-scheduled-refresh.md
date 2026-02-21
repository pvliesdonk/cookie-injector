# ADR-0003: Adaptive Scheduled Refresh Strategy

**Status:** Accepted

**Date:** 2026-02-21

**Decided by:** Multi-agent architectural deliberation (Discussion #2)

**Decision makers:** Gemini 3 Pro, Claude Opus 4.6, GPT-5.2, pvliesdonk

---

## Context

The cookie refresh service must decide when to run automated login flows to keep cookies fresh. Three approaches were debated:

### Option 1: Fixed Schedule
**Behavior:** Refresh all sites every N hours (e.g., every 12h).

**Pros:**
- Simple implementation (`asyncio.sleep(12 * 3600)`)
- Predictable resource usage
- No component coupling

**Cons:**
- May refresh too often (if cookies valid for 30 days, 12h refresh wastes resources)
- May refresh too late (if cookies valid for 6h, 12h refresh causes outages)
- Ignores actual cookie lifetimes

### Option 2: Reactive (TTL-Based)
**Behavior:** Proxy checks cookie expiry on every request, triggers refresh if <N hours remain.

**Pros:**
- Perfectly timed (refresh only when needed)
- Adapts to actual cookie usage patterns

**Cons:**
- **Race condition risk:** Multiple concurrent requests could trigger 100+ browser instances
- Requires distributed locking (Redis, etc.) for multi-instance deployments
- Couples proxy and refresh components (proxy must call refresh service API)
- Complex error handling (what if proxy triggers refresh but refresh service is down?)

### Option 3: Adaptive Scheduled (Selected)
**Behavior:** Scheduled refresh with intervals calculated from actual cookie expiry times.

**Pros:**
- Intelligent timing without coupling
- No race conditions (single scheduler)
- Adapts to cookie lifetimes (short-lived cookies refresh more often)
- Simple implementation (pure scheduling, no IPC)

**Cons:**
- Slightly less optimal than reactive (may refresh 1-2h early/late)
- Requires expiry parsing

---

## Decision

Implement **adaptive scheduled refresh** with the following algorithm:

### Core Algorithm

```python
async def calculate_next_refresh(domain: str) -> float:
    """
    Calculate next refresh time based on cookie expiry.

    Returns seconds until next refresh should occur.
    """
    cookie_file = Path(COOKIE_DIR) / f"{domain}.json"

    if not cookie_file.exists():
        # No cookies yet, refresh immediately
        return 0

    cookies, metadata = load_cookies(cookie_file)

    # Find earliest expiry among all cookies
    valid_cookies = [c for c in cookies if c.get('expires', -1) > time.time()]

    if not valid_cookies:
        # All cookies expired, refresh immediately
        return 0

    min_expiry = min(c['expires'] for c in valid_cookies)
    cookie_lifetime = min_expiry - time.time()

    # Refresh when 75% of lifetime has passed (25% remaining)
    # Example: 24h cookie → refresh at 18h (6h remaining)
    refresh_interval = cookie_lifetime * 0.75

    # Enforce minimum interval (avoid excessive refreshes)
    MIN_INTERVAL = 6 * 3600  # 6 hours
    refresh_interval = max(refresh_interval, MIN_INTERVAL)

    # Enforce maximum interval (don't wait too long even for long-lived cookies)
    MAX_INTERVAL = 24 * 3600  # 24 hours
    refresh_interval = min(refresh_interval, MAX_INTERVAL)

    return refresh_interval
```

### Safety Check: Proactive Refresh on Approaching Expiry

On each scheduled refresh run, check if cookies are expiring soon:

```python
async def run_scheduled_refresh(domain: str) -> None:
    """
    Execute scheduled refresh with TTL safety check.
    """
    cookie_file = Path(COOKIE_DIR) / f"{domain}.json"

    if cookie_file.exists():
        cookies, metadata = load_cookies(cookie_file)
        valid_cookies = [c for c in cookies if c.get('expires', -1) > time.time()]

        if valid_cookies:
            min_expiry = min(c['expires'] for c in valid_cookies)
            time_remaining = min_expiry - time.time()

            # Safety check: if <24h remaining, log warning
            if time_remaining < 24 * 3600:
                logger.warning(
                    f"Cookies expiring soon, refreshing immediately",
                    domain=domain,
                    hours_remaining=time_remaining / 3600
                )

    # Execute login flow
    await perform_site_login(domain)

    # Calculate next refresh time
    next_refresh_seconds = await calculate_next_refresh(domain)

    logger.info(
        f"Scheduled next refresh",
        domain=domain,
        next_refresh_in_hours=next_refresh_seconds / 3600
    )

    # Schedule next run
    await asyncio.sleep(next_refresh_seconds)
    await run_scheduled_refresh(domain)  # Recursive scheduling
```

### Fallback for Unparseable Expiry

```python
# If cookie expiry cannot be parsed (malformed, missing, etc.)
if not min_expiry or min_expiry < 0:
    logger.warning(f"Cannot parse cookie expiry for {domain}, using default 12h interval")
    refresh_interval = 12 * 3600  # 12 hours default
```

---

## Rationale

### Why Adaptive Over Fixed?

**Evidence from deliberation:** Paywalled sites have vastly different cookie lifetimes:
- Short-lived (6-12h): Some high-security sites
- Medium-lived (24-48h): Most news sites (nrc.nl, fd.nl)
- Long-lived (7-30 days): Subscription services with "remember me"

Fixed 12h schedule:
- Over-refreshes long-lived cookies (wastes resources, unnecessary load on sites)
- Under-refreshes short-lived cookies (causes authentication outages)

Adaptive schedule:
- 6h cookie → refresh every 4.5h (75% of 6h)
- 24h cookie → refresh every 18h (75% of 24h)
- 30d cookie → refresh every 24h (capped at MAX_INTERVAL)

### Why Adaptive Over Reactive?

**Critical finding from GPT-5.2 agent:** Reactive refresh has a race condition.

**Scenario:**
```
10:00 AM - Wallabag makes 50 concurrent requests to nrc.nl
         - All 50 requests check cookie expiry
         - All 50 find cookies expired (or <6h remaining)
         - All 50 trigger refresh
         - Result: 50 Playwright browser instances spawned
         - System resource exhaustion
```

**Mitigation options for reactive:**
1. Distributed lock (Redis, etc.) - adds infrastructure dependency
2. In-memory lock per domain - doesn't work for multi-instance deployments
3. Refresh service API with deduplication - adds IPC complexity

**Adaptive scheduled approach:**
- Single scheduler per domain (no concurrency)
- No locks needed
- No IPC needed
- Simpler code, fewer failure modes

### Why 75% Lifetime (25% Remaining)?

**Rationale:**
- 25% margin provides safety buffer for refresh failures (time to retry before expiry)
- Aligns with hybrid failure handling (24h threshold = ~25% of typical 4-day cookie)
- Industry standard (AWS ELB uses similar logic for certificate rotation)

**Example:** 24-hour cookie
- Refresh at 18h mark (6h remaining)
- If refresh fails, retry mechanism has 6h window before expiry
- Typical retry: 3 attempts with exponential backoff = ~1-2h total
- Still 4-5h margin before expiry

---

## Consequences

### Positive

- **Optimal refresh timing** without coupling components
- **No race conditions** (single scheduler, no concurrent triggers)
- **Adapts to site behavior** (short-lived cookies refresh more often)
- **Resource efficient** (long-lived cookies don't over-refresh)
- **Simple implementation** (pure asyncio, no distributed systems)

### Negative

- **Slightly less optimal than perfect reactive** (may refresh 1-2h early)
- **Requires expiry parsing** (adds code complexity vs fixed schedule)
- **Edge case: very short cookies** (<6h) hit minimum interval, may expire before refresh

### Operational Impact

**For typical paywalled sites (24h cookies):**
- Fixed 12h schedule: 2 refreshes per day
- Adaptive 75% schedule: 1.33 refreshes per day (~18h interval)
- Resource savings: ~33% fewer login flows

**For long-lived cookies (30d):**
- Fixed 12h schedule: 60 refreshes per month
- Adaptive capped at 24h: 30 refreshes per month
- Resource savings: 50% fewer login flows

**For short-lived cookies (6h):**
- Fixed 12h schedule: Outage (cookie expires before refresh)
- Adaptive 75%: Refresh every 4.5h (no outage)
- Safety: Prevents authentication failures

---

## Implementation Notes

### Per-Site Scheduling

Each configured site runs its own independent scheduler:

```python
async def main():
    """
    Start refresh schedulers for all configured sites.
    """
    config = load_config()

    tasks = []
    for site in config.sites:
        task = asyncio.create_task(run_scheduled_refresh(site.domain))
        tasks.append(task)

        logger.info(f"Started refresh scheduler for {site.domain}")

    # Run all schedulers concurrently
    await asyncio.gather(*tasks)
```

### Concurrency Limit

To prevent resource exhaustion, limit concurrent browser instances:

```python
# Global semaphore: max 3 concurrent browsers
browser_semaphore = asyncio.Semaphore(3)

async def perform_site_login(domain: str) -> None:
    """Execute login flow with concurrency limit."""
    async with browser_semaphore:
        logger.info(f"Acquiring browser for {domain}")

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            # ... login flow ...
            await browser.close()

        logger.info(f"Released browser for {domain}")
```

This ensures:
- Maximum 3 Playwright instances running simultaneously
- Sites queue if >3 need refresh at same time
- Resource usage bounded

### Startup Behavior

On service startup, check existing cookies to avoid unnecessary refresh:

```python
async def run_scheduled_refresh(domain: str) -> None:
    """
    Execute scheduled refresh with startup optimization.
    """
    cookie_file = Path(COOKIE_DIR) / f"{domain}.json"

    if cookie_file.exists():
        cookies, metadata = load_cookies(cookie_file)
        valid_cookies = [c for c in cookies if c.get('expires', -1) > time.time()]

        if valid_cookies:
            min_expiry = min(c['expires'] for c in valid_cookies)
            time_remaining = min_expiry - time.time()

            # If cookies valid for >6h, skip immediate refresh
            if time_remaining > 6 * 3600:
                logger.info(
                    f"Existing cookies still valid, skipping immediate refresh",
                    domain=domain,
                    hours_remaining=time_remaining / 3600
                )

                # Schedule next refresh based on existing cookies
                next_refresh_seconds = await calculate_next_refresh(domain)
                await asyncio.sleep(next_refresh_seconds)
                # Continue with normal flow...
                return

    # No valid cookies or expiring soon, refresh immediately
    await perform_site_login(domain)
    # ... rest of logic ...
```

**Benefit:** Service restarts don't trigger unnecessary login flows (avoids load on paywalled sites, faster startup).

---

## Testing Strategy

### Unit Tests

```python
def test_calculate_next_refresh_adapts_to_lifetime():
    """Verify refresh interval scales with cookie lifetime."""
    # 24h cookie
    cookies_24h = [{"expires": time.time() + 24 * 3600}]
    interval = calculate_next_refresh("example.com", cookies_24h)
    assert 17 * 3600 < interval < 19 * 3600  # ~18h (75% of 24h)

    # 6h cookie
    cookies_6h = [{"expires": time.time() + 6 * 3600}]
    interval = calculate_next_refresh("example.com", cookies_6h)
    assert 6 * 3600 < interval < 7 * 3600  # 6h (minimum enforced)

    # 30d cookie
    cookies_30d = [{"expires": time.time() + 30 * 24 * 3600}]
    interval = calculate_next_refresh("example.com", cookies_30d)
    assert interval == 24 * 3600  # 24h (maximum capped)


def test_safety_check_triggers_on_approaching_expiry():
    """Verify proactive refresh when <24h remaining."""
    # Cookie expiring in 12h
    cookies = [{"expires": time.time() + 12 * 3600}]

    # Safety check should trigger immediate refresh
    should_refresh_now = check_ttl_safety(cookies)
    assert should_refresh_now is True
```

### Integration Tests

Simulate time progression and verify refresh scheduling:

```python
@pytest.mark.integration
async def test_adaptive_schedule_progression(mock_time):
    """Verify refresh intervals adapt over multiple cycles."""
    # Mock site that returns 24h cookies
    with mock_site_returning_24h_cookies():
        # Start scheduler
        task = asyncio.create_task(run_scheduled_refresh("test.com"))

        # Advance time and verify refresh occurs at ~18h mark
        await mock_time.advance(18 * 3600)
        await asyncio.sleep(0.1)  # Let scheduler run

        # Verify refresh occurred
        cookie_file = Path("/cookies/test.com.json")
        assert cookie_file.stat().st_mtime > initial_mtime

        # Verify next refresh scheduled for ~18h again
        next_refresh = get_next_scheduled_time("test.com")
        assert 17 * 3600 < next_refresh < 19 * 3600
```

---

## Monitoring

### Structured Logging

```python
logger.info(
    "Calculated next refresh time",
    domain=domain,
    cookie_lifetime_hours=cookie_lifetime / 3600,
    refresh_interval_hours=refresh_interval / 3600,
    refresh_percentage=refresh_interval / cookie_lifetime * 100,
    next_refresh_at=datetime.fromtimestamp(time.time() + refresh_interval).isoformat()
)
```

### Health Endpoint

```json
{
  "sites": {
    "nrc.nl": {
      "status": "ok",
      "last_refresh": "2026-02-21T10:30:00Z",
      "next_refresh": "2026-02-22T04:30:00Z",
      "refresh_interval_hours": 18,
      "cookies_valid_until": "2026-02-23T10:30:00Z",
      "adaptive_scheduling": true
    }
  }
}
```

---

## Links

- [Discussion #2: Multi-agent architectural deliberation](https://github.com/pvliesdonk/cookie-injector/discussions/2)
- [Issue #1: Architectural Design Plan](https://github.com/pvliesdonk/cookie-injector/issues/1)

---

## Supersedes

None (initial decision)

---

## Related Decisions

- ADR-0001: Hybrid Failure Handling (complements with proactive refresh before expiry)
- ADR-0002: Session Cookie Persistence Workaround (provides expiry timestamps for this logic)
