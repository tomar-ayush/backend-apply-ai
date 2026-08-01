"""Centralized validators reused across schemas, services, and models.

Keep domain-agnostic validation here (URLs, enums, etc.).
Business rules (e.g. "job must be parsed before generating referrals")
belong in services.
"""

import re
from urllib.parse import urlparse

from pydantic import HttpUrl


# ---------------------------------------------------------------------------
# URL validators
# ---------------------------------------------------------------------------

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def is_http_url(value: str) -> bool:
    """Return True if *value* starts with http:// or https://."""
    return bool(_URL_SCHEME_RE.match(value))


def normalize_linkedin_url(url: str) -> str:
    """Normalize a LinkedIn profile URL to the canonical www form.

    Strips country-code subdomains and ensures a single trailing slash.
    """
    if not url:
        return url
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host.endswith("linkedin.com"):
        host = "www.linkedin.com"
    path = parsed.path
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path = path + "/"
    return f"https://{host}{path}"


# ---------------------------------------------------------------------------
# String validators
# ---------------------------------------------------------------------------


def nonempty_string(value: str | None, label: str = "value") -> str:
    """Strip and return *value* or raise ValueError if empty/None."""
    if value is None:
        raise ValueError(f"{label} is required")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} cannot be empty")
    return cleaned
