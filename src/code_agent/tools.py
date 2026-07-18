from __future__ import annotations

import base64
import difflib
import html.parser
import inspect
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from .command_diagnostics import diagnose_command, format_diagnostic_summary
from .container_manager import ContainerManager
from .memory import (
    MemoryUpdate,
    build_memory_write_plan,
    ensure_memory_dir,
    read_project_memory,
    write_memory_plan,
)
from .managed_processes import ManagedProcessError
from .lsp import LspError, LspManager, WorkspaceEditPreview
from .processes import CancellationToken, ProcessSupervisor, terminate_process_tree
from .schema import (
    AgentAction,
    ApplyPatchAction,
    DeleteFileAction,
    DependencyGraphAction,
    DetectVerificationAction,
    EditFileAction,
    InspectGitDiffAction,
    InspectProcessAction,
    ListFilesAction,
    ListProcessesAction,
    LspCodeActionsAction,
    LspCompletionAction,
    LspDefinitionAction,
    LspDiagnosticsAction,
    LspFormattingAction,
    LspHoverAction,
    LspReferencesAction,
    LspRenameAction,
    LspStatusAction,
    LspWorkspaceSymbolsAction,
    ListTransactionsAction,
    MoveFileAction,
    RecoverTransactionsAction,
    RedoTransactionAction,
    RestoreSnapshotAction,
    RankContextAction,
    ReadMemoryAction,
    ReadProcessLogsAction,
    ReadFileAction,
    RepoMapAction,
    RestartProcessAction,
    RunShellAction,
    SendProcessInputAction,
    SearchAction,
    SuggestVerificationAction,
    SymbolIndexAction,
    SummarizeCodeAction,
    StartProcessAction,
    StopProcessAction,
    ToolResult,
    UpdateMemoryAction,
    UndoTransactionAction,
    ProcessEventsAction,
    WebSearchAction,
    WriteFileAction,
)
from .parsing import summarize_code_file
from .repo_index import (
    BackgroundIndexRefresh,
    RepoIndexCache,
    build_dependency_graph,
    build_repo_map,
    build_symbol_index,
    rank_context,
    start_background_index_refresh,
)
from .safety import classify_network_url, classify_shell_command, is_sensitive_path, redact_secrets
from .sandbox_security import (
    SandboxAuditLog,
    SandboxPolicy,
    SandboxRunner,
    local_command_path_rejection,
    validate_workspace_boundary,
)
from .verification import detect_verification_commands, suggest_verification_commands
from .transactions import (
    TransactionConflict,
    TransactionError,
    TransactionPlan,
    WorkspaceTransactionManager,
)

IGNORED_NAMES = {
    ".code-agent",
    ".env",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
}

MAX_MUTATION_OUTPUT_CHARS = 12000
MAX_SHELL_OUTPUT_CHARS = 20000
SAFE_ENV_KEYS = {
    "ALLUSERSPROFILE",
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PSMODULEPATH",
    "PUBLIC",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
}


