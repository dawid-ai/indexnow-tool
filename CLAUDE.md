# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

A local-only tool that submits URLs to the IndexNow API (generic endpoint or Bing
direct), tracks every URL in SQLite so it only submits new ones, and exposes both a
FastAPI web UI and an `argparse` CLI over the same service layer.

Nothing here is deployed. It binds to `127.0.0.1` and there is no auth beyond that.

## Commands

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt

python main.py serve                 # start the local UI (auto-picks a free port from DEFAULT_UI_PORT)
python main.py projects              # list configured projects
python main.py status                # last 20 runs as JSON
python main.py run --project demo --source sitemap --sitemap-url https://example.com/sitemap.xml
python main.py run --project demo --source txt --file urls.txt --endpoint bing
python main.py retry-failed --project demo [--ids 12,13]
python main.py mark-success --project demo [--ids 12,13]
```

There is no test suite and no linter config. If you add tests, use `pytest` and put
them in `tests/`.

## Architecture

Layered, with the CLI and the UI as two thin adapters over one service:

```
main.py -> indexnow_tool/cli.py ----\
                                      +--> service.IndexNowService --> db.Database (SQLite)
           indexnow_tool/ui.py -----/                              \-> indexnow_client (httpx POST)
```

- `config.py` — reads `.env` via `python-dotenv` into frozen dataclasses (`AppConfig`,
  `ProjectConfig`). Validates key format and fails fast at startup on a bad or missing
  project. Every entry point calls `load_config()` first.
- `sources.py` — turns a sitemap URL, TXT bytes, CSV bytes, or pasted text into a list
  of URLs. Sitemap parsing follows nested sitemap indexes up to `max_depth=2`.
- `normalize.py` — strips the fragment, then rejects any URL whose scheme isn't
  http/https or whose host doesn't match the project host. IndexNow requires host
  consistency, so this check is not optional.
- `service.py` — orchestration. `run_from_source` validates, dedupes in-memory, upserts
  into SQLite (existing hashes are skipped, not resubmitted), submits only `new`
  entries, then records a run plus per-URL items. `retry_failed` and
  `mark_failed_success` reuse the same submission path.
- `indexnow_client.py` — the only place that talks to IndexNow. Batches at 10,000 URLs,
  treats 200/202 as success, retries 429 honoring `Retry-After` and retries transport
  errors, up to `max_retries=3`.
- `db.py` — schema-on-connect SQLite. Three tables: `url_entries` (unique on
  `project_name + url_hash`), `submission_runs`, `submission_items`. Statuses are
  `new`, `submitted`, `failed`, `manually_marked_success`.
- `ports.py` — scans upward from the start port for a free one so a stale server never
  blocks a new run.
- `ui.py` — FastAPI routes rendering Jinja2 templates in `indexnow_tool/templates/`.

## Conventions

- `from __future__ import annotations` at the top of every module; PEP 604 unions
  (`str | None`).
- Frozen dataclasses for config and result objects.
- Business logic goes in `service.py`. Keep `cli.py` and `ui.py` to argument parsing,
  request handling, and output formatting so both stay in sync.
- Anything touching the IndexNow protocol (endpoints, batch size, success codes, retry
  rules) belongs in `indexnow_client.py`.

## Constraints

- Never commit `.env` or `data/*.db` — both are gitignored. `.env` holds live IndexNow
  keys for real hosts.
- Don't run submission commands against real projects while testing. `run`,
  `retry-failed`, and `serve` + a form POST all hit the live IndexNow API and write to
  SQLite. Use `projects` and `status` for read-only checks.
- The `Database` connection is shared with `check_same_thread=False` because FastAPI
  handlers run in worker threads. Writes commit immediately; there is no pooling.

## Reference

- Design spec: `docs/superpowers/specs/2026-05-08-indexnow-bing-automation-design.md`
- Full `.env` reference and examples: `README.md`
