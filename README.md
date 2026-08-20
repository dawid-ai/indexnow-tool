# IndexNow Automation Tool (UI + CLI)

Local Python tool to submit IndexNow updates with multi-project config, SQLite tracking, and retry/override controls.

## Features

- Multi-project setup in `.env` (`PROJECTS=site1,site2,...`)
- Per-run endpoint choice:
  - `indexnow` -> `https://api.indexnow.org/indexnow` (default)
  - `bing` -> `https://www.bing.com/indexnow`
- URL sources:
  - Sitemap URL (project default supported)
  - TXT file (one URL per line; Windows/Mac/Linux line endings supported)
  - CSV file (first column used)
  - Direct paste (one URL per line)
- SQLite history + dedupe:
  - Submit only new URLs by default
  - Retry failed URLs (bulk or selected IDs)
  - Mark failed as manually successful (bulk or selected IDs)
- Local web UI on `127.0.0.1` with automatic free-port selection

## Protocol Rules Applied

- Batch limit: max 10,000 URLs per POST.
- Key format validation (`8-128` chars, `A-Z a-z 0-9 -`).
- Host consistency validation: submitted URLs must match project host.
- Success codes: `200` and `202`.
- Error handling for `400`, `403`, `422`, `429`, including retry-on-429 and `Retry-After` support.

Reference docs:

- [IndexNow Documentation](https://www.indexnow.org/documentation)
- [IndexNow FAQ](https://www.indexnow.org/faq)
- [Bing IndexNow Get Started](https://www.bing.com/indexnow/getstarted)

## Quick Start

1. Create virtualenv and install dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copy env template:

   ```bash
   copy .env.example .env
   ```

3. Edit `.env` with your project(s), key(s), and hosts.

4. Start UI:

   ```bash
   python main.py serve
   ```

   The app auto-finds a free localhost port and prints the URL.

## .env Settings Reference (including alternatives)

### Global settings

- `PROJECTS` (required): comma-separated project IDs, for example `PROJECTS=demo,store,blog`
- `DB_PATH` (optional): SQLite file path, default `data/indexnow.db`
- `DEFAULT_ENDPOINT` (optional): `indexnow` or `bing`, default `indexnow`
- `DEFAULT_UI_PORT` (optional): preferred start port for UI auto-scan, default `8787`

### Per-project settings

For each project in `PROJECTS`, define:

- `PROJECT_<NAME>_HOST` (required): host only, example `www.example.com`
- `PROJECT_<NAME>_KEY` (required): IndexNow key
- `PROJECT_<NAME>_KEY_LOCATION` (optional): full public URL to key file
- `PROJECT_<NAME>_SITEMAP_URL` (optional): default sitemap URL for sitemap mode
- `PROJECT_<NAME>_DEFAULT_ENDPOINT` (optional): `indexnow` or `bing` for that project

`<NAME>` is uppercased internally, so `PROJECT_demo_HOST` and `PROJECT_DEMO_HOST` are treated the same in practice if your environment preserves case.

### Endpoint alternatives

- `indexnow` = `https://api.indexnow.org/indexnow` (recommended default)
- `bing` = `https://www.bing.com/indexnow`

You can set endpoint at 3 levels:

1. Global default via `DEFAULT_ENDPOINT`
2. Per-project override via `PROJECT_<NAME>_DEFAULT_ENDPOINT`
3. Per-run override in UI dropdown or CLI `--endpoint`

### Example A: Single project (minimal)

```env
PROJECTS=demo
PROJECT_DEMO_HOST=www.example.com
PROJECT_DEMO_KEY=90d0eecfb5144dc4bdbb41c28fd71a15
```

### Example B: Single project (full options)

```env
PROJECTS=demo
DB_PATH=data/indexnow.db
DEFAULT_ENDPOINT=indexnow
DEFAULT_UI_PORT=8787

PROJECT_DEMO_HOST=www.example.com
PROJECT_DEMO_KEY=90d0eecfb5144dc4bdbb41c28fd71a15
PROJECT_DEMO_KEY_LOCATION=https://www.example.com/90d0eecfb5144dc4bdbb41c28fd71a15.txt
PROJECT_DEMO_SITEMAP_URL=https://www.example.com/sitemap.xml
PROJECT_DEMO_DEFAULT_ENDPOINT=bing
```

### Example C: Multi-project

```env
PROJECTS=mainstore,blog
DEFAULT_ENDPOINT=indexnow

PROJECT_MAINSTORE_HOST=store.example.com
PROJECT_MAINSTORE_KEY=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
PROJECT_MAINSTORE_SITEMAP_URL=https://store.example.com/sitemap.xml
PROJECT_MAINSTORE_DEFAULT_ENDPOINT=bing

PROJECT_BLOG_HOST=www.example.com
PROJECT_BLOG_KEY=11111111-2222-3333-4444-555555555555
PROJECT_BLOG_KEY_LOCATION=https://www.example.com/keys/indexnow.txt
PROJECT_BLOG_DEFAULT_ENDPOINT=indexnow
```

### Notes

- If `PROJECT_<NAME>_SITEMAP_URL` is missing, you can still pass sitemap URL per run using CLI `--sitemap-url` or UI field.
- If `PROJECT_<NAME>_KEY_LOCATION` is omitted, requests are sent without `keyLocation` (works when key file is at host root).
- Invalid project keys fail fast at startup (must be 8-128 chars: letters, numbers, `-`).

## CLI Examples

Run from sitemap:

```bash
python main.py run --project demo --source sitemap --endpoint indexnow --sitemap-url https://www.example.com/sitemap.xml
```

Run from txt:

```bash
python main.py run --project demo --source txt --file urls.txt --endpoint bing
```

Run from csv:

```bash
python main.py run --project demo --source csv --file urls.csv
```

Run from paste:

```bash
python main.py run --project demo --source paste --paste "https://www.example.com/a
https://www.example.com/b"
```

Retry failed:

```bash
python main.py retry-failed --project demo --endpoint bing
python main.py retry-failed --project demo --ids 12,13,14
```

Mark failed as success:

```bash
python main.py mark-success --project demo
python main.py mark-success --project demo --ids 12,13,14
```

Show status:

```bash
python main.py status
```
