from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from .channels import ChannelError, ChannelRegistry
from .database import create_database, init_database
from .schemas import (
    DashboardSummary,
    NotificationAccepted,
    NotificationCreate,
    TaskDetail,
    TaskList,
    TaskView,
)
from .service import (
    TaskConflictError,
    TaskNotFoundError,
    create_notification,
    dashboard_summary,
    get_task_detail,
    list_tasks,
    retry_task,
)
from .settings import Settings


VALID_STATUSES = {"PENDING", "PROCESSING", "RETRY_WAIT", "SUCCEEDED", "DEAD"}


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    engine, session_factory = create_database(app_settings.database_url)
    registry = ChannelRegistry.from_file(app_settings.channel_config_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_database(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Reliable HTTP Notification Service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    app.state.channel_registry = registry
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )

    def get_session():
        with session_factory() as session:
            yield session

    @app.get("/healthz")
    def healthz(session: Session = Depends(get_session)) -> dict[str, str]:
        session.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/api/v1/channels")
    def channels() -> dict[str, list[str]]:
        return {"items": registry.names()}

    @app.post(
        "/api/v1/notifications",
        response_model=NotificationAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_notification(payload: NotificationCreate, request: Request) -> NotificationAccepted:
        header_key = request.headers.get("Idempotency-Key")
        if header_key:
            payload.idempotency_key = header_key
        try:
            task, duplicated = create_notification(session_factory, registry, payload)
        except ChannelError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return NotificationAccepted(id=task.id, status=task.status, duplicated=duplicated)

    @app.get("/api/v1/tasks", response_model=TaskList)
    def query_tasks(
        status_filter: Optional[list[str]] = Query(
            default=None, alias="status", description="可重复传递；默认展示待重试和死信"
        ),
        channel: Optional[str] = None,
        q: Optional[str] = Query(default=None, max_length=200),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        session: Session = Depends(get_session),
    ) -> TaskList:
        statuses = status_filter if status_filter is not None else ["RETRY_WAIT", "DEAD"]
        invalid = set(statuses) - VALID_STATUSES
        if invalid:
            raise HTTPException(status_code=422, detail=f"未知状态: {', '.join(sorted(invalid))}")
        return list_tasks(
            session,
            statuses=statuses,
            channel=channel,
            query=q,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskDetail)
    def task_detail(task_id: str, session: Session = Depends(get_session)) -> TaskDetail:
        try:
            return get_task_detail(session, task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc

    @app.post("/api/v1/tasks/{task_id}/retry", response_model=TaskView)
    def manual_retry(task_id: str, session: Session = Depends(get_session)) -> TaskView:
        try:
            return retry_task(session, task_id, registry)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ChannelError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/dashboard/summary", response_model=DashboardSummary)
    def summary(session: Session = Depends(get_session)) -> DashboardSummary:
        return dashboard_summary(session)

    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            candidate = (frontend_dist / path).resolve()
            if candidate.is_file() and frontend_dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(frontend_dist / "index.html")

    return app


app = create_app()

