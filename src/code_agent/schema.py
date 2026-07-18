from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class FinalAction(BaseModel):
    type: Literal["final"]
    message: str


class PlanStep(BaseModel):
    step: str
    status: Literal["pending", "in_progress", "completed", "blocked"]
    note: Optional[str] = None


class UpdatePlanAction(BaseModel):
    type: Literal["update_plan"]
    steps: list[PlanStep] = Field(min_length=1)
    target_files: list[str] = Field(default_factory=list, max_length=20)
    owned_files: list[str] = Field(default_factory=list, max_length=20)
    checks: list[str] = Field(default_factory=list, max_length=20)
    blockers: list[str] = Field(default_factory=list, max_length=10)
    risk_notes: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_plan(self) -> "UpdatePlanAction":
        in_progress = [step for step in self.steps if step.status == "in_progress"]
        if len(in_progress) > 1:
            raise ValueError("Only one plan step can be in_progress at a time.")
        for field_name in ["target_files", "owned_files"]:
            for path in getattr(self, field_name):
                if not _is_safe_relative_plan_path(path):
                    raise ValueError(
                        f"{field_name} must contain workspace-relative paths without '..': {path}"
                    )
        return self


def _is_safe_relative_plan_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ":" in normalized:
        return False
    return ".." not in normalized.split("/")


class ListFilesAction(BaseModel):
    type: Literal["list_files"]
    path: Optional[str] = None


class ReadFileAction(BaseModel):
    type: Literal["read_file"]
    path: str


class WriteFileAction(BaseModel):
    type: Literal["write_file"]
    path: str
    content: str


class EditFileAction(BaseModel):
    type: Literal["edit_file"]
    path: str
    find: str
    replace: str


class ApplyPatchAction(BaseModel):
    type: Literal["apply_patch"]
    patch: str


class DeleteFileAction(BaseModel):
    type: Literal["delete_file"]
    path: str


class MoveFileAction(BaseModel):
    type: Literal["move_file"]
    source: str
    destination: str


class ListTransactionsAction(BaseModel):
    type: Literal["list_transactions"]
    run_id: Optional[int] = Field(default=None, ge=1)


class UndoTransactionAction(BaseModel):
    type: Literal["undo_transaction"]
    transaction_id: str
    paths: list[str] = Field(default_factory=list, max_length=100)


class RedoTransactionAction(BaseModel):
    type: Literal["redo_transaction"]
    transaction_id: str
    paths: list[str] = Field(default_factory=list, max_length=100)


class RestoreSnapshotAction(BaseModel):
    type: Literal["restore_snapshot"]
    transaction_id: str
    paths: list[str] = Field(default_factory=list, max_length=100)


class RecoverTransactionsAction(BaseModel):
    type: Literal["recover_transactions"]


class RunShellAction(BaseModel):
    type: Literal["run_shell"]
    command: str


class StartProcessAction(BaseModel):
    type: Literal["start_process"]
    command: str
    name: Optional[str] = Field(default=None, max_length=80)
    working_directory: Optional[str] = None
    interactive: bool = False
    pty: bool = False
    timeout_seconds: int = Field(default=0, ge=0, le=604800)
    readiness_port: Optional[int] = Field(default=None, ge=1, le=65535)
    auto_restart: bool = False
    max_restarts: int = Field(default=3, ge=0, le=20)
    restart_backoff_seconds: float = Field(default=1.0, ge=0.1, le=300.0)
    max_restart_backoff_seconds: float = Field(default=30.0, ge=0.1, le=3600.0)
    memory_limit_mb: Optional[int] = Field(default=None, ge=16, le=131072)
    cpu_time_limit_seconds: Optional[int] = Field(default=None, ge=1, le=604800)
    log_max_bytes: int = Field(default=5_000_000, ge=65536, le=100_000_000)
    log_backups: int = Field(default=3, ge=1, le=20)


class ListProcessesAction(BaseModel):
    type: Literal["list_processes"]
    include_finished: bool = True


class InspectProcessAction(BaseModel):
    type: Literal["inspect_process"]
    process_id: str


class ReadProcessLogsAction(BaseModel):
    type: Literal["read_process_logs"]
    process_id: str
    stream: Literal["all", "stdout", "stderr", "terminal"] = "all"
    tail_chars: int = Field(default=20000, ge=100, le=200000)


class ProcessEventsAction(BaseModel):
    type: Literal["process_events"]
    process_id: str
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2000)


class SendProcessInputAction(BaseModel):
    type: Literal["send_process_input"]
    process_id: str
    data: str = Field(max_length=100000)


class StopProcessAction(BaseModel):
    type: Literal["stop_process"]
    process_id: str
    grace_seconds: float = Field(default=5.0, ge=0.1, le=60.0)


class RestartProcessAction(BaseModel):
    type: Literal["restart_process"]
    process_id: str


class SearchAction(BaseModel):
    type: Literal["search"]
    query: str
    path: Optional[str] = None


