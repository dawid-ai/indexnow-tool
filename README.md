# IndexNow Automation Tool (UI + CLI)

Local Python tool to submit URLs to IndexNow. Manage projects and keys in a web UI,
pull URLs from a sitemap, file, or paste, and watch each run report progress and
errors live. Every URL is tracked in SQLite so nothing gets submitted twice.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; use source .venv/bin/activate elsewhere
pip install -r requirements.txt
python main.py
```

That starts the web UI and prints its address, for example
`http://localhost:8787`. If that port is taken it scans upward, so read the printed
line. Open it, go to **Projects & keys**, add a project, and press **Verify key**
before your first run.

The server binds both `127.0.0.1` and `::1`, so `localhost` works whichever one your
browser picks.

## Setting up a project

1. Choose a key: 8-128 characters of `A-Z a-z 0-9 -`. Any random string works.
2. Save it in a file named `<key>.txt` containing only the key.
3. Upload that file to the host root, so `https://www.example.com/<key>.txt` serves it.
4. Add the project in the UI: name, host, key.
5. Press **Verify key**. It fetches the file exactly as a search engine would.

Skipping step 5 is the usual reason runs come back `403`, or get accepted and then
silently ignored.

## Submitting URLs

Pick a source on the **Submit** page:

| Source | Notes |
| --- | --- |
| Sitemap URL | Follows sitemap indexes up to 3 levels. Handles `.xml.gz`. |
| TXT file | One URL per line. Any line endings. |
| CSV file | First column. A header row is skipped automatically. |
| Paste | One URL per line. |

The run page then shows a progress bar, live counters, and a log of every batch
result and every rejected URL.

### What gets sent

- URLs whose host does not match the project are rejected before any request, with
  the reason logged. IndexNow rejects a whole batch on a host mismatch.
- URLs are canonicalized before hashing, so `HTTPS://WWW.Example.com/A:443` and
  `https://www.example.com/A` count as one URL.
- A URL the API already accepted is skipped. Tick **Resubmit URLs that were already
  accepted** to override.
- A URL that failed, or was left behind by an interrupted run, is picked up again on
  the next run of the same source.
- Submissions go out in batches of 1,000 so progress is visible and one rejected
  batch does not take down the whole run.

## Protocol rules applied

- Max 10,000 URLs per POST (the tool uses 1,000).
- Key format validated as 8-128 chars of `A-Z a-z 0-9 -`.
- Host consistency enforced against the project host.
- `200` and `202` count as success.
- `400`, `403`, `404`, `422`, `429` are reported with what each one means. `429`
  retries and honors `Retry-After`; network errors retry up to 3 times.

Reference: [IndexNow docs](https://www.indexnow.org/documentation) ·
[FAQ](https://www.indexnow.org/faq) ·
[Bing get started](https://www.bing.com/indexnow/getstarted)

## Handling failures

The **Failed** view per project lists every rejected URL with its HTTP code, the
response excerpt, and its id. From there you can retry all of them, or copy ids into
the dashboard's retry form to retry a subset. **Mark failed as successful** closes
URLs you confirmed were indexed some other way; it only changes local bookkeeping.

Export any project's URLs as CSV from the dashboard or the Failed view.

## CLI

```bash
python main.py projects                       # list projects
python main.py project-add --name demo --host www.example.com --key <key>
python main.py verify-key --project demo      # exits non-zero if the key file is wrong
python main.py status                         # recent runs as JSON

python main.py run --project demo --source sitemap --sitemap-url https://www.example.com/sitemap.xml
python main.py run --project demo --source txt --file urls.txt --endpoint bing
python main.py run --project demo --source csv --file urls.csv
python main.py run --project demo --source paste --paste "https://www.example.com/a"
python main.py run --project demo --source sitemap --force     # resubmit known URLs

python main.py retry-failed --project demo --ids 12,13
python main.py retry-failed --project demo --all
python main.py mark-success --project demo --ids 12,13
python main.py export --project demo --status failed > failed.csv

python main.py serve --start-port 9000     # explicit; bare `python main.py` is the same as `serve`
```

`run` and `retry-failed` stream the same log the UI shows and exit non-zero if
anything failed, so they drop straight into a scheduled task.

`retry-failed` and `mark-success` require either `--ids` or `--all`. An empty
selection never means "everything".

## Configuration

Projects live in the SQLite database and are edited in the UI. The `.env` file only
holds app-level settings:

```env
DB_PATH=data/indexnow.db      # default
DEFAULT_ENDPOINT=indexnow     # indexnow | bing
DEFAULT_UI_PORT=8787          # first port tried; the app scans upward for a free one
```

### Endpoints

- `indexnow` -> `https://api.indexnow.org/indexnow` (recommended; shared across engines)
- `bing` -> `https://www.bing.com/indexnow`

Set per project in the UI, or per run in the dropdown or `--endpoint`.

### Legacy `.env` projects

Projects defined the old way are imported into the database on startup, and only if
no project of that name exists yet, so UI edits are never overwritten:

```env
PROJECTS=demo
PROJECT_DEMO_HOST=www.example.com
PROJECT_DEMO_KEY=90d0eecfb5144dc4bdbb41c28fd71a15
PROJECT_DEMO_KEY_LOCATION=https://www.example.com/90d0eecfb5144dc4bdbb41c28fd71a15.txt
PROJECT_DEMO_SITEMAP_URL=https://www.example.com/sitemap.xml
PROJECT_DEMO_DEFAULT_ENDPOINT=bing
```

Once imported you can delete those lines. Keys are then stored only in
`data/indexnow.db`, which is gitignored.

## Tests

```bash
python tests/test_core.py     # or: pytest
```

Covers URL canonicalization, host validation, sitemap index detection, dedupe rules,
and the selection-scope guards. No network calls.
