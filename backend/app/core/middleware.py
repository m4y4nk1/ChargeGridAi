"""HTTP hardening (Section 14, OWASP ASVS L2): security headers on every response and
a per-client limit on sign-in attempts. The ingress adds its own request-rate limit in
production; this one also protects single-process deployments."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class LoginRateLimit(BaseHTTPMiddleware):
    """At most `limit` sign-in attempts per client IP per `window_s`."""

    def __init__(self, app: object, limit: int = 10, window_s: float = 60.0) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "POST" and request.url.path.endswith("/auth/login"):
            ip = request.client.host if request.client else "unknown"
            now = time.monotonic()
            q = self.hits[ip]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return JSONResponse(
                    {"detail": "Too many sign-in attempts; try again in a minute"},
                    status_code=429,
                    headers={"Retry-After": str(int(self.window))},
                )
            q.append(now)
        return await call_next(request)
