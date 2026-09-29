from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable
from urllib.parse import unquote, urlparse

from .patches import git_style_unified_diff
from .processes import windows_creation_flags
from .sandbox_security import validate_workspace_boundary


class LspError(RuntimeError):
    pass


class LspServerUnavailable(LspError):
    pass


class LspServerCrashed(LspError):
    pass


@dataclass(frozen=True)
class LspServerSpec:
    name: str
    language_id: str
    extensions: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]
    root_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedLspServer:
    spec: LspServerSpec
    command: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceEditPreview:
    patch: str
    paths: tuple[str, ...]
    unsupported_operations: tuple[str, ...] = ()


DEFAULT_LSP_SERVERS = (
    LspServerSpec(
        name="python",
        language_id="python",
        extensions=(".py", ".pyi"),
        commands=(
            ("basedpyright-langserver", "--stdio"),
            ("pyright-langserver", "--stdio"),
            ("pylsp",),
        ),
        root_markers=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
    ),
    LspServerSpec(
        name="typescript",
        language_id="typescript",
        extensions=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
        commands=(("typescript-language-server", "--stdio"),),
        root_markers=("tsconfig.json", "jsconfig.json", "package.json"),
    ),
    LspServerSpec(
        name="rust",
        language_id="rust",
        extensions=(".rs",),
        commands=(("rust-analyzer",),),
        root_markers=("Cargo.toml",),
    ),
    LspServerSpec(
        name="go",
        language_id="go",
        extensions=(".go",),
        commands=(("gopls", "serve"), ("gopls",)),
        root_markers=("go.work", "go.mod"),
    ),
    LspServerSpec(
        name="java",
        language_id="java",
        extensions=(".java",),
        commands=(("jdtls",),),
        root_markers=("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle"),
    ),
    LspServerSpec(
        name="clangd",
        language_id="cpp",
        extensions=(".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"),
        commands=(("clangd", "--background-index"),),
        root_markers=("compile_commands.json", "compile_flags.txt", "CMakeLists.txt"),
    ),
)


