"""
RFC 7807 Problem Details error handling for the FastAPI application.
"""

import http
import logging
from collections.abc import Mapping
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_serializer, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config.schema import SonghiveConfig

logger = logging.getLogger(__name__)


class ProblemDetails(BaseModel):
    """RFC 7807 Problem Details response model."""

    type: str = "about:blank"
    title: Optional[str] = None
    status: int
    detail: Optional[str] = None
    instance: Optional[str] = None
    extra: dict[str, Any] = {}

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _set_default_title(self) -> "ProblemDetails":
        """Default the title to the standard HTTP status phrase."""
        if self.title is None:
            try:
                self.title = http.HTTPStatus(self.status).phrase
            except ValueError:
                self.title = "Error"
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, handler) -> dict[str, Any]:
        """Serialize the model with extension keys at the top level."""
        data = handler(self)
        extensions = data.pop("extra", {})
        merged = dict(data)
        for key, value in extensions.items():
            if key not in merged:
                merged[key] = value
        return merged


class ProblemJSONResponse(JSONResponse):
    """JSON response with the RFC 7807 problem details media type."""

    media_type = "application/problem+json"


def _problem_response(
    problem: ProblemDetails,
    status_code: int,
    headers: Optional[Mapping[str, str]] = None,
) -> ProblemJSONResponse:
    """Return a :class:`ProblemJSONResponse` for the given problem details."""
    return ProblemJSONResponse(
        content=jsonable_encoder(problem.model_dump()),
        status_code=status_code,
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register RFC 7807 problem detail handlers for the FastAPI application."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> ProblemJSONResponse:
        if exc.detail is None:
            detail: Optional[str] = None
        elif isinstance(exc.detail, str):
            detail = exc.detail
        else:
            detail = str(exc.detail)

        problem = ProblemDetails(
            status=exc.status_code,
            detail=detail,
            instance=str(request.url.path),
        )
        return _problem_response(
            problem,
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> ProblemJSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        detail = first.get("msg", "Validation failed") if first else "Validation failed"
        problem = ProblemDetails(
            status=422,
            title="Validation failed",
            detail=detail,
            instance=str(request.url.path),
            extra={"errors": errors},
        )
        return _problem_response(problem, status_code=422)

    @app.exception_handler(Exception)
    async def _catch_all_exception_handler(
        request: Request,
        exc: Exception,
    ) -> ProblemJSONResponse:
        config: SonghiveConfig = request.app.state.config
        if config.server.debug:
            detail = str(exc)
        else:
            detail = "An internal server error occurred."

        logger.exception(
            "Unhandled exception at %s",
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )

        problem = ProblemDetails(
            status=500,
            title="Internal server error",
            detail=detail,
            instance=str(request.url.path),
        )
        return _problem_response(problem, status_code=500)
