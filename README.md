# IndexNow Tool

Submit URLs to [IndexNow](https://www.indexnow.org) from a web UI or the command
line. Manage several sites and their keys in one place, pull URLs from a sitemap,
file, or paste, and watch each run report progress and errors as it happens.

Every URL is tracked in SQLite, so re-running a sitemap only submits what is
genuinely new.

## Why

The IndexNow API is a single POST, but doing it properly means keeping track of
what you already sent, matching every URL to the right host and key, and
understanding a `403` when it arrives. This tool handles that part.

- **No duplicate submissions.** URLs are canonicalized and hashed, so
  `HTTPS://WWW.Example.com/A` and `https://www.example.com/A` count once.
- **Errors you can act on.** Every rejected batch is recorded with the HTTP code
  and what it means, and every invalid URL with the reason it was skipped.
- **Key verification.** One click checks the key file the way a search engine
  will, before a bad key turns into a silent no-op.
- **Live progress.** Long runs show a progress bar and a running log instead of a
  spinner.

## Install

### pipx (a local command)

```bash
pipx install indexnow-tool
indexnow
```

`indexnow` with no arguments starts the web UI and prints its address. The
database goes to `~/.indexnow/indexnow.db`.

### Docker

```bash
git clone https://github.com/dawidjozwiak/indexnow-tool
cd indexnow-tool
echo "AUTH_PASSWORD=pick-a-password" > .env
docker compose up -d
```

Then open <http://localhost:8787>. The database lives on the `indexnow-data`
volume. The compose file publishes to `127.0.0.1` only — see
[Exposing it to a network](#exposing-it-to-a-network) before changing that.

### From source

```bash
git clone https://github.com/dawidjozwiak/indexnow-tool
cd indexnow-tool
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
python main.py
```

## Setting up a site

1. Choose a key: 8-128 characters of `A-Z a-z 0-9 -`. Any random string works.
2. Save it in a file named `<key>.txt` whose only contents are the key.
3. Upload it to the host root, so `https://www.example.com/<key>.txt` serves it.
4. Add the project in the UI: a name, the host, and the key.
5. Press **Verify key**.

Step 5 is the one people skip. A missing or mismatched key file is the usual
cause of a `403`, and in the worse case submissions look accepted and are then
quietly ignored.

## Submitting URLs

| Source | Notes |
| --- | --- |
| Sitemap URL | Follows sitemap indexes up to 3 levels. Handles `.xml.gz`. |
| TXT file | One URL per line. Any line endings. |
| CSV file | First column. A header row is skipped automatically. |
| Paste | One URL per line. |

What actually gets sent:

- URLs whose host does not match the project are rejected before any request.
  IndexNow rejects an entire batch on a host mismatch, so one stray URL would
  otherwise take down the whole submission.
- A URL the API already accepted is skipped. Tick **Resubmit URLs that were
  already accepted** to override.
- A URL that failed, or was stranded by an interrupted run, is picked up again on
  the next run of the same source.
- Batches are 1,000 URLs, well under the 10,000 limit, so progress is visible and
  one rejected batch does not fail the rest.

## Handling failures

The **Failed** view lists every rejected URL with its HTTP code, the response
excerpt, and its id. Retry all of them, or copy ids into the dashboard's retry
form for a subset. **Mark failed as successful** closes out URLs you confirmed
were indexed another way; it changes local bookkeeping only.

Any project's URLs export as CSV.

## CLI

```bash
indexnow                                      # start the web UI (default command)
indexnow projects                             # list projects
indexnow project-add --name demo --host www.example.com --key <key>
indexnow verify-key --project demo            # non-zero exit if the key file is wrong
indexnow status                               # recent runs as JSON

indexnow run --project demo --source sitemap --sitemap-url https://www.example.com/sitemap.xml
indexnow run --project demo --source txt --file urls.txt --endpoint bing
indexnow run --project demo --source paste --paste "https://www.example.com/a"
indexnow run --project demo --source sitemap --force    # resubmit known URLs

indexnow retry-failed --project demo --ids 12,13
indexnow retry-failed --project demo --all
indexnow mark-success --project demo --ids 12,13
indexnow export --project demo --status failed > failed.csv
```

`run` and `retry-failed` stream the same log the UI shows and exit non-zero if
anything failed, so they drop straight into cron or Task Scheduler.

`retry-failed` and `mark-success` require either `--ids` or `--all`. An empty
selection never means "everything".

From a source checkout, use `python main.py` in place of `indexnow`.

## Security

**This tool stores IndexNow keys in plaintext in its SQLite database.** Anyone who
can reach the UI can read them and submit URLs for your domains.

- Bound to `localhost` with no password, it is a local tool and needs nothing more.
- Bound to anything else, set `AUTH_PASSWORD`. The app **refuses to start** on a
  non-loopback host without it.

Authentication is a single shared password and a signed, `HttpOnly`, `SameSite=Lax`
session cookie. There are no user accounts. That is the right size for a
single-operator tool and explicitly not enough for a shared, multi-tenant service.

### Exposing it to a network

1. Set `AUTH_PASSWORD` to something long.
2. Set `AUTH_SECRET` to a long random string. Without it a fresh one is generated
   each start, signing everyone out on restart — and any second worker or replica
   would reject the first one's cookies.
3. Terminate TLS in a reverse proxy and set `AUTH_COOKIE_SECURE=true`.
4. Only then change the compose port mapping from `127.0.0.1:8787:8787` to
   `8787:8787`.

Report security issues via a private GitHub security advisory rather than a public
issue.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DB_PATH` | `data/indexnow.db` in a checkout, else `~/.indexnow/indexnow.db` | SQLite file |
| `DEFAULT_ENDPOINT` | `indexnow` | `indexnow` or `bing` |
| `DEFAULT_UI_PORT` | `8787` | First port tried; scans upward for a free one |
| `AUTH_PASSWORD` | unset | UI password. Required off loopback |
| `AUTH_SECRET` | generated per start | Signs session cookies |
| `AUTH_COOKIE_SECURE` | `false` | Set `true` when serving over HTTPS |

Read from the environment or a `.env` file. See `.env.example`.

Endpoints: `indexnow` posts to `https://api.indexnow.org/indexnow`, which shares
with every participating engine; `bing` posts to `https://www.bing.com/indexnow`.
Set it per project in the UI, or per run with `--endpoint`.

Projects defined the old way, with `PROJECTS` and `PROJECT_<NAME>_*` variables, are
imported on startup only when no project of that name exists, so UI edits are never
overwritten.

## Protocol rules applied

- Max 10,000 URLs per POST; this tool sends 1,000.
- Key format validated as 8-128 chars of `A-Z a-z 0-9 -`.
- Host consistency enforced against the project host.
- `200` and `202` count as success.
- `400`, `403`, `404`, `422`, `429` reported with what each means. `429` retries
  and honors `Retry-After`; network errors retry up to 3 times.

Reference: [documentation](https://www.indexnow.org/documentation) ·
[FAQ](https://www.indexnow.org/faq) ·
[Bing](https://www.bing.com/indexnow/getstarted)

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests cover URL canonicalization, host validation, sitemap index detection,
dedupe rules, selection-scope guards, schema migration from the first release, and
session token forgery. They make no network calls.

Do not point `run` or `retry-failed` at a real project while testing — both hit the
live API. Stub `indexnow_tool.service.submit_url_batch` to exercise the run
pipeline offline.

`CLAUDE.md` documents the architecture and the invariants that encode past bugs.
Read it before changing the dedupe or scope logic.

## License

MIT. See [LICENSE](LICENSE).
