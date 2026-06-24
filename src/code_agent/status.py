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
        self._timeline: list[str] = []
        self._live = Live(console=console, transient=False, refresh_per_second=10)
        self._live.start()
        # remember last workspace summary to avoid reprinting it multiple times
        self._last_workspace_summary: str | None = None

    def _render(self) -> Group:
        lines = []

        if self._current_label or self._is_generating:
            label = self._current_label or "Working"
            detail = self._current_detail or ""
            text = Text()
            text.append(" ")
            text.append(label, style="bold cyan")
            if detail:
                text.append(f" — {detail}", style="default")
            spinner = Spinner("dots", text=text, style="cyan", speed=0.1)
            lines.append(spinner)
        elif self._timeline:
            # Intentionally avoid a repeated 'Awaiting next action' line to reduce noise;
            # the timeline will be shown below when present.
            pass
        else:
            lines.append(Text("Ready for your task. Use /help for commands.", style="muted"))

        if self._timeline:
            timeline = Text()
            for item in self._timeline[-6:]:
                timeline.append("✓ ", style="green")
                timeline.append(f"{item}\n")
            lines.append(timeline)

        return Group(*lines)

    def _update(self) -> None:
        self._live.update(self._render())

    def _complete_current(self) -> None:
        if self._current_detail:
            # Append completed details but avoid consecutive duplicates
            if not self._timeline or self._timeline[-1] != self._current_detail:
                self._timeline.append(self._current_detail)
                self._timeline = self._timeline[-6:]
        self._current_label = ""
        self._current_detail = ""
        self._update()

    def thinking(self, step: int) -> None:
        if self._current_label or self._timeline:
            return
        self._current_label = "Inspecting Project"
        self._current_detail = "Gathering context"
        self._update()

    def action(self, action: AgentAction) -> None:
        self._consecutive_retries = 0
        self._complete_current()
        stage, detail = _semantic_stage(action)
        if stage:
            self._current_label = stage
            self._current_detail = detail
            self._update()

    def recovery(self, detail: str) -> None:
        self._consecutive_retries += 1
        if self._consecutive_retries >= 2:
            self._complete_current()
            self._current_label = "Recovering"
            self._current_detail = friendly_retry_detail(detail)
            self._update()

    def done(self) -> None:
        self._complete_current()
        self._live.stop()

    def model_stream_start(self, step: int) -> None:
        self._is_generating = True
        self._current_label = "Generating output"
        self._update()

    def model_stream_chunk(self, chunk: str) -> None:
        pass

    def model_stream_end(self) -> None:
        self._is_generating = False
        self._update()

    def workspace_analysis(self, summary: str) -> None:
        # Only print the workspace summary if it changed (or first time)
        if summary == self._last_workspace_summary:
            return
        self._last_workspace_summary = summary
        console.print(Text("WORKSPACE", style="bold cyan") + Text("   ") + Text(summary.splitlines()[0], style="default"))
        for line in summary.splitlines()[1:]:
            console.print(Text(line, style="default"))
        console.print()

    def mutation_preview(
        self,
        creates: list[str],
        modifies: list[str],
        deletes: list[str],
    ) -> None:
        if creates:
            console.print(Text("Will Create:", style="yellow"))
            for p in creates:
                console.print(f"• {p}")
        if modifies:
            console.print(Text("Will Modify:", style="yellow"))
            for p in modifies:
                console.print(f"• {p}")
        if deletes:
            console.print(Text("Will Delete:", style="red"))
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
    if (cwd / "next.config.js").exists() or (cwd / "next.config.mjs").exists():
        project_type = "Next.js App"
    elif (cwd / "package.json").exists():
        if (cwd / "src" / "App.tsx").exists() or (cwd / "src" / "App.jsx").exists():
            project_type = "React App"
        elif (cwd / "src" / "main.ts").exists() or (cwd / "src" / "main.js").exists():
            project_type = "Web App"
        else:
            project_type = "Node.js Project"
    elif (cwd / "pyproject.toml").exists() or (cwd / "requirements.txt").exists() or (cwd / "setup.py").exists():
        project_type = "Python Package"
    elif (cwd / "Cargo.toml").exists():
        project_type = "Rust Workspace"
    elif (cwd / "go.mod").exists():
        project_type = "Go Project"
    elif (cwd / "index.html").exists():
        project_type = "Static Website"
    else:
        project_type = "Project"

    detected = []
    for f in ["package.json", "pyproject.toml", "Cargo.toml", "go.mod", "index.html", "src", "tests", "test", "docs", "README.md"]:
        if (cwd / f).exists():
            detected.append(f"{f}/" if (cwd / f).is_dir() else f)

    summary = project_type
    if detected:
        summary += "\nDetected:\n" + "\n".join(f"• {d}" for d in detected)
    return summary
