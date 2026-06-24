from __future__ import annotations

from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from .schema import (
    AgentAction,
    ApplyPatchAction,
    DeleteFileAction,
    DependencyGraphAction,
    DetectVerificationAction,
    EditFileAction,
    InspectGitDiffAction,
    ListFilesAction,
    RankContextAction,
    ReadFileAction,
    RepoMapAction,
    RunShellAction,
    SearchAction,
    SuggestVerificationAction,
    SymbolIndexAction,
    SummarizeCodeAction,
    UpdatePlanAction,
    WebSearchAction,
    WriteFileAction,
)
from .terminal_ui import console


class StatusReporter:
    def __init__(self) -> None:
        self._consecutive_retries = 0
        self._current_label = ""
        self._current_detail = ""
        self._is_generating = False
        self._live = Live(console=console, transient=False, refresh_per_second=10)
        self._live.start()

    def _update_spinner(self) -> None:
        if not self._current_label and not self._is_generating:
            self._live.update("")
            return

        group = Group()
        
        if self._current_label:
            group.renderables.append(Spinner("dots", text=Text(self._current_label, style="cyan")))
        
        if self._is_generating:
            group.renderables.append(Spinner("dots", text=Text("Generating...", style="blue")))
            
        self._live.update(group)

    def _complete_current(self) -> None:
        if self._current_detail:
            console.print(f"[green]✓[/green] {self._current_detail}")
        self._current_label = ""
        self._current_detail = ""

    def thinking(self, step: int) -> None:
        # Suppress generic thinking
        pass

    def action(self, action: AgentAction) -> None:
        self._consecutive_retries = 0
        
        self._complete_current()
        
        stage, detail = _semantic_stage(action)
        if stage:
            self._current_label = stage
            self._current_detail = detail
            self._update_spinner()

    def recovery(self, detail: str) -> None:
        self._consecutive_retries += 1
        if self._consecutive_retries >= 2:
            self._complete_current()
            console.print(f"[yellow]⚠[/yellow] {friendly_retry_detail(detail)}")
            self._current_label = "Recovering"
            self._update_spinner()

    def done(self) -> None:
        self._complete_current()
        self._live.stop()

    def model_stream_start(self, step: int) -> None:
        self._is_generating = True
        self._update_spinner()

    def model_stream_chunk(self, chunk: str) -> None:
        pass

    def model_stream_end(self) -> None:
        self._is_generating = False
        self._update_spinner()

    def workspace_analysis(self, summary: str) -> None:
        console.print(f"[bold cyan]WORKSPACE[/bold cyan]   {summary.splitlines()[0]}")
        for line in summary.splitlines()[1:]:
            console.print(line)
        console.print()

    def mutation_preview(
        self,
        creates: list[str],
        modifies: list[str],
        deletes: list[str],
    ) -> None:
        if creates:
            console.print("[yellow]Will Create:[/yellow]")
            for p in creates:
                console.print(f"• {p}")
        if modifies:
            console.print("[yellow]Will Modify:[/yellow]")
            for p in modifies:
                console.print(f"• {p}")
        if deletes:
            console.print("[red]Will Delete:[/red]")
            for p in deletes:
                console.print(f"• {p}")
        if creates or modifies or deletes:
            console.print()


def _semantic_stage(action: AgentAction) -> tuple[str, str]:
    if isinstance(action, UpdatePlanAction):
        return "Planning Changes", "Generated execution plan"
    if isinstance(action, (WriteFileAction, EditFileAction, ApplyPatchAction)):
        path = getattr(action, "path", "<file>")
        return "Editing Files", f"Updated {path}"
    if isinstance(action, DeleteFileAction):
        return "Editing Files", f"Deleted {action.path}"
    if isinstance(action, RunShellAction):
        if any(w in action.command.lower() for w in ["test", "pytest", "lint", "check"]):
            return "Running Verification", f"Ran {action.command}"
        return "Applying Fixes", f"Ran {action.command}"
    if isinstance(action, (ReadFileAction, ListFilesAction, SummarizeCodeAction, RepoMapAction, RankContextAction, SymbolIndexAction, DependencyGraphAction, SearchAction, InspectGitDiffAction)):
        path = getattr(action, "path", "") or getattr(action, "query", "") or "project"
        return "Inspecting Project", f"Inspected {path}"
    if isinstance(action, WebSearchAction):
        return "Understanding Request", f"Searched web for {action.query}"
    if isinstance(action, (DetectVerificationAction, SuggestVerificationAction)):
        return "Inspecting Project", "Checked verification commands"
    return "Working", f"Executed {action.type}"


def friendly_retry_detail(detail: str) -> str:
    lowered = detail.lower()
    if "invalid model action" in lowered or "parse" in lowered:
        return "Retrying with a cleaner response"
    if "non-workspace" in lowered or "blocked workspace tool" in lowered:
        return "Switching back to chat mode"
    if "tool failed" in lowered:
        return "Trying another approach"
    if "model failed" in lowered:
        return "Model call failed"
    return "Trying again"


def analyze_workspace(cwd: Path) -> str:
    indicators: list[str] = []

    if (cwd / "package.json").exists():
        indicators.append("Node.js project")
    elif (cwd / "pyproject.toml").exists():
        indicators.append("Python Package")
    elif (cwd / "Cargo.toml").exists():
        indicators.append("Rust Workspace")
    elif (cwd / "go.mod").exists():
        indicators.append("Go project")
    else:
        indicators.append("Project")
        
    detected = []
    for f in ["package.json", "pyproject.toml", "Cargo.toml", "src", "tests", "test", "docs"]:
        if (cwd / f).exists():
            detected.append(f"{f}/" if (cwd / f).is_dir() else f)
            
    summary = indicators[0]
    if detected:
        summary += "\nDetected:\n" + "\n".join(f"• {d}" for d in detected)
    return summary
