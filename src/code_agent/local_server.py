"""Reusable localhost browser infrastructure.

Provides a secure local web server capable of handling OAuth callbacks,
form submissions (like API key setups), and other interactive flows.

Security invariants
-------------------
* Binds **only** to ``127.0.0.1`` — never ``0.0.0.0``.
* Uses port ``0`` so the OS assigns an available ephemeral port.
* Rejects duplicate submissions with *409 Conflict* to prevent replay.
* Suppresses all request logging so that secrets never appear in output.
* Enforces a 16 KB maximum POST body size.
* Validates CSRF tokens using timing-safe comparison.
"""

from __future__ import annotations

import importlib.resources
import html
import secrets
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread
from typing import Any, Generic, Protocol, TypeVar
from urllib.parse import parse_qs, urlparse


T = TypeVar("T")


@dataclass(frozen=True)
class OAuthCallback:
    """Parsed OAuth redirect parameters from the authorization server."""
    code: str | None
    state: str | None
    error: str | None
    error_description: str | None


@dataclass(frozen=True)
class FormSubmission:
    """Parsed form submission payload."""
    fields: dict[str, str]


def _load_asset(filename: str) -> str:
    try:
        return importlib.resources.files("code_agent.auth.assets").joinpath(filename).read_text(encoding="utf-8")
    except Exception:
        from pathlib import Path
        path = Path(__file__).parent / "auth" / "assets" / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""


def _render_page(
    html_file: str,
    css_file: str,
    js_file: str | None,
    context: dict[str, str],
    *,
    raw_html_keys: frozenset[str] = frozenset(),
) -> str:
    html = _load_asset(html_file)
    css = _load_asset(css_file)
    
    html = html.replace("/* CSS_INJECTION_PLACEHOLDER */", css)
    
    if js_file:
        js = _load_asset(js_file)
        html = html.replace("/* JS_INJECTION_PLACEHOLDER */", js)
        
    for key, value in context.items():
        rendered_value = value if key in raw_html_keys else html_escape(value)
        html = html.replace(f"{{{{{key}}}}}", rendered_value)
        
    return html


def html_escape(value: str) -> str:
    """Escape text inserted into browser templates, including quotes."""
    return html.escape(value, quote=True)


# ------------------------------------------------------------------
# Handler protocol and shared rendering
# ------------------------------------------------------------------


class BrowserFlowHandler(Protocol[T]):
    def do_GET(self, request: "_LocalRequestHandler") -> T | None: ...
    def do_POST(self, request: "_LocalRequestHandler") -> T | None: ...
    def render_success(self, data: Any) -> str: ...
    def render_error(self, title: str, message: str) -> str: ...
    def render_timeout(self) -> str: ...


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


class _BasePageRenderer:
    """Shared rendering logic for success, error, and timeout pages.

    Subclasses override ``timeout_title`` and ``timeout_message`` to
    customise the 504 page.  All other rendering is identical across
    handlers.
    """

    timeout_title: str = "Timeout"
    timeout_message: str = "The operation timed out."

    def render_success(self, data: Any) -> str:
        title, subtitle = data
        return _render_page("success.html", "success.css", "success.js", {"name": title, "email": subtitle})

    def render_error(self, title: str, message: str) -> str:
        return _render_page("error.html", "error.css", None, {"title": title, "message": message})

    def render_timeout(self) -> str:
        return self.render_error(self.timeout_title, self.timeout_message)


class OAuthHandler(_BasePageRenderer):
    """Handles GET /callback for OAuth flows."""

    timeout_title = "Authentication Timeout"
    timeout_message = "The local server took too long to exchange the authentication token. Please try signing in again."

    def do_GET(self, request: "_LocalRequestHandler") -> OAuthCallback | None:
        parsed = urlparse(request.path)
        if parsed.path != "/callback":
            request.send_error(404)
            return None

        with request.server.lock:
            if request.server.payload is not None:
                request._send_simple(409, "Callback already received. This request has been rejected.")
                return None

        query = parse_qs(parsed.query)
        return OAuthCallback(
            code=_first(query, "code"),
            state=_first(query, "state"),
            error=_first(query, "error"),
            error_description=_first(query, "error_description"),
        )

    def do_POST(self, request: "_LocalRequestHandler") -> OAuthCallback | None:
        request.send_error(405)
        return None


