from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


class FinalAction(BaseModel):
    type: Literal["final"]
    message: str


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


class LocalTimeAction(BaseModel):
    type: Literal["local_time"]
    location: str


class SummarizeCodeAction(BaseModel):
    type: Literal["summarize_code"]
    path: str


class DetectVerificationAction(BaseModel):
    type: Literal["detect_verification"]


class SuggestVerificationAction(BaseModel):
    type: Literal["suggest_verification"]
    changed_paths: list[str] = Field(default_factory=list)


AgentAction = Union[
    FinalAction,
    ListFilesAction,
    ReadFileAction,
    WriteFileAction,
    EditFileAction,
    ApplyPatchAction,
    RunShellAction,
    SearchAction,
    WebSearchAction,
    LocalTimeAction,
    SummarizeCodeAction,
    DetectVerificationAction,
    SuggestVerificationAction,
]


class ToolResult(BaseModel):
    ok: bool
    output: str = Field(default="")
