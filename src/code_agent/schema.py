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


AgentAction = Union[
    FinalAction,
    ListFilesAction,
    ReadFileAction,
    WriteFileAction,
    EditFileAction,
    RunShellAction,
    SearchAction,
    WebSearchAction,
    SummarizeCodeAction,
]


class ToolResult(BaseModel):
    ok: bool
    output: str = Field(default="")