class LspClient:
    def __init__(
        self,
        workspace: Path,
        server: ResolvedLspServer,
        *,
        request_timeout: float = 10.0,
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        runtime_workspace: str | None = None,
        host_uri_to_runtime: Callable[[str], str] | None = None,
        runtime_uri_to_host: Callable[[str], str] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.server = server
        self.request_timeout = request_timeout
        self.process_factory = process_factory
        self.runtime_workspace = runtime_workspace
        self.host_uri_to_runtime = host_uri_to_runtime or (lambda uri: uri)
        self.runtime_uri_to_host = runtime_uri_to_host or (lambda uri: uri)
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 0
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._notifications: deque[dict[str, Any]] = deque(maxlen=500)
        self._notification_condition = threading.Condition()
        self._stderr: deque[str] = deque(maxlen=200)
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._documents: dict[str, tuple[int, str]] = {}
        self.capabilities: dict[str, Any] = {}
        self.restart_count = 0

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.alive:
            return
        self._documents.clear()
        self.capabilities = {}
        with self._notification_condition:
            self._notifications.clear()
        command = [self._runtime_command_part(part) for part in self.server.command]
        try:
            self.process = self.process_factory(
                command,
                cwd=self.workspace,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_lsp_environment(),
                shell=False,
                creationflags=windows_creation_flags(),
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise LspServerUnavailable(
                f"Could not start {self.server.spec.name} language server: {exc}"
            ) from exc
        if self.process.stdin is None or self.process.stdout is None or self.process.stderr is None:
            self.close(force=True)
            raise LspServerUnavailable(
                f"{self.server.spec.name} language server did not expose stdio pipes."
            )
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"agent47-lsp-{self.server.spec.name}-reader",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop,
            name=f"agent47-lsp-{self.server.spec.name}-stderr",
            daemon=True,
        )
        self._reader_thread.start()
        self._stderr_thread.start()
        try:
            workspace_uri = _workspace_uri(self.runtime_workspace, self.workspace)
            response = self.request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "clientInfo": {"name": "Agent47", "version": "0.1.0"},
                    "rootUri": workspace_uri,
                    "workspaceFolders": [
                        {"uri": workspace_uri, "name": self.workspace.name}
                    ],
                    "capabilities": _client_capabilities(),
                    "initializationOptions": {},
                    "trace": "off",
                },
            )
            if isinstance(response, dict):
                self.capabilities = _dict(response.get("capabilities"))
            self.notify("initialized", {})
        except LspError:
            self.close(force=True)
            raise

    def request(self, method: str, params: Any, *, timeout: float | None = None) -> Any:
        if not self.alive:
            raise LspServerCrashed(self._crash_detail())
        self._next_id += 1
        request_id = self._next_id
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            try:
                response = response_queue.get(timeout=timeout or self.request_timeout)
            except queue.Empty as exc:
                if not self.alive:
                    raise LspServerCrashed(self._crash_detail()) from exc
                raise LspError(
                    f"{self.server.spec.name} timed out handling {method}."
                ) from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in response:
            raise LspError("Language server rejected the request.")
        return response.get("result")

    def notify(self, method: str, params: Any) -> None:
        if not self.alive:
            raise LspServerCrashed(self._crash_detail())
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def sync_document(self, path: Path) -> str:
        resolved = path.resolve()
        validate_workspace_boundary(resolved, self.workspace)
        content = resolved.read_text(encoding="utf-8")
        uri = self.host_uri_to_runtime(resolved.as_uri())
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        previous = self._documents.get(uri)
        if previous is None:
            version = 1
            self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": _language_id(self.server.spec, resolved),
                        "version": version,
                        "text": content,
                    }
                },
            )
        elif previous[1] != digest:
            version = previous[0] + 1
            self.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": content}],
                },
            )
        else:
            return uri
        self._documents[uri] = (version, digest)
        return uri

    def wait_for_notification(
        self,
        method: str,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        with self._notification_condition:
            while True:
                for index, message in enumerate(self._notifications):
                    if message.get("method") == method and predicate(message):
                        selected = message
                        del self._notifications[index]
                        return selected
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._notification_condition.wait(remaining)

    def close(self, *, force: bool = False) -> None:
        process = self.process
        if process is None:
            return
        if not force and process.poll() is None:
            try:
                self.request("shutdown", None, timeout=2.0)
                self.notify("exit", None)
            except LspError:
                force = True
        if process.poll() is None:
            if force:
                process.kill()
            else:
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
        self.process = None
        current_thread = threading.current_thread()
        for thread in (self._reader_thread, self._stderr_thread):
            if thread is not None and thread is not current_thread:
                thread.join(timeout=1.0)
        self._reader_thread = None
        self._stderr_thread = None
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for response_queue in pending:
            try:
                response_queue.put_nowait(
                    {"error": {"message": f"{self.server.spec.name} closed"}}
                )
            except queue.Full:
                continue

    def _send(self, payload: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise LspServerCrashed(self._crash_detail())
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            try:
                process.stdin.write(header + body)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise LspServerCrashed(self._crash_detail()) from exc

    def _read_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        while True:
            try:
                message = _map_payload_uris(
                    read_lsp_message(process.stdout),
                    self.runtime_uri_to_host,
                )
            except (EOFError, OSError, ValueError):
                break
            if "id" in message and ("result" in message or "error" in message):
                try:
                    response_id = int(message["id"])
                except (TypeError, ValueError):
                    continue
                with self._pending_lock:
                    response_queue = self._pending.get(response_id)
                if response_queue is not None:
                    try:
                        response_queue.put_nowait(message)
                    except queue.Full:
                        pass
                continue
            if "id" in message and "method" in message:
                self._respond_to_server_request(message)
                continue
            with self._notification_condition:
                self._notifications.append(message)
                self._notification_condition.notify_all()
        with self._pending_lock:
            pending = list(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(
                    {"error": {"message": self._crash_detail()}}
                )
            except queue.Full:
                continue

    def _respond_to_server_request(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = _dict(message.get("params"))
        if method == "workspace/configuration":
            items = params.get("items", [])
            result: Any = [None] * len(items) if isinstance(items, list) else []
        elif method == "workspace/workspaceFolders":
            workspace_uri = _workspace_uri(self.runtime_workspace, self.workspace)
            result = [{"uri": workspace_uri, "name": self.workspace.name}]
        else:
            result = None
        try:
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        except LspError:
            return

    def _stderr_loop(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        while True:
            raw = process.stderr.readline()
            if not raw:
                return
            self._stderr.append(raw.decode("utf-8", errors="replace").rstrip())

    def _crash_detail(self) -> str:
        suffix = "\n".join(list(self._stderr)[-10:]).strip()
        detail = f"{self.server.spec.name} language server stopped unexpectedly."
        return detail + (f" Recent stderr:\n{suffix}" if suffix else "")

    def _runtime_command_part(self, value: str) -> str:
        value = value.replace(
            "{workspace}",
            self.runtime_workspace or str(self.workspace),
        )
        if self.runtime_workspace:
            host = str(self.workspace)
            if value == host or value.startswith(host + os.sep):
                suffix = value[len(host) :].replace("\\", "/")
                return self.runtime_workspace.rstrip("/") + suffix
        return value


class LspManager:
    def __init__(
        self,
        workspace: Path,
        *,
        specs: tuple[LspServerSpec, ...] = DEFAULT_LSP_SERVERS,
        request_timeout: float = 10.0,
        diagnostics_wait: float = 1.0,
        max_restarts: int = 1,
        client_factory: Callable[..., LspClient] = LspClient,
        enabled: bool = True,
        disabled_reason: str = "",
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        command_resolver: Callable[[tuple[tuple[str, ...], ...]], tuple[str, ...] | None]
        | None = None,
        runtime_workspace: str | None = None,
        host_uri_to_runtime: Callable[[str], str] | None = None,
        runtime_uri_to_host: Callable[[str], str] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.specs = specs
        self.request_timeout = request_timeout
        self.diagnostics_wait = diagnostics_wait
        self.max_restarts = max_restarts
        self.client_factory = client_factory
        self.enabled = enabled
        self.disabled_reason = disabled_reason
        self.process_factory = process_factory
        self.command_resolver = command_resolver
        self.runtime_workspace = runtime_workspace
        self.host_uri_to_runtime = host_uri_to_runtime
        self.runtime_uri_to_host = runtime_uri_to_host
        self._clients: dict[str, LspClient] = {}
        self._lock = threading.RLock()

    def status(self, path: Path | None = None) -> dict[str, Any]:
        relevant = [self._spec_for_path(path)] if path is not None else list(self.specs)
        servers = []
        for spec in relevant:
            resolved = self._resolve_server(spec)
            client = self._clients.get(spec.name)
            servers.append(
                {
                    "name": spec.name,
                    "language_id": spec.language_id,
                    "extensions": list(spec.extensions),
                    "available": resolved is not None,
                    "command": list(resolved.command) if resolved else None,
                    "running": bool(client and client.alive),
                    "restart_count": client.restart_count if client else 0,
                    "capabilities": sorted(client.capabilities) if client else [],
                }
            )
        return {
            "workspace": str(self.workspace),
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason or None,
            "servers": servers,
        }

    def definition(self, path: Path, line: int, column: int) -> list[dict[str, Any]]:
        result = self._document_request(path, "textDocument/definition", line, column)
        return normalize_locations(result, self.workspace)

    def references(
        self,
        path: Path,
        line: int,
        column: int,
        *,
        include_declaration: bool = True,
    ) -> list[dict[str, Any]]:
        result = self._document_request(
            path,
            "textDocument/references",
            line,
            column,
            extra={"context": {"includeDeclaration": include_declaration}},
        )
        return normalize_locations(result, self.workspace)

    def hover(self, path: Path, line: int, column: int) -> dict[str, Any] | None:
        result = self._document_request(path, "textDocument/hover", line, column)
        if not isinstance(result, dict):
            return None
        return {
            "contents": normalize_hover_contents(result.get("contents")),
            "range": normalize_range(result.get("range")),
        }

    def completion(
        self,
        path: Path,
        line: int,
        column: int,
        *,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        result = self._document_request(path, "textDocument/completion", line, column)
        items = result.get("items", []) if isinstance(result, dict) else result
        if not isinstance(items, list):
            return []
        normalized = []
        for item in items[:max_results]:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    key: item[key]
                    for key in ["label", "kind", "detail", "documentation", "insertText"]
                    if key in item
                }
            )
        return normalized

    def workspace_symbols(self, query: str, *, max_results: int = 100) -> list[dict[str, Any]]:
        symbols = []
        failures = []
        specs = self._workspace_symbol_specs()
        for spec in specs:
            try:
                client = self._client_for_spec(spec)
                result = self._request_with_restart(client, "workspace/symbol", {"query": query})
            except LspError as exc:
                failures.append(str(exc))
                continue
            if not isinstance(result, list):
                continue
            for item in result:
                if not isinstance(item, dict):
                    continue
                location = normalize_locations(item.get("location"), self.workspace)
                symbols.append(
                    {
                        "name": item.get("name"),
                        "kind": item.get("kind"),
                        "container": item.get("containerName"),
                        "location": location[0] if location else None,
                        "server": spec.name,
                    }
                )
                if len(symbols) >= max_results:
                    return symbols
        if not symbols and failures and len(failures) == len(specs):
            raise LspServerUnavailable("; ".join(failures))
        return symbols

    def rename(self, path: Path, line: int, column: int, new_name: str) -> WorkspaceEditPreview:
        result = self._document_request(
            path,
            "textDocument/rename",
            line,
            column,
            extra={"newName": new_name},
        )
        return workspace_edit_to_patch(result, self.workspace)

    def formatting(
        self,
        path: Path,
        *,
        tab_size: int = 4,
        insert_spaces: bool = True,
    ) -> WorkspaceEditPreview:
        client = self._client_for_path(path)
        uri = client.sync_document(path)
        result = self._request_with_restart(
            client,
            "textDocument/formatting",
            {
                "textDocument": {"uri": uri},
                "options": {"tabSize": tab_size, "insertSpaces": insert_spaces},
            },
        )
        return workspace_edit_to_patch({"changes": {uri: result or []}}, self.workspace)

    def code_actions(
        self,
        path: Path,
        start_line: int,
        start_column: int,
        end_line: int,
        end_column: int,
        *,
        only: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        client = self._client_for_path(path)
        uri = client.sync_document(path)
        result = self._request_with_restart(
            client,
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": lsp_position(start_line, start_column),
                    "end": lsp_position(end_line, end_column),
                },
                "context": {"diagnostics": [], "only": list(only)},
            },
        )
        if not isinstance(result, list):
            return []
        actions = []
        for item in result:
            if not isinstance(item, dict):
                continue
            normalized = {
                "title": item.get("title"),
                "kind": item.get("kind"),
                "preferred": bool(item.get("isPreferred", False)),
                "disabled": item.get("disabled"),
                "command": item.get("command"),
            }
            if item.get("edit"):
                preview = workspace_edit_to_patch(item["edit"], self.workspace)
                normalized["patch"] = preview.patch
                normalized["paths"] = list(preview.paths)
                normalized["unsupported_operations"] = list(preview.unsupported_operations)
            actions.append(normalized)
        return actions

    def diagnostics(self, path: Path, *, wait: float | None = None) -> list[dict[str, Any]]:
        client = self._client_for_path(path)
        uri = client.sync_document(path)
        notification = client.wait_for_notification(
            "textDocument/publishDiagnostics",
            lambda message: _dict(message.get("params")).get("uri") == uri,
            timeout=self.diagnostics_wait if wait is None else wait,
        )
        if notification is None:
            try:
                result = self._request_with_restart(
                    client,
                    "textDocument/diagnostic",
                    {"textDocument": {"uri": uri}},
                )
            except LspError:
                return []
            raw = result.get("items", []) if isinstance(result, dict) else []
        else:
            raw = _dict(notification.get("params")).get("diagnostics", [])
        return normalize_diagnostics(raw, path, self.workspace)

    def close(self) -> int:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()
        return len(clients)

    def _document_request(
        self,
        path: Path,
        method: str,
        line: int,
        column: int,
        *,
        extra: dict[str, Any] | None = None,
    ) -> Any:
        client = self._client_for_path(path)
        uri = client.sync_document(path)
        params = {
            "textDocument": {"uri": uri},
            "position": lsp_position(line, column),
            **(extra or {}),
        }
        return self._request_with_restart(client, method, params, path=path)

    def _request_with_restart(
        self,
        client: LspClient,
        method: str,
        params: Any,
        *,
        path: Path | None = None,
    ) -> Any:
        try:
            return client.request(method, params)
        except LspServerCrashed:
            if client.restart_count >= self.max_restarts:
                raise
            client.close(force=True)
            client.restart_count += 1
            client.start()
            if path is not None:
                client.sync_document(path)
            return client.request(method, params)

    def _client_for_path(self, path: Path) -> LspClient:
        return self._client_for_spec(self._spec_for_path(path))

    def _client_for_spec(self, spec: LspServerSpec) -> LspClient:
        if not self.enabled:
            raise LspServerUnavailable(
                self.disabled_reason or "Language servers are disabled by policy."
            )
        with self._lock:
            client = self._clients.get(spec.name)
            if client is not None and client.alive:
                return client
            restart_count = 0
            if client is not None:
                if client.restart_count >= self.max_restarts:
                    raise LspServerCrashed(
                        f"{spec.name} language server exceeded its restart budget."
                    )
                restart_count = client.restart_count + 1
                client.close(force=True)
            resolved = self._resolve_server(spec)
            if resolved is None:
                commands = ", ".join(command[0] for command in spec.commands)
                raise LspServerUnavailable(
                    f"No {spec.name} language server was found. Install one of: {commands}."
                )
            client_options: dict[str, Any] = {"request_timeout": self.request_timeout}
            if self.runtime_workspace is not None:
                client_options.update(
                    {
                        "process_factory": self.process_factory,
                        "runtime_workspace": self.runtime_workspace,
                        "host_uri_to_runtime": self.host_uri_to_runtime,
                        "runtime_uri_to_host": self.runtime_uri_to_host,
                    }
                )
            client = self.client_factory(self.workspace, resolved, **client_options)
            client.restart_count = restart_count
            client.start()
            self._clients[spec.name] = client
            return client

    def _resolve_server(self, spec: LspServerSpec) -> ResolvedLspServer | None:
        if self.command_resolver is not None:
            command = self.command_resolver(spec.commands)
            if command is None:
                return None
            if spec.name == "java":
                command = (*command, "-data", "{workspace}/.code-agent/lsp/java")
            return ResolvedLspServer(spec=spec, command=command)
        for candidate in spec.commands:
            executable = shutil.which(candidate[0])
            if executable:
                command = (executable, *candidate[1:])
                if spec.name == "java":
                    data_dir = self.workspace / ".code-agent" / "lsp" / "java"
                    data_dir.mkdir(parents=True, exist_ok=True)
                    command = (*command, "-data", str(data_dir))
                return ResolvedLspServer(spec=spec, command=command)
        return None

    def _spec_for_path(self, path: Path | None) -> LspServerSpec:
        if path is None:
            raise LspServerUnavailable("A source path is required to select a language server.")
        resolved = path.resolve()
        validate_workspace_boundary(resolved, self.workspace)
        for spec in self.specs:
            if resolved.suffix.lower() in spec.extensions:
                return spec
        raise LspServerUnavailable(
            f"No configured language server supports {resolved.suffix or '<no extension>'}."
        )

    def _workspace_symbol_specs(self) -> list[LspServerSpec]:
        relevant = []
        for spec in self.specs:
            has_marker = any((self.workspace / marker).exists() for marker in spec.root_markers)
            has_source = any(
                path.is_file() and path.suffix.lower() in spec.extensions
                for path in self.workspace.rglob("*")
                if ".git" not in path.parts and ".code-agent" not in path.parts
            )
            if (has_marker or has_source) and self._resolve_server(spec) is not None:
                relevant.append(spec)
        if not relevant:
            raise LspServerUnavailable("No language server is available for workspace symbol search.")
        return relevant


def read_lsp_message(stream: BinaryIO) -> dict[str, Any]:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            raise EOFError
        if line in {b"\r\n", b"\n"}:
            break
        decoded = line.decode("ascii", errors="strict").strip()
        if ":" not in decoded:
            raise ValueError("Malformed LSP header")
        name, value = decoded.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    try:
        length = int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise ValueError("LSP response omitted a valid Content-Length header") from exc
    body = stream.read(length)
    if len(body) != length:
        raise EOFError
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("LSP message must be a JSON object")
    return payload


def lsp_position(line: int, column: int) -> dict[str, int]:
    if line < 1 or column < 1:
        raise ValueError("LSP line and column values are 1-based and must be positive.")
    return {"line": line - 1, "character": column - 1}


def normalize_locations(value: Any, workspace: Path) -> list[dict[str, Any]]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    normalized = []
    for item in values:
        if not isinstance(item, dict):
            continue
        target = item.get("targetUri") or item.get("uri")
        range_value = item.get("targetSelectionRange") or item.get("range")
        path = uri_to_workspace_path(str(target or ""), workspace)
        if path is None:
            continue
        location_range = normalize_range(range_value)
        normalized.append(
            {
                "path": path,
                "line": _dict(location_range.get("start")).get("line", 0) + 1,
                "column": _dict(location_range.get("start")).get("character", 0) + 1,
                "range": location_range,
            }
        )
    return normalized


def normalize_range(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}}
    return {
        "start": _dict(value.get("start")),
        "end": _dict(value.get("end")),
    }


def normalize_hover_contents(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("value") or value.get("language") or "")
    if isinstance(value, list):
        return "\n\n".join(filter(None, (normalize_hover_contents(item) for item in value)))
    return ""


def normalize_diagnostics(value: Any, path: Path, workspace: Path) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    relative = path.resolve().relative_to(workspace.resolve()).as_posix()
    severities = {1: "error", 2: "warning", 3: "information", 4: "hint"}
    diagnostics = []
    for item in value:
        if not isinstance(item, dict):
            continue
        diagnostic_range = normalize_range(item.get("range"))
        start = _dict(diagnostic_range["start"])
        diagnostics.append(
            {
                "path": relative,
                "line": int(start.get("line", 0)) + 1,
                "column": int(start.get("character", 0)) + 1,
                "severity": severities.get(item.get("severity"), "unknown"),
                "code": item.get("code"),
                "source": item.get("source"),
                "message": item.get("message", ""),
                "range": diagnostic_range,
            }
        )
    return diagnostics


def workspace_edit_to_patch(value: Any, workspace: Path) -> WorkspaceEditPreview:
    if not isinstance(value, dict):
        return WorkspaceEditPreview(patch="", paths=())
    edits_by_uri: dict[str, list[dict[str, Any]]] = {}
    unsupported = []
    changes = value.get("changes", {})
    if isinstance(changes, dict):
        for uri, edits in changes.items():
            if isinstance(uri, str) and isinstance(edits, list):
                edits_by_uri.setdefault(uri, []).extend(
                    edit for edit in edits if isinstance(edit, dict)
                )
    document_changes = value.get("documentChanges", [])
    if isinstance(document_changes, list):
        for change in document_changes:
            if not isinstance(change, dict):
                continue
            text_document = change.get("textDocument")
            edits = change.get("edits")
            if isinstance(text_document, dict) and isinstance(edits, list):
                uri = text_document.get("uri")
                if isinstance(uri, str):
                    edits_by_uri.setdefault(uri, []).extend(
                        edit for edit in edits if isinstance(edit, dict)
                    )
            elif change.get("kind"):
                unsupported.append(str(change["kind"]))
            else:
                unsupported.append("resource operation")
    patches = []
    paths = []
    for uri, edits in sorted(edits_by_uri.items()):
        path = uri_to_path(uri)
        if path is None:
            unsupported.append(f"non-file URI: {uri}")
            continue
        validate_workspace_boundary(path, workspace)
        relative = path.relative_to(workspace.resolve()).as_posix()
        before = path.read_text(encoding="utf-8")
        after = apply_text_edits(before, edits)
        if before == after:
            continue
        patches.append(
            git_style_unified_diff(
                relative,
                before,
                after,
                before_exists=True,
                after_exists=True,
            )
        )
        paths.append(relative)
    return WorkspaceEditPreview(
        patch="\n".join(part.rstrip() for part in patches if part).rstrip() + ("\n" if patches else ""),
        paths=tuple(paths),
        unsupported_operations=tuple(unsupported),
    )


def apply_text_edits(content: str, edits: list[dict[str, Any]]) -> str:
    indexed = []
    for edit in edits:
        edit_range = edit.get("range")
        if not isinstance(edit_range, dict):
            raise LspError("LSP text edit omitted a range.")
        start = position_to_offset(content, _dict(edit_range.get("start")))
        end = position_to_offset(content, _dict(edit_range.get("end")))
        if end < start:
            raise LspError("LSP text edit has an inverted range.")
        indexed.append((start, end, str(edit.get("newText", ""))))
    indexed.sort(key=lambda item: (item[0], item[1]), reverse=True)
    previous_start = len(content) + 1
    result = content
    for start, end, replacement in indexed:
        if end > previous_start:
            raise LspError("LSP workspace edit contains overlapping ranges.")
        result = result[:start] + replacement + result[end:]
        previous_start = start
    return result


def position_to_offset(content: str, position: dict[str, Any]) -> int:
    line = int(position.get("line", 0))
    character = int(position.get("character", 0))
    if line < 0 or character < 0:
        raise LspError("LSP position cannot be negative.")
    lines = content.splitlines(keepends=True)
    if line > len(lines):
        raise LspError("LSP position line exceeds the document.")
    if line == len(lines):
        if character == 0:
            return len(content)
        raise LspError("LSP position exceeds the document.")
    prefix = "".join(lines[:line])
    current = lines[line]
    codepoints = _utf16_units_to_codepoints(current, character)
    return len(prefix) + codepoints


def uri_to_workspace_path(uri: str, workspace: Path) -> str | None:
    path = uri_to_path(uri)
    if path is None:
        return None
    try:
        validate_workspace_boundary(path, workspace)
        return path.relative_to(workspace.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def uri_to_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path).resolve()


def _utf16_units_to_codepoints(value: str, units: int) -> int:
    if units == 0:
        return 0
    consumed = 0
    for index, character in enumerate(value):
        width = len(character.encode("utf-16-le")) // 2
        if consumed + width > units:
            raise LspError("LSP UTF-16 position splits a surrogate pair.")
        if consumed + width == units:
            return index + 1
        consumed += width
    if consumed == units:
        return len(value)
    raise LspError("LSP character position exceeds the line.")


def _language_id(spec: LspServerSpec, path: Path) -> str:
    suffix = path.suffix.lower()
    if spec.name == "typescript":
        return {
            ".js": "javascript",
            ".jsx": "javascriptreact",
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".mjs": "javascript",
            ".cjs": "javascript",
        }.get(suffix, spec.language_id)
    if spec.name == "clangd":
        return "c" if suffix in {".c", ".h"} else "cpp"
    return spec.language_id


def _client_capabilities() -> dict[str, Any]:
    return {
        "workspace": {
            "workspaceFolders": True,
            "symbol": {"dynamicRegistration": False},
            "configuration": True,
        },
        "textDocument": {
            "synchronization": {"dynamicRegistration": False, "didSave": True},
            "definition": {"dynamicRegistration": False, "linkSupport": True},
            "references": {"dynamicRegistration": False},
            "hover": {"contentFormat": ["markdown", "plaintext"]},
            "rename": {"prepareSupport": True},
            "completion": {
                "completionItem": {
                    "snippetSupport": False,
                    "documentationFormat": ["markdown", "plaintext"],
                }
            },
            "publishDiagnostics": {"relatedInformation": True, "versionSupport": True},
            "diagnostic": {"dynamicRegistration": False},
            "formatting": {"dynamicRegistration": False},
            "codeAction": {
                "dynamicRegistration": False,
                "codeActionLiteralSupport": {
                    "codeActionKind": {"valueSet": ["quickfix", "refactor", "source"]}
                },
            },
        },
    }


def _lsp_environment() -> dict[str, str]:
    allowed = {
        "APPDATA",
        "CARGO_HOME",
        "GOCACHE",
        "GOMODCACHE",
        "GOPATH",
        "HOME",
        "JAVA_HOME",
        "JDK_HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "NODE_PATH",
        "PATH",
        "PATHEXT",
        "RUSTUP_HOME",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "VIRTUAL_ENV",
        "WINDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _map_payload_uris(value: Any, mapper: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        return {key: _map_payload_uris(item, mapper) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_payload_uris(item, mapper) for item in value]
    if isinstance(value, str) and value.startswith("file:"):
        return mapper(value)
    return value


def _workspace_uri(runtime_workspace: str | None, host_workspace: Path) -> str:
    if runtime_workspace is None:
        return host_workspace.as_uri()
    from urllib.parse import quote

    return "file://" + quote(runtime_workspace, safe="/")