class FormSubmissionHandler(_BasePageRenderer):
    """Handles GET /setup and POST /setup for interactive browser forms."""

    timeout_title = "Setup Timeout"
    timeout_message = "The local server took too long to validate your submission."

    def __init__(self, title: str, subtitle: str, description: str, fields_html: str, submit_text: str):
        self.title = title
        self.subtitle = subtitle
        self.description = description
        self.fields_html = fields_html
        self.submit_text = submit_text
        self.csrf_token = secrets.token_urlsafe(32)

    def do_GET(self, request: "_LocalRequestHandler") -> FormSubmission | None:
        parsed = urlparse(request.path)
        if parsed.path != "/setup":
            request.send_error(404)
            return None
            
        body_html = _render_page(
            "browser_form.html",
            "browser_form.css",
            "browser_form.js",
            {
                "title": self.title,
                "subtitle": self.subtitle,
                # These fragments are owned by the local CLI, not request or
                # OAuth input. They intentionally contain form markup.
                "description": self.description,
                "csrf_token": self.csrf_token,
                "fields_html": self.fields_html,
                "submit_text": self.submit_text,
            },
            raw_html_keys=frozenset({"description", "fields_html"}),
        )
        request._send_simple(200, body_html)
        return None

    def do_POST(self, request: "_LocalRequestHandler") -> FormSubmission | None:
        parsed = urlparse(request.path)
        if parsed.path != "/setup":
            request.send_error(404)
            return None

        with request.server.lock:
            if request.server.payload is not None:
                request._send_simple(409, "Form already submitted.")
                return None

        raw_content_length = request.headers.get("Content-Length")
        if raw_content_length is None:
            request._send_simple(400, "Missing Content-Length")
            return None
        try:
            content_length = int(raw_content_length)
        except ValueError:
            request._send_simple(400, "Invalid Content-Length")
            return None
        if content_length < 0:
            request._send_simple(400, "Invalid Content-Length")
            return None
        if content_length > 16 * 1024:
            # Drain the body so the client receives the response cleanly.
            remaining = content_length
            while remaining > 0:
                chunk = min(remaining, 4096)
                request.rfile.read(chunk)
                remaining -= chunk
            request._send_simple(413, "Payload Too Large")
            return None

        body = request.rfile.read(content_length).decode("utf-8")
        data = parse_qs(body)

        csrf = _first(data, "csrf_token")
        if not csrf or not secrets.compare_digest(csrf, self.csrf_token):
            request._send_simple(403, "Invalid CSRF token")
            return None

        fields = {k: _first(data, k) or "" for k in data.keys() if k != "csrf_token"}
        return FormSubmission(fields)


# ------------------------------------------------------------------
# HTTP plumbing
# ------------------------------------------------------------------


class _LocalRequestHandler(BaseHTTPRequestHandler):
    server: "CallbackHTTPServer"

    def do_GET(self) -> None:  # noqa: N802
        payload = self.server.handler.do_GET(self)
        if payload is not None:
            self._process_payload(payload)

    def do_POST(self) -> None:  # noqa: N802
        payload = self.server.handler.do_POST(self)
        if payload is not None:
            self._process_payload(payload)

    def _process_payload(self, payload: Any) -> None:
        if not self.server.try_set_payload(payload):
            self._send_simple(409, "Request already processed.")
            return

        # Wait up to response_timeout seconds for validation/processing
        if self.server.response_event.wait(timeout=self.server.response_timeout):
            response_data = self.server.response_data
            if response_data is not None:
                kind, html = response_data
                self._send_simple(200 if kind == "success" else 400, html)
            else:
                self._send_simple(500, "Internal Server Error")
        else:
            html = self.server.handler.render_timeout()
            self._send_simple(504, html)

    # Block other HTTP methods
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
        """Suppress logs to avoid leaking secrets."""


class CallbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    lock: Lock
    handler: BrowserFlowHandler[Any]
    payload: Any | None
    received: Event
    response_event: Event
    response_data: tuple[str, str] | None  # ("success"|"error", rendered_html)
    response_timeout: float

    def try_set_payload(self, payload: Any) -> bool:
        with self.lock:
            if self.payload is not None:
                return False
            self.payload = payload
            self.received.set()
            return True


# ------------------------------------------------------------------
# Public server API
# ------------------------------------------------------------------


class LocalBrowserServer(Generic[T]):
    """Reusable secure local HTTP server for interactive browser flows."""

    def __init__(
        self,
        handler: BrowserFlowHandler[T],
        *,
        response_timeout: float = 30.0,
    ) -> None:
        try:
            self._server = CallbackHTTPServer(("127.0.0.1", 0), _LocalRequestHandler)
        except OSError as exc:
            from .auth.oauth import OAuthError
            raise OAuthError(
                "Could not start the local listener.  "
                "Another process may be using the required port."
            ) from exc
            
        self._server.lock = Lock()
        self._server.handler = handler
        self._server.payload = None
        self._server.received = Event()
        self._server.response_event = Event()
        self._server.response_data = None
        self._server.response_timeout = response_timeout
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._lifecycle_lock = Lock()
        self._started = False
        self._closed = False

    @property
    def server_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"
        
    @property
    def redirect_uri(self) -> str:
        """Convenience property for OAuth compatibility."""
        return f"{self.server_url}/callback"

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Local browser server is already closed.")
            if self._started:
                return
            self._thread.start()
            self._started = True

    def wait(self, timeout_seconds: float) -> T | None:
        if not self._server.received.wait(timeout_seconds):
            return None
        return self._server.payload  # type: ignore

    def send_success(self, *args: Any) -> None:
        """Render the success page and unblock the HTTP handler thread.

        Arguments are forwarded to the handler's ``render_success`` method
        as a tuple, preserving full flexibility for future handlers.
        """
        html = self._server.handler.render_success(args)
        self._server.response_data = ("success", html)
        self._server.response_event.set()

    def send_error(self, title: str, message: str) -> None:
        html = self._server.handler.render_error(title, message)
        self._server.response_data = ("error", html)
        self._server.response_event.set()

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            started = self._started

        if not self._server.response_event.is_set():
            self._server.response_event.set()
        if started:
            self._server.shutdown()
        self._server.server_close()
        if started and self._thread.is_alive():
            self._thread.join(timeout=2)

    def __enter__(self) -> "LocalBrowserServer[T]":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
