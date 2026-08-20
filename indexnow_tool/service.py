from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .config import AppConfig, ProjectConfig, import_projects_from_env
from .db import Database
from .indexnow_client import chunked, submit_url_batch, verify_key_file
from .normalize import validate_project_url
from .sources import SourceError, parse_csv_bytes, parse_paste, parse_sitemap_url, parse_txt_bytes

# One POST may legally carry 10,000 URLs, but smaller batches keep the progress
# bar moving and stop a single rejected batch from failing tens of thousands of
# URLs at once.
SUBMIT_BATCH = 1000

# An invalid-URL list of 50k lines helps nobody; log a readable sample.
MAX_LOGGED_INVALID = 50


@dataclass(frozen=True)
class RunRequest:
    project_name: str
    source_type: str
    endpoint: str | None = None
    sitemap_url: str | None = None
    file_bytes: bytes | None = None
    pasted_urls: str | None = None
    force: bool = False
    label: str | None = None


@dataclass
class RunSummary:
    run_id: int
    status: str = "running"
    submitted_count: int = 0
    accepted_count: int = 0
    failed_count: int = 0
    skipped_existing_count: int = 0
    invalid_count: int = 0
    error_message: str | None = None
    invalid_details: list[str] = field(default_factory=list)


class IndexNowService:
    def __init__(self, config: AppConfig, db: Database | None = None) -> None:
        self.config = config
        self.db = db or Database(config.db_path)
        import_projects_from_env(self.db)

    # ---------------------------------------------------------------- projects

    def projects(self) -> dict[str, ProjectConfig]:
        return {row["name"]: ProjectConfig.from_row(row) for row in self.db.list_projects()}

    def get_project(self, name: str) -> ProjectConfig:
        row = self.db.get_project(name)
        if row is None:
            raise ValueError(f"Unknown project '{name}'. Add it on the Projects page first.")
        return ProjectConfig.from_row(row)

    def resolve_endpoint(self, project: ProjectConfig, selected: str | None) -> str:
        if selected in {"indexnow", "bing"}:
            return selected
        return project.default_endpoint or self.config.default_endpoint

    def verify_project_key(self, name: str) -> tuple[bool, str]:
        project = self.get_project(name)
        return verify_key_file(project.host, project.key, project.key_location)

    # -------------------------------------------------------------- URL loading

    def load_source_urls(
        self, request: RunRequest, project: ProjectConfig, on_progress=None
    ) -> tuple[list[str], str | None]:
        source_type = request.source_type

        if source_type == "sitemap":
            sitemap_url = (request.sitemap_url or "").strip() or project.sitemap_url
            if not sitemap_url:
                raise SourceError(
                    "Sitemap source needs a sitemap URL, either in the form or on the project."
                )
            return parse_sitemap_url(sitemap_url, on_progress=on_progress), sitemap_url

        if source_type == "txt":
            if not request.file_bytes:
                raise SourceError("TXT source needs an uploaded file with content.")
            return parse_txt_bytes(request.file_bytes), request.label or "uploaded.txt"

        if source_type == "csv":
            if not request.file_bytes:
                raise SourceError("CSV source needs an uploaded file with content.")
            return parse_csv_bytes(request.file_bytes), request.label or "uploaded.csv"

        if source_type == "paste":
            if not (request.pasted_urls or "").strip():
                raise SourceError("Paste source needs at least one URL.")
            return parse_paste(request.pasted_urls), "pasted"

        raise SourceError(f"Unsupported source '{source_type}'.")

    def _validate_urls(self, urls: Iterable[str], host: str) -> tuple[list[str], list[str]]:
        valid: list[str] = []
        invalid: list[str] = []
        seen: set[str] = set()
        for url in urls:
            result = validate_project_url(url, host)
            if not result.is_valid:
                invalid.append(f"{result.url or url} :: {result.error}")
                continue
            if result.url in seen:
                continue
            seen.add(result.url)
            valid.append(result.url)
        return valid, invalid

    # ------------------------------------------------------------------- runs

    def start_run(self, request: RunRequest) -> int:
        """Create the run, execute it on a background thread, return the run id."""
        project = self.get_project(request.project_name)
        endpoint = self.resolve_endpoint(project, request.endpoint)
        run_id = self.db.create_run(
            project.name, endpoint, request.source_type, request.sitemap_url
        )
        thread = threading.Thread(
            target=self._execute_source_run,
            args=(run_id, request, project, endpoint),
            daemon=True,
            name=f"indexnow-run-{run_id}",
        )
        thread.start()
        return run_id

    def run_from_source(self, request: RunRequest) -> RunSummary:
        """Create and execute a run inline. Used by the CLI."""
        project = self.get_project(request.project_name)
        endpoint = self.resolve_endpoint(project, request.endpoint)
        run_id = self.db.create_run(
            project.name, endpoint, request.source_type, request.sitemap_url
        )
        self._execute_source_run(run_id, request, project, endpoint)
        return self.summary(run_id)

    def _execute_source_run(
        self, run_id: int, request: RunRequest, project: ProjectConfig, endpoint: str
    ) -> None:
        log = lambda level, message: self.db.add_run_message(run_id, level, message)
        try:
            self.db.update_run(run_id, phase="reading source")
            log("info", f"Reading {request.source_type} source for project '{project.name}'.")

            source_urls, source_ref = self.load_source_urls(
                request, project, on_progress=lambda msg: log("info", msg)
            )
            self.db.update_run(run_id, source_ref=source_ref)
            log("info", f"Source returned {len(source_urls)} URLs.")

            self.db.update_run(run_id, phase="validating")
            valid_urls, invalid_details = self._validate_urls(source_urls, project.host)
            self.db.update_run(run_id, invalid_count=len(invalid_details))
            for detail in invalid_details[:MAX_LOGGED_INVALID]:
                log("warning", f"Skipped invalid URL: {detail}")
            if len(invalid_details) > MAX_LOGGED_INVALID:
                log("warning", f"...and {len(invalid_details) - MAX_LOGGED_INVALID} more invalid URLs.")

            self.db.update_run(run_id, phase="deduplicating")
            entry_ids, skipped = self.db.stage_urls(
                project.name, request.source_type, source_ref, valid_urls, force=request.force
            )
            self.db.update_run(run_id, skipped_existing_count=skipped)
            if skipped:
                log(
                    "info",
                    f"Skipped {skipped} URLs already accepted by the API. "
                    "Tick 'Resubmit known URLs' to send them anyway.",
                )
            if request.force:
                log("info", "Force mode on: previously accepted URLs are being resubmitted.")

            entries = self.db.get_entries_by_ids(entry_ids)
            self._submit_entries(run_id, project, endpoint, entries)
            self.db.finish_run(run_id, "completed")
            log("info", "Run finished.")

        except SourceError as exc:
            self._fail_run(run_id, str(exc))
        except ValueError as exc:
            self._fail_run(run_id, str(exc))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            self._fail_run(run_id, f"{type(exc).__name__}: {exc}")

    def _fail_run(self, run_id: int, message: str) -> None:
        self.db.add_run_message(run_id, "error", message)
        self.db.finish_run(run_id, "failed", message)

    def _submit_entries(
        self,
        run_id: int,
        project: ProjectConfig,
        endpoint: str,
        entries: Sequence,
    ) -> None:
        total = len(entries)
        self.db.update_run(
            run_id, phase="submitting", total_urls=total, submitted_count=total, processed_urls=0
        )

        if total == 0:
            self.db.add_run_message(run_id, "info", "Nothing to submit: no new URLs in this source.")
            return

        self.db.add_run_message(
            run_id, "info", f"Submitting {total} URLs to the {endpoint} endpoint."
        )

        entry_lookup = {entry["url"]: int(entry["id"]) for entry in entries}
        urls = [entry["url"] for entry in entries]
        accepted = 0
        failed = 0
        processed = 0

        for batch_number, url_batch in enumerate(chunked(urls, SUBMIT_BATCH), start=1):
            result = submit_url_batch(
                endpoint_choice=endpoint,
                host=project.host,
                key=project.key,
                key_location=project.key_location,
                urls=url_batch,
            )
            ids = [entry_lookup[url] for url in url_batch]
            status = "submitted" if result.is_success else "failed"

            self.db.mark_entries(
                ids,
                status=status,
                http_code=result.status_code,
                response_excerpt=result.response_excerpt,
                endpoint=endpoint,
                increment_attempts=True,
            )
            self.db.add_run_items(
                run_id,
                [
                    (entry_id, result.status_code, result.response_excerpt, "accepted" if result.is_success else "failed")
                    for entry_id in ids
                ],
            )

            if result.is_success:
                accepted += len(ids)
                self.db.add_run_message(
                    run_id, "info", f"Batch {batch_number} ({len(ids)} URLs): {result.detail}"
                )
            else:
                failed += len(ids)
                self.db.add_run_message(
                    run_id, "error", f"Batch {batch_number} ({len(ids)} URLs) failed: {result.detail}"
                )

            processed += len(ids)
            self.db.update_run(
                run_id, processed_urls=processed, accepted_count=accepted, failed_count=failed
            )

    # ------------------------------------------------------------------ retry

    def start_retry(
        self, project_name: str, endpoint: str | None = None, entry_ids: Sequence[int] | None = None
    ) -> int:
        project = self.get_project(project_name)
        resolved = self.resolve_endpoint(project, endpoint)
        run_id = self.db.create_run(project.name, resolved, "retry-failed", "manual")
        thread = threading.Thread(
            target=self._execute_retry,
            args=(run_id, project, resolved, entry_ids),
            daemon=True,
            name=f"indexnow-retry-{run_id}",
        )
        thread.start()
        return run_id

    def retry_failed(
        self, project_name: str, endpoint: str | None = None, entry_ids: Sequence[int] | None = None
    ) -> RunSummary:
        project = self.get_project(project_name)
        resolved = self.resolve_endpoint(project, endpoint)
        run_id = self.db.create_run(project.name, resolved, "retry-failed", "manual")
        self._execute_retry(run_id, project, resolved, entry_ids)
        return self.summary(run_id)

    def _execute_retry(
        self, run_id: int, project: ProjectConfig, endpoint: str, entry_ids: Sequence[int] | None
    ) -> None:
        try:
            scope = "all failed URLs" if entry_ids is None else f"{len(entry_ids)} selected URLs"
            self.db.add_run_message(run_id, "info", f"Retrying {scope} for '{project.name}'.")
            entries = self.db.get_entries_by_status(project.name, ["failed"], entry_ids)
            if not entries and entry_ids is not None:
                self.db.add_run_message(
                    run_id, "warning", "None of the selected IDs are failed URLs in this project."
                )
            self._submit_entries(run_id, project, endpoint, entries)
            self.db.finish_run(run_id, "completed")
        except Exception as exc:  # noqa: BLE001
            self._fail_run(run_id, f"{type(exc).__name__}: {exc}")

    def mark_failed_success(
        self, project_name: str, entry_ids: Sequence[int] | None = None
    ) -> int:
        self.get_project(project_name)
        failed_entries = self.db.get_entries_by_status(project_name, ["failed"], entry_ids)
        return self.db.mark_manual_success([int(item["id"]) for item in failed_entries])

    # --------------------------------------------------------------- reporting

    def summary(self, run_id: int) -> RunSummary:
        row = self.db.get_run(run_id)
        if row is None:
            raise ValueError(f"Unknown run id {run_id}")
        return RunSummary(
            run_id=run_id,
            status=row["status"],
            submitted_count=row["submitted_count"],
            accepted_count=row["accepted_count"],
            failed_count=row["failed_count"],
            skipped_existing_count=row["skipped_existing_count"],
            invalid_count=row["invalid_count"],
            error_message=row["error_message"],
        )

    def progress(self, run_id: int, after_message_id: int = 0) -> dict:
        row = self.db.get_run(run_id)
        if row is None:
            raise ValueError(f"Unknown run id {run_id}")
        messages = self.db.list_run_messages(run_id, after_message_id)
        total = row["total_urls"] or 0
        processed = row["processed_urls"] or 0
        return {
            "run_id": run_id,
            "project": row["project_name"],
            "endpoint": row["endpoint"],
            "source_type": row["source_type"],
            "source_ref": row["source_ref"],
            "status": row["status"],
            "phase": row["phase"],
            "total": total,
            "processed": processed,
            "percent": round(processed / total * 100) if total else (100 if row["status"] != "running" else 0),
            "submitted": row["submitted_count"],
            "accepted": row["accepted_count"],
            "failed": row["failed_count"],
            "skipped": row["skipped_existing_count"],
            "invalid": row["invalid_count"],
            "error": row["error_message"],
            "finished": row["status"] != "running",
            "messages": [
                {"id": m["id"], "level": m["level"], "message": m["message"], "at": str(m["created_at"])}
                for m in messages
            ],
        }
