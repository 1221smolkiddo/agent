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
    _seen_workspace_summaries: set[str] = set()

    def __init__(self) -> None:
        self._consecutive_retries = 0
        self._current_label = ""
        self._current_detail = ""
        self._is_generating = False
        self._stopped = False
        # stages: list of tuples (stage_detail, status) where status is 'pending','in-progress','done'
        self._stages: list[tuple[str, str]] = []
        self._live = Live(console=console, transient=True, refresh_per_second=10)
        self._live.start()

    @classmethod
    def mark_workspace_seen(cls, summary: str) -> None:
        cls._seen_workspace_summaries.add(summary)

    def _render(self) -> Group:
        lines = []

        if self._current_label or self._is_generating:
            label = self._current_label or "Thinking"
            detail = self._current_detail or ""
            text = Text()
            text.append(" ")
            text.append(label, style="bold cyan")
            if detail:
                text.append(f" — {detail}", style="default")
            spinner = Spinner("dots", text=text, style="cyan", speed=0.1)
            lines.append(spinner)
        else:
            lines.append(Text("Ready. /help /history /report /diff", style="muted"))

        # Keep one compact progress surface for actual tool work. While waiting
        # for the model, a single Thinking spinner is calmer and avoids noise.
        if self._stages and not self._is_generating:
            prog = Text()
            prog.append("Progress:\n", style="muted")
            for stage, status in self._stages[-8:]:
                if status == "done":
                    prog.append("✓ ", style="green")
                    prog.append(f"{stage}\n")
                elif status == "in-progress":
                    prog.append(Text("⠋ ", style="cyan"))
                    prog.append(f"{stage}\n", style="cyan")
                else:
                    prog.append("  ")
                    prog.append(f"{stage}\n", style="muted")
            lines.append(prog)

        return Group(*lines)

    def _update(self) -> None:
        if self._stopped:
            return
        self._live.update(self._render())

    def _complete_current(self) -> None:
        if self._current_detail:
            # Mark the corresponding stage as done
            for idx in range(len(self._stages) - 1, -1, -1):
                if self._stages[idx][0] == self._current_detail:
                    self._stages[idx] = (self._stages[idx][0], "done")
                    break
        self._current_label = ""
        self._current_detail = ""
        self._update()

    def thinking(self, step: int) -> None:
        if self._current_label or self._stages:
            return
        self._current_label = "Thinking"
        self._current_detail = ""
        self._update()

    def action(self, action: AgentAction) -> None:
        self._consecutive_retries = 0
        self._complete_current()
        stage, detail = _semantic_stage(action)
        if stage:
            self._current_label = stage
            self._current_detail = detail
            # add or mark stage as in-progress
            if self._stages and self._stages[-1][0] == detail:
                self._stages[-1] = (detail, "in-progress")
            else:
                # avoid duplicate consecutive stage descriptions
                if not any(s == detail for s, _ in self._stages):
                    self._stages.append((detail, "in-progress"))
            self._update()

    def recovery(self, detail: str) -> None:
        self._consecutive_retries += 1
        if self._consecutive_retries >= 2:
            self._complete_current()
            self._current_label = "Recovering"
            self._current_detail = friendly_retry_detail(detail)
            if not any(s == self._current_detail for s, _ in self._stages):
                self._stages.append((self._current_detail, "in-progress"))
            self._update()

    def done(self) -> None:
        if self._stopped:
            return
        self._current_label = ""
        self._current_detail = ""
        self._is_generating = False
        self._stages.clear()
        self._stopped = True
        self._live.stop()

    def model_stream_start(self, step: int) -> None:
        self._is_generating = True
        self._current_label = "Thinking"
        self._current_detail = ""
        self._update()

    def model_stream_chunk(self, chunk: str) -> None:
        pass

    def model_stream_end(self) -> None:
        self._is_generating = False
        if self._current_label == "Thinking":
            self._current_label = ""
            self._current_detail = ""
        self._update()

    def workspace_analysis(self, summary: str) -> None:
        if summary in self._seen_workspace_summaries:
            return
        self.mark_workspace_seen(summary)
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
