# Changelog

## 1.0.1 - 2026-08-20

Turns a confusing IndexNow `422` into a readable error before anything is submitted.

- Verify the key file at the start of every run and every retry. A key file that
  404s or does not match now fails the run with the reason, instead of surfacing
  as `422 Unprocessable` from the API after the URLs are already spent.
- Enforce the key file's folder scope. IndexNow only trusts a key for URLs under
  the folder holding the key file, so a key at `/public/key.txt` cannot submit
  `https://example.com/tools`. Those URLs are now rejected locally, naming the
  folder that would work.
- Reject a key file URL pointing at a different host when saving a project.
- Report a run as failed when every URL was rejected. It previously finished as
  completed with nothing sent, which read like success.

## 1.0.0 - 2026-08-20

First public release. Web UI and CLI for the IndexNow protocol, with per-project
keys, SQLite-backed deduplication, live run progress, key file verification, CSV
export, and optional password authentication.
