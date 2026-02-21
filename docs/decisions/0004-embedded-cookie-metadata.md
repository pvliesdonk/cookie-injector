# ADR-0004: Embedded Cookie Metadata Format

**Status:** Accepted

**Date:** 2026-02-21

**Decided by:** Multi-agent architectural deliberation (Discussion #2)

**Decision makers:** Gemini 3 Pro, GPT-5.2, Claude Opus 4.6, pvliesdonk

---

## Context

The cookie storage system must track operational metadata alongside cookie data (refresh timestamps, source, status, etc.) for debugging and monitoring. Two approaches were debated:

### Option 1: Separate Metadata Files
**Approach:** Store cookies in `domain.json` and metadata in `domain.json.meta`

**Pros:**
- Clear separation of concerns (data vs metadata)
- Cookies file remains "pure" (standard Playwright format)
- Easy to add new metadata fields without touching cookie data

**Cons:**
- **No atomic writes** - two files must be updated, race condition possible
- **Consistency risk** - metadata and cookies can become desynchronized
- **More complex code** - must open/write/sync two files per refresh
- **File proliferation** - doubles number of files in cookies directory

### Option 2: Embedded Metadata (Selected)
**Approach:** Store cookies and metadata in single JSON file

**Pros:**
- **Atomic writes** - single file update with atomic rename
- **Guaranteed consistency** - metadata always matches cookie data
- **Simpler code** - one file read/write operation
- **Self-documenting** - metadata travels with cookies
- **Easier backup** - single file per domain

**Cons:**
- Slight deviation from "pure" Playwright cookie format
- Metadata must be filtered when passing cookies to Playwright

---

## Decision

Store cookies and metadata in a **single JSON file** with the following schema:

### File Format

```json
{
  "cookies": [
    {
      "name": "session_id",
      "value": "abc123xyz",
      "domain": ".nrc.nl",
      "path": "/",
      "expires": 1780000000,
      "httpOnly": true,
      "secure": true,
      "sameSite": "Lax"
    },
    {
      "name": "csrf_token",
      "value": "def456uvw",
      "domain": ".nrc.nl",
      "path": "/",
      "expires": 1780000000,
      "httpOnly": false,
      "secure": true,
      "sameSite": "Strict"
    }
  ],
  "metadata": {
    "refreshed_at": "2026-02-21T10:30:00Z",
    "refresh_source": "scheduled",
    "next_refresh": "2026-02-22T04:30:00Z",
    "site_config": "nrc",
    "playwright_version": "1.50.0",
    "session_cookie_workaround": true,
    "refresh_attempt": 1,
    "last_error": null
  }
}
```

### Schema Specification

#### Required Fields

**`cookies`** (array of objects):
- Standard RFC 6265 / Playwright cookie format
- Each cookie object contains:
  - `name` (string): Cookie name
  - `value` (string): Cookie value
  - `domain` (string): Cookie domain (e.g., `.nrc.nl`)
  - `path` (string): Cookie path (usually `/`)
  - `expires` (number): Unix timestamp, -1 for session cookies (see ADR-0002 workaround)
  - `httpOnly` (boolean): HttpOnly flag
  - `secure` (boolean): Secure flag
  - `sameSite` (string): SameSite attribute (`Strict`, `Lax`, `None`)

**`metadata`** (object):
- `refreshed_at` (ISO 8601 string): When cookies were last refreshed
- `refresh_source` (string): How refresh was triggered (`scheduled`, `manual`, `startup`)
- `site_config` (string): Site identifier from config (e.g., `nrc`, `fd`)

#### Optional Fields (Metadata)

- `next_refresh` (ISO 8601 string): When next refresh is scheduled (for monitoring)
- `playwright_version` (string): Playwright version used for refresh (debugging)
- `session_cookie_workaround` (boolean): Whether ADR-0002 workaround was applied
- `refresh_attempt` (number): Number of attempts for this refresh (for retry tracking)
- `last_error` (string | null): Error message from last failed refresh attempt
- `cookies_count` (number): Total number of cookies (convenience for monitoring)
- `session_cookies_converted` (number): How many session cookies got explicit expiry

#### Future Extensions

Easy to add without schema migration:
- `refresh_duration_ms` (number): How long login flow took
- `user_agent` (string): User agent used during refresh
- `screenshot_path` (string): Path to debug screenshot on failure
- `http_status_codes` (array): HTTP status codes encountered during login

---

## Rationale

### Atomic Writes Eliminate Race Conditions

**Scenario with separate files:**

```python
# Refresh service writes cookies
with open("nrc.nl.json", "w") as f:
    json.dump(cookies, f)
    f.flush()
    os.fsync(f.fileno())

# ← CRASH HERE: metadata file not written yet

with open("nrc.nl.json.meta", "w") as f:
    json.dump(metadata, f)
    f.flush()
    os.fsync(f.fileno())
```

**Result:** Proxy reads cookies but metadata is missing or stale. Cannot determine when cookies were refreshed, when they expire, or whether session cookie workaround was applied.

**Scenario with embedded metadata:**

```python
# Write to temporary file
with open("nrc.nl.json.tmp", "w") as f:
    json.dump({"cookies": cookies, "metadata": metadata}, f)
    f.flush()
    os.fsync(f.fileno())

# Atomic rename (POSIX guarantees atomicity)
os.rename("nrc.nl.json.tmp", "nrc.nl.json")

# ← CRASH HERE: no problem, rename already succeeded
```

**Result:** Proxy always reads consistent cookies + metadata, or sees old file (before refresh). No partial state possible.

### Simplicity

**Code comparison:**

<table>
<tr>
<th>Separate Files</th>
<th>Embedded Metadata</th>
</tr>
<tr>
<td>

```python
# Write
with open(f"{domain}.json", "w") as f:
    json.dump(cookies, f)
    f.flush()
    os.fsync(f.fileno())

with open(f"{domain}.json.meta", "w") as f:
    json.dump(metadata, f)
    f.flush()
    os.fsync(f.fileno())

# Read
with open(f"{domain}.json") as f:
    cookies = json.load(f)

try:
    with open(f"{domain}.json.meta") as f:
        metadata = json.load(f)
except FileNotFoundError:
    metadata = {}  # Handle missing metadata
```

</td>
<td>

```python
# Write
data = {"cookies": cookies, "metadata": metadata}
tmp_path = f"{domain}.json.tmp"

with open(tmp_path, "w") as f:
    json.dump(data, f)
    f.flush()
    os.fsync(f.fileno())

os.rename(tmp_path, f"{domain}.json")

# Read
with open(f"{domain}.json") as f:
    data = json.load(f)

cookies = data["cookies"]
metadata = data.get("metadata", {})
```

</td>
</tr>
</table>

**Winner:** Embedded metadata - 30% less code, guaranteed consistency.

### Backward Compatibility with Playwright

The embedded format is easily converted to Playwright's expected format:

```python
# Load from file
with open("nrc.nl.json") as f:
    data = json.load(f)

# Pass to Playwright (just extract cookies array)
await context.add_cookies(data["cookies"])

# Or save from Playwright
cookies = await context.cookies()
data = {
    "cookies": cookies,
    "metadata": {
        "refreshed_at": datetime.utcnow().isoformat() + "Z",
        "refresh_source": "scheduled"
    }
}
```

**No impedance mismatch** - cookies array is standard Playwright format.

---

## Consequences

### Positive

- **Atomic operations** - no race conditions between cookies and metadata
- **Guaranteed consistency** - metadata always matches cookie data
- **Simpler code** - single file read/write, 30% less code
- **Self-documenting** - operators can inspect single file to see all info
- **Easier backup** - single file per domain, simpler restore process
- **Future-proof** - easy to add new metadata fields without migration

### Negative

- **Slight format deviation** - not pure Playwright format (requires extracting `cookies` array)
- **File size** - metadata adds ~100-200 bytes per file (negligible)
- **Schema validation** - must validate both cookies and metadata sections

### Operational Impact

**Debugging is simpler:**

```bash
# Single file shows everything
$ cat /cookies/nrc.nl.json
{
  "cookies": [...],
  "metadata": {
    "refreshed_at": "2026-02-21T10:30:00Z",
    "last_error": null,
    "next_refresh": "2026-02-22T04:30:00Z"
  }
}

# vs separate files (must check both)
$ cat /cookies/nrc.nl.json      # Just cookies
$ cat /cookies/nrc.nl.json.meta # Must also check this
```

**Backup/restore is simpler:**

```bash
# Embedded: backup single file
cp /cookies/nrc.nl.json /backup/

# Separate: must backup both
cp /cookies/nrc.nl.json /backup/
cp /cookies/nrc.nl.json.meta /backup/  # Easy to forget!
```

---

## Implementation

### Writing Cookies with Metadata

```python
from pathlib import Path
import json
import time
import os
from datetime import datetime

def save_cookies_with_metadata(
    domain: str,
    cookies: list[dict],
    refresh_source: str = "scheduled",
    **extra_metadata
) -> None:
    """
    Save cookies and metadata atomically to domain.json file.
    
    Args:
        domain: Canonical domain (e.g., "nrc.nl")
        cookies: List of cookie dictionaries (Playwright format)
        refresh_source: How refresh was triggered
        **extra_metadata: Additional metadata fields
    """
    cookie_file = Path(COOKIE_DIR) / f"{domain}.json"
    tmp_file = cookie_file.with_suffix(".json.tmp")
    
    # Build metadata
    metadata = {
        "refreshed_at": datetime.utcnow().isoformat() + "Z",
        "refresh_source": refresh_source,
        "site_config": domain,
        "cookies_count": len(cookies),
        "session_cookies_converted": sum(
            1 for c in cookies if c.get("expires", -1) > time.time() + 30 * 24 * 3600
        ),
        **extra_metadata
    }
    
    # Combine cookies and metadata
    data = {
        "cookies": cookies,
        "metadata": metadata
    }
    
    # Write to temp file
    with open(tmp_file, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    
    # Atomic rename
    tmp_file.rename(cookie_file)
    
    logger.info(
        f"Saved cookies with metadata",
        domain=domain,
        cookies_count=len(cookies),
        file_size_bytes=cookie_file.stat().st_size
    )
```

### Reading Cookies with Metadata

```python
def load_cookies_with_metadata(domain: str) -> tuple[list[dict], dict]:
    """
    Load cookies and metadata from domain.json file.
    
    Returns:
        (cookies, metadata) tuple
        
    Raises:
        FileNotFoundError: If cookie file doesn't exist
        ValueError: If file format is invalid
    """
    cookie_file = Path(COOKIE_DIR) / f"{domain}.json"
    
    if not cookie_file.exists():
        raise FileNotFoundError(f"No cookies for domain: {domain}")
    
    with open(cookie_file) as f:
        data = json.load(f)
    
    # Validate schema
    if "cookies" not in data:
        raise ValueError(f"Invalid cookie file format (missing 'cookies' key): {domain}")
    
    cookies = data["cookies"]
    metadata = data.get("metadata", {})
    
    # Validate cookie structure
    for cookie in cookies:
        required_fields = ["name", "value", "domain"]
        if not all(field in cookie for field in required_fields):
            raise ValueError(f"Invalid cookie structure in {domain}")
    
    return cookies, metadata
```

### Backward Compatibility Helper

For migration from pure Playwright format (if any early adopters exist):

```python
def migrate_legacy_format(domain: str) -> None:
    """
    Migrate legacy pure-Playwright format to embedded metadata format.
    """
    cookie_file = Path(COOKIE_DIR) / f"{domain}.json"
    
    with open(cookie_file) as f:
        data = json.load(f)
    
    # Detect legacy format (array at top level)
    if isinstance(data, list):
        logger.info(f"Migrating legacy format for {domain}")
        
        cookies = data
        metadata = {
            "refreshed_at": datetime.fromtimestamp(
                cookie_file.stat().st_mtime
            ).isoformat() + "Z",
            "refresh_source": "migrated",
            "site_config": domain,
            "migrated_from_legacy": True
        }
        
        # Rewrite in new format
        save_cookies_with_metadata(domain, cookies, "migrated", **metadata)
```

---

## Validation

### Schema Validation with Pydantic

```python
from pydantic import BaseModel, Field
from typing import Literal

class Cookie(BaseModel):
    """RFC 6265 cookie with Playwright extensions."""
    name: str
    value: str
    domain: str
    path: str = "/"
    expires: int | float  # Unix timestamp, -1 for session
    httpOnly: bool = False
    secure: bool = False
    sameSite: Literal["Strict", "Lax", "None"] = "Lax"


class CookieMetadata(BaseModel):
    """Operational metadata for cookie storage."""
    refreshed_at: str  # ISO 8601
    refresh_source: Literal["scheduled", "manual", "startup", "migrated"]
    site_config: str
    cookies_count: int = Field(ge=0)
    
    # Optional fields
    next_refresh: str | None = None
    playwright_version: str | None = None
    session_cookie_workaround: bool = False
    refresh_attempt: int = Field(default=1, ge=1)
    last_error: str | None = None


class CookieStorage(BaseModel):
    """Complete cookie storage format."""
    cookies: list[Cookie]
    metadata: CookieMetadata


# Usage
def load_cookies_validated(domain: str) -> CookieStorage:
    """Load and validate cookies against schema."""
    cookie_file = Path(COOKIE_DIR) / f"{domain}.json"
    
    with open(cookie_file) as f:
        data = json.load(f)
    
    # Pydantic validation
    try:
        return CookieStorage(**data)
    except ValidationError as e:
        logger.error(f"Invalid cookie file format: {domain}", error=str(e))
        raise ValueError(f"Schema validation failed for {domain}") from e
```

---

## Monitoring

### Health Endpoint

```json
{
  "sites": {
    "nrc.nl": {
      "status": "ok",
      "last_refresh": "2026-02-21T10:30:00Z",
      "next_refresh": "2026-02-22T04:30:00Z",
      "cookies_count": 5,
      "file_size_bytes": 1247,
      "metadata_embedded": true
    }
  }
}
```

### Structured Logging

```python
logger.info(
    "Loaded cookies",
    domain=domain,
    cookies_count=len(cookies),
    refreshed_at=metadata.get("refreshed_at"),
    next_refresh=metadata.get("next_refresh"),
    session_cookie_workaround=metadata.get("session_cookie_workaround", False)
)
```

---

## Links

- [Discussion #2: Multi-agent architectural deliberation](https://github.com/pvliesdonk/cookie-injector/discussions/2)
- [RFC 6265: HTTP State Management Mechanism](https://datatracker.ietf.org/doc/html/rfc6265)
- [Playwright Cookie API](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-cookies)

---

## Supersedes

None (initial decision)

---

## Related Decisions

- ADR-0002: Session Cookie Persistence Workaround (uses `session_cookie_workaround` metadata flag)
- ADR-0003: Adaptive Scheduled Refresh (uses `next_refresh` metadata for monitoring)
- ADR-0001: Hybrid Failure Handling (reads metadata for debug info in 502 responses)
