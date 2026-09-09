import asyncio

import pytest

from backend.app.main import app


def request(path="/metrics", method="GET", host="10.0.1.41:8000"):
    async def run():
        messages = []
        body_sent = False
        response_done = asyncio.Event()

        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await response_done.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_done.set()

        await app(
            {
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                "http_version": "1.1", "scheme": "http", "method": method,
                "path": path, "raw_path": path.encode(), "query_string": b"",
                "root_path": "", "headers": [(b"host", host.encode())],
                "client": ("10.0.0.198", 12345), "server": ("10.0.1.41", 8000),
            }, receive, send,
        )
        start = next(m for m in messages if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return start["status"], dict(start["headers"]), body

    return asyncio.run(run())


@pytest.mark.parametrize("host", ["10.0.1.41:8000", "localhost", "untrusted.example"])
def test_metrics_accepts_scraper_host_without_exposing_application_data(host):
    status, headers, body = request(host=host)
    assert status == 200
    assert headers[b"content-type"] == b"text/plain; version=0.0.4; charset=utf-8"
    assert headers[b"cache-control"] == b"no-store"
    lines = body.decode().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("# HELP sessions_process_uptime_seconds ")
    assert lines[1] == "# TYPE sessions_process_uptime_seconds gauge"
    metric, value = lines[2].split()
    assert metric == "sessions_process_uptime_seconds"
    assert float(value) >= 0
    assert body.endswith(b"\n")
    assert b"set-cookie" not in headers


def test_metrics_head_has_no_body():
    status, headers, body = request(method="HEAD")
    assert status == 200
    assert body == b""
    assert int(headers[b"content-length"]) > 0


@pytest.mark.parametrize("path,method", [
    ("/api/health", "GET"), ("/api/me", "GET"), ("/", "GET"),
    ("/metrics/", "GET"), ("/metrics/anything", "GET"),
    ("/metrics", "POST"), ("/metrics", "DELETE"),
])
def test_exception_does_not_disable_host_protection_elsewhere(path, method):
    status, _, body = request(path=path, method=method)
    assert status == 400
    assert body == b"Invalid host header"
