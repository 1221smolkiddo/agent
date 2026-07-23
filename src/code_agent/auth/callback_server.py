"""Single-use localhost OAuth callback receiver.

Security invariants
-------------------
* Binds **only** to ``127.0.0.1`` — never ``0.0.0.0``.
* Uses port ``0`` so the OS assigns an available ephemeral port.
* Accepts a single ``GET /callback`` request, then rejects all further
  requests with *409 Conflict* to prevent replay through the local server.
* Rejects non-GET HTTP methods with *405 Method Not Allowed*.
* Suppresses all request logging so that authorization codes and state
  parameters never appear in debug output.
* Shuts down immediately after the callback is received.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class OAuthCallback:
    """Parsed OAuth redirect parameters from the authorization server."""

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

        # Reject duplicate callbacks to prevent replay attacks through the
        # local listener.  Only the first callback is accepted.
        if self.server.callback is not None:
            self._send_simple(409, "Callback already received.  This request has been rejected.")
            return

        query = parse_qs(parsed.query)
        self.server.callback = OAuthCallback(
            code=_first(query, "code"),
            state=_first(query, "state"),
            error=_first(query, "error"),
            error_description=_first(query, "error_description"),
        )
        self.server.received.set()
        self._send_simple(
            200,
            "<html><body><h2>Agent47 authentication complete</h2>"
            "<p>You can return to the terminal and close this tab.</p></body></html>",
        )

    # Block every other HTTP method so the listener cannot be probed.
    def do_POST(self) -> None:  # noqa: N802
        self.send_error(405)

    def do_PUT(self) -> None:  # noqa: N802
        self.send_error(405)

    def do_DELETE(self) -> None:  # noqa: N802
        self.send_error(405)

    def do_PATCH(self) -> None:  # noqa: N802
        self.send_error(405)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_error(405)

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_error(405)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_simple(self, code: int, body_html: str) -> None:
        body = body_html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        """Callback query parameters contain authorization codes; never log them."""


class CallbackHTTPServer(HTTPServer):
    callback: OAuthCallback | None
    received: Event


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


class LocalCallbackServer:
    """Single-use OAuth receiver, bound exclusively to ``127.0.0.1``."""

    def __init__(self) -> None:
        try:
            self._server = CallbackHTTPServer(("127.0.0.1", 0), _CallbackHandler)
        except OSError as exc:
            from .oauth import OAuthError

            raise OAuthError(
                "Could not start the local sign-in listener.  "
                "Another process may be using the required port.  "
                "Close conflicting applications and try again."
            ) from exc
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
            self._thread.join(timeout=2)

    def __enter__(self) -> "LocalCallbackServer":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
