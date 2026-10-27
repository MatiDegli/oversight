"""The single error format of section 4.7: `{"error": {"code", "message"}}`, for every error."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from oversight_contracts.commands import ERROR_STATUS, ErrorCode, ErrorDetail, ErrorResponse


class ApiError(Exception):
    """Raised by a route; answered with the code's status from `ERROR_STATUS`."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message


def error_response(code: ErrorCode, message: str) -> JSONResponse:
    """The error body with its status from `ERROR_STATUS`: no caller picks a status of its own."""
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(mode="json"), status_code=ERROR_STATUS[code])


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, error: ApiError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return error_response(error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
            for problem in error.errors()
        )
        return error_response("invalid_request", problems or "invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, error: StarletteHTTPException) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        # An unknown path is not_found. The contract has no code for the rest, such as 405, so
        # they are invalid_request, with the status the contract gives it.
        if error.status_code == 404:
            return error_response("not_found", "no such resource")
        return error_response("invalid_request", str(error.detail))

    @app.exception_handler(Exception)
    async def _internal(_: Request, error: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        # Anything unexpected: the contract's internal (500), with no traceback in the body. The
        # module that failed has sent an error event with the details it may show.
        return error_response(
            "internal", f"the server failed ({type(error).__name__}); see the error events"
        )
