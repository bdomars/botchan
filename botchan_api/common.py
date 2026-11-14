"""HTTP plumbing shared by the user-facing and admin apps."""

import logging
import secrets
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import RequestResponseEndpoint


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


def error(status: int, code: str, message: str) -> APIError:
    return APIError(status, code, message)


def now() -> datetime:
    return datetime.now(UTC)


def security_compare(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)


def install_common(app: FastAPI) -> None:
    """Security headers and the JSON error format every BotChan app uses."""

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_REQUEST",
                "message": "The request did not pass validation.",
                "details": jsonable_encoder(exc.errors()),
            },
        )


def session_dependency(
    session_factory: async_sessionmaker,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    return db_session


def install_probes(app: FastAPI, session_factory: async_sessionmaker, log: logging.Logger) -> None:
    db_session = session_dependency(session_factory)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        # Liveness only: the process is up and serving. Deliberately does not
        # touch the database, so a database outage never restarts the pods.
        return JSONResponse({"status": "ok"})

    @app.get("/readyz", include_in_schema=False)
    async def readyz(db: Annotated[AsyncSession, Depends(db_session)]) -> JSONResponse:
        try:
            await db.execute(text("SELECT 1"))
        except Exception:
            log.exception("Readiness check failed")
            return JSONResponse(status_code=503, content={"status": "no database"})
        return JSONResponse({"status": "ok"})
