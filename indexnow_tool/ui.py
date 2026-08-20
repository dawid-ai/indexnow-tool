from __future__ import annotations

import csv
import io
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .config import AppConfig, clean_endpoint, normalize_host, validate_project_fields
from .service import IndexNowService, RunRequest


def _parse_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for item in (raw or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit():
            raise ValueError(f"'{item}' is not a numeric URL id.")
        ids.append(int(item))
    return ids


def _redirect(path: str, **params: str) -> RedirectResponse:
    query = "&".join(f"{key}={quote(str(value))}" for key, value in params.items() if value)
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


def create_app(config: AppConfig, service: IndexNowService | None = None) -> FastAPI:
    app = FastAPI(title="IndexNow Tool")
    service = service or IndexNowService(config)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    def render(name: str, request: Request, **context) -> HTMLResponse:
        return templates.TemplateResponse(
            name,
            {
                "request": request,
                "projects": service.projects(),
                "notice": request.query_params.get("notice"),
                "error": request.query_params.get("error"),
                **context,
            },
        )

    # ------------------------------------------------------------- dashboard

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return render(
            "dashboard.html",
            request,
            runs=service.db.list_recent_runs(30),
            summaries=service.db.list_projects_summary(),
        )

    @app.post("/run")
    async def start_run(
        project: str = Form(...),
        endpoint: str = Form("indexnow"),
        source_type: str = Form(...),
        sitemap_url: str = Form(""),
        pasted_urls: str = Form(""),
        force: str = Form("false"),
        file: UploadFile | None = File(default=None),
    ):
        file_bytes = None
        label = None
        if file is not None and file.filename:
            file_bytes = await file.read()
            label = file.filename

        request_obj = RunRequest(
            project_name=project,
            source_type=source_type,
            endpoint=clean_endpoint(endpoint),
            sitemap_url=sitemap_url,
            file_bytes=file_bytes,
            pasted_urls=pasted_urls,
            force=force.lower() == "true",
            label=label,
        )
        try:
            run_id = service.start_run(request_obj)
        except ValueError as exc:
            return _redirect("/", error=str(exc))
        return _redirect(f"/runs/{run_id}")

    # ------------------------------------------------------------- live view

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_view(request: Request, run_id: int) -> HTMLResponse:
        run = service.db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        return render("run.html", request, run=run)

    @app.get("/api/runs/{run_id}")
    def run_progress(run_id: int, after: int = 0) -> JSONResponse:
        try:
            return JSONResponse(service.progress(run_id, after))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -------------------------------------------------------------- projects

    @app.get("/projects", response_class=HTMLResponse)
    def projects_page(request: Request) -> HTMLResponse:
        return render(
            "projects.html",
            request,
            rows=service.db.list_projects(),
            summaries={r["project_name"]: r for r in service.db.list_projects_summary()},
            edit=request.query_params.get("edit", ""),
        )

    @app.post("/projects/save")
    def save_project(
        name: str = Form(...),
        host: str = Form(...),
        key: str = Form(...),
        key_location: str = Form(""),
        sitemap_url: str = Form(""),
        default_endpoint: str = Form("indexnow"),
        original_name: str = Form(""),
    ):
        name = name.strip()
        host = normalize_host(host)
        key = key.strip()
        key_location = key_location.strip() or None
        sitemap_url = sitemap_url.strip() or None

        errors = validate_project_fields(name, host, key, key_location, sitemap_url)
        if errors:
            return _redirect("/projects", error=" ".join(errors))

        original = original_name.strip() or None
        if original != name and service.db.get_project(name) is not None:
            return _redirect("/projects", error=f"A project named '{name}' already exists.")

        service.db.upsert_project(
            name=name,
            host=host,
            key=key,
            key_location=key_location,
            sitemap_url=sitemap_url,
            default_endpoint=clean_endpoint(default_endpoint),
            original_name=original,
        )
        return _redirect("/projects", notice=f"Saved project '{name}'.")

    @app.post("/projects/delete")
    def delete_project(name: str = Form(...), confirm_name: str = Form("")):
        if confirm_name.strip() != name:
            return _redirect(
                "/projects",
                error=f"Type '{name}' in the confirm box to delete it.",
            )
        service.db.delete_project(name)
        return _redirect(
            "/projects",
            notice=f"Deleted project '{name}'. Its URL history was kept.",
        )

    @app.post("/projects/verify")
    def verify_project(name: str = Form(...)):
        try:
            ok, message = service.verify_project_key(name)
        except ValueError as exc:
            return _redirect("/projects", error=str(exc))
        return _redirect("/projects", **({"notice": message} if ok else {"error": message}))

    # ---------------------------------------------------------------- failed

    @app.post("/failed/retry")
    def retry_failed(
        project: str = Form(...),
        endpoint: str = Form("indexnow"),
        selected_ids: str = Form(""),
        scope: str = Form("selected"),
    ):
        try:
            ids = None if scope == "all" else _parse_ids(selected_ids)
        except ValueError as exc:
            return _redirect("/", error=str(exc))

        if ids is not None and not ids:
            return _redirect("/", error="No URL ids given. Enter ids or choose 'All failed URLs'.")

        try:
            run_id = service.start_retry(project, endpoint=clean_endpoint(endpoint), entry_ids=ids)
        except ValueError as exc:
            return _redirect("/", error=str(exc))
        return _redirect(f"/runs/{run_id}")

    @app.post("/failed/mark-success")
    def mark_success(
        project: str = Form(...),
        selected_ids: str = Form(""),
        scope: str = Form("selected"),
    ):
        try:
            ids = None if scope == "all" else _parse_ids(selected_ids)
        except ValueError as exc:
            return _redirect("/", error=str(exc))

        if ids is not None and not ids:
            return _redirect("/", error="No URL ids given. Enter ids or choose 'All failed URLs'.")

        try:
            count = service.mark_failed_success(project, entry_ids=ids)
        except ValueError as exc:
            return _redirect("/", error=str(exc))
        return _redirect("/", notice=f"Marked {count} failed URLs as manually successful.")

    @app.get("/projects/{name}/failed", response_class=HTMLResponse)
    def failed_view(request: Request, name: str) -> HTMLResponse:
        try:
            service.get_project(name)
        except ValueError as exc:
            return _redirect("/", error=str(exc))
        return render(
            "failed.html",
            request,
            project_name=name,
            rows=service.db.list_failed_entries(name),
            failed_total=service.db.count_failed_entries(name),
        )

    @app.get("/projects/{name}/export.csv")
    def export_csv(name: str, status: str = ""):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["id", "url", "status", "last_http_code", "attempt_count", "last_endpoint", "last_response_excerpt", "updated_at"]
        )
        for row in service.db.iter_entries_for_export(name, status or None):
            writer.writerow(
                [
                    row["id"], row["url"], row["status"], row["last_http_code"],
                    row["attempt_count"], row["last_endpoint"],
                    (row["last_response_excerpt"] or "").replace("\n", " "), row["updated_at"],
                ]
            )
        buffer.seek(0)
        filename = f"{name}-{status or 'all'}.csv"
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app
