"""FastAPI application factory."""

import os

from fastapi import FastAPI, Request, Response

from app.auth import make_current_user
from app.db import init_db, make_engine, make_session_factory, session_dependency
from app.directory import Directory, directory_from_env
from app.metrics import install_metrics
from app.routes import accounts, approvals, tickets


def create_app(
    database_url: str | None = None,
    directory: Directory | None = None,
    base_url: str | None = None,
) -> FastAPI:
    engine = make_engine(database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    get_session = session_dependency(factory)
    current_user = make_current_user(get_session)
    directory = directory if directory is not None else directory_from_env()
    base_url = base_url or os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000")

    app = FastAPI(title="HelpDesk Autopilot", version="0.4.0")
    app.state.session_factory = factory
    app.state.directory = directory

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    install_metrics(app, factory)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(tickets.build_router(get_session, current_user, directory, base_url))
    app.include_router(accounts.build_router(get_session, current_user, directory, base_url))
    app.include_router(approvals.build_router(get_session, current_user, directory, base_url))
    return app
