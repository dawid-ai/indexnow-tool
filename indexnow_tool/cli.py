from __future__ import annotations

import argparse
import json

import uvicorn

from .config import load_config
from .ports import find_free_port
from .service import IndexNowService
from .ui import create_app


def _parse_ids(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IndexNow automation tool")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("projects", help="List configured projects")
    sub.add_parser("status", help="Show recent run status")

    serve = sub.add_parser("serve", help="Start local UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--start-port", type=int, default=None)

    run = sub.add_parser("run", help="Run submission from a source")
    run.add_argument("--project", required=True)
    run.add_argument("--endpoint", choices=["indexnow", "bing"], default=None)
    run.add_argument("--source", choices=["sitemap", "txt", "csv", "paste"], required=True)
    run.add_argument("--sitemap-url", default=None)
    run.add_argument("--file", default=None)
    run.add_argument("--paste", default=None)

    retry = sub.add_parser("retry-failed", help="Retry failed URLs")
    retry.add_argument("--project", required=True)
    retry.add_argument("--endpoint", choices=["indexnow", "bing"], default=None)
    retry.add_argument("--ids", default=None, help="Comma separated URL entry ids")

    mark = sub.add_parser("mark-success", help="Mark failed URLs as successful")
    mark.add_argument("--project", required=True)
    mark.add_argument("--ids", default=None, help="Comma separated URL entry ids")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    config = load_config()
    service = IndexNowService(config)

    if args.command == "projects":
        for name, project in config.projects.items():
            print(
                f"{name}: host={project.host} default_endpoint={project.default_endpoint} "
                f"sitemap={project.sitemap_url or '-'}"
            )
        return

    if args.command == "status":
        runs = service.db.list_recent_runs(20)
        print(json.dumps([dict(row) for row in runs], indent=2, default=str))
        return

    if args.command == "serve":
        app = create_app(config)
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
        summary = service.run_from_source(
            project_name=args.project,
            source_type=args.source,
            endpoint=args.endpoint,
            sitemap_url=args.sitemap_url,
            file_bytes=file_bytes,
            pasted_urls=args.paste,
        )
        print(json.dumps(summary.__dict__, indent=2))
        return

    if args.command == "retry-failed":
        summary = service.retry_failed(
            project_name=args.project,
            endpoint=args.endpoint,
            entry_ids=_parse_ids(args.ids),
        )
        print(json.dumps(summary.__dict__, indent=2))
        return

    if args.command == "mark-success":
        count = service.mark_failed_success(
            project_name=args.project,
            entry_ids=_parse_ids(args.ids),
        )
        print(json.dumps({"marked_success_count": count}, indent=2))
        return


if __name__ == "__main__":
    main()
