from __future__ import annotations

import argparse
import json
import sys
import time

import uvicorn

from .config import clean_endpoint, load_config, normalize_host, validate_project_fields
from .ports import find_free_port
from .service import IndexNowService, RunRequest
from .ui import create_app

LEVEL_PREFIX = {"info": "  ", "warning": "! ", "error": "X "}


def _parse_ids(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    ids = [int(item.strip()) for item in raw.split(",") if item.strip()]
    if not ids:
        raise SystemExit("--ids was given but contained no numbers.")
    return ids


def _resolve_scope(ids_raw: str | None, use_all: bool, action: str) -> list[int] | None:
    """Return None for 'everything', a list for 'these ids'. Never guess."""
    if use_all and ids_raw:
        raise SystemExit("Pass either --all or --ids, not both.")
    if not use_all and not ids_raw:
        raise SystemExit(f"Specify what to {action}: --ids 12,13 or --all.")
    return None if use_all else _parse_ids(ids_raw)


def _follow_run(service: IndexNowService, run_id: int) -> int:
    """Stream a background run's log to stdout. Returns a process exit code."""
    after = 0
    while True:
        state = service.progress(run_id, after)
        for message in state["messages"]:
            after = max(after, message["id"])
            print(f"{LEVEL_PREFIX.get(message['level'], '  ')}{message['message']}")
        if state["finished"]:
            print(
                f"\nRun #{run_id} {state['status']}: "
                f"{state['accepted']} accepted, {state['failed']} failed, "
                f"{state['skipped']} skipped as duplicate, {state['invalid']} invalid."
            )
            if state["error"]:
                print(f"Error: {state['error']}", file=sys.stderr)
            return 1 if state["status"] != "completed" or state["failed"] else 0
        time.sleep(0.4)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="indexnow", description="IndexNow automation tool")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("projects", help="List configured projects")
    sub.add_parser("status", help="Show recent run status as JSON")

    add = sub.add_parser("project-add", help="Add or update a project")
    add.add_argument("--name", required=True)
    add.add_argument("--host", required=True)
    add.add_argument("--key", required=True)
    add.add_argument("--key-location", default=None)
    add.add_argument("--sitemap-url", default=None)
    add.add_argument("--endpoint", choices=["indexnow", "bing"], default="indexnow")

    verify = sub.add_parser("verify-key", help="Check the key file is reachable and correct")
    verify.add_argument("--project", required=True)

    serve = sub.add_parser("serve", help="Start the local UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--start-port", type=int, default=None)

    run = sub.add_parser("run", help="Submit URLs from a source")
    run.add_argument("--project", required=True)
    run.add_argument("--endpoint", choices=["indexnow", "bing"], default=None)
    run.add_argument("--source", choices=["sitemap", "txt", "csv", "paste"], required=True)
    run.add_argument("--sitemap-url", default=None)
    run.add_argument("--file", default=None)
    run.add_argument("--paste", default=None)
    run.add_argument(
        "--force", action="store_true", help="Resubmit URLs already accepted by the API"
    )

    retry = sub.add_parser("retry-failed", help="Retry failed URLs")
    retry.add_argument("--project", required=True)
    retry.add_argument("--endpoint", choices=["indexnow", "bing"], default=None)
    retry.add_argument("--ids", default=None, help="Comma separated URL entry ids")
    retry.add_argument("--all", action="store_true", help="Every failed URL in the project")

    mark = sub.add_parser("mark-success", help="Mark failed URLs as manually successful")
    mark.add_argument("--project", required=True)
    mark.add_argument("--ids", default=None, help="Comma separated URL entry ids")
    mark.add_argument("--all", action="store_true", help="Every failed URL in the project")

    export = sub.add_parser("export", help="Write URL entries to CSV on stdout")
    export.add_argument("--project", required=True)
    export.add_argument("--status", default=None, choices=["new", "submitted", "failed", "manually_marked_success"])

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    config = load_config()
    service = IndexNowService(config)

    if args.command == "projects":
        projects = service.projects()
        if not projects:
            print("No projects configured. Add one with 'project-add' or on the Projects page.")
            return
        for name, project in projects.items():
            print(
                f"{name}: host={project.host} endpoint={project.default_endpoint} "
                f"sitemap={project.sitemap_url or '-'}"
            )
        return

    if args.command == "project-add":
        host = normalize_host(args.host)
        errors = validate_project_fields(
            args.name, host, args.key, args.key_location, args.sitemap_url
        )
        if errors:
            raise SystemExit("\n".join(errors))
        service.db.upsert_project(
            name=args.name.strip(),
            host=host,
            key=args.key.strip(),
            key_location=args.key_location,
            sitemap_url=args.sitemap_url,
            default_endpoint=clean_endpoint(args.endpoint),
        )
        print(f"Saved project '{args.name}'.")
        return

    if args.command == "verify-key":
        ok, message = service.verify_project_key(args.project)
        print(message)
        raise SystemExit(0 if ok else 1)

    if args.command == "status":
        runs = service.db.list_recent_runs(20)
        print(json.dumps([dict(row) for row in runs], indent=2, default=str))
        return

    if args.command == "serve":
        app = create_app(config, service)
        start_port = args.start_port or config.default_ui_port
        selected_port = find_free_port(host=args.host, start_port=start_port)
        print(f"IndexNow UI running on http://{args.host}:{selected_port}")
        uvicorn.run(app, host=args.host, port=selected_port)
        return

    if args.command == "run":
        file_bytes = None
        if args.file:
            with open(args.file, "rb") as handle:
                file_bytes = handle.read()
        run_id = service.start_run(
            RunRequest(
                project_name=args.project,
                source_type=args.source,
                endpoint=args.endpoint,
                sitemap_url=args.sitemap_url,
                file_bytes=file_bytes,
                pasted_urls=args.paste,
                force=args.force,
                label=args.file,
            )
        )
        raise SystemExit(_follow_run(service, run_id))

    if args.command == "retry-failed":
        ids = _resolve_scope(args.ids, args.all, "retry")
        run_id = service.start_retry(args.project, endpoint=args.endpoint, entry_ids=ids)
        raise SystemExit(_follow_run(service, run_id))

    if args.command == "mark-success":
        ids = _resolve_scope(args.ids, args.all, "mark")
        count = service.mark_failed_success(args.project, entry_ids=ids)
        print(json.dumps({"marked_success_count": count}, indent=2))
        return

    if args.command == "export":
        import csv

        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(["id", "url", "status", "last_http_code", "attempt_count", "last_endpoint", "updated_at"])
        for row in service.db.iter_entries_for_export(args.project, args.status):
            writer.writerow(
                [row["id"], row["url"], row["status"], row["last_http_code"],
                 row["attempt_count"], row["last_endpoint"], row["updated_at"]]
            )
        return


if __name__ == "__main__":
    main()
