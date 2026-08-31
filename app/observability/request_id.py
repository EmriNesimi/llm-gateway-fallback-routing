import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Matches AuditLogEntry.request_id's column width (app/db/models.py). An
# oversized client-supplied X-Request-ID doesn't just get truncated
# somewhere harmless — on Postgres it's a hard INSERT failure
# (StringDataRightTruncationError). record_audit_log already catches that
# (see decision 004) so no request fails because of it, but a caller
# sending an oversized ID on every request would silently and permanently
# lose audit logging for all of their traffic. Capping it here, at the
# source, means that failure mode can't happen at all.
_MAX_REQUEST_ID_LENGTH = 64


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Every request gets a correlation ID: honors an incoming X-Request-ID
    (so a caller's own tracing can carry through), otherwise generates one.
    Stashed on request.state for handlers/audit log/spans, and echoed back
    on the response so a caller always has something to hand to support."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming = request.headers.get("X-Request-ID")
        if incoming and len(incoming) <= _MAX_REQUEST_ID_LENGTH:
            request_id = incoming
        else:
            request_id = uuid.uuid4().hex

        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
