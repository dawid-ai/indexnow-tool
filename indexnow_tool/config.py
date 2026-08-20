from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

KEY_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,128}$")
ENDPOINT_CHOICES = ("indexnow", "bing")


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    host: str
    key: str
    key_location: str | None
    sitemap_url: str | None
    default_endpoint: str

    @classmethod
    def from_row(cls, row) -> "ProjectConfig":
        return cls(
            name=row["name"],
            host=row["host"],
            key=row["key"],
            key_location=row["key_location"],
            sitemap_url=row["sitemap_url"],
            default_endpoint=row["default_endpoint"],
        )


@dataclass(frozen=True)
class AppConfig:
    db_path: Path
    default_endpoint: str
    default_ui_port: int


def clean_endpoint(value: str | None, fallback: str = "indexnow") -> str:
    candidate = (value or "").strip().lower()
    return candidate if candidate in ENDPOINT_CHOICES else fallback


def normalize_host(raw: str) -> str:
    """Accept a bare host or a pasted URL and return the bare lowercase host."""
    value = (raw or "").strip().lower().rstrip("/")
    if "//" in value:
        value = urlparse(value).netloc or value
    return value.split("/")[0].strip()


def load_config() -> AppConfig:
    load_dotenv()
    return AppConfig(
        db_path=Path(os.getenv("DB_PATH", "data/indexnow.db")),
        default_endpoint=clean_endpoint(os.getenv("DEFAULT_ENDPOINT"), "indexnow"),
        default_ui_port=int(os.getenv("DEFAULT_UI_PORT", "8787")),
    )


def validate_project_fields(
    name: str, host: str, key: str, key_location: str | None, sitemap_url: str | None
) -> list[str]:
    """Return human-readable problems with a project definition, empty if valid."""
    errors: list[str] = []

    if not name.strip():
        errors.append("Name is required.")
    elif not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name.strip()):
        errors.append("Name may only contain letters, numbers, dot, dash, underscore (max 64).")

    if not host.strip():
        errors.append("Host is required, for example www.example.com")
    elif "." not in normalize_host(host):
        errors.append(f"Host '{host}' does not look like a domain.")

    if not key.strip():
        errors.append("Key is required.")
    elif not KEY_PATTERN.fullmatch(key.strip()):
        errors.append("Key must be 8-128 characters using only A-Z a-z 0-9 and dash.")

    for label, value in (("Key location", key_location), ("Sitemap URL", sitemap_url)):
        if value and value.strip():
            parsed = urlparse(value.strip())
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append(f"{label} must be a full http(s) URL.")

    return errors


def import_projects_from_env(db) -> list[str]:
    """Seed the projects table from legacy PROJECTS/PROJECT_<NAME>_* variables.

    Runs on every startup but only adds names that do not exist yet, so the .env
    file stays a valid source without ever overwriting edits made in the UI.
    """
    load_dotenv()
    raw_names = [p.strip() for p in os.getenv("PROJECTS", "").split(",") if p.strip()]
    if not raw_names:
        return []

    existing = {row["name"] for row in db.list_projects()}
    imported: list[str] = []
    fallback_endpoint = clean_endpoint(os.getenv("DEFAULT_ENDPOINT"), "indexnow")

    for name in raw_names:
        if name in existing:
            continue
        prefix = f"PROJECT_{name.upper()}_"
        host = normalize_host(os.getenv(prefix + "HOST", ""))
        key = os.getenv(prefix + "KEY", "").strip()
        if not host or not key:
            continue
        if validate_project_fields(name, host, key, None, None):
            continue

        db.upsert_project(
            name=name,
            host=host,
            key=key,
            key_location=(os.getenv(prefix + "KEY_LOCATION", "").strip() or None),
            sitemap_url=(os.getenv(prefix + "SITEMAP_URL", "").strip() or None),
            default_endpoint=clean_endpoint(
                os.getenv(prefix + "DEFAULT_ENDPOINT"), fallback_endpoint
            ),
        )
        imported.append(name)

    return imported
