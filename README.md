# IndexNow Tool

Tell search engines your pages changed, the moment they change.

A self-hosted web UI and CLI for the [IndexNow](https://www.indexnow.org/) protocol.
Manage several sites and their keys in one place, pull URLs from a sitemap, file, or
paste, and watch each submission report progress and errors as it runs.

MIT licensed. Runs on your machine or in a container. No account, no third party.

![Recent runs](https://raw.githubusercontent.com/dawid-ai/indexnow-tool/main/docs/images/recent-runs.png)

*Every run is recorded. Run #10 re-submitted the same sitemap and sent only the 112
URLs that were new, skipping 1,465 already accepted.*

---

## What is IndexNow?

Search engines normally find your changes by re-crawling your site and hoping to
notice. That can take days or weeks, and most of the crawling is wasted on pages
that did not change.

[IndexNow](https://www.indexnow.org/) inverts it. You tell the engines directly:
*these URLs changed, come look.* It is an open protocol, and the whole thing is
three moving parts:

1. **A key.** Any random string, 8–128 characters of `A-Z a-z 0-9 -`.
2. **A key file.** That key, saved as `<key>.txt` at the root of your site, so
   `https://www.example.com/<key>.txt` returns it. This is how an engine proves the
   submission came from someone who controls the domain.
3. **A POST.** Your host, your key, and a list of up to 10,000 changed URLs.

Put the key file at your **site root**. Its folder decides what it authorizes: a key
at `https://example.com/public/key.txt` can only submit URLs under
`https://example.com/public/`, so a root-level key file is the only one that covers
the whole site. This tool checks that rule before submitting and tells you which
folder would work.

Submit once and it reaches every participating engine — they share submissions with
each other. You do not repeat the call per engine.

It does not guarantee indexing. It removes the discovery delay and tells the engines
where to spend their crawl budget. Whether a page then gets indexed is still up to
them.

### Which search engines use it

Per [indexnow.org](https://www.indexnow.org/), the participating engines are:

| Engine | |
| --- | --- |
| **Microsoft Bing** | Also feeds Copilot, DuckDuckGo, Ecosia, Yahoo and others that use the Bing index |
| **Yandex** | |
| **Seznam.cz** | Dominant in the Czech Republic |
| **Naver** | Dominant in South Korea |
| **Yep** | |

**Google does not participate.** It evaluated IndexNow and has not adopted it. Use
Search Console and sitemaps for Google. If you are only targeting Google, this tool
will not help you.

---

## What this tool does

The POST itself is one HTTP request. Doing it *properly*, repeatedly, across several
sites, is the part that gets tedious — and that is what this handles.

- **No duplicate submissions.** Every URL is tracked in SQLite. Re-run the same
  sitemap and only genuinely new URLs go out. URLs are canonicalized before
  comparison, so `HTTPS://WWW.Example.com/A` and `https://www.example.com/A` count
  once.
- **Multiple sites.** Each project has its own host, key, key-file location, default
  sitemap, and endpoint. Add and edit them in the UI.
- **Key verification.** One click fetches your key file the way an engine will, and
  tells you if it is missing or does not match. This is the single most common
  reason submissions fail.
- **Errors you can act on.** Every rejected batch is stored with its HTTP code and
  what that code actually means. Every skipped URL records why.
- **Live progress.** Long runs show a progress bar and a running log, not a spinner.
- **Retry and close-out.** Failed URLs are listed with their ids; retry all or a
  subset, or mark them resolved if you confirmed indexing another way.
- **CLI for automation.** Every UI action has a command, exits non-zero on failure,
  and drops straight into cron or Task Scheduler.

### URL sources

![New run](https://raw.githubusercontent.com/dawid-ai/indexnow-tool/main/docs/images/new-run.png)

| Source | Notes |
| --- | --- |
| Sitemap URL | Follows sitemap indexes up to 3 levels. Handles `.xml.gz`. |
| TXT file | One URL per line. Any line endings. |
| CSV file | First column. A header row is skipped automatically. |
| Paste | One URL per line. |

---

## Install

### pipx

```bash
pipx install indexnow-tool
indexnow
```

`indexnow` with no arguments starts the web UI and prints its address. The database
goes to `~/.indexnow/indexnow.db`.

### Docker

```bash
git clone https://github.com/dawid-ai/indexnow-tool
cd indexnow-tool
echo "AUTH_PASSWORD=pick-a-password" > .env
docker compose up -d
```

Open <http://localhost:8787>. Data lives on the `indexnow-data` volume. The compose
file publishes to `127.0.0.1` only — read [Security](#security) before changing that.

### From source

```bash
git clone https://github.com/dawid-ai/indexnow-tool
cd indexnow-tool
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
python main.py
```

Requires Python 3.10 or newer.

---

## First run

1. Open the UI and go to **Projects & keys**.
2. **Add a project**: a name you choose, the host (`www.example.com`), and your key.
   Do not have a key? Any random 8–128 character string of `A-Z a-z 0-9 -` works.
3. Save that key in a file named `<key>.txt`, containing nothing but the key, and
   upload it to your site root so `https://www.example.com/<key>.txt` serves it.
4. Press **Verify key**.
5. Go to **Submit**, pick the project and a source, and start a run.

Step 4 is the one people skip. A missing or mismatched key file gives you a `403`,
and in the worse case submissions look accepted and are silently ignored. Every run
re-checks it anyway and stops before submitting if it is wrong.

Leave **Key file URL** empty unless the key file is somewhere other than the site
root. Empty is correct for `https://www.example.com/<key>.txt`, and it is what you
want in almost every case.

### What actually gets sent

- URLs whose host does not match the project are rejected before any request.
  IndexNow rejects an entire batch on a host mismatch, so one stray URL would
  otherwise take down the whole submission.
- A URL the API already accepted is skipped. Tick **Resubmit URLs that were already
  accepted** to override.
- A URL that failed, or was stranded by an interrupted run, is picked up again next
  time.
- Batches are 1,000 URLs, well under the 10,000 limit, so progress is visible and
  one rejected batch does not fail the rest.

---

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
anything failed.

`retry-failed` and `mark-success` require either `--ids` or `--all`. An empty
selection never means "everything".

From a source checkout, use `python main.py` in place of `indexnow`.

### Daily sitemap submission

```bash
0 6 * * *  indexnow run --project mysite --source sitemap
```

Already-submitted URLs are skipped, so running this daily only sends what changed.

---

## Security

**This tool stores IndexNow keys in plaintext in its SQLite database.** Anyone who
can reach the UI can read them and submit URLs for your domains.

- On `localhost` with no password, it is a local tool and needs nothing more.
- Anywhere else, set `AUTH_PASSWORD`. The app **refuses to start** on a non-loopback
  host without it.

Authentication is one shared password and a signed, `HttpOnly`, `SameSite=Lax`
session cookie. There are no user accounts. That fits a single-operator tool and is
deliberately not enough for a shared multi-tenant service.

### Putting it on a network

1. Set `AUTH_PASSWORD` to something long.
2. Set `AUTH_SECRET` to a long random string. Without it a new one is generated each
   start, signing everyone out on restart — and a second worker or replica would
   reject the first one's cookies.
3. Terminate TLS in a reverse proxy and set `AUTH_COOKIE_SECURE=true`.
4. Only then change the compose port mapping from `127.0.0.1:8787:8787` to
   `8787:8787`.

Report security issues through a private GitHub security advisory, not a public issue.

---

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

**Endpoints.** `indexnow` posts to `https://api.indexnow.org/indexnow`, which shares
with every participating engine — that is the one you want. `bing` posts directly to
`https://www.bing.com/indexnow` if you specifically want Bing only. Set it per
project, or per run with `--endpoint`.

Projects defined the older way, with `PROJECTS` and `PROJECT_<NAME>_*` variables, are
imported on startup only when no project of that name exists, so UI edits are never
overwritten.

### Protocol rules applied

- Max 10,000 URLs per POST; this tool sends 1,000 at a time.
- Key format validated as 8–128 chars of `A-Z a-z 0-9 -`.
- Host consistency enforced against the project host.
- `200` and `202` count as success.
- `400`, `403`, `404`, `422`, `429` reported with what each means. `429` retries and
  honors `Retry-After`; network errors retry up to 3 times.

Reference: [documentation](https://www.indexnow.org/documentation) ·
[FAQ](https://www.indexnow.org/faq) ·
[Bing](https://www.bing.com/indexnow/getstarted)

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests cover URL canonicalization, host validation, sitemap index detection, dedupe
rules, selection-scope guards, schema migration from the first release, and session
token forgery. They make no network calls.

Do not point `run` or `retry-failed` at a real project while testing — both hit the
live API. Stub `indexnow_tool.service.submit_url_batch` to exercise the run pipeline
offline.

`CLAUDE.md` documents the architecture and the invariants that encode past bugs.
Read it before changing the dedupe or scope logic.

Issues and pull requests welcome.

## License

MIT — see [LICENSE](LICENSE).
