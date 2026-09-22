"""nosniff, DENY, no-referrer — on every response, including streamed ones."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Response headers that cost nothing and rule out whole classes of attack.

    MIME sniffing and clickjacking, on any (MIME sniffing, clickjacking) on any
    endpoint that ends up rendered rather than consumed by a script.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        """Add the three security headers to whatever response comes back."""
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
