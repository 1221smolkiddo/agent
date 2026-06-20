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


class RunShellAction(BaseModel):
    type: Literal["run_shell"]
    command: str


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


class SymbolIndexAction(BaseModel):
    type: Literal["symbol_index"]
    max_files: int = Field(default=40, ge=5, le=200)
    max_symbols: int = Field(default=120, ge=10, le=500)


class DependencyGraphAction(BaseModel):
    type: Literal["dependency_graph"]
    max_files: int = Field(default=60, ge=5, le=200)
    max_edges: int = Field(default=160, ge=10, le=500)


AgentAction = Union[
    FinalAction,
    UpdatePlanAction,
    ListFilesAction,
    ReadFileAction,
    WriteFileAction,
    EditFileAction,
    ApplyPatchAction,
    DeleteFileAction,
    RunShellAction,
    SearchAction,
    WebSearchAction,
    SummarizeCodeAction,
    DetectVerificationAction,
    SuggestVerificationAction,
    InspectGitDiffAction,
    RepoMapAction,
    RankContextAction,
    SymbolIndexAction,
    DependencyGraphAction,
]


class ToolResult(BaseModel):
    ok: bool
    output: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
