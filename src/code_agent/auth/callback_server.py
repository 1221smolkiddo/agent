from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class OAuthCallback:
    code: str | None
    state: str | None
    error: str | None
    error_description: str | None


class _CallbackHandler(BaseHTTPRequestHandler):
    server: "CallbackHTTPServer"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        self.server.callback = OAuthCallback(
            code=_first(query, "code"),
            state=_first(query, "state"),
            error=_first(query, "error"),
            error_description=_first(query, "error_description"),
        )
        self.server.received.set()
        body = (
            "<html><body><h2>Agent47 authentication complete</h2>"
            "<p>You can return to the terminal and close this tab.</p></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        """Callback query parameters can contain sensitive codes; never log them."""


class CallbackHTTPServer(HTTPServer):
    callback: OAuthCallback | None
    received: Event


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


class LocalCallbackServer:
    """Single-use OAuth receiver, bound exclusively to 127.0.0.1."""

    def __init__(self) -> None:
        self._server = CallbackHTTPServer(("127.0.0.1", 0), _CallbackHandler)
        self._server.callback = None
        self._server.received = Event()
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/callback"

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout_seconds: float) -> OAuthCallback | None:
        if not self._server.received.wait(timeout_seconds):
            return None
        return self._server.callback

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=1)

    def __enter__(self) -> "LocalCallbackServer":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
