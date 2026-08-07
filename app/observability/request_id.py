import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Every request gets a correlation ID: honors an incoming X-Request-ID
    (so a caller's own tracing can carry through), otherwise generates one.
    Stashed on request.state for handlers/audit log/spans, and echoed back
    on the response so a caller always has something to hand to support."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
