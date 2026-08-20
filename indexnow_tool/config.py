from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv

KEY_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,128}$")


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    host: str
    key: str
    key_location: str | None
    sitemap_url: str | None
    default_endpoint: str


@dataclass(frozen=True)
class AppConfig:
    projects: Dict[str, ProjectConfig]
    db_path: Path
    default_endpoint: str
    default_ui_port: int


def _env_name(project_name: str, suffix: str) -> str:
    return f"PROJECT_{project_name.upper()}_{suffix}"


def _clean_endpoint(value: str | None, fallback: str) -> str:
    candidate = (value or fallback).strip().lower()
    if candidate not in {"indexnow", "bing"}:
        return fallback
    return candidate


def load_config() -> AppConfig:
    load_dotenv()

    default_endpoint = _clean_endpoint(os.getenv("DEFAULT_ENDPOINT"), "indexnow")
    db_path = Path(os.getenv("DB_PATH", "data/indexnow.db"))
    default_ui_port = int(os.getenv("DEFAULT_UI_PORT", "8787"))

    project_names = [p.strip() for p in os.getenv("PROJECTS", "").split(",") if p.strip()]
    projects: Dict[str, ProjectConfig] = {}

    for project_name in project_names:
        host = os.getenv(_env_name(project_name, "HOST"), "").strip().lower()
        key = os.getenv(_env_name(project_name, "KEY"), "").strip()
        key_location = os.getenv(_env_name(project_name, "KEY_LOCATION"), "").strip() or None
        sitemap_url = os.getenv(_env_name(project_name, "SITEMAP_URL"), "").strip() or None
        project_endpoint = _clean_endpoint(
            os.getenv(_env_name(project_name, "DEFAULT_ENDPOINT")), default_endpoint
        )

        if not host:
            raise ValueError(f"Missing host for project '{project_name}'")
        if not key:
            raise ValueError(f"Missing key for project '{project_name}'")
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError(
                f"Invalid key format for project '{project_name}'. "
                "Expected 8-128 chars: A-Z a-z 0-9 -"
            )

        projects[project_name] = ProjectConfig(
            name=project_name,
            host=host,
            key=key,
            key_location=key_location,
            sitemap_url=sitemap_url,
            default_endpoint=project_endpoint,
        )

    if not projects:
        raise ValueError("No projects configured. Add PROJECTS and PROJECT_<NAME>_* variables to .env")

    return AppConfig(
        projects=projects,
        db_path=db_path,
        default_endpoint=default_endpoint,
        default_ui_port=default_ui_port,
    )
