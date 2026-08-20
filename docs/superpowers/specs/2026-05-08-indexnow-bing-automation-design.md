# IndexNow Automation Tool Design

## Goal

Build a local tool with both UI and CLI that submits URL updates to IndexNow/Bing using project-specific keys, tracks submissions in SQLite, and only sends new or explicitly retried URLs.

## Scope

This design covers:

- Multi-project configuration from `.env` (project host, key, key location, default endpoint, optional sitemap URL).
- URL ingestion from sitemap URL, TXT file, CSV file, and direct paste.
- Deduplication and "new only" filtering using SQLite state.
- Submission with endpoint selection per run (global IndexNow or Bing direct).
- Response tracking, retry handling, and manual success marking for failed URLs.
- Local web UI and CLI on the same backend.
- Dynamic free-port startup for the UI server.

Out of scope for v1:

- Authentication beyond localhost binding.
- Distributed queue workers and multi-machine coordination.
- Full XML sitemap index recursion across many nested levels beyond practical defaults (will support common nested sitemap indexes).

## Requirements and Protocol Rules

Rules implemented from official docs:

- POST endpoint accepts up to 10,000 URLs per request.
- Key format must be 8-128 chars and only `[A-Za-z0-9-]`.
- URLs in each request must belong to the same host as payload `host`.
- URL encoding must follow RFC 3986 semantics.
- Key file ownership verification is required; optional `keyLocation` is supported.
- Response handling must recognize `200`, `202`, `400`, `403`, `422`, `429`.
- `429` should honor `Retry-After` where present.

Operational best practices:

- Avoid resubmitting unchanged URLs by default.
- Allow explicit retries of failed URLs.
- Provide per-URL logs and summary metrics.

## Architecture

Single Python application with shared core modules:

- `core/config.py` - project/env loading and validation.
- `core/db.py` - SQLite schema and data access functions.
- `core/sources.py` - URL ingestion from sitemap/txt/csv/paste.
- `core/normalize.py` - URL normalization and project-host validation.
- `core/indexnow_client.py` - API payload creation, submission, retries, response parsing.
- `core/services.py` - orchestration (source -> diff -> submit -> persist).
- `cli.py` - CLI commands for run/list/retry/mark operations.
- `ui.py` and templates - local UI pages and action endpoints.
- `server.py` - dynamic free-port binding and app launch.

The UI and CLI both call the same service layer to keep behavior identical.

## Data Model (SQLite)

Tables:

- `projects` (cached project metadata from env; optional sync helper).
- `url_entries`
  - `id`
  - `project_name`
  - `url`
  - `url_hash`
  - `source_type` (`sitemap|txt|csv|paste`)
  - `source_ref` (sitemap URL or file name)
  - `status` (`new|submitted|failed|manually_marked_success`)
  - `last_http_code`
  - `last_response_excerpt`
  - `attempt_count`
  - `last_endpoint`
  - `created_at`
  - `updated_at`
  - `last_submitted_at`
- `submission_runs`
  - `id`
  - `project_name`
  - `endpoint`
  - `source_type`
  - `source_ref`
  - `submitted_count`
  - `accepted_count`
  - `failed_count`
  - `skipped_existing_count`
  - `created_at`
- `submission_items`
  - `run_id`
  - `url_entry_id`
  - `http_code`
  - `response_excerpt`
  - `result` (`accepted|failed|skipped`)

Uniqueness:

- Unique index on `(project_name, url_hash)` for stable dedupe.

## URL Source Handling

Sitemap source:

- Save one default sitemap URL per project in config.
- Download XML sitemap with timeout and clear errors.
- Parse `urlset` and `sitemapindex`.
- Extract all `<loc>` entries, normalize and dedupe.

TXT source:

- Read as UTF-8 (with fallback handling for BOM).
- Split with universal newlines support (`\r\n`, `\n`, `\r`).
- One URL per non-empty line.

CSV source:

- Parse CSV robustly.
- Use first column from each non-empty row.

Paste source:

- Accept textarea/string input.
- Split using universal newlines.

## Submission Flow

1. Load selected project config.
2. Load URLs from selected source.
3. Normalize and reject non-project-host URLs.
4. Compare against SQLite:
   - default mode: submit only `new`.
   - retry mode: submit failed only (bulk or selected IDs).
5. Chunk into max 10,000 URLs.
6. Send POST JSON payload:
   - `host`
   - `key`
   - optional `keyLocation`
   - `urlList`
7. Classify response:
   - success: `200`, `202`
   - failure: all others
8. Persist run + item results + URL status updates.
9. Render summaries and actionable feedback.

## Retry and Manual Marking

Retry failed:

- Bulk retry by project/source filter.
- Selective retry by entry IDs/URL picks.

Mark failed as success:

- Bulk and selective operations.
- Track as `manually_marked_success` with timestamp and note.

This addresses practical cases where Bing tools later show processed URLs despite earlier API errors.

## UI Design

Local-only UI on `127.0.0.1` with dynamic port:

- Dashboard:
  - project selector
  - endpoint selector (default from project; global IndexNow preselected if none)
  - source selector (sitemap/txt/csv/paste)
  - source-specific input controls
  - run button
- Results:
  - run summary metrics
  - per-URL table with status and code
  - filters for failed/submitted/new
- Recovery actions:
  - retry failed (all/selected)
  - mark failed as success (all/selected)

## CLI Design

Commands:

- `serve` - start UI on first free port.
- `projects` - list configured projects and defaults.
- `run` - submit from selected source.
- `retry-failed` - resend failed URLs (all or selected).
- `mark-success` - manually mark failures as successful (all or selected).
- `status` - show recent runs and failure summary.

Key options:

- `--project`
- `--endpoint [indexnow|bing]`
- `--source [sitemap|txt|csv|paste]`
- `--file`
- `--paste`
- `--selected-ids`
- `--all-failed`

## Port Selection

`serve` behavior:

- Start from preferred port (default 8787).
- Probe sequentially for free localhost port.
- Bind to first available and print final URL.
- If preferred busy, no failure; auto-shift to free port.

## Error Handling and Feedback

- Friendly diagnostics for invalid key format, host mismatch, bad sitemap, malformed CSV, and network errors.
- HTTP-specific guidance:
  - `403`: key file/key mismatch likely.
  - `422`: host or payload mismatch.
  - `429`: throttled; retry later (honor `Retry-After`).
- Persist short response excerpts for auditing.

## Testing Strategy

- Unit tests for:
  - key and URL validators
  - source parsers
  - dedupe logic
  - chunking logic
  - response classification
- Integration tests for:
  - SQLite state transitions
  - run/retry/mark flows
- Endpoint calls mocked in tests for deterministic behavior.

## Dependencies

Small Python footprint:

- `fastapi`
- `uvicorn`
- `httpx`
- `jinja2`
- `python-multipart`
- `python-dotenv`

Standard library used for SQLite, CSV parsing, XML parsing, hashing, and socket probing.

## Success Criteria

- User can define multiple projects in `.env`.
- User can choose project + endpoint and run from UI or CLI.
- Tool submits only new URLs by default.
- Failed URLs can be retried or manually marked successful (bulk/selective).
- Full run and per-URL feedback visible and persisted.
- UI server starts reliably on a free localhost port.
