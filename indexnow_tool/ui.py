from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import AppConfig
from .service import IndexNowService


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="IndexNow Tool")
    service = IndexNowService(config)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "projects": config.projects,
                "runs": service.db.list_recent_runs(30),
                "summaries": service.db.list_projects_summary(),
            },
        )

    @app.post("/run", response_class=HTMLResponse)
    async def run_submission(
        request: Request,
        project: str = Form(...),
        endpoint: str = Form("indexnow"),
        source_type: str = Form(...),
        sitemap_url: str = Form(""),
        pasted_urls: str = Form(""),
        file: UploadFile | None = File(default=None),
    ) -> HTMLResponse:
        file_bytes = await file.read() if file else None
        project_cfg = service.get_project(project)
        selected_sitemap = sitemap_url.strip() or project_cfg.sitemap_url

        summary = service.run_from_source(
            project_name=project,
            source_type=source_type,
            endpoint=endpoint,
            sitemap_url=selected_sitemap,
            file_bytes=file_bytes,
            pasted_urls=pasted_urls,
        )

        failed_rows = service.db.list_failed_entries(project)
        return templates.TemplateResponse(
            "result.html",
            {
                "request": request,
                "summary": summary,
                "project": project,
                "endpoint": endpoint,
                "failed_rows": failed_rows,
            },
        )

    @app.post("/failed/retry", response_class=HTMLResponse)
    async def retry_failed(
        request: Request,
        project: str = Form(...),
        endpoint: str = Form("indexnow"),
        selected_ids: str = Form(""),
        all_failed: str = Form("false"),
    ) -> HTMLResponse:
        ids = [int(item.strip()) for item in selected_ids.split(",") if item.strip()]
        if all_failed.lower() == "true":
            ids = []
        summary = service.retry_failed(project, endpoint=endpoint, entry_ids=ids or None)
        failed_rows = service.db.list_failed_entries(project)
        return templates.TemplateResponse(
            "result.html",
            {
                "request": request,
                "summary": summary,
                "project": project,
                "endpoint": endpoint,
                "failed_rows": failed_rows,
            },
        )

    @app.post("/failed/mark-success", response_class=HTMLResponse)
    async def mark_failed_success(
        request: Request,
        project: str = Form(...),
        selected_ids: str = Form(""),
        all_failed: str = Form("false"),
    ) -> HTMLResponse:
        if all_failed.lower() == "true":
            ids = None
        else:
            ids = [int(item.strip()) for item in selected_ids.split(",") if item.strip()]
        count = service.mark_failed_success(project, entry_ids=ids)
        failed_rows = service.db.list_failed_entries(project)
        return templates.TemplateResponse(
            "result.html",
            {
                "request": request,
                "summary": {
                    "run_id": "-",
                    "submitted_count": 0,
                    "accepted_count": 0,
                    "failed_count": 0,
                    "skipped_existing_count": 0,
                    "invalid_count": 0,
                    "invalid_details": [],
                },
                "project": project,
                "endpoint": "-",
                "failed_rows": failed_rows,
                "message": f"Marked {count} failed URLs as manually successful.",
            },
        )

    return app
