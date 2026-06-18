from __future__ import annotations

from typing import Literal, Optional, Union

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

    @model_validator(mode="after")
    def only_one_step_in_progress(self) -> "UpdatePlanAction":
        in_progress = [step for step in self.steps if step.status == "in_progress"]
        if len(in_progress) > 1:
            raise ValueError("Only one plan step can be in_progress at a time.")
        return self


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
]


class ToolResult(BaseModel):
    ok: bool
    output: str = Field(default="")
