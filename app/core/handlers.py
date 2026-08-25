import logging

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.schemas.responses import ErrorDetail, ErrorResponse
from app.core.utils import get_current_timestamp

logger = logging.getLogger(__name__)


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Handler for HTTP exceptions."""
    request_id = getattr(request.state, "request_id", "unknown")

    error_response = ErrorResponse(
        request_id=request_id,
        timestamp=get_current_timestamp(),
        error=ErrorDetail(
            code=f"HTTP_{exc.status_code}",
            message=(
                exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            ),
        ),
    )

    return JSONResponse(
        status_code=exc.status_code, content=error_response.model_dump()
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handler for validation errors."""
    request_id = getattr(request.state, "request_id", "unknown")

    errors = []
    for error in exc.errors():
        errors.append(
            ErrorDetail(
                code="VALIDATION_ERROR",
                message=error.get("msg", "Validation error"),
                field=".".join(str(loc) for loc in error.get("loc", [])),
            )
        )

    error_response = ErrorResponse(
        request_id=request_id,
        timestamp=get_current_timestamp(),
        error=ErrorDetail(
            code="VALIDATION_ERROR", message="Request validation failed"
        ),
        errors=errors,
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_response.model_dump(),
    )


async def general_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Handler for unexpected exceptions."""
    request_id = getattr(request.state, "request_id", "unknown")

    logger.exception(
        f"Unhandled exception: {type(exc).__name__}: {str(exc)}",
        extra={"request_id": request_id, "path": request.url.path},
    )

    error_response = ErrorResponse(
        request_id=request_id,
        timestamp=get_current_timestamp(),
        error=ErrorDetail(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected error occurred",
        ),
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response.model_dump(),
    )
