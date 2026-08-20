# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

A local-only tool that submits URLs to the IndexNow API (generic endpoint or Bing
direct), tracks every URL in SQLite so it only submits new ones, and exposes both a
FastAPI web UI and an `argparse` CLI over the same service layer.

It binds `localhost` (both `127.0.0.1` and `::1`) by default. Off loopback it
requires `AUTH_PASSWORD` and refuses to start without it, because the database
holds IndexNow keys in plaintext.

Published as MIT on PyPI (`indexnow-tool`, console script `indexnow`) and as a
container image. `main.py` stays as the source-checkout entry point.

## Commands

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt

python main.py                       # start the local UI; `serve` is the default command
python main.py projects              # list projects
python main.py project-add --name demo --host www.example.com --key <key>
python main.py verify-key --project demo
python main.py status                # last 20 runs as JSON
python main.py run --project demo --source sitemap [--force]
python main.py retry-failed --project demo (--ids 12,13 | --all)
python main.py mark-success --project demo (--ids 12,13 | --all)
python main.py export --project demo --status failed

python tests/test_core.py            # or: pytest
```

There is no linter config.

## Architecture

Layered, with the CLI and the UI as two thin adapters over one service:

```
main.py -> indexnow_tool/cli.py ----\
                                      +--> service.IndexNowService --> db.Database (SQLite)
           indexnow_tool/ui.py -----/                              \-> indexnow_client (httpx POST)
```

- `config.py` — app settings from `.env` (`DB_PATH`, `DEFAULT_ENDPOINT`,
  `DEFAULT_UI_PORT`). Also holds `validate_project_fields`, shared by the UI form and
  the CLI, and `import_projects_from_env`, which seeds the projects table from legacy
  `PROJECT_<NAME>_*` variables without ever overwriting a project that exists.
- `db.py` — schema-on-connect SQLite plus additive column migrations in `_migrate`.
  Tables: `projects`, `url_entries` (unique on `project_name + url_hash`),
  `submission_runs`, `submission_items`, `run_messages`. Entry statuses are `new`,
  `submitted`, `failed`, `manually_marked_success`.
- `sources.py` — sitemap URL, TXT, CSV, or paste into a URL list. Raises `SourceError`
  with a message that is safe to show the user.
- `normalize.py` — canonicalizes a URL (lowercase scheme and host, drop the default
  port and fragment) and rejects anything whose host is not the project host.
- `service.py` — orchestration and the run lifecycle.
- `indexnow_client.py` — the only place that talks to IndexNow, plus `verify_key_file`.
- `ports.py` — scans upward for a free port. It resolves the host through
  `getaddrinfo` and probes every address, because `localhost` maps to both `::1` and
  `127.0.0.1` and Windows browsers try the IPv6 one first. Serving only `127.0.0.1`
  made the UI unreachable at `localhost`. Default host is `localhost` so uvicorn binds
  both stacks; do not narrow it back to a single literal IP.
- `auth.py` — single shared password, signed cookie over stdlib `hmac`. No new
  dependency and no session store, so multiple workers are possible as long as
  `AUTH_SECRET` is set. `startup_warning` is the refuse-to-start guard.
- `ui.py` — FastAPI routes over Jinja2 templates in `indexnow_tool/templates/`.
  A middleware gates everything except `PUBLIC_PATHS` (`/login`, `/healthz`);
  `/api/*` gets a 401, everything else a redirect to the login page.

## Invariants worth preserving

These encode bugs that were fixed; changing them reintroduces the bug.

- **An empty id list means "nothing", never "everything."**
  `db.get_entries_by_status(..., ids=[])` returns `[]` while `ids=None` returns all.
  The UI and CLI both require an explicit "all" scope. This is what stops an empty
  selection from retrying or force-closing a whole project.
- **Dedupe is by canonical hash, not raw string.** `normalize_url` runs before
  `url_hash`, so case and default-port variants collapse to one entry.
- **Only `submitted` and `manually_marked_success` block resubmission**
  (`db.ACTIVE_STATUSES`). A `new` entry stranded by an interrupted run is picked up
  by the next run of the same source; `force=True` overrides the skip entirely.
- **Sitemap type is decided by the root element** (`sitemapindex` vs `urlset`), not
  by pattern-matching the link text. Guessing dropped every page URL in a sitemap
  that contained a link with "sitemap" in it.
- **Every column is written explicitly on insert.** `CREATE TABLE IF NOT EXISTS`
  never revises an existing table, so a column default added later does not exist
  on an upgraded database. Relying on one passes on a fresh schema and raises
  `IntegrityError` on every real install. `tests/test_core.py` builds a v1 database
  and migrates it; keep that test green for any schema change.
- **A key file's folder scopes what it can submit.** IndexNow only trusts a key for
  URLs under the directory holding the key file, so `key_location` of
  `/public/key.txt` authorizes only `/public/*`. `normalize.key_scope_prefix`
  derives that prefix and `validate_project_url` enforces it. Removing the check
  turns a clear local message back into an opaque `422` from the API.
- **The key file is verified before every submission**, in `_submit_entries`, so
  both runs and retries are covered by one call. Failing there raises
  `KeyFileError`, which the run wrapper reports as a failed run.
- **`templates.TemplateResponse(request, name, context)`** — the old
  `(name, {"request": ...})` order was removed in Starlette 1.0. The deprecated
  form still works on older pinned versions, so this breaks only on fresh installs.

## Run lifecycle and live progress

`service.start_run` creates the run row with `status='running'`, then executes it on
a daemon thread and returns immediately; `run_from_source` does the same inline for
the CLI. Both funnel into `_execute_source_run`, which:

1. Updates `phase` at each step and appends to `run_messages` as it goes.
2. Catches every exception and calls `_fail_run`, so a failure lands in the run row
   and the log rather than as a 500 or a traceback.

`service.progress(run_id, after_message_id)` is the single read model. `/api/runs/{id}`
serves it as JSON, `templates/run.html` polls it, and the CLI's `_follow_run` prints
it. Add new progress information there and all three surfaces get it.

Because a run lives on a thread, a process restart can strand one. `db._migrate`
marks any leftover `running` row as `interrupted` at startup.

## Conventions

- `from __future__ import annotations` at the top of every module; PEP 604 unions.
- Frozen dataclasses for config and request objects.
- Business logic goes in `service.py`. Keep `cli.py` and `ui.py` to argument parsing,
  request handling, and output formatting so both stay in sync.
- Anything touching the IndexNow protocol (endpoints, batch size, success codes,
  retry rules, status meanings) belongs in `indexnow_client.py`.
- All writes go through `db.py`, which serializes them behind `self._lock` because
  FastAPI handlers and the run thread share one connection.
- New templates extend `templates/base.html`.

## Constraints

- Never commit `.env` or `data/*.db` — both are gitignored. The database holds live
  IndexNow keys for real hosts. This is a public repository; audit history before
  pushing anything that touched a real key.
- Dependencies are unpinned in `pyproject.toml` and CI installs the latest, on
  purpose: a breaking upstream change should fail CI rather than users' installs.
- Don't run submission commands against real projects while testing. `run`,
  `retry-failed`, and a form POST from `serve` all hit the live IndexNow API. Use
  `projects`, `status`, and `export` for read-only checks, and stub
  `service.submit_url_batch` to exercise the run pipeline offline.
- `verify-key` makes an outbound GET to the project host. Harmless, but it is a
  network call.

## Reference

- Design spec: `docs/superpowers/specs/2026-05-08-indexnow-bing-automation-design.md`
- User-facing setup and usage: `README.md`
