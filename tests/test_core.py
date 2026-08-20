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


def test_rename_keeps_history_attached():
    db = _fresh_db()
    db.upsert_project("old", "ex.com", "key-12345678", None, None, "indexnow")
    db.stage_urls("old", "paste", None, ["https://ex.com/a"])
    db.upsert_project("new", "ex.com", "key-12345678", None, None, "bing", original_name="old")

    assert db.get_project("old") is None
    assert db.get_project("new")["default_endpoint"] == "bing"
    assert len(db.get_entries_by_status("new", ["new"], None)) == 1


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
