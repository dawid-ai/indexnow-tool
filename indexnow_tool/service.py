from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .config import AppConfig, ProjectConfig
from .db import Database
from .indexnow_client import chunked, submit_url_batch
from .normalize import validate_project_url
from .sources import parse_csv_bytes, parse_paste, parse_sitemap_url, parse_txt_bytes


@dataclass(frozen=True)
class RunSummary:
    run_id: int
    submitted_count: int
    accepted_count: int
    failed_count: int
    skipped_existing_count: int
    invalid_count: int
    invalid_details: list[str]


class IndexNowService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db = Database(config.db_path)

    def get_project(self, name: str) -> ProjectConfig:
        if name not in self.config.projects:
            raise ValueError(f"Unknown project '{name}'")
        return self.config.projects[name]

    def resolve_endpoint(self, project: ProjectConfig, selected: str | None) -> str:
        if selected in {"indexnow", "bing"}:
            return selected
        return project.default_endpoint or self.config.default_endpoint

    def load_source_urls(
        self,
        source_type: str,
        *,
        sitemap_url: str | None = None,
        file_bytes: bytes | None = None,
        pasted_urls: str | None = None,
    ) -> tuple[list[str], str | None]:
        if source_type == "sitemap":
            if not sitemap_url:
                raise ValueError("Sitemap source requires sitemap URL")
            return parse_sitemap_url(sitemap_url), sitemap_url
        if source_type == "txt":
            if file_bytes is None:
                raise ValueError("TXT source requires file content")
            return parse_txt_bytes(file_bytes), "uploaded.txt"
        if source_type == "csv":
            if file_bytes is None:
                raise ValueError("CSV source requires file content")
            return parse_csv_bytes(file_bytes), "uploaded.csv"
        if source_type == "paste":
            if not pasted_urls:
                raise ValueError("Paste source requires input text")
            return parse_paste(pasted_urls), "pasted"
        raise ValueError(f"Unsupported source '{source_type}'")

    def _validate_urls(self, urls: Iterable[str], host: str) -> tuple[list[str], list[str]]:
        valid: list[str] = []
        invalid: list[str] = []
        seen = set()
        for url in urls:
            result = validate_project_url(url, host)
            if not result.is_valid:
                invalid.append(f"{result.url} :: {result.error}")
                continue
            if result.url in seen:
                continue
            seen.add(result.url)
            valid.append(result.url)
        return valid, invalid

    def run_from_source(
        self,
        project_name: str,
        source_type: str,
        endpoint: str | None = None,
        *,
        sitemap_url: str | None = None,
        file_bytes: bytes | None = None,
        pasted_urls: str | None = None,
    ) -> RunSummary:
        project = self.get_project(project_name)
        resolved_endpoint = self.resolve_endpoint(project, endpoint)

        source_urls, source_ref = self.load_source_urls(
            source_type,
            sitemap_url=sitemap_url,
            file_bytes=file_bytes,
            pasted_urls=pasted_urls,
        )
        valid_urls, invalid_details = self._validate_urls(source_urls, project.host)

        inserted_ids, skipped_existing = self.db.upsert_urls(
            project_name, source_type, source_ref, valid_urls
        )
        entries = self.db.get_entries_by_status(project_name, ["new"], inserted_ids)
        run_id, accepted_count, failed_count = self._submit_entries(
            project_name=project_name,
            endpoint=resolved_endpoint,
            source_type=source_type,
            source_ref=source_ref,
            entries=entries,
            project=project,
            skipped_existing=skipped_existing,
        )

        return RunSummary(
            run_id=run_id,
            submitted_count=len(entries),
            accepted_count=accepted_count,
            failed_count=failed_count,
            skipped_existing_count=skipped_existing,
            invalid_count=len(invalid_details),
            invalid_details=invalid_details,
        )

    def _submit_entries(
        self,
        *,
        project_name: str,
        endpoint: str,
        source_type: str,
        source_ref: str | None,
        entries: Sequence,
        project: ProjectConfig,
        skipped_existing: int,
    ) -> tuple[int, int, int]:
        accepted = 0
        failed = 0
        run_items: list[tuple[int, int | None, str | None, str]] = []
        entry_lookup = {entry["url"]: entry for entry in entries}
        urls = [entry["url"] for entry in entries]

        for url_batch in chunked(urls):
            result = submit_url_batch(
                endpoint_choice=endpoint,
                host=project.host,
                key=project.key,
                key_location=project.key_location,
                urls=url_batch,
            )
            ids = [int(entry_lookup[url]["id"]) for url in url_batch]
            if result.is_success:
                self.db.mark_entries(
                    ids,
                    status="submitted",
                    http_code=result.status_code,
                    response_excerpt=result.response_excerpt,
                    endpoint=endpoint,
                    increment_attempts=True,
                )
                accepted += len(ids)
                run_items.extend((entry_id, result.status_code, result.response_excerpt, "accepted") for entry_id in ids)
            else:
                self.db.mark_entries(
                    ids,
                    status="failed",
                    http_code=result.status_code,
                    response_excerpt=result.response_excerpt,
                    endpoint=endpoint,
                    increment_attempts=True,
                )
                failed += len(ids)
                run_items.extend((entry_id, result.status_code, result.response_excerpt, "failed") for entry_id in ids)

        run_id = self.db.create_run(
            project_name=project_name,
            endpoint=endpoint,
            source_type=source_type,
            source_ref=source_ref,
            submitted_count=len(entries),
            accepted_count=accepted,
            failed_count=failed,
            skipped_existing_count=skipped_existing,
        )
        if run_items:
            self.db.add_run_items(run_id, run_items)
        return run_id, accepted, failed

    def retry_failed(
        self, project_name: str, endpoint: str | None = None, entry_ids: Sequence[int] | None = None
    ) -> RunSummary:
        project = self.get_project(project_name)
        resolved_endpoint = self.resolve_endpoint(project, endpoint)
        entries = self.db.get_entries_by_status(project_name, ["failed"], entry_ids)
        run_id, accepted_count, failed_count = self._submit_entries(
            project_name=project_name,
            endpoint=resolved_endpoint,
            source_type="retry-failed",
            source_ref="manual",
            entries=entries,
            project=project,
            skipped_existing=0,
        )
        return RunSummary(
            run_id=run_id,
            submitted_count=len(entries),
            accepted_count=accepted_count,
            failed_count=failed_count,
            skipped_existing_count=0,
            invalid_count=0,
            invalid_details=[],
        )

    def mark_failed_success(
        self, project_name: str, entry_ids: Sequence[int] | None = None
    ) -> int:
        failed_entries = self.db.get_entries_by_status(project_name, ["failed"], entry_ids)
        ids = [int(item["id"]) for item in failed_entries]
        return self.db.mark_manual_success(ids)
