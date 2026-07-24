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
* Shuts down immediately after the callback is received and the response is sent.
"""

from __future__ import annotations

import importlib.resources
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


def _load_asset(filename: str) -> str:
    # We use importlib.resources to read the files packaged in the auth/assets directory
    try:
        # For Python 3.9+ we use files()
        return importlib.resources.files("code_agent.auth.assets").joinpath(filename).read_text(encoding="utf-8")
    except Exception:
        # Fallback if package is not installed normally, try relative path
        from pathlib import Path
        path = Path(__file__).parent / "assets" / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""


def _render_page(html_file: str, css_file: str, js_file: str | None, context: dict[str, str]) -> str:
    html = _load_asset(html_file)
    css = _load_asset(css_file)
    
    html = html.replace("/* CSS_INJECTION_PLACEHOLDER */", css)
    
    if js_file:
        js = _load_asset(js_file)
        html = html.replace("/* JS_INJECTION_PLACEHOLDER */", js)
        
    for key, value in context.items():
        html = html.replace(f"{{{{{key}}}}}", value)
        
    return html


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
        # Notify the main thread that we received the callback
        self.server.received.set()
        
        # Wait for the main thread to fetch tokens and profile
        # We give it a generous timeout (e.g. 15 seconds) to complete network requests
        if self.server.response_event.wait(timeout=15.0):
            if self.server.success_data:
                name, email = self.server.success_data
                body_html = _render_page("success.html", "success.css", "success.js", {"name": name, "email": email})
                self._send_simple(200, body_html)
            elif self.server.error_data:
                title, message = self.server.error_data
                body_html = _render_page("error.html", "error.css", None, {"title": title, "message": message})
                self._send_simple(400, body_html)
            else:
                self._send_simple(500, "Internal Server Error")
        else:
            # If the main thread timed out or crashed
            body_html = _render_page("error.html", "error.css", None, {
                "title": "Authentication Timeout",
                "message": "The local server took too long to exchange the authentication token. Please try signing in again."
            })
            self._send_simple(504, body_html)

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
    response_event: Event
    success_data: tuple[str, str] | None
    error_data: tuple[str, str] | None


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
        self._server.response_event = Event()
        self._server.success_data = None
        self._server.error_data = None
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

    def send_success(self, name: str, email: str) -> None:
        """Signal the callback handler to send the success page."""
        self._server.success_data = (name, email)
        self._server.response_event.set()

    def send_error(self, title: str, message: str) -> None:
        """Signal the callback handler to send the error page."""
        self._server.error_data = (title, message)
        self._server.response_event.set()

    def close(self) -> None:
        # If the response hasn't been sent yet (e.g. error before wait), unblock it
        if not self._server.response_event.is_set():
            self._server.response_event.set()
            
        self._server.shutdown()
        self._server.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def __enter__(self) -> "LocalCallbackServer":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