class ToolRegistry:
    def __init__(
        self,
        workspace: Path,
        dry_run: bool,
        approval_callback: Callable[[str, str], bool] | None = None,
        shell_network_policy: str = "allow",
        index_cache: RepoIndexCache | None = None,
        background_index: bool = False,
        cancellation_token: CancellationToken | None = None,
        process_supervisor: ProcessSupervisor | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        lsp_manager: LspManager | None = None,
        transaction_manager: WorkspaceTransactionManager | None = None,
        transaction_validator: Callable[[TransactionPlan], tuple[bool, str]] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.dry_run = dry_run
        self.approval_callback = approval_callback
        self.shell_network_policy = shell_network_policy.strip().lower()
        self.index_cache = index_cache
        self.index_refresh: BackgroundIndexRefresh | None = None
        self.cancellation_token = cancellation_token or CancellationToken()
        self.sandbox_policy = sandbox_policy or SandboxPolicy.from_workspace(self.workspace)
        self.audit_log = SandboxAuditLog(
            self.workspace,
            enabled=self.sandbox_policy.audit_enabled,
        )
        self.container_manager = (
            ContainerManager(self.workspace, self.sandbox_policy, self.audit_log)
            if self.sandbox_policy.process_isolated
            else None
        )
        self.process_supervisor = process_supervisor or ProcessSupervisor(self.workspace)
        self.process_supervisor.configure_workspace(
            self.workspace,
            container_manager=self.container_manager,
        )
        self.lsp_manager = lsp_manager or LspManager(
            self.workspace,
            enabled=(
                not self.sandbox_policy.process_isolation_required
                or self.container_manager is not None
            ),
            disabled_reason=(
                "Host language-server processes are refused because this sandbox requires "
                "process isolation and no container execution backend is available."
                if self.sandbox_policy.process_isolation_required
                and self.container_manager is None
                else ""
            ),
            process_factory=(
                self.container_manager.popen
                if self.container_manager is not None
                else subprocess.Popen
            ),
            command_resolver=(
                self.container_manager.resolve_command
                if self.container_manager is not None
                else None
            ),
            runtime_workspace=(
                self.container_manager.container_workspace
                if self.container_manager is not None
                else None
            ),
            host_uri_to_runtime=(
                self.container_manager.host_uri_to_container
                if self.container_manager is not None
                else None
            ),
            runtime_uri_to_host=(
                self.container_manager.container_uri_to_host
                if self.container_manager is not None
                else None
            ),
        )
        self.transaction_manager = transaction_manager or WorkspaceTransactionManager(
            self.workspace
        )
        self.transaction_validator = transaction_validator
        self.sandbox_runner = SandboxRunner(
            self.workspace,
            self.sandbox_policy,
            self.process_supervisor,
            self.audit_log,
            container_manager=self.container_manager,
        )
        if self.shell_network_policy not in {"allow", "deny"}:
            raise ValueError("shell_network_policy must be one of: allow, deny")
        if background_index and self.index_cache is not None:
            self.index_refresh = start_background_index_refresh(
                self.workspace,
                self.index_cache,
            )

    def cancel_running_processes(self, reason: str = "cancelled") -> int:
        self.cancellation_token.cancel(reason)
        return self.process_supervisor.cancel_all(reason) + self.lsp_manager.close()

    def close(self) -> int:
        if self.index_refresh is not None:
            self.index_refresh.stop()
        return self.lsp_manager.close()

    def attach_transaction_context(
        self,
        transaction_id: str,
        *,
        run_id: int,
        step: int,
    ) -> None:
        self.transaction_manager.attach_run_context(
            transaction_id,
            run_id=run_id,
            step=step,
        )

    def reset_cancellation(self) -> None:
        self.cancellation_token.reset()

    def run(self, action: AgentAction) -> ToolResult:
        if isinstance(action, ListFilesAction):
            return self._list_files(action.path)
        if isinstance(action, ReadFileAction):
            return self._read_file(action.path)
        if isinstance(action, WriteFileAction):
            return self._write_file(action.path, action.content)
        if isinstance(action, EditFileAction):
            return self._edit_file(action.path, action.find, action.replace)
        if isinstance(action, ApplyPatchAction):
            return self._apply_patch(action.patch)
        if isinstance(action, DeleteFileAction):
            return self._delete_file(action.path)
        if isinstance(action, MoveFileAction):
            return self._move_file(action.source, action.destination)
        if isinstance(action, ListTransactionsAction):
            return self._list_transactions(action.run_id)
        if isinstance(action, UndoTransactionAction):
            return self._transaction_history_action(
                "undo_transaction",
                action.transaction_id,
                action.paths,
            )
        if isinstance(action, RedoTransactionAction):
            return self._transaction_history_action(
                "redo_transaction",
                action.transaction_id,
                action.paths,
            )
        if isinstance(action, RestoreSnapshotAction):
            return self._transaction_history_action(
                "restore_snapshot",
                action.transaction_id,
                action.paths,
            )
        if isinstance(action, RecoverTransactionsAction):
            return self._recover_transactions()
        if isinstance(action, RunShellAction):
            return self._run_shell(action.command)
        if isinstance(action, StartProcessAction):
            return self._start_process(action)
        if isinstance(action, ListProcessesAction):
            return self._list_processes(action.include_finished)
        if isinstance(action, InspectProcessAction):
            return self._inspect_process(action.process_id)
        if isinstance(action, ReadProcessLogsAction):
            return self._read_process_logs(action.process_id, action.stream, action.tail_chars)
        if isinstance(action, ProcessEventsAction):
            return self._process_events(action.process_id, action.after, action.limit)
        if isinstance(action, SendProcessInputAction):
            return self._send_process_input(action.process_id, action.data)
        if isinstance(action, StopProcessAction):
            return self._stop_process(action.process_id, action.grace_seconds)
        if isinstance(action, RestartProcessAction):
            return self._restart_process(action.process_id)
        if isinstance(action, SearchAction):
            return self._search(action.query, action.path)
        if isinstance(action, WebSearchAction):
            return self._web_search(action.query)
        if isinstance(action, SummarizeCodeAction):
            return self._summarize_code(action.path)
        if isinstance(action, DetectVerificationAction):
            return self._detect_verification()
        if isinstance(action, SuggestVerificationAction):
            return self._suggest_verification(action.changed_paths)
        if isinstance(action, InspectGitDiffAction):
            return self._inspect_git_diff(action.include_diff, action.max_chars)
        if isinstance(action, RepoMapAction):
            return self._repo_map(action.max_files)
        if isinstance(action, RankContextAction):
            return self._rank_context(action.task, action.max_results, action.max_tokens)
        if isinstance(action, SymbolIndexAction):
            return self._symbol_index(action.max_files, action.max_symbols)
        if isinstance(action, LspStatusAction):
            return self._lsp_status(action.path)
        if isinstance(action, LspDefinitionAction):
            return self._run_lsp(
                "lsp_definition",
                action.path,
                lambda path: self.lsp_manager.definition(path, action.line, action.column),
            )
        if isinstance(action, LspReferencesAction):
            return self._run_lsp(
                "lsp_references",
                action.path,
                lambda path: self.lsp_manager.references(
                    path,
                    action.line,
                    action.column,
                    include_declaration=action.include_declaration,
                ),
            )
        if isinstance(action, LspHoverAction):
            return self._run_lsp(
                "lsp_hover",
                action.path,
                lambda path: self.lsp_manager.hover(path, action.line, action.column),
            )
        if isinstance(action, LspRenameAction):
            return self._run_lsp(
                "lsp_rename",
                action.path,
                lambda path: self.lsp_manager.rename(
                    path, action.line, action.column, action.new_name
                ),
            )
        if isinstance(action, LspWorkspaceSymbolsAction):
            return self._run_lsp(
                "lsp_workspace_symbols",
                None,
                lambda _path: self.lsp_manager.workspace_symbols(
                    action.query, max_results=action.max_results
                ),
            )
        if isinstance(action, LspCompletionAction):
            return self._run_lsp(
                "lsp_completion",
                action.path,
                lambda path: self.lsp_manager.completion(
                    path, action.line, action.column, max_results=action.max_results
                ),
            )
        if isinstance(action, LspDiagnosticsAction):
            return self._run_lsp(
                "lsp_diagnostics",
                action.path,
                lambda path: self.lsp_manager.diagnostics(path, wait=action.wait_seconds),
            )
        if isinstance(action, LspFormattingAction):
            return self._run_lsp(
                "lsp_formatting",
                action.path,
                lambda path: self.lsp_manager.formatting(
                    path, tab_size=action.tab_size, insert_spaces=action.insert_spaces
                ),
            )
        if isinstance(action, LspCodeActionsAction):
            return self._run_lsp(
                "lsp_code_actions",
                action.path,
                lambda path: self.lsp_manager.code_actions(
                    path,
                    action.start_line,
                    action.start_column,
                    action.end_line,
                    action.end_column,
                    only=tuple(action.only),
                ),
            )
        if isinstance(action, DependencyGraphAction):
            return self._dependency_graph(action.max_files, action.max_edges)
        if isinstance(action, ReadMemoryAction):
            return self._read_memory(action.max_chars)
        if isinstance(action, UpdateMemoryAction):
            return self._update_memory(action.entries)
        return ToolResult(ok=False, output=f"Unsupported action: {action.type}")

    def resolve_inside_workspace(self, requested_path: str | None = None) -> Path:
        target = (self.workspace / (requested_path or ".")).resolve()
        validate_workspace_boundary(target, self.workspace)
        return target

    def _list_files(self, requested_path: str | None) -> ToolResult:
        if not self._approve("list_files", f"List files in {requested_path or '.'}"):
            return ToolResult(ok=False, output="Permission denied for list_files.")
        target = self.resolve_inside_workspace(requested_path)
        entries = []
        for entry in sorted(target.iterdir(), key=lambda item: item.name.lower()):
            if entry.name in IGNORED_NAMES:
                continue
            prefix = "dir " if entry.is_dir() else "file"
            entries.append(f"{prefix} {entry.name}")
        return ToolResult(ok=True, output="\n".join(entries) or "<empty>")

    def _read_file(self, requested_path: str) -> ToolResult:
        target = self.resolve_inside_workspace(requested_path)
        if is_sensitive_path(target, self.workspace):
            return ToolResult(
                ok=False,
                output=(
                    f"Refusing to read sensitive file: {requested_path}. "
                    "Ask the user to provide only the specific non-secret value needed."
                ),
            )
        if not self._approve("read_file", requested_path):
            return ToolResult(ok=False, output="Permission denied for read_file.")
        return ToolResult(ok=True, output=redact_secrets(target.read_text(encoding="utf-8")))

    def _write_file(self, requested_path: str, content: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped write_file. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before editing files."
                ),
            )
        target = self.resolve_inside_workspace(requested_path)
        if is_sensitive_path(target, self.workspace):
            return ToolResult(ok=False, output=f"Refusing to write sensitive file: {requested_path}.")
        try:
            before_state, before = self.transaction_manager.read_text(requested_path)
            plan = self.transaction_manager.plan_text(
                "write_file",
                requested_path,
                content,
                expected=before_state,
            )
        except TransactionError as exc:
            return ToolResult(ok=False, output=str(exc))
        diff = self._diff(requested_path, before, content)
        approval_metadata = self._transaction_preview_metadata(plan)
        if not self._approve(
            "write_file",
            diff or f"Create or overwrite {requested_path}",
            approval_metadata,
        ):
            plan.abort("permission denied")
            return ToolResult(ok=False, output="Permission denied for write_file.")
        result = plan.commit(validator=self.transaction_validator)
        self._refresh_index_after_mutation(result.ok, list(result.paths))
        return ToolResult(
            ok=result.ok,
            output=(
                self._truncate(diff, MAX_MUTATION_OUTPUT_CHARS)
                if result.ok
                else result.output
            ),
            metadata=result.as_metadata(),
        )

    def _edit_file(self, requested_path: str, find: str, replace: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped edit_file. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before editing files."
                ),
            )
        target = self.resolve_inside_workspace(requested_path)
        if is_sensitive_path(target, self.workspace):
            return ToolResult(ok=False, output=f"Refusing to edit sensitive file: {requested_path}.")
        try:
            before_state, before = self.transaction_manager.read_text(requested_path)
        except TransactionError as exc:
            return ToolResult(ok=False, output=str(exc))
        if find not in before:
            return ToolResult(ok=False, output=f"Could not find exact text in {requested_path}")
        after = before.replace(find, replace, 1)
        diff = self._diff(requested_path, before, after)
        try:
            plan = self.transaction_manager.plan_text(
                "edit_file",
                requested_path,
                after,
                expected=before_state,
            )
        except TransactionError as exc:
            return ToolResult(ok=False, output=str(exc))
        if not self._approve(
            "edit_file",
            diff,
            self._transaction_preview_metadata(plan),
        ):
            plan.abort("permission denied")
            return ToolResult(ok=False, output="Permission denied for edit_file.")
        result = plan.commit(validator=self.transaction_validator)
        self._refresh_index_after_mutation(result.ok, list(result.paths))
        return ToolResult(
            ok=result.ok,
            output=(
                self._truncate(diff, MAX_MUTATION_OUTPUT_CHARS)
                if result.ok
                else result.output
            ),
            metadata=result.as_metadata(),
        )

    def _apply_patch(self, patch: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped apply_patch. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before editing files."
                ),
            )
        try:
            metadata = self._patch_metadata(patch)
            paths = set(metadata["paths"])
            if not paths:
                return ToolResult(ok=False, output="Patch does not contain any target file paths.")
            for path in paths:
                target = self.resolve_inside_workspace(path)
                if is_sensitive_path(target, self.workspace):
                    return ToolResult(ok=False, output=f"Refusing to patch sensitive file: {path}.")
        except ValueError as exc:
            return ToolResult(ok=False, output=str(exc))
        try:
            plan = self.transaction_manager.plan_patch(
                "apply_patch",
                patch,
                paths,
                metadata=metadata,
            )
        except (TransactionConflict, TransactionError) as exc:
            return ToolResult(ok=False, output=str(exc), metadata=metadata | {"stage": "check"})
        approval_detail = self._patch_approval_detail(patch, metadata)
        approval_metadata = metadata | self._transaction_preview_metadata(plan)
        if not self._approve("apply_patch", approval_detail, approval_metadata):
            plan.abort("permission denied")
            return ToolResult(ok=False, output="Permission denied for apply_patch.")
        result = plan.commit(validator=self.transaction_validator)
        self._refresh_index_after_mutation(result.ok, list(result.paths))
        return ToolResult(
            ok=result.ok,
            output=self._patch_applied_output(metadata) if result.ok else result.output,
            metadata=metadata | {"stage": "apply"} | result.as_metadata(),
        )

    def _delete_file(self, requested_path: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped delete_file. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before deleting files."
                ),
            )
        try:
            target = self.resolve_inside_workspace(requested_path)
        except ValueError as exc:
            return ToolResult(ok=False, output=str(exc))
        if is_sensitive_path(target, self.workspace):
            return ToolResult(ok=False, output=f"Refusing to delete sensitive file: {requested_path}.")
        if not target.exists():
            return ToolResult(ok=False, output=f"File does not exist: {requested_path}")
        if not target.is_file():
            return ToolResult(ok=False, output=f"Refusing to delete non-file path: {requested_path}")
        try:
            before_state, before = self.transaction_manager.read_text(requested_path)
            plan = self.transaction_manager.plan_delete(
                "delete_file",
                requested_path,
                expected=before_state,
            )
        except TransactionError as exc:
            return ToolResult(ok=False, output=str(exc))
        diff = self._diff(requested_path, before, "")
        if not self._approve(
            "delete_file",
            diff or f"Delete {requested_path}",
            self._transaction_preview_metadata(plan),
        ):
            plan.abort("permission denied")
            return ToolResult(ok=False, output="Permission denied for delete_file.")
        result = plan.commit(validator=self.transaction_validator)
        self._refresh_index_after_mutation(result.ok, list(result.paths))
        return ToolResult(
            ok=result.ok,
            output=(
                self._truncate(
                    f"Deleted {requested_path}.\n{diff}",
                    MAX_MUTATION_OUTPUT_CHARS,
                )
                if result.ok
                else result.output
            ),
            metadata=result.as_metadata(),
        )

    def _move_file(self, source: str, destination: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output="Dry-run mode skipped move_file. Enable write mode before moving files.",
            )
        try:
            source_target = self.resolve_inside_workspace(source)
            destination_target = self.resolve_inside_workspace(destination)
        except ValueError as exc:
            return ToolResult(ok=False, output=str(exc))
        if is_sensitive_path(source_target, self.workspace) or is_sensitive_path(
            destination_target, self.workspace
        ):
            return ToolResult(ok=False, output="Refusing to move a sensitive file.")
        try:
            plan = self.transaction_manager.plan_move(
                "move_file",
                source,
                destination,
            )
        except TransactionError as exc:
            return ToolResult(ok=False, output=str(exc))
        detail = self.transaction_manager.format_preview(plan)
        if not self._approve(
            "move_file",
            detail,
            self._transaction_preview_metadata(plan),
        ):
            plan.abort("permission denied")
            return ToolResult(ok=False, output="Permission denied for move_file.")
        result = plan.commit(validator=self.transaction_validator)
        self._refresh_index_after_mutation(result.ok, list(result.paths))
        return ToolResult(
            ok=result.ok,
            output=(
                f"Moved {source} to {destination} transactionally."
                if result.ok
                else result.output
            ),
            metadata=result.as_metadata(),
        )

    def _list_transactions(self, run_id: int | None) -> ToolResult:
        if not self._approve(
            "list_transactions",
            f"List transaction history{f' for run {run_id}' if run_id else ''}.",
        ):
            return ToolResult(ok=False, output="Permission denied for list_transactions.")
        transactions = self.transaction_manager.list_transactions()
        if run_id is not None:
            transactions = [
                item for item in transactions if item.get("run_id") == run_id
            ]
        return ToolResult(
            ok=True,
            output=json.dumps(transactions, indent=2, ensure_ascii=False),
            metadata={"transactions": transactions},
        )

    def _transaction_history_action(
        self,
        action: str,
        transaction_id: str,
        paths: list[str],
    ) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=f"Dry-run mode skipped {action}. Enable write mode first.",
            )
        try:
            if action == "undo_transaction":
                plan = self.transaction_manager.plan_undo(
                    transaction_id,
                    paths=paths or None,
                )
            elif action == "redo_transaction":
                plan = self.transaction_manager.plan_redo(
                    transaction_id,
                    paths=paths or None,
                )
            else:
                plan = self.transaction_manager.plan_restore_snapshot(
                    transaction_id,
                    paths=paths or None,
                )
        except TransactionError as exc:
            return ToolResult(ok=False, output=str(exc))
        detail = self.transaction_manager.format_preview(plan)
        if not self._approve(
            action,
            detail,
            self._transaction_preview_metadata(plan),
        ):
            plan.abort("permission denied")
            return ToolResult(ok=False, output=f"Permission denied for {action}.")
        result = plan.commit(validator=self.transaction_validator)
        self._refresh_index_after_mutation(result.ok, list(result.paths))
        return ToolResult(
            ok=result.ok,
            output=result.output,
            metadata=result.as_metadata(),
        )

    def _recover_transactions(self) -> ToolResult:
        if not self._approve(
            "recover_transactions",
            "Recover interrupted transaction journals without overwriting newer user edits.",
        ):
            return ToolResult(ok=False, output="Permission denied for recover_transactions.")
        results = [
            *self.transaction_manager.consume_recovery_results(),
            *self.transaction_manager.recover_incomplete(),
        ]
        payload = [
            {
                "id": result.transaction_id,
                "ok": result.ok,
                "state": result.state,
                "paths": list(result.paths),
                "conflicts": list(result.conflicts),
            }
            for result in results
        ]
        recovered_paths = sorted(
            {
                path
                for result in results
                if result.ok
                for path in result.paths
            }
        )
        self._refresh_index_after_mutation(bool(recovered_paths), recovered_paths)
        return ToolResult(
            ok=all(item["ok"] for item in payload),
            output=json.dumps(payload, indent=2) if payload else "No interrupted transactions.",
            metadata={"recoveries": payload},
        )

    def _refresh_index_after_mutation(self, ok: bool, paths: list[str]) -> None:
        if not ok or not paths or self.index_cache is None:
            return
        if self.index_refresh is not None:
            self.index_refresh.request_refresh(paths)
        else:
            self.index_cache.invalidate(self.workspace, paths)

    def _run_shell(self, command: str) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped run_shell. Tell the user to run /write, "
                    "/sandbox plus /write, or use --sandbox/without --dry-run before shell commands."
                ),
            )
        policy = classify_shell_command(command)
        if not policy.allowed:
            return ToolResult(
                ok=False,
                output=(
                    f"Blocked {policy.category} shell command ({policy.risk} risk): "
                    f"{policy.reason}"
                ),
            )
        if self.shell_network_policy == "deny" and policy.may_network:
            return ToolResult(
                ok=False,
                output=(
                    "Blocked install/network shell command because shell network access is denied "
                    "for this run. Use AGENT_SHELL_NETWORK=allow or omit --deny-network-shell only "
                    "when network access is intentional."
                ),
                metadata={
                    "category": policy.category,
                    "risk": policy.risk,
                    "shell_network_policy": self.shell_network_policy,
                },
            )
        sandbox_rejection = self.sandbox_policy.command_rejection(command, policy)
        if sandbox_rejection:
            return ToolResult(
                ok=False,
                output=sandbox_rejection,
                metadata={
                    "category": policy.category,
                    "risk": policy.risk,
                    "sandbox_backend": self.sandbox_policy.backend,
                },
            )
        if self.sandbox_policy.backend == "local":
            local_path_rejection = local_command_path_rejection(command, self.workspace)
            if local_path_rejection:
                return ToolResult(
                    ok=False,
                    output=local_path_rejection,
                    metadata={
                        "category": policy.category,
                        "risk": policy.risk,
                        "sandbox_backend": self.sandbox_policy.backend,
                        "local_command_path_rejected": True,
                    },
                )
        approval_detail = (
            f"Risk: {policy.risk}\n"
            f"Category: {policy.category}\n"
            f"Reason: {policy.reason}\n"
            f"Sandbox backend: {self.sandbox_policy.backend}\n"
            f"May write files: {'yes' if policy.may_write else 'no'}\n"
            f"May access network: {'yes' if policy.may_network else 'no'}\n"
            f"Shell network policy: {self.shell_network_policy}\n"
            f"Sandbox network: {'offline' if self.sandbox_policy.commands.offline else 'allow'}\n"
            f"Arbitrary code: {'yes' if policy.arbitrary_code else 'no'}\n"
            f"Timeout: {self.sandbox_policy.effective_timeout(policy)}s\n"
            f"Resource limits: cpus={self.sandbox_policy.resources.cpus}, "
            f"memory={self.sandbox_policy.resources.memory_mb}MB, "
            f"disk={self.sandbox_policy.resources.disk_mb}MB, "
            f"pids={self.sandbox_policy.resources.pids}\n"
            f"Command: {command}"
        )
        if not self._approve("run_shell", approval_detail):
            return ToolResult(ok=False, output="Permission denied for run_shell.")
        if self._is_python_test_command(command):
            self._clear_python_bytecode_cache()
        self.reset_cancellation()
        process_result = self._normalize_shell_process_result(
            self._run_shell_process(
                command,
                timeout_seconds=self.sandbox_policy.effective_timeout(policy),
                env=self._safe_shell_env(python_no_bytecode=self._is_python_test_command(command)),
            )
        )
        completed = process_result["completed"]
        timed_out = bool(process_result["timed_out"])
        cancelled = bool(process_result["cancelled"])
        timeout_output = str(process_result["output"])
        cleanup_attempted = bool(process_result["cleanup_attempted"])
        disk_metadata = dict(process_result["metadata"])
        duration_ms = float(process_result["duration_ms"])
        events = list(process_result["events"])
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        ordered_output = timeout_output or "\n".join(
            part for part in [stdout, stderr] if part
        ).strip()
        execution = {
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "cwd": disk_metadata.get("cwd", str(self.workspace)),
            "argv": disk_metadata.get("argv", []),
            "environment_keys": disk_metadata.get("environment_keys", []),
            "environment_sha256": disk_metadata.get("environment_sha256"),
            "timed_out": timed_out,
            "cancelled": cancelled,
            "signal": disk_metadata.get("signal"),
            "termination_reason": disk_metadata.get("termination_reason", "exit"),
            "timeout_seconds": self.sandbox_policy.effective_timeout(policy),
            "sandbox_backend": self.sandbox_policy.backend,
        }
        diagnostic_report = diagnose_command(
            command,
            stdout=stdout,
            stderr=stderr,
            ordered_output=ordered_output,
            execution=execution,
            workspace=self.workspace,
        )
        diagnostic_payload = self._redact_payload(diagnostic_report.as_payload())
        log_metadata = {
            "stdout": self._bounded_log(redact_secrets(stdout)),
            "stderr": self._bounded_log(redact_secrets(stderr)),
            "events": self._bounded_events(events),
        }
        common_metadata = {
            "category": policy.category,
            "risk": policy.risk,
            "timeout_seconds": self.sandbox_policy.effective_timeout(policy),
            "cancelled": False,
            "process_tree_cleanup": cleanup_attempted,
            "shell_network_policy": self.shell_network_policy,
            "sandbox_backend": self.sandbox_policy.backend,
            "audit_log": str(self.audit_log.path),
            "execution": execution,
            "logs": log_metadata,
            "diagnostics": diagnostic_payload,
            **disk_metadata,
        }
        if cancelled:
            output = ordered_output.strip()
            detail = f"Shell command cancelled: {self.cancellation_token.reason}."
            if output:
                detail += "\n" + output
            return ToolResult(
                ok=False,
                output=redact_secrets(self._truncate(detail, MAX_SHELL_OUTPUT_CHARS)),
                metadata={
                    **common_metadata,
                    "cancelled": True,
                },
            )
        if timed_out:
            output = ordered_output.strip()
            detail = f"Shell command timed out after {self.sandbox_policy.effective_timeout(policy)}s."
            if output:
                detail += "\n" + output
            return ToolResult(
                ok=False,
                output=redact_secrets(self._truncate(detail, MAX_SHELL_OUTPUT_CHARS)),
                metadata=common_metadata,
            )
        output = ordered_output
        diagnostic_summary = format_diagnostic_summary(diagnostic_report)
        if diagnostic_report.diagnostics or diagnostic_report.failed_tests:
            output = "\n\n".join(part for part in [output, diagnostic_summary] if part)
        return ToolResult(
            ok=completed.returncode == 0,
            output=redact_secrets(self._truncate(output, MAX_SHELL_OUTPUT_CHARS)) or "<no output>",
            metadata=common_metadata,
        )

    def _start_process(self, action: StartProcessAction) -> ToolResult:
        if self.dry_run:
            return ToolResult(ok=False, output="Dry-run mode skipped start_process.")
        policy = classify_shell_command(action.command)
        if not policy.allowed:
            return ToolResult(
                ok=False,
                output=(
                    f"Blocked {policy.category} background command ({policy.risk} risk): "
                    f"{policy.reason}"
                ),
            )
        if self.shell_network_policy == "deny" and policy.may_network:
            return ToolResult(
                ok=False,
                output="Blocked background command because shell network access is denied.",
            )
        sandbox_rejection = self.sandbox_policy.command_rejection(action.command, policy)
        if sandbox_rejection:
            return ToolResult(ok=False, output=sandbox_rejection)
        if self.sandbox_policy.backend != "local" and self.container_manager is None:
            return ToolResult(
                ok=False,
                output=(
                    "Persistent background processes require a managed container-job backend when "
                    "container isolation is active; refusing to launch an unisolated host process."
                ),
            )
        path_rejection = local_command_path_rejection(action.command, self.workspace)
        if path_rejection:
            return ToolResult(ok=False, output=path_rejection)
        try:
            cwd = self.resolve_inside_workspace(action.working_directory)
        except ValueError as exc:
            return ToolResult(ok=False, output=str(exc))
        detail = (
            f"Risk: {policy.risk}\nCategory: {policy.category}\nReason: {policy.reason}\n"
            f"Command: {action.command}\nWorking directory: {cwd}\n"
            f"Interactive: {action.interactive}\nPTY: {action.pty}\n"
            f"Auto restart: {action.auto_restart} (max {action.max_restarts})\n"
            f"Readiness port: {action.readiness_port or 'auto-detect'}\n"
            f"Memory limit: {action.memory_limit_mb or 'monitor only'} MB\n"
            f"CPU-time limit: {action.cpu_time_limit_seconds or 'monitor only'} seconds"
        )
        if not self._approve("start_process", detail):
            return ToolResult(ok=False, output="Permission denied for start_process.")
        try:
            record = self.process_supervisor.start_managed(
                action.command,
                cwd=cwd,
                env=self._safe_shell_env(),
                name=action.name,
                interactive=action.interactive,
                pty=action.pty,
                timeout_seconds=action.timeout_seconds,
                readiness_port=action.readiness_port,
                auto_restart=action.auto_restart,
                max_restarts=action.max_restarts,
                restart_backoff_seconds=action.restart_backoff_seconds,
                max_restart_backoff_seconds=action.max_restart_backoff_seconds,
                memory_limit_mb=action.memory_limit_mb,
                cpu_time_limit_seconds=action.cpu_time_limit_seconds,
                log_max_bytes=action.log_max_bytes,
                log_backups=action.log_backups,
            )
        except (ManagedProcessError, OSError, ValueError) as exc:
            return ToolResult(ok=False, output=f"Failed to start managed process: {exc}")
        return ToolResult(
            ok=True,
            output=(
                f"Started managed process {record['process_id']} "
                f"(pid={record.get('pid') or 'starting'}, status={record.get('status')})."
            ),
            metadata={"process": record},
        )

    def _list_processes(self, include_finished: bool) -> ToolResult:
        if not self._approve("list_processes", "List persisted managed process state."):
            return ToolResult(ok=False, output="Permission denied for list_processes.")
        records = self.process_supervisor.list_managed(include_finished=include_finished)
        return ToolResult(
            ok=True,
            output=json.dumps(records, indent=2, ensure_ascii=False),
            metadata={"processes": records},
        )

    def _inspect_process(self, process_id: str) -> ToolResult:
        return self._managed_process_read(
            "inspect_process",
            process_id,
            lambda: self.process_supervisor.inspect_managed(process_id),
        )

    def _read_process_logs(self, process_id: str, stream: str, tail_chars: int) -> ToolResult:
        return self._managed_process_read(
            "read_process_logs",
            process_id,
            lambda: self.process_supervisor.managed_logs(
                process_id,
                stream=stream,
                tail_chars=tail_chars,
            ),
        )

    def _process_events(self, process_id: str, after: int, limit: int) -> ToolResult:
        return self._managed_process_read(
            "process_events",
            process_id,
            lambda: self.process_supervisor.managed_events(
                process_id,
                after=after,
                limit=limit,
            ),
        )

    def _managed_process_read(
        self,
        action: str,
        process_id: str,
        operation: Callable[[], dict[str, Any]],
    ) -> ToolResult:
        if not self._approve(action, process_id):
            return ToolResult(ok=False, output=f"Permission denied for {action}.")
        try:
            payload = operation()
        except (ManagedProcessError, OSError, ValueError) as exc:
            return ToolResult(ok=False, output=str(exc))
        return ToolResult(
            ok=True,
            output=json.dumps(payload, indent=2, ensure_ascii=False),
            metadata={"process": payload},
        )

    def _send_process_input(self, process_id: str, data: str) -> ToolResult:
        if not self._approve(
            "send_process_input",
            f"Process: {process_id}\nInput bytes: {len(data.encode('utf-8'))}",
        ):
            return ToolResult(ok=False, output="Permission denied for send_process_input.")
        try:
            payload = self.process_supervisor.send_managed_input(process_id, data)
        except (ManagedProcessError, OSError, ValueError) as exc:
            return ToolResult(ok=False, output=str(exc))
        return ToolResult(ok=True, output="Process input accepted.", metadata={"process": payload})

    def _stop_process(self, process_id: str, grace_seconds: float) -> ToolResult:
        if not self._approve(
            "stop_process",
            f"Stop {process_id} gracefully within {grace_seconds:.1f}s, then force its process tree.",
        ):
            return ToolResult(ok=False, output="Permission denied for stop_process.")
        try:
            payload = self.process_supervisor.stop_managed(
                process_id,
                grace_seconds=grace_seconds,
            )
        except (ManagedProcessError, OSError, ValueError) as exc:
            return ToolResult(ok=False, output=str(exc))
        return ToolResult(
            ok=payload.get("status") in {"stopped", "exited", "failed", "orphaned"},
            output=f"Process {process_id} status: {payload.get('status')}.",
            metadata={"process": payload},
        )

    def _restart_process(self, process_id: str) -> ToolResult:
        if not self._approve("restart_process", f"Restart managed process {process_id}."):
            return ToolResult(ok=False, output="Permission denied for restart_process.")
        try:
            payload = self.process_supervisor.restart_managed(process_id)
        except (ManagedProcessError, OSError, ValueError) as exc:
            return ToolResult(ok=False, output=str(exc))
        return ToolResult(ok=True, output="Process restart requested.", metadata={"process": payload})

    def _search(self, query: str, requested_path: str | None) -> ToolResult:
        if not self._approve("search", f"Search {requested_path or '.'} for {query}"):
            return ToolResult(ok=False, output="Permission denied for search.")
        target = self.resolve_inside_workspace(requested_path)
        try:
            completed = subprocess.run(
                [
                    "rg",
                    "--line-number",
                    "--hidden",
                    *[item for name in IGNORED_NAMES for item in ["--glob", f"!{name}"]],
                    query,
                    str(target),
                ],
                cwd=self.workspace,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError:
            return ToolResult(ok=True, output=redact_secrets(self._python_search(query, target)))
        if completed.returncode not in {0, 1}:
            return ToolResult(ok=False, output=redact_secrets(completed.stderr.strip()))
        return ToolResult(ok=True, output=redact_secrets(completed.stdout.strip()) or "<no matches>")

    def _summarize_code(self, requested_path: str) -> ToolResult:
        if not self._approve("summarize_code", requested_path):
            return ToolResult(ok=False, output="Permission denied for summarize_code.")
        target = self.resolve_inside_workspace(requested_path)
        return ToolResult(ok=True, output=redact_secrets(summarize_code_file(target)))

    def _detect_verification(self) -> ToolResult:
        if not self._approve(
            "detect_verification",
            "Detect test, lint, typecheck, and build commands from project configuration.",
        ):
            return ToolResult(ok=False, output="Permission denied for detect_verification.")
        return ToolResult(ok=True, output=detect_verification_commands(self.workspace))

    def _suggest_verification(self, changed_paths: list[str]) -> ToolResult:
        detail = "Suggest focused verification commands"
        if changed_paths:
            detail += " for: " + ", ".join(changed_paths)
        if not self._approve("suggest_verification", detail):
            return ToolResult(ok=False, output="Permission denied for suggest_verification.")
        return ToolResult(ok=True, output=suggest_verification_commands(self.workspace, changed_paths))

    def _inspect_git_diff(self, include_diff: bool, max_chars: int) -> ToolResult:
        detail = "Inspect git status and changed paths"
        if include_diff:
            detail += " with diff hunks"
        if not self._approve("inspect_git_diff", detail):
            return ToolResult(ok=False, output="Permission denied for inspect_git_diff.")

        inside_work_tree = self._git(["rev-parse", "--is-inside-work-tree"], timeout=15)
        if not inside_work_tree.ok or inside_work_tree.output.strip() != "true":
            return ToolResult(ok=False, output="Workspace is not inside a git work tree.")

        sections = [
            ("Status", self._git(["status", "--short"], timeout=30)),
            ("Unstaged changes", self._git(["diff", "--name-status"], timeout=30)),
            ("Staged changes", self._git(["diff", "--cached", "--name-status"], timeout=30)),
        ]
        output_parts: list[str] = []
        for title, result in sections:
            if not result.ok:
                return result
            output_parts.append(f"{title}:\n{result.output.strip() or '<clean>'}")

        if include_diff:
            diff_sections = [
                ("Unstaged diff", self._git(["diff", "--no-ext-diff"], timeout=60)),
                ("Staged diff", self._git(["diff", "--cached", "--no-ext-diff"], timeout=60)),
            ]
            for title, result in diff_sections:
                if not result.ok:
                    return result
                output_parts.append(f"{title}:\n{result.output.strip() or '<empty>'}")

        output = "\n\n".join(output_parts)
        return ToolResult(ok=True, output=self._truncate(redact_secrets(output), max_chars))

    def _repo_map(self, max_files: int) -> ToolResult:
        if not self._approve("repo_map", "Build a lightweight repository map and important-file summary."):
            return ToolResult(ok=False, output="Permission denied for repo_map.")
        return ToolResult(
            ok=True,
            output=build_repo_map(self.workspace, max_files=max_files, cache=self.index_cache),
        )

    def _rank_context(self, task: str, max_results: int, max_tokens: int) -> ToolResult:
        if not self._approve(
            "rank_context",
            f"Rank likely relevant files for task: {task}",
        ):
            return ToolResult(ok=False, output="Permission denied for rank_context.")
        return ToolResult(
            ok=True,
            output=rank_context(
                self.workspace,
                task,
                max_results=max_results,
                max_tokens=max_tokens,
                cache=self.index_cache,
            ),
        )

    def _symbol_index(self, max_files: int, max_symbols: int) -> ToolResult:
        if not self._approve(
            "symbol_index",
            "Build a compact symbol index from source and test files.",
        ):
            return ToolResult(ok=False, output="Permission denied for symbol_index.")
        return ToolResult(
            ok=True,
            output=build_symbol_index(
                self.workspace,
                max_files=max_files,
                max_symbols=max_symbols,
                cache=self.index_cache,
            ),
        )

    def _lsp_status(self, requested_path: str | None) -> ToolResult:
        if not self._approve("lsp_status", requested_path or "."):
            return ToolResult(ok=False, output="Permission denied for lsp_status.")
        path = self.resolve_inside_workspace(requested_path) if requested_path else None
        try:
            status = self.lsp_manager.status(path)
        except (LspError, OSError, ValueError) as exc:
            return ToolResult(ok=False, output=f"LSP status failed: {exc}")
        return ToolResult(ok=True, output=json.dumps(status, indent=2, ensure_ascii=False))

    def _run_lsp(
        self,
        action: str,
        requested_path: str | None,
        operation: Callable[[Path | None], Any],
    ) -> ToolResult:
        detail = requested_path or "workspace"
        if not self._approve(action, detail):
            return ToolResult(ok=False, output=f"Permission denied for {action}.")
        path = self.resolve_inside_workspace(requested_path) if requested_path else None
        try:
            result = operation(path)
        except (LspError, OSError, UnicodeError, ValueError) as exc:
            return ToolResult(ok=False, output=f"{action} failed: {exc}")
        if isinstance(result, WorkspaceEditPreview):
            metadata = {
                "paths": list(result.paths),
                "unsupported_operations": list(result.unsupported_operations),
                "patch": result.patch,
            }
            if not result.patch:
                return ToolResult(
                    ok=False,
                    output="The language server returned no applicable text edits.",
                    metadata=metadata,
                )
            output = [
                "Language-server edit preview; no files were changed.",
                "Inspect this diff, then use apply_patch to apply it transactionally.",
                "",
                result.patch,
            ]
            if result.unsupported_operations:
                output.extend(
                    [
                        "",
                        "Unsupported resource operations: "
                        + ", ".join(result.unsupported_operations),
                    ]
                )
            return ToolResult(ok=True, output="\n".join(output), metadata=metadata)
        return ToolResult(
            ok=True,
            output=json.dumps(result, indent=2, ensure_ascii=False, default=str),
        )

    def _dependency_graph(self, max_files: int, max_edges: int) -> ToolResult:
        if not self._approve(
            "dependency_graph",
            "Build a lightweight dependency graph from source and test imports.",
        ):
            return ToolResult(ok=False, output="Permission denied for dependency_graph.")
        return ToolResult(
            ok=True,
            output=build_dependency_graph(
                self.workspace,
                max_files=max_files,
                max_edges=max_edges,
                cache=self.index_cache,
            ),
        )

    def _read_memory(self, max_chars: int) -> ToolResult:
        if not self._approve(
            "read_memory",
            "Read local project memory from .code-agent/memory/project.md.",
        ):
            return ToolResult(ok=False, output="Permission denied for read_memory.")
        output = read_project_memory(self.workspace, max_chars=max_chars)
        return ToolResult(
            ok=True,
            output=output,
            metadata={
                "path": ".code-agent/memory/project.md",
                "max_chars": max_chars,
                "secret_redacted": True,
            },
        )

    def _update_memory(self, entries: list[Any]) -> ToolResult:
        if self.dry_run:
            return ToolResult(
                ok=False,
                output=(
                    "Dry-run mode skipped update_memory. Enable write mode before saving "
                    "project memory."
                ),
            )
        updates = [MemoryUpdate(section=item.section, content=item.content) for item in entries]
        try:
            plan = build_memory_write_plan(self.workspace, updates)
        except ValueError as exc:
            return ToolResult(ok=False, output=str(exc))
        if not plan.changed:
            return ToolResult(
                ok=True,
                output="Project memory already contains those facts.",
                metadata={"path": ".code-agent/memory/project.md", "changed": False},
            )
        detail = "\n".join(
            [
                "Project memory update preview:",
                "Path: .code-agent/memory/project.md",
                "Sections: " + ", ".join(plan.sections),
                "",
                plan.diff,
            ]
        )
        if not self._approve(
            "update_memory",
            self._truncate(redact_secrets(detail), MAX_MUTATION_OUTPUT_CHARS),
            {"paths": [".code-agent/memory/project.md"], "sections": plan.sections},
        ):
            return ToolResult(ok=False, output="Permission denied for update_memory.")
        ensure_memory_dir(self.workspace)
        write_memory_plan(plan)
        return ToolResult(
            ok=True,
            output="Project memory updated: .code-agent/memory/project.md",
            metadata={
                "path": ".code-agent/memory/project.md",
                "changed": True,
                "sections": plan.sections,
            },
        )

    def _web_search(self, query: str) -> ToolResult:
        approval_detail = (
            f"Risk: medium\n"
            f"Category: public-web-search\n"
            f"Providers: www.bing.com, duckduckgo.com\n"
            f"Domain allowlist: {self._domain_allowlist_detail()}\n"
            f"Query: {query}"
        )
        if not self._approve("web_search", approval_detail):
            return ToolResult(ok=False, output="Permission denied for web_search.")

        provider_errors: list[str] = []
        results: list[tuple[str, str]] = []
        try:
            results = self._search_bing(query)
        except ValueError as exc:
            provider_errors.append(str(exc))
        if not results:
            try:
                results = self._search_duckduckgo(query)
            except ValueError as exc:
                provider_errors.append(str(exc))
        results = self._filter_safe_web_results(results)
        if not results:
            detail = ""
            if provider_errors:
                detail = " Provider errors: " + " | ".join(provider_errors)
            return ToolResult(
                ok=False,
                output=(
                    "No web results were found from the configured search providers. "
                    "Try a more specific query or another source."
                    + detail
                ),
                metadata={
                    "domain_allowlist": list(self.sandbox_policy.commands.domain_allowlist),
                    "provider_errors": provider_errors,
                },
            )
        return ToolResult(
            ok=True,
            output=redact_secrets(
                "\n".join(f"{index + 1}. {title}\n{href}" for index, (title, href) in enumerate(results))
            ),
        )

    def _approve(self, action: str, detail: str, metadata: dict[str, Any] | None = None) -> bool:
        if self.approval_callback is None:
            return False
        if metadata is not None and self._callback_accepts_metadata():
            return self._approval_result_to_bool(self.approval_callback(action, detail, metadata))
        return self._approval_result_to_bool(self.approval_callback(action, detail))

    @staticmethod
    def _approval_result_to_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"y", "yes", "a", "approve", "approved"}
        return bool(value)

    def _callback_accepts_metadata(self) -> bool:
        if self.approval_callback is None:
            return False
        try:
            signature = inspect.signature(self.approval_callback)
        except (TypeError, ValueError):
            return False
        positional = 0
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                return True
            if parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }:
                positional += 1
        return positional >= 3

    def _search_bing(self, query: str) -> list[tuple[str, str]]:
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
        html = self._fetch_url(url)
        parser = BingParser()
        parser.feed(html)
        return parser.results[:5]

    def _search_duckduckgo(self, query: str) -> list[tuple[str, str]]:
        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        html = self._fetch_url(url)
        parser = DuckDuckGoParser()
        parser.feed(html)
        return parser.results[:5]

    def _fetch_url(self, url: str) -> str:
        policy = classify_network_url(
            url,
            domain_allowlist=self.sandbox_policy.commands.domain_allowlist,
            resolve_dns=True,
        )
        if not policy.allowed:
            raise ValueError(
                f"Blocked {policy.category} web target ({policy.risk} risk): {policy.reason}"
            )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Agent47/0.1 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")

    def _filter_safe_web_results(self, results: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return [
            (title, href)
            for title, href in results
            if classify_network_url(
                href,
                domain_allowlist=self.sandbox_policy.commands.domain_allowlist,
                resolve_dns=True,
            ).allowed
        ]

    def _domain_allowlist_detail(self) -> str:
        if not self.sandbox_policy.commands.domain_allowlist:
            return "<public web allowed>"
        return ", ".join(self.sandbox_policy.commands.domain_allowlist)

    def _python_search(self, query: str, target: Path) -> str:
        try:
            pattern = re.compile(query)
        except re.error:
            pattern = re.compile(re.escape(query))

        files = [target] if target.is_file() else sorted(target.rglob("*"))
        matches: list[str] = []
        for path in files:
            if self._is_ignored_path(path) or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            relative = path.relative_to(self.workspace)
            for line_number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(f"{relative}:{line_number}:{line}")
        return "\n".join(matches) or "<no matches>"

    def _is_ignored_path(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.workspace)
        except ValueError:
            return True
        return any(part in IGNORED_NAMES for part in relative.parts)

    @staticmethod
    def _safe_shell_env(*, python_no_bytecode: bool = False) -> dict[str, str]:
        env: dict[str, str] = {}
        for key, value in os.environ.items():
            upper = key.upper()
            if upper in SAFE_ENV_KEYS:
                env[key] = value
        env["AGENT47_SANDBOXED_SHELL"] = "1"
        if python_no_bytecode:
            env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def _run_shell_process(
        self,
        command: str,
        *,
        timeout_seconds: int,
        env: dict[str, str],
    ) -> tuple[subprocess.CompletedProcess[str], bool, str, bool, bool, dict[str, Any]]:
        result = self.sandbox_runner.run_shell(
            command,
            timeout_seconds=timeout_seconds,
            env=env,
            cancellation_token=self.cancellation_token,
        )
        return (
            result.completed,
            result.timed_out,
            result.output,
            result.cancelled,
            result.cleanup_attempted,
            result.metadata or {},
            [event.as_payload() for event in result.events],
            result.duration_ms,
        )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        terminate_process_tree(process)

    @staticmethod
    def _normalize_shell_process_result(value: Any) -> dict[str, Any]:
        completed, timed_out, output, *rest = value
        cancelled = bool(rest[0]) if len(rest) >= 1 else False
        cleanup_attempted = bool(rest[1]) if len(rest) >= 2 else bool(timed_out or cancelled)
        metadata = rest[2] if len(rest) >= 3 and isinstance(rest[2], dict) else {}
        events = rest[3] if len(rest) >= 4 and isinstance(rest[3], list) else []
        duration_ms = float(rest[4]) if len(rest) >= 5 else float(
            metadata.get("duration_ms", 0.0)
        )
        return {
            "completed": completed,
            "timed_out": timed_out,
            "output": output,
            "cancelled": cancelled,
            "cleanup_attempted": cleanup_attempted,
            "metadata": metadata,
            "events": events,
            "duration_ms": duration_ms,
        }

    @staticmethod
    def _bounded_log(value: str, max_chars: int = MAX_SHELL_OUTPUT_CHARS) -> dict[str, Any]:
        digest = sha256(value.encode("utf-8")).hexdigest()
        if len(value) <= max_chars:
            rendered = value
            truncated = False
        else:
            rendered = value[:max_chars].rstrip() + f"\n<truncated {len(value) - max_chars} chars>"
            truncated = True
        return {
            "text": rendered,
            "chars": len(value),
            "sha256": digest,
            "truncated": truncated,
        }

    @staticmethod
    def _bounded_events(
        events: list[dict[str, Any]],
        *,
        max_events: int = 1000,
        max_chars: int = MAX_SHELL_OUTPUT_CHARS * 2,
    ) -> dict[str, Any]:
        output: list[dict[str, Any]] = []
        chars = 0
        for event in events:
            text = redact_secrets(str(event.get("text", "")))
            if len(output) >= max_events or chars + len(text) > max_chars:
                break
            output.append({**event, "text": text})
            chars += len(text)
        return {
            "items": output,
            "event_count": len(events),
            "captured_event_count": len(output),
            "truncated": len(output) < len(events),
        }

    @staticmethod
    def _transaction_preview_metadata(plan: Any) -> dict[str, Any]:
        return {
            "transaction": {
                "id": plan.transaction_id,
                "state": plan.state,
                "action": plan.action,
                "paths": plan.paths,
                "checkpoint": True,
                "atomic": True,
            }
        }

    @staticmethod
    def _redact_payload(value: Any) -> Any:
        if isinstance(value, str):
            return redact_secrets(value)
        if isinstance(value, dict):
            return {key: ToolRegistry._redact_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [ToolRegistry._redact_payload(item) for item in value]
        if isinstance(value, tuple):
            return [ToolRegistry._redact_payload(item) for item in value]
        return value

    @staticmethod
    def _is_python_test_command(command: str) -> bool:
        lowered = command.lower()
        return "pytest" in lowered

    def _clear_python_bytecode_cache(self) -> None:
        for cache_dir in self.workspace.rglob("__pycache__"):
            shutil.rmtree(cache_dir, ignore_errors=True)

    @staticmethod
    def _coerce_process_output(value: str | bytes) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    @staticmethod
    def _diff(path: str, before: str, after: str) -> str:
        return "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )

    @staticmethod
    def _paths_from_patch(patch: str) -> set[str]:
        return set(ToolRegistry._patch_metadata(patch)["paths"])

    @staticmethod
    def _patch_metadata(patch: str) -> dict[str, Any]:
        files: dict[str, dict[str, Any]] = {}
        current_paths: set[str] = set()
        for line in patch.splitlines():
            if line.startswith("diff --git "):
                current_paths = set()
                parts = line.split()
                if len(parts) >= 4:
                    for raw_path in [parts[2], parts[3]]:
                        path = ToolRegistry._clean_patch_path(raw_path)
                        if path:
                            current_paths.add(path)
                            files.setdefault(path, ToolRegistry._empty_patch_file_metadata(path))
                continue
            if line.startswith("--- ") or line.startswith("+++ "):
                if line.startswith("--- "):
                    current_paths = set()
                raw_path = line[4:].split("\t", 1)[0].strip()
                path = ToolRegistry._clean_patch_path(raw_path)
                if path:
                    current_paths.add(path)
                    files.setdefault(path, ToolRegistry._empty_patch_file_metadata(path))
                continue
            if not current_paths:
                continue
            if line.startswith("+") and not line.startswith("+++"):
                for path in current_paths:
                    files[path]["additions"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                for path in current_paths:
                    files[path]["deletions"] += 1

        for item in files.values():
            additions = int(item["additions"])
            deletions = int(item["deletions"])
            if additions and deletions:
                item["operation"] = "update"
            elif additions:
                item["operation"] = "create_or_update"
            elif deletions:
                item["operation"] = "delete_or_update"
            else:
                item["operation"] = "metadata_only"

        ordered_files = sorted(files.values(), key=lambda item: str(item["path"]))
        return {
            "kind": "unified_diff_change_set",
            "file_count": len(ordered_files),
            "paths": [str(item["path"]) for item in ordered_files],
            "files": ordered_files,
            "total_additions": sum(int(item["additions"]) for item in ordered_files),
            "total_deletions": sum(int(item["deletions"]) for item in ordered_files),
        }

    @staticmethod
    def _empty_patch_file_metadata(path: str) -> dict[str, Any]:
        return {"path": path, "operation": "update", "additions": 0, "deletions": 0}

    @staticmethod
    def _patch_approval_detail(patch: str, metadata: dict[str, Any]) -> str:
        file_lines = [
            (
                f"- {item['path']}: {item['operation']}, "
                f"+{item['additions']} -{item['deletions']}"
            )
            for item in metadata.get("files", [])
        ]
        summary = [
            "Patch preview:",
            f"Files: {metadata['file_count']}",
            f"Total changes: +{metadata['total_additions']} -{metadata['total_deletions']}",
            *file_lines,
            "",
            "Unified diff:",
            patch,
        ]
        return "\n".join(summary)

    @staticmethod
    def _patch_applied_output(metadata: dict[str, Any]) -> str:
        file_lines = [
            f"- {item['path']}: {item['operation']} (+{item['additions']} -{item['deletions']})"
            for item in metadata.get("files", [])
        ]
        return "\n".join(
            [
                "Patch applied.",
                f"Files changed: {metadata['file_count']}",
                f"Total changes: +{metadata['total_additions']} -{metadata['total_deletions']}",
                *file_lines,
            ]
        )

    @staticmethod
    def _clean_patch_path(path: str) -> str | None:
        if path == "/dev/null":
            return None
        for prefix in ("a/", "b/"):
            if path.startswith(prefix):
                return path[len(prefix) :]
        return path

    def _git_apply(self, patch: str, check: bool) -> ToolResult:
        command = ["git", "apply", "--whitespace=nowarn"]
        if check:
            command.append("--check")
        completed = subprocess.run(
            command,
            cwd=self.workspace,
            input=patch,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        if completed.returncode == 0:
            return ToolResult(ok=True, output="Patch can be applied." if check else "Patch applied.")
        stage = "Patch check failed" if check else "Patch apply failed"
        return ToolResult(ok=False, output=redact_secrets(f"{stage}: {output or '<no output>'}"))

    def _git(self, args: list[str], timeout: int) -> ToolResult:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        return ToolResult(ok=completed.returncode == 0, output=redact_secrets(output))

    @staticmethod
    def _truncate(output: str, max_chars: int) -> str:
        if len(output) <= max_chars:
            return output
        remaining = len(output) - max_chars
        return output[:max_chars].rstrip() + f"\n<truncated {remaining} chars>"


class DuckDuckGoParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[tuple[str, str]] = []
        self._in_result_link = False
        self._current_href = ""
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in class_name:
            self._in_result_link = True
            self._current_href = attrs_dict.get("href") or ""
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._in_result_link:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_result_link:
            return
        title = " ".join("".join(self._current_text).split())
        href = self._clean_href(self._current_href)
        if title and href:
            self.results.append((title, href))
        self._in_result_link = False
        self._current_href = ""
        self._current_text = []

    @staticmethod
    def _clean_href(href: str) -> str:
        if href.startswith("//duckduckgo.com/l/?"):
            parsed = urllib.parse.urlparse("https:" + href)
            query = urllib.parse.parse_qs(parsed.query)
            return query.get("uddg", [href])[0]
        return href


class BingParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[tuple[str, str]] = []
        self._in_result = False
        self._in_heading = False
        self._in_title_link = False
        self._current_href = ""
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")
        if tag == "li" and "b_algo" in class_name:
            self._in_result = True
            return
        if tag == "h2" and self._in_result:
            self._in_heading = True
            return
        if tag == "a" and self._in_result and self._in_heading and not self._in_title_link:
            href = attrs_dict.get("href") or ""
            if href.startswith("http"):
                self._in_title_link = True
                self._current_href = self._clean_href(href)
                self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._in_title_link:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title_link:
            title = " ".join("".join(self._current_text).split())
            if title and self._current_href:
                self.results.append((title, self._current_href))
            self._in_title_link = False
            self._current_href = ""
            self._current_text = []
            return
        if tag == "h2" and self._in_heading:
            self._in_heading = False
            return
        if tag == "li" and self._in_result:
            self._in_result = False
            self._in_heading = False

    @staticmethod
    def _clean_href(href: str) -> str:
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/"):
            values = urllib.parse.parse_qs(parsed.query).get("u", [])
            if not values:
                match = re.search(r"[?&]u=([^&]+)", href)
                if match:
                    values = [urllib.parse.unquote(match.group(1))]
            if values:
                encoded = values[0]
                if encoded.startswith("a1"):
                    encoded = encoded[2:]
                padding = "=" * (-len(encoded) % 4)
                try:
                    return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    return href
        return href
