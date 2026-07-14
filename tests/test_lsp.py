from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from code_agent.lsp import (
    DEFAULT_LSP_SERVERS,
    LspError,
    LspManager,
    LspServerCrashed,
    LspServerSpec,
    WorkspaceEditPreview,
    apply_text_edits,
    position_to_offset,
    read_lsp_message,
    workspace_edit_to_patch,
)
from code_agent.sandbox_security import SandboxPolicy
from code_agent.schema import LspHoverAction, LspRenameAction, LspStatusAction
from code_agent.tools import ToolRegistry


FAKE_SERVER = r'''
import json
import sys

documents = {}

def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            raise EOFError
        if line in (b"\r\n", b"\n"):
            break
        name, value = line.decode("ascii").split(":", 1)
        headers[name.lower()] = value.strip()
    return json.loads(sys.stdin.buffer.read(int(headers["content-length"])))

def send(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()

while True:
    try:
        message = read_message()
    except EOFError:
        break
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    if method == "exit":
        break
    if method == "textDocument/didOpen":
        document = params["textDocument"]
        documents[document["uri"]] = document["text"]
        send({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": document["uri"],
                "diagnostics": [{
                    "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}},
                    "severity": 2,
                    "source": "fake",
                    "message": "demo warning"
                }]
            }
        })
        continue
    if request_id is None:
        continue
    uri = ((params.get("textDocument") or {}).get("uri"))
    location = {"uri": uri, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}}
    if method == "initialize":
        result = {"capabilities": {"definitionProvider": True, "renameProvider": True}}
    elif method == "shutdown":
        result = None
    elif method == "textDocument/definition":
        result = location
    elif method == "textDocument/references":
        result = [location]
    elif method == "textDocument/hover":
        result = {"contents": {"kind": "markdown", "value": "**demo**"}}
    elif method == "textDocument/completion":
        result = {"items": [{"label": "demo", "kind": 3, "detail": "fake completion"}]}
    elif method == "textDocument/rename":
        result = {"changes": {uri: [{"range": location["range"], "newText": params["newName"]}]}}
    elif method == "textDocument/formatting":
        result = [{"range": location["range"], "newText": "fmt"}]
    elif method == "textDocument/codeAction":
        result = [{"title": "Fix demo", "kind": "quickfix", "edit": {"changes": {uri: [{"range": location["range"], "newText": "fix"}]}}}]
    elif method == "workspace/symbol":
        first_uri = next(iter(documents), None)
        result = [{"name": "demo", "kind": 12, "location": {**location, "uri": first_uri}}]
    elif method == "textDocument/diagnostic":
        result = {"items": []}
    else:
        result = None
    send({"jsonrpc": "2.0", "id": request_id, "result": result})
'''


def test_reads_json_rpc_content_length_frame() -> None:
    body = b'{"jsonrpc":"2.0","id":1,"result":null}'
    stream = io.BytesIO(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

    assert read_lsp_message(stream)["id"] == 1


def test_utf16_positions_handle_zero_and_surrogate_pairs() -> None:
    assert position_to_offset("a😀b\n", {"line": 0, "character": 0}) == 0
    assert position_to_offset("a😀b\n", {"line": 0, "character": 3}) == 2
    with pytest.raises(LspError, match="surrogate pair"):
        position_to_offset("a😀b\n", {"line": 0, "character": 2})


def test_workspace_edit_builds_reviewable_patch(tmp_path: Path) -> None:
    source = tmp_path / "demo.py"
    source.write_text("old = 1\n", encoding="utf-8")

    preview = workspace_edit_to_patch(
        {
            "changes": {
                source.as_uri(): [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 3},
                        },
                        "newText": "new",
                    }
                ]
            }
        },
        tmp_path,
    )

    assert preview.paths == ("demo.py",)
    assert "-old = 1" in preview.patch
    assert "+new = 1" in preview.patch
    assert source.read_text(encoding="utf-8") == "old = 1\n"


def test_text_edits_reject_overlapping_ranges() -> None:
    with pytest.raises(LspError, match="overlapping"):
        apply_text_edits(
            "abcdef",
            [
                {"range": {"start": {"line": 0, "character": 1}, "end": {"line": 0, "character": 4}}, "newText": "x"},
                {"range": {"start": {"line": 0, "character": 3}, "end": {"line": 0, "character": 5}}, "newText": "y"},
            ],
        )


