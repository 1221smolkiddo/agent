"""Tests for the browser-based API key setup flow."""

from __future__ import annotations

import concurrent.futures
import socket
import threading
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
from typer.testing import CliRunner

from code_agent.cli import _build_key_setup_handler, app
from code_agent.local_server import LocalBrowserServer, FormSubmissionHandler


runner = CliRunner()


class FakeCredentialStore:
    def __init__(self):
        self.keys = {}
        
    def set_provider_key(self, name, key):
        self.keys[name] = key
        
    def get_provider_key(self, name):
        return self.keys.get(name)


@pytest.fixture
def fake_keyring(monkeypatch):
    store = FakeCredentialStore()
    monkeypatch.setattr("code_agent.cli.CredentialStore", lambda: store)
    return store


@pytest.fixture
def mock_validation(monkeypatch):
    monkeypatch.setattr("code_agent.cli.validate_provider_key", lambda name, key, base_url: (True, "Valid"))


# ------------------------------------------------------------------
# Helper: extract CSRF token from rendered HTML
# ------------------------------------------------------------------

def _extract_csrf(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.find(marker) + len(marker)
    end = html.find('"', start)
    return html[start:end]


# ------------------------------------------------------------------
# Unit tests: FormSubmissionHandler security
# ------------------------------------------------------------------


def test_form_submission_handler_csrf_protection():
    """Verify CSRF token is required and validated."""
    handler = FormSubmissionHandler("Test", "Sub", "Desc", "<input>", "Submit")
    with LocalBrowserServer(handler=handler) as server:
        # GET request to get the form
        with urlopen(server.server_url + "/setup", timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert "csrf_token" in html
            
        # POST request without CSRF
        data = urlencode({"api_key": "secret123"}).encode("utf-8")
        req = Request(server.server_url + "/setup", data=data, method="POST")
        
        try:
            urlopen(req, timeout=5)
            pytest.fail("Expected 403 Forbidden")
        except HTTPError as exc:
            assert exc.code == 403
            assert b"Invalid CSRF token" in exc.read()


def test_browser_form_escapes_provider_display_name():
    handler = _build_key_setup_handler('Provider <script>"x"</script>')
    with LocalBrowserServer(handler=handler) as server:
        with urlopen(server.server_url + "/setup", timeout=5) as response:
            page = response.read().decode("utf-8")

    assert 'Provider &lt;script&gt;&quot;x&quot;&lt;/script&gt; API Key' in page
    assert 'Provider <script>' not in page


def test_form_submission_handler_payload_size():
    """Verify max payload size is enforced."""
    handler = FormSubmissionHandler("Test", "Sub", "Desc", "<input>", "Submit")
    with LocalBrowserServer(handler=handler) as server:
        large_data = b"x" * (20 * 1024)  # 20KB
        req = Request(server.server_url + "/setup", data=large_data, method="POST")
        try:
            urlopen(req, timeout=5)
            pytest.fail("Expected 413 Payload Too Large")
        except HTTPError as exc:
            assert exc.code == 413


@pytest.mark.parametrize(
    ("content_length", "expected_body"),
    [
        (None, b"Missing Content-Length"),
        ("not-a-number", b"Invalid Content-Length"),
        ("-1", b"Invalid Content-Length"),
    ],
)
def test_form_submission_handler_rejects_invalid_content_length(content_length, expected_body):
    handler = FormSubmissionHandler("Test", "Sub", "Desc", "<input>", "Submit")
    with LocalBrowserServer(handler=handler) as server:
        host_port = server.server_url.removeprefix("http://")
        headers = "Content-Type: application/x-www-form-urlencoded\r\n"
        if content_length is not None:
            headers += f"Content-Length: {content_length}\r\n"
        request = (
            f"POST /setup HTTP/1.1\r\nHost: {host_port}\r\n{headers}Connection: close\r\n\r\n"
        ).encode("ascii")
        with socket.create_connection(("127.0.0.1", server._server.server_port), timeout=5) as client:
            client.sendall(request)
            response = client.makefile("rb").read()

    assert response.startswith(b"HTTP/1.0 400")
    assert expected_body in response


def test_form_submission_handler_rejects_duplicate_post():
    """Second POST must be rejected with 409 to prevent replay.

    Exercise the real LocalBrowserServer and FormSubmissionHandler.
    Verify:
    - second POST returns HTTP 409
    - first payload is preserved
    - server state is unchanged
    """
    handler = FormSubmissionHandler("Test", "Sub", "Desc", "<input>", "Submit")
    with LocalBrowserServer(handler=handler) as server:
        # GET the form to extract the CSRF token.
        with urlopen(server.server_url + "/setup", timeout=5) as resp:
            csrf_token = _extract_csrf(resp.read().decode("utf-8"))

        # First POST — accepted.  This blocks the HTTP handler thread
        # until response_event is set, so we POST from a background thread.
        first_data = urlencode({"api_key": "first-key", "csrf_token": csrf_token}).encode("utf-8")
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                urlopen,
                Request(server.server_url + "/setup", data=first_data, method="POST"),
                timeout=5,
            )

            # Main thread waits for the payload to arrive.
            submission = server.wait(timeout_seconds=2)
            assert submission is not None
            assert submission.fields["api_key"] == "first-key"

            # Second POST — must be rejected with 409.
            second_data = urlencode({"api_key": "second-key", "csrf_token": csrf_token}).encode("utf-8")
            try:
                urlopen(
                    Request(server.server_url + "/setup", data=second_data, method="POST"),
                    timeout=5,
                )
                pytest.fail("Expected 409 Conflict")
            except HTTPError as exc:
                assert exc.code == 409
                assert b"Form already submitted" in exc.read()

            # Unblock the first request so it completes.
            server.send_success("Done", "OK")
            response = future.result(timeout=2)
            assert response.status == 200

        # Verify first payload was not overwritten.
        assert server.wait(timeout_seconds=0) is not None
        assert server.wait(timeout_seconds=0).fields["api_key"] == "first-key"


def test_form_submission_handler_http_timeout():
    """Verify the browser receives 504 when send_success/send_error are never called.

    This exercises the real synchronization logic: the HTTP handler thread
    blocks on response_event, the response_timeout (0.5s) fires, and the handler
    renders the timeout page.
    """
    handler = FormSubmissionHandler("Test", "Sub", "Desc", "<input>", "Submit")
    with LocalBrowserServer(handler=handler, response_timeout=0.5) as server:
        # GET the form to extract the CSRF token.
        with urlopen(server.server_url + "/setup", timeout=5) as resp:
            csrf_token = _extract_csrf(resp.read().decode("utf-8"))

        # POST valid data.  The handler thread will block on response_event.wait()
        # until response_timeout (0.5s) expires. We intentionally never call
        # send_success() or send_error(), so it times out.
        post_data = urlencode({"api_key": "timeout-key", "csrf_token": csrf_token}).encode("utf-8")
        try:
            urlopen(
                Request(server.server_url + "/setup", data=post_data, method="POST"),
                timeout=5,
            )
            pytest.fail("Expected 504 Gateway Timeout")
        except HTTPError as exc:
            assert exc.code == 504
            body = exc.read().decode("utf-8")
            assert "Setup Timeout" in body


# ------------------------------------------------------------------
# CLI integration tests
# ------------------------------------------------------------------


def test_cli_keys_add_browser_flow(monkeypatch, fake_keyring, mock_validation):
    """Test the end-to-end keys add browser flow."""
    # Mock rich prompt to choose "1" (Browser setup)
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *args, **kwargs: "1")
    
    # We need to simulate the browser POSTing the form data to the server
    # We will intercept webbrowser.open to start a background thread that POSTs.
    
    submitted = threading.Event()
    errors: list[BaseException] = []

    def mock_browser_open(url):
        def submit_form():
            try:
                # The server is started before webbrowser.open is called.
                with urlopen(url, timeout=5) as resp:
                    html = resp.read().decode("utf-8")
                    csrf_token = _extract_csrf(html)
                data = urlencode({"api_key": "browser-key-123", "csrf_token": csrf_token}).encode("utf-8")
                with urlopen(Request(url, data=data, method="POST"), timeout=5) as resp:
                    assert resp.status == 200
            except BaseException as exc:
                errors.append(exc)
            finally:
                submitted.set()

        threading.Thread(target=submit_form, daemon=True).start()

    monkeypatch.setattr("webbrowser.open", mock_browser_open)
    
    result = runner.invoke(app, ["keys", "add", "gemini"])
    assert submitted.wait(timeout=2)
    assert errors == []
    assert result.exit_code == 0
    assert "Saved Google Gemini API key to secure local storage." in result.output
    assert fake_keyring.keys["gemini"] == "browser-key-123"


def test_cli_keys_add_terminal_fallback(monkeypatch, fake_keyring, mock_validation):
    """Test the terminal fallback logic."""
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *args, **kwargs: "2")
    
    result = runner.invoke(app, ["keys", "add", "gemini"], input="terminal-key-abc\nterminal-key-abc\n")
    assert result.exit_code == 0
    assert "Saved Google Gemini API key to secure local storage." in result.output
    assert fake_keyring.keys["gemini"] == "terminal-key-abc"
