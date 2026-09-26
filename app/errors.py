"""Errors the services raise, and how the web layer turns them into responses.

Services describe what went wrong - an upstream API refused, a place was not found,
the DEM is missing - without knowing they run behind HTTP. app.main registers one
handler that turns any of them into a JSON error with the status it carries.
"""

from fastapi import Request
from fastapi.responses import JSONResponse


class ServiceError(Exception):
    """A failure with the HTTP status that describes it best."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class NotFound(ServiceError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class BadRequest(ServiceError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class Unavailable(ServiceError):
    """A dependency is missing or down: an upstream API, or data on disk."""

    def __init__(self, detail: str) -> None:
        super().__init__(503, detail)


class UpstreamError(ServiceError):
    """An upstream API answered, but not with what was asked for."""

    def __init__(self, detail: str, status_code: int = 502) -> None:
        super().__init__(status_code, detail)


async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