def test_protocol_client_supports_semantic_operations(tmp_path: Path) -> None:
    server_script = tmp_path / "fake_lsp.py"
    server_script.write_text(FAKE_SERVER, encoding="utf-8")
    source = tmp_path / "demo.py"
    source.write_text("old = 1\n", encoding="utf-8")
    spec = LspServerSpec(
        name="fake-python",
        language_id="python",
        extensions=(".py",),
        commands=((sys.executable, str(server_script)),),
    )
    manager = LspManager(tmp_path, specs=(spec,), diagnostics_wait=2.0)
    try:
        assert manager.definition(source, 1, 1)[0]["path"] == "demo.py"
        assert manager.references(source, 1, 1)[0]["line"] == 1
        assert manager.hover(source, 1, 1) == {"contents": "**demo**", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}}}
        assert manager.completion(source, 1, 1)[0]["label"] == "demo"
        assert "new_name" in manager.rename(source, 1, 1, "new_name").patch
        assert "+fmt = 1" in manager.formatting(source).patch
        assert manager.code_actions(source, 1, 1, 1, 3)[0]["title"] == "Fix demo"
        assert manager.diagnostics(source)[0]["message"] == "demo warning"
        assert manager.workspace_symbols("demo")[0]["server"] == "fake-python"
    finally:
        assert manager.close() == 1


def test_workspace_symbols_aggregate_multiple_language_servers(tmp_path: Path) -> None:
    server_script = tmp_path / "fake_lsp.py"
    server_script.write_text(FAKE_SERVER, encoding="utf-8")
    (tmp_path / "demo.py").write_text("demo\n", encoding="utf-8")
    (tmp_path / "demo.ts").write_text("demo\n", encoding="utf-8")
    specs = (
        LspServerSpec(
            "fake-python", "python", (".py",), ((sys.executable, str(server_script)),)
        ),
        LspServerSpec(
            "fake-typescript",
            "typescript",
            (".ts",),
            ((sys.executable, str(server_script)),),
        ),
    )
    manager = LspManager(tmp_path, specs=specs)
    try:
        symbols = manager.workspace_symbols("demo")
        assert {symbol["server"] for symbol in symbols} == {
            "fake-python",
            "fake-typescript",
        }
    finally:
        assert manager.close() == 2


def test_builtin_registry_covers_major_languages() -> None:
    assert {spec.name for spec in DEFAULT_LSP_SERVERS} == {
        "python",
        "typescript",
        "rust",
        "go",
        "java",
        "clangd",
    }


def test_manager_restarts_crashed_server_once(tmp_path: Path) -> None:
    source = tmp_path / "demo.py"
    source.write_text("demo\n", encoding="utf-8")

    class RestartingClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.alive = False
            self.restart_count = 0
            self.capabilities = {}
            self.requests = 0

        def start(self) -> None:
            self.alive = True

        def close(self, *, force: bool = False) -> None:
            self.alive = False

        def sync_document(self, path: Path) -> str:
            return path.as_uri()

        def request(self, _method: str, _params) -> dict:
            self.requests += 1
            if self.requests == 1:
                raise LspServerCrashed("crashed")
            return {"contents": "recovered"}

    spec = LspServerSpec("fake", "python", (".py",), ((sys.executable,),))
    manager = LspManager(tmp_path, specs=(spec,), client_factory=RestartingClient)

    assert manager.hover(source, 1, 1)["contents"] == "recovered"
    assert manager.status(source)["servers"][0]["restart_count"] == 1


class FakeManager:
    def __init__(self) -> None:
        self.closed = 0

    def status(self, _path=None):
        return {"enabled": True, "servers": []}

    def hover(self, _path, _line, _column):
        return {"contents": "semantic"}

    def rename(self, _path, _line, _column, _new_name):
        return WorkspaceEditPreview("diff --git a/demo.py b/demo.py\n", ("demo.py",))

    def close(self) -> int:
        self.closed += 1
        return 1


def test_tool_registry_gates_lsp_and_returns_patch_preview(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("old\n", encoding="utf-8")
    denied = ToolRegistry(
        tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: False,
        lsp_manager=FakeManager(),
    )
    assert not denied.run(LspHoverAction(type="lsp_hover", path="demo.py", line=1, column=1)).ok

    manager = FakeManager()
    tools = ToolRegistry(
        tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: True,
        lsp_manager=manager,
    )
    result = tools.run(
        LspRenameAction(
            type="lsp_rename", path="demo.py", line=1, column=1, new_name="new"
        )
    )

    assert result.ok
    assert "no files were changed" in result.output
    assert result.metadata["paths"] == ["demo.py"]
    assert tools.close() == 1
    assert manager.closed == 1


def test_strict_sandbox_disables_host_language_servers(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("demo\n", encoding="utf-8")
    tools = ToolRegistry(
        tmp_path,
        dry_run=True,
        approval_callback=lambda *_args: True,
        sandbox_policy=SandboxPolicy(process_isolation_required=True),
    )

    status = tools.run(LspStatusAction(type="lsp_status", path="demo.py"))
    hover = tools.run(LspHoverAction(type="lsp_hover", path="demo.py", line=1, column=1))

    assert json.loads(status.output)["enabled"] is False
    assert not hover.ok
    assert "process isolation" in hover.output
