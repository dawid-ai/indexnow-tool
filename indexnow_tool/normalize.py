from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class UrlValidationResult:
    url: str
    is_valid: bool
    error: str | None = None


def normalize_url(raw: str) -> str:
    value = (raw or "").strip()
    parsed = urlparse(value)
    normalized = parsed._replace(fragment="")
    return urlunparse(normalized)


def validate_project_url(url: str, project_host: str) -> UrlValidationResult:
    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        return UrlValidationResult(url=normalized, is_valid=False, error="URL must use http/https")
    if not parsed.netloc:
        return UrlValidationResult(url=normalized, is_valid=False, error="URL must include host")

    host = (parsed.hostname or "").lower()
    if host != project_host.lower():
        return UrlValidationResult(
            url=normalized,
            is_valid=False,
            error=f"Host mismatch: expected '{project_host}', got '{host}'",
        )
    return UrlValidationResult(url=normalized, is_valid=True)
