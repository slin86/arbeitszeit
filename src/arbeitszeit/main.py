from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from . import db as dbmod
from .config import Settings, get_settings
from .models import Project, Role, User
from .oidc import build_oauth
from .routers import admin, approvals, auth, reports, timesheet
from .security import NotAuthenticated, hash_password
from .web import render


def bootstrap(settings: Settings) -> None:
    """Legt beim ersten Start den Admin an (AZ_ADMIN_EMAIL / AZ_ADMIN_PASSWORD) und ein Demo-Projekt."""
    with dbmod.session_factory()() as db:
        if db.scalar(select(func.count(User.id))) == 0 and settings.admin_email and settings.admin_password:
            db.add(
                User(
                    email=settings.admin_email.strip().lower(),
                    name=settings.admin_name,
                    password_hash=hash_password(settings.admin_password),
                    role=Role.admin,
                    annual_hours=settings.default_annual_hours,
                    vacation_days=settings.default_vacation_days,
                    state=settings.default_state,
                    start_date=date.today(),
                )
            )
        if db.scalar(select(func.count(Project.id))) == 0:
            db.add(Project(code="ALLG", name="Allgemein / Intern"))
        db.commit()


def create_app(settings: Settings | None = None, database_url: str | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.secret_key == "dev-secret-bitte-aendern" and settings.https_only:
        raise RuntimeError("AZ_SECRET_KEY muss in Produktion gesetzt werden.")
    dbmod.init_engine(database_url or settings.database_url)
    bootstrap(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(title="Arbeitszeit", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        https_only=settings.https_only,
        same_site="lax",
        max_age=60 * 60 * 12,
        session_cookie="az_session",
    )
    providers = settings.oidc_providers()
    app.state.providers = providers
    app.state.provider_keys = {p.key for p in providers}
    app.state.oauth = build_oauth(providers)

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.exception_handler(NotAuthenticated)
    async def _not_auth(request: Request, exc: NotAuthenticated):
        target = "/login"
        if request.method == "GET" and request.url.path != "/":
            target += f"?next={request.url.path}" + (f"%3F{request.url.query}" if request.url.query else "")
        if request.headers.get("hx-request"):
            return HTMLResponse(status_code=200, headers={"HX-Redirect": "/login"})
        return RedirectResponse(target, status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        user = None
        if uid := request.session.get("uid"):
            with dbmod.session_factory()() as db:
                user = db.get(User, uid)
        return render(request, "error.html", status_code=exc.status_code, user=user, code=exc.status_code, detail=exc.detail)

    for r in (auth.router, timesheet.router, approvals.router, reports.router, admin.router):
        app.include_router(r)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    return app


def app_factory() -> FastAPI:  # uvicorn --factory arbeitszeit.main:app_factory
    return create_app()
