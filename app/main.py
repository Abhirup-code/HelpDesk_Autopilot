"""FastAPI application factory."""

from fastapi import FastAPI, Request, Response

from app.auth import make_current_user
from app.db import Base, make_engine, make_session_factory, session_dependency
from app.routes.tickets import build_router


def create_app(database_url: str | None = None) -> FastAPI:
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    get_session = session_dependency(factory)
    current_user = make_current_user(get_session)

    app = FastAPI(title="HelpDesk Autopilot", version="0.1.0")
    app.state.session_factory = factory

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_router(get_session, current_user))
    return app
