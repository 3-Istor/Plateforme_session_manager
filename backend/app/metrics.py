"""Public process metrics, without database or user data."""

import time

from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


class MetricsMiddleware:
    """Serve only read-only /metrics before host validation.

    Scrapers may use pod IPs as Host. This endpoint never uses Host-derived
    content, sessions, secrets or the database. Other paths keep all checks.
    """

    def __init__(self, app: ASGIApp):
        self.app = app
        self.started_at = time.monotonic()

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            scope["type"] == "http"
            and scope["path"] == "/metrics"
            and scope["method"] in {"GET", "HEAD"}
        ):
            uptime = max(0.0, time.monotonic() - self.started_at)
            body = (
                "# HELP sessions_process_uptime_seconds Seconds since metrics middleware initialization; not a database health check.\n"
                "# TYPE sessions_process_uptime_seconds gauge\n"
                f"sessions_process_uptime_seconds {uptime:.3f}\n"
            )
            response = Response(
                content=body,
                headers={
                    "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
            if scope["method"] == "HEAD":
                response.body = b""
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
