from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class UrlValidationResult:
    url: str
    is_valid: bool
    error: str | None = None


def normalize_url(raw: str) -> str:
    """Canonical form used for dedupe hashing.

    Scheme and host are lowercased and the default port is dropped, so
    `HTTPS://WWW.Example.com:443/Page` and `https://www.example.com/Page` hash to
    the same entry instead of being submitted twice. The path keeps its case
    because paths are case-sensitive.
    """
    value = (raw or "").strip()
    if not value:
        return ""

    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    netloc = host
    if parsed.port is not None and parsed.port != DEFAULT_PORTS.get(parsed.scheme.lower()):
        netloc = f"{host}:{parsed.port}"

    return urlunparse(
        (parsed.scheme.lower(), netloc, parsed.path, parsed.params, parsed.query, "")
    )


def validate_project_url(url: str, project_host: str) -> UrlValidationResult:
    normalized = normalize_url(url)
    if not normalized:
        return UrlValidationResult(url=url, is_valid=False, error="Empty URL")

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        return UrlValidationResult(url=normalized, is_valid=False, error="URL must use http/https")
    if not parsed.netloc:
        return UrlValidationResult(url=normalized, is_valid=False, error="URL must include host")

    host = (parsed.hostname or "").lower()
    expected = (project_host or "").lower()
    if host != expected:
        # IndexNow rejects the whole batch on a host mismatch, so catch it here.
        return UrlValidationResult(
            url=normalized,
            is_valid=False,
            error=f"Host mismatch: expected '{expected}', got '{host}'",
        )
    return UrlValidationResult(url=normalized, is_valid=True)