class WebSearchAction(BaseModel):
    type: Literal["web_search"]
    query: str


class SummarizeCodeAction(BaseModel):
    type: Literal["summarize_code"]
    path: str


class DetectVerificationAction(BaseModel):
    type: Literal["detect_verification"]


class SuggestVerificationAction(BaseModel):
    type: Literal["suggest_verification"]
    changed_paths: list[str] = Field(default_factory=list)


class InspectGitDiffAction(BaseModel):
    type: Literal["inspect_git_diff"]
    include_diff: bool = False
    max_chars: int = Field(default=12000, ge=1000, le=50000)


class RepoMapAction(BaseModel):
    type: Literal["repo_map"]
    max_files: int = Field(default=80, ge=10, le=300)


class RankContextAction(BaseModel):
    type: Literal["rank_context"]
    task: str
    max_results: int = Field(default=12, ge=3, le=50)
    max_tokens: int = Field(default=8000, ge=500, le=50000)


class SymbolIndexAction(BaseModel):
    type: Literal["symbol_index"]
    max_files: int = Field(default=40, ge=5, le=200)
    max_symbols: int = Field(default=120, ge=10, le=500)


class LspStatusAction(BaseModel):
    type: Literal["lsp_status"]
    path: Optional[str] = None


class LspDefinitionAction(BaseModel):
    type: Literal["lsp_definition"]
    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)


class LspReferencesAction(BaseModel):
    type: Literal["lsp_references"]
    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    include_declaration: bool = True


class LspHoverAction(BaseModel):
    type: Literal["lsp_hover"]
    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)


class LspRenameAction(BaseModel):
    type: Literal["lsp_rename"]
    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    new_name: str = Field(min_length=1, max_length=300)


class LspWorkspaceSymbolsAction(BaseModel):
    type: Literal["lsp_workspace_symbols"]
    query: str = Field(max_length=500)
    max_results: int = Field(default=100, ge=1, le=500)


class LspCompletionAction(BaseModel):
    type: Literal["lsp_completion"]
    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    max_results: int = Field(default=50, ge=1, le=200)


class LspDiagnosticsAction(BaseModel):
    type: Literal["lsp_diagnostics"]
    path: str
    wait_seconds: float = Field(default=1.0, ge=0, le=10)


class LspFormattingAction(BaseModel):
    type: Literal["lsp_formatting"]
    path: str
    tab_size: int = Field(default=4, ge=1, le=16)
    insert_spaces: bool = True


class LspCodeActionsAction(BaseModel):
    type: Literal["lsp_code_actions"]
    path: str
    start_line: int = Field(ge=1)
    start_column: int = Field(ge=1)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=1)
    only: list[str] = Field(default_factory=list, max_length=20)


class DependencyGraphAction(BaseModel):
    type: Literal["dependency_graph"]
    max_files: int = Field(default=60, ge=5, le=200)
    max_edges: int = Field(default=160, ge=10, le=500)


class ReadMemoryAction(BaseModel):
    type: Literal["read_memory"]
    max_chars: int = Field(default=12000, ge=1000, le=50000)


class MemoryEntry(BaseModel):
    section: Literal[
        "project_conventions",
        "user_preferences",
        "architecture_notes",
        "common_commands",
        "known_pitfalls",
        "project_glossary",
        "successful_patterns",
        "verification_strategy",
        "dependencies_integrations",
        "release_notes",
    ]
    content: str = Field(min_length=1, max_length=1000)


class UpdateMemoryAction(BaseModel):
    type: Literal["update_memory"]
    entries: list[MemoryEntry] = Field(min_length=1, max_length=20)


AgentAction = Union[
    FinalAction,
    UpdatePlanAction,
    ListFilesAction,
    ReadFileAction,
    WriteFileAction,
    EditFileAction,
    ApplyPatchAction,
    DeleteFileAction,
    MoveFileAction,
    ListTransactionsAction,
    UndoTransactionAction,
    RedoTransactionAction,
    RestoreSnapshotAction,
    RecoverTransactionsAction,
    RunShellAction,
    StartProcessAction,
    ListProcessesAction,
    InspectProcessAction,
    ReadProcessLogsAction,
    ProcessEventsAction,
    SendProcessInputAction,
    StopProcessAction,
    RestartProcessAction,
    SearchAction,
    WebSearchAction,
    SummarizeCodeAction,
    DetectVerificationAction,
    SuggestVerificationAction,
    InspectGitDiffAction,
    RepoMapAction,
    RankContextAction,
    SymbolIndexAction,
    LspStatusAction,
    LspDefinitionAction,
    LspReferencesAction,
    LspHoverAction,
    LspRenameAction,
    LspWorkspaceSymbolsAction,
    LspCompletionAction,
    LspDiagnosticsAction,
    LspFormattingAction,
    LspCodeActionsAction,
    DependencyGraphAction,
    ReadMemoryAction,
    UpdateMemoryAction,
]


class ToolResult(BaseModel):
    ok: bool
    output: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
