"""Global FastAPI exception handlers.

Donor: veda/exceptions/handlers.py. Two bugs fixed during migration:

1. The donor's RequestValidationError handler always returned
   `errors[0] if len(errors) == 1 else errors[0]` — both branches are
   identical, so multi-field validation errors silently dropped every
   error but the first. Fixed: returns the full list when there is more
   than one.

2. The donor's AppException/VedaException handler always returned HTTP 500
   regardless of the error's semantic (a NOT_FOUND was reported as a server
   error). Fixed: added an explicit code -> status mapping.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from core.enums import ExceptionCode
from core.logging_config import logger
from exceptions.exception import AppException
from schemas.schemas import AppError, AppResponse

# One entry per ExceptionCode. Anything unmapped falls back to 500 —
# see the catch-all handler at the bottom.
_STATUS_BY_CODE: dict[str, int] = {
    ExceptionCode.VALIDATION_ERROR: 422,
    ExceptionCode.NOT_FOUND: 404,
    ExceptionCode.INFERENCE_ERROR: 500,
    ExceptionCode.TIMEOUT_ERROR: 504,
    ExceptionCode.SYSTEM_ERROR: 500,
    ExceptionCode.CONFIG_ERROR: 500,
    ExceptionCode.AGENT_ROUTING_ERROR: 500,
    ExceptionCode.SKILL_EXECUTION_ERROR: 500,
    ExceptionCode.SKILL_NOT_FOUND: 404,
    ExceptionCode.PERMISSION_DENIED: 403,
    ExceptionCode.RATE_LIMITED: 429,
    ExceptionCode.APPROVAL_TIMEOUT: 408,
    ExceptionCode.APPROVAL_REJECTED: 403,
    ExceptionCode.EGRESS_DENIED: 403,
}


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the FastAPI app."""

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        errors = []
        for err in exc.errors():
            location = "/".join(str(loc) for loc in err["loc"])
            errors.append(AppError(code=err["type"], message=f"{err['msg']} - {location}"))
        logger.warning(f"Request validation failed: {errors}")
        payload = errors if len(errors) > 1 else errors[0]
        return JSONResponse(
            status_code=422,
            content=AppResponse(error=payload).model_dump(mode="json", exclude_none=True),
        )

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        status_code = _STATUS_BY_CODE.get(exc.code, 500)
        logger.error(f"[{exc.class_name}] {exc.code}: {exc.message}")
        return JSONResponse(
            status_code=status_code,
            content=AppResponse(error=AppError(code=exc.code, message=exc.message)).model_dump(
                mode="json", exclude_none=True
            ),
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_handler(request: Request, exc: ValidationError):
        errors = []
        for err in exc.errors():
            location = "/".join(str(loc) for loc in err["loc"])
            errors.append(AppError(code=err["type"], message=f"{err['msg']} - {location}"))
        logger.warning(f"Pydantic validation failed: {errors}")
        payload = errors if len(errors) > 1 else errors[0]
        return JSONResponse(
            status_code=422,
            content=AppResponse(error=payload).model_dump(mode="json", exclude_none=True),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=AppResponse(
                error=AppError(code="SYSTEM_ERROR", message="An unexpected error occurred")
            ).model_dump(mode="json", exclude_none=True),
        )