"""Self-checks for the logic that decides what gets sent to the API.

Run with `python tests/test_core.py` or `pytest`. No network, no fixtures.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexnow_tool import sources
from indexnow_tool.db import Database, url_hash
from indexnow_tool.normalize import normalize_url, validate_project_url
from indexnow_tool.sources import parse_csv_bytes, parse_sitemap_url

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://ex.com/pages.xml</loc></sitemap>
</sitemapindex>"""

# A urlset whose pages happen to mention "sitemap". The old heuristic treated
# these as nested sitemaps and dropped every real URL in the document.
URLSET_WITH_TRAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://ex.com/blog/sitemap-guide</loc></url>
  <url><loc>https://ex.com/about</loc></url>
</urlset>"""


def test_normalize_collapses_case_and_default_port():
    canonical = "https://www.example.com/Page"
    for variant in (
        "https://www.example.com/Page",
        "HTTPS://WWW.Example.com/Page",
        "https://www.example.com:443/Page",
        "https://www.example.com/Page#section",
        "  https://WWW.EXAMPLE.com/Page  ",
    ):
        assert normalize_url(variant) == canonical, variant
        assert url_hash(normalize_url(variant)) == url_hash(canonical)

    # Path case is significant and must survive.
    assert normalize_url("https://ex.com/A") != normalize_url("https://ex.com/a")
    # A non-default port is part of the identity.
    assert normalize_url("https://ex.com:8443/a") == "https://ex.com:8443/a"


def test_validate_rejects_foreign_hosts():
    assert validate_project_url("https://www.example.com/a", "www.example.com").is_valid
    assert validate_project_url("https://WWW.EXAMPLE.COM/a", "www.example.com").is_valid
    assert not validate_project_url("https://evil.com/a", "www.example.com").is_valid
    assert not validate_project_url("ftp://www.example.com/a", "www.example.com").is_valid
    assert not validate_project_url("/relative/path", "www.example.com").is_valid


def test_sitemap_branches_on_root_tag(monkeypatch=None):
    pages = {
        "https://ex.com/index.xml": SITEMAP_INDEX,
        "https://ex.com/pages.xml": URLSET_WITH_TRAP,
    }
    original = sources._fetch
    sources._fetch = lambda url, timeout: pages[url]
    try:
        urls = parse_sitemap_url("https://ex.com/index.xml")
    finally:
        sources._fetch = original

    assert urls == ["https://ex.com/blog/sitemap-guide", "https://ex.com/about"], urls


def test_csv_skips_header_row():
    assert parse_csv_bytes(b"url\nhttps://ex.com/a\n") == ["https://ex.com/a"]
    assert parse_csv_bytes(b"https://ex.com/a\nhttps://ex.com/b\n") == [
        "https://ex.com/a",
        "https://ex.com/b",
    ]


def _fresh_db() -> Database:
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    return Database(tmp)


def test_empty_id_list_never_means_everything():
    db = _fresh_db()
    db.stage_urls("p", "paste", None, ["https://ex.com/a", "https://ex.com/b"])
    all_new = db.get_entries_by_status("p", ["new"], None)
    assert len(all_new) == 2

    # The bug this guards: [] falling through to "no filter" and hitting every row.
    assert db.get_entries_by_status("p", ["new"], []) == []
    assert db.get_entries_by_ids([]) == []
    assert db.mark_manual_success([]) == 0


def test_dedupe_skips_accepted_but_recovers_stalled_urls():
    db = _fresh_db()
    urls = ["https://ex.com/a", "https://ex.com/b"]

    first_ids, skipped = db.stage_urls("p", "paste", None, urls)
    assert len(first_ids) == 2 and skipped == 0

    # 'a' was accepted by the API, 'b' never left the starting gate.
    db.mark_entries([first_ids[0]], status="submitted", http_code=200)

    second_ids, skipped = db.stage_urls("p", "paste", None, urls)
    assert skipped == 1, "an accepted URL must not be resubmitted"
    assert second_ids == [first_ids[1]], "a stalled URL must be picked up again"

    # Force overrides the skip.
    forced_ids, skipped = db.stage_urls("p", "paste", None, urls, force=True)
    assert skipped == 0 and len(forced_ids) == 2

    # A failed URL is retryable, a manually-closed one is not.
    db.mark_entries([first_ids[1]], status="failed", http_code=403)
    db.mark_entries([first_ids[0]], status="manually_marked_success")
    ids, skipped = db.stage_urls("p", "paste", None, urls)
    assert ids == [first_ids[1]] and skipped == 1


# The v1 schema, verbatim. Its counter columns are NOT NULL with no default, and
# CREATE TABLE IF NOT EXISTS never revises an existing table — so anything that
# relies on a default added later breaks only on an upgraded database, never on a
# fresh one. Every schema change needs a check against this.
LEGACY_SCHEMA = """
CREATE TABLE url_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    last_http_code INTEGER,
    last_response_excerpt TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_endpoint TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_submitted_at DATETIME
);
CREATE UNIQUE INDEX idx_url_project_hash ON url_entries(project_name, url_hash);
CREATE TABLE submission_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT,
    submitted_count INTEGER NOT NULL,
    accepted_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    skipped_existing_count INTEGER NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE submission_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    url_entry_id INTEGER NOT NULL,
    http_code INTEGER,
    response_excerpt TEXT,
    result TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(run_id) REFERENCES submission_runs(id),
    FOREIGN KEY(url_entry_id) REFERENCES url_entries(id)
);
"""


def test_upgraded_database_still_works():
    import sqlite3

    path = Path(tempfile.mkdtemp()) / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(LEGACY_SCHEMA)
    legacy.execute(
        "INSERT INTO url_entries(project_name, url, url_hash, source_type, status) "
        "VALUES ('p', 'https://ex.com/old', 'deadbeef', 'paste', 'submitted')"
    )
    legacy.execute(
        "INSERT INTO submission_runs(project_name, endpoint, source_type, "
        "submitted_count, accepted_count, failed_count, skipped_existing_count) "
        "VALUES ('p', 'indexnow', 'paste', 1, 1, 0, 0)"
    )
    legacy.commit()
    legacy.close()

    db = Database(path)

    # Existing data survives the migration.
    assert len(db.get_entries_by_status("p", ["submitted"], None)) == 1
    assert len(db.list_recent_runs()) == 1

    # The whole run lifecycle works on the upgraded table.
    run_id = db.create_run("p", "indexnow", "paste", None)
    row = db.get_run(run_id)
    assert row["status"] == "running" and row["submitted_count"] == 0
    db.update_run(run_id, total_urls=2, processed_urls=1, accepted_count=1)
    db.add_run_message(run_id, "info", "hello")
    db.finish_run(run_id, "completed")
    assert db.get_run(run_id)["status"] == "completed"
    assert len(db.list_run_messages(run_id)) == 1

    # A run stranded by a crash is closed out on the next startup, not left running.
    db.conn.execute("UPDATE submission_runs SET status='running' WHERE id = ?", (run_id,))
    db.conn.commit()
    db.conn.close()
    assert Database(path).get_run(run_id)["status"] == "interrupted"


def test_rename_keeps_history_attached():
    db = _fresh_db()
    db.upsert_project("old", "ex.com", "key-12345678", None, None, "indexnow")
    db.stage_urls("old", "paste", None, ["https://ex.com/a"])
    db.upsert_project("new", "ex.com", "key-12345678", None, None, "bing", original_name="old")

    assert db.get_project("old") is None
    assert db.get_project("new")["default_endpoint"] == "bing"
    assert len(db.get_entries_by_status("new", ["new"], None)) == 1


def test_session_tokens_cannot_be_forged():
    from indexnow_tool.auth import (
        AuthConfig,
        SESSION_MAX_AGE,
        check_password,
        issue_token,
        is_loopback,
        startup_warning,
        token_is_valid,
    )

    config = AuthConfig(password="hunter2", secret=b"server-secret", cookie_secure=False)
    token = issue_token(config, now=1000)

    assert token_is_valid(config, token, now=1000)
    assert token_is_valid(config, token, now=1000 + SESSION_MAX_AGE - 1)
    assert not token_is_valid(config, token, now=1000 + SESSION_MAX_AGE + 1), "expired"

    # A token signed with a different secret must not verify.
    other = AuthConfig(password="hunter2", secret=b"other-secret", cookie_secure=False)
    assert not token_is_valid(config, issue_token(other, now=1000))

    for forged in (None, "", "garbage", "1000.", "1000.deadbeef", ".", "notanumber.x"):
        assert not token_is_valid(config, forged), forged

    assert check_password(config, "hunter2")
    assert not check_password(config, "Hunter2")
    assert not check_password(config, "")
    # With no password configured nothing authenticates.
    assert not check_password(AuthConfig(None, b"s", False), "")

    assert is_loopback("localhost") and is_loopback("127.0.0.1") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.10")


def test_open_instance_refuses_to_serve_the_network():
    from indexnow_tool.auth import AuthConfig, startup_warning

    no_password = AuthConfig(password=None, secret=b"s", cookie_secure=False)
    with_password = AuthConfig(password="pw", secret=b"s", cookie_secure=False)

    # Loopback without a password is fine; that is the local-tool case.
    assert startup_warning(no_password, "localhost") is None
    assert startup_warning(no_password, "127.0.0.1") is None
    # Anything reachable from the network without a password must not start.
    for host in ("0.0.0.0", "192.168.1.10", "::"):
        assert startup_warning(no_password, host) is not None, host
    # A password makes any bind address acceptable.
    assert startup_warning(with_password, "0.0.0.0") is None


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"ok    {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s)")
    raise SystemExit(1 if failures else 0)
