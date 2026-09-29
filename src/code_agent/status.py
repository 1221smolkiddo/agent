from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
import sys
import time
from pathlib import Path
from typing import TypeVar

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
    RankContextAction,
    ReadFileAction,
    ReadProcessLogsAction,
    RecoverTransactionsAction,
    RedoTransactionAction,
    RepoMapAction,
    RestartProcessAction,
    RunShellAction,
    SendProcessInputAction,
    SearchAction,
    RestoreSnapshotAction,
    SuggestVerificationAction,
    SymbolIndexAction,
    SummarizeCodeAction,
    StartProcessAction,
    StopProcessAction,
    ToolResult,
    UpdatePlanAction,
    UndoTransactionAction,
    ProcessEventsAction,
    WebSearchAction,
    WriteFileAction,
)
from .terminal_ui import console
from .safety import redact_command_for_display, redact_command_output_for_display, redact_secrets, sanitize_payload

CallbackResult = TypeVar("CallbackResult")


@dataclass
class CommandDisplay:
    command: str
    status: str = "running"
    summary: str = "Running..."
    output: str = ""
    duration_ms: float = 0.0


class StatusReporter:
    _seen_workspace_summaries: set[str] = set()

    def __init__(self) -> None:
        self._consecutive_retries = 0
        self._current_label = ""
        self._current_detail = ""
        self._is_generating = False
        self._stopped = False
        self._paused = False
        self._interactive = bool(console.is_terminal and sys.stdin.isatty())
        self._started_at = time.monotonic()
        self._step = 0
        self._context_percent: int | None = None
        self._commands: list[CommandDisplay] = []
        self._expanded = False
        # stages: list of tuples (stage_detail, status) where status is 'pending','in-progress','done'
        self._stages: list[tuple[str, str]] = []
        self._live = self._new_live()
        self._live.start()

    def _new_live(self) -> Live:
        return Live(console=console, transient=True, refresh_per_second=4, get_renderable=self._render)

    @classmethod
    def mark_workspace_seen(cls, summary: str) -> None:
        cls._seen_workspace_summaries.add(summary)

    def _render(self) -> Group:
        self._current_label = redact_secrets(self._current_label)
        self._current_detail = redact_secrets(self._current_detail)
        self._stages = [(redact_secrets(stage), status) for stage, status in self._stages]
        lines = []

        if self._current_label or self._is_generating:
            label = self._current_label or "Thinking"
            detail = self._current_detail or ""
            text = Text()
            text.append(" ")
            text.append(redact_secrets(label), style="bold cyan")
            if detail:
                text.append(f" — {detail}", style="default")
            spinner = Spinner("dots", text=text, style="cyan", speed=0.8)
            lines.append(spinner)
        else:
            lines.append(Text("Ready. /help /history /report /diff", style="muted"))

        # Keep one compact progress surface for actual tool work. While waiting
        # for the model, a single Thinking spinner is calmer and avoids noise.
        if self._stages and not self._is_generating:
            prog = Text()
            if hasattr(self, "_active_profile") and self._active_profile:
                prog.append(f"[{redact_secrets(self._active_profile).upper()}] ", style="bold magenta")
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

        if self._interactive and self._commands:
            lines.extend(self._command_lines())
        if self._interactive:
            lines.append(self._bottom_status())
        return Group(*lines)

    def set_profile(self, profile: str) -> None:
        self._active_profile = profile
        self._update()

    def _update(self) -> None:
        if self._stopped or self._paused:
            return
        self._live.update(self._render())

    def _complete_current(self, *, refresh: bool = True) -> None:
        if self._current_detail:
            # Mark the corresponding stage as done
            for idx in range(len(self._stages) - 1, -1, -1):
                if self._stages[idx][0] == self._current_detail:
                    self._stages[idx] = (self._stages[idx][0], "done")
                    break
        self._current_label = ""
        self._current_detail = ""
        if refresh:
            self._update()

    def pause(self) -> None:
        if self._stopped or self._paused:
            return
        self._paused = True
        self._live.stop()

    def resume(self) -> None:
        if self._stopped or not self._paused:
            return
        self._paused = False
        self._live = self._new_live()
        self._live.start()
        self._update()

    def guard_prompt(
        self,
        callback: Callable[..., CallbackResult],
    ) -> Callable[..., CallbackResult]:
        def guarded(*args: object, **kwargs: object) -> CallbackResult:
            self.pause()
            try:
                return callback(*args, **kwargs)
            finally:
                self.resume()

        return guarded

    def phase(self, label: str) -> None:
        self._current_label = redact_secrets(label)
        self._current_detail = ""
        self._update()

    def thinking(self, step: int) -> None:
        self._current_label = "Thinking"
        self._current_detail = "Waiting for model"
        self._step = step
        self._update()

    def action(self, action: AgentAction) -> None:
        self._consecutive_retries = 0
        self._complete_current(refresh=False)
        stage, detail = _semantic_stage(action)
        if isinstance(action, (RunShellAction, StartProcessAction)):
            self._begin_command(action.command)
        stage, detail = redact_secrets(stage), redact_secrets(detail)
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
        if self._consecutive_retries >= 2 or "replan" in detail.lower() or "strategy" in detail.lower():
            self._complete_current(refresh=False)
            self._current_label = "Replanning" if "replan" in detail.lower() or "strategy" in detail.lower() else "Recovering"
            self._current_detail = friendly_retry_detail(detail)
            if not any(s == self._current_detail for s, _ in self._stages):
                self._stages.append((self._current_detail, "in-progress"))
            self._update()

    def tool_result(
        self,
        action: AgentAction,
        result: ToolResult,
        elapsed_ms: float,
    ) -> None:
        result.metadata = sanitize_payload(result.metadata)
        if isinstance(action, (RunShellAction, StartProcessAction)):
            self._finish_command(action.command, result, elapsed_ms)
        transaction = result.metadata.get("transaction")
        if isinstance(transaction, dict) and transaction.get("id"):
            style = "bold green" if result.ok else "bold red"
            console.print(
                Text("TRANSACTION", style=style)
                + Text(
                    f"   {transaction['id']}  {transaction.get('state', 'unknown')}  "
                    f"files={len(transaction.get('paths', []))}"
                )
            )
        if action.type != "run_shell":
            process = result.metadata.get("process")
            if isinstance(process, dict) and process.get("process_id"):
                status = process.get("status") or ("accepted" if result.ok else "failed")
                console.print(
                    Text("PROCESS", style="bold green" if result.ok else "bold red")
                    + Text(f"   {process['process_id']}  {status}")
                )
            return
        diagnostics = result.metadata.get("diagnostics")
        if not isinstance(diagnostics, dict):
            return
        items = diagnostics.get("diagnostics", [])
        failed_tests = diagnostics.get("failed_tests", [])
        if not isinstance(items, list) or (not items and not failed_tests):
            return
        summary = str(diagnostics.get("summary") or "Command diagnostics")
        counts = diagnostics.get("counts", {})
        count_detail = ""
        if isinstance(counts, dict):
            count_detail = (
                f"  errors={counts.get('error', 0)} warnings={counts.get('warning', 0)}"
            )
        console.print(
            Text("DIAGNOSTICS", style="bold red") + Text(f"   {summary}{count_detail}")
        )
        grouped: dict[str, list[dict[str, object]]] = {}
        for item in items[:8]:
            if isinstance(item, dict):
                grouped.setdefault(str(item.get("path") or "<process>"), []).append(item)
        for path, group in grouped.items():
            tool = str(group[0].get("tool") or "command")
            console.print(Text(f"  {path} [{tool}]", style="bold"))
            for item in group:
                _print_terminal_diagnostic(item)
        if len(items) > 8:
            console.print(Text(f"  … {len(items) - 8} more diagnostics", style="dim"))
        console.print(Text(f"  completed in {elapsed_ms:.0f}ms", style="dim"))

    def _begin_command(self, command: str) -> None:
        display = redact_command_for_display(command)
        self._commands.append(CommandDisplay(command=display))
        self._commands = self._commands[-8:]
        self._expanded = False
        if not self._interactive:
            console.print(Text(f"RUN: {display}"))
        self._update()

    @staticmethod
    def _safe_command_output(output: str) -> str:
        private_field = re.compile(
            r"(?i)\b(?:reasoning_content|reasoning_details|chain_of_thought|private_analysis|provider_debug)\b\s*[:=]"
        )
        visible = [line for line in output.splitlines() if not private_field.search(line)]
        return redact_secrets("\n".join(visible))

    @staticmethod
    def _command_summary(output: str, ok: bool) -> str:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        summary = next(
            (line for line in reversed(lines)
             if re.search(r"\b\d+\s+(?:passed|failed|skipped|errors?)\b", line, re.I)),
            lines[-1] if lines else ("Completed" if ok else "FAILED"),
        )
        return redact_secrets(summary[:180])

    def _finish_command(self, command: str, result: ToolResult, elapsed_ms: float) -> None:
        display = redact_command_for_display(command)
        entry = next(
            (item for item in reversed(self._commands)
             if item.command == display and item.status == "running"),
            None,
        )
        if entry is None:
            self._begin_command(command)
            entry = self._commands[-1]
        safe_output = self._safe_command_output(redact_command_output_for_display(command, result.output))
        entry.output = safe_output[-4000:]
        entry.status = "cancelled" if result.metadata.get("cancelled") else ("passed" if result.ok else "failed")
        entry.summary = self._command_summary(safe_output, result.ok)
        entry.duration_ms = elapsed_ms
        symbol = "!" if entry.status == "cancelled" else ("✓" if result.ok else "✗")
        if self._interactive:
            console.print(Text(f"{symbol} {entry.command}\n  {entry.summary}", style="green" if result.ok else "red"))
        else:
            label = "PASS" if result.ok else ("CANCELLED" if entry.status == "cancelled" else "FAIL")
            console.print(Text(f"{label}: {entry.summary}"))
        self._update()

    def _command_lines(self) -> list[Text]:
        entry = self._commands[-1]
        symbol = "›" if entry.status == "running" else ("✓" if entry.status == "passed" else "✗")
        width = max(12, console.width - 4)
        command = Text(f"{symbol} {entry.command}", style="cyan")
        command.truncate(width, overflow="ellipsis")
        lines = [command, Text(f"  {entry.summary}", style="dim")]
        if self._expanded and entry.output:
            for line in entry.output.splitlines()[-40:]:
                rendered = Text("  " + redact_secrets(line), style="dim")
                rendered.truncate(width, overflow="ellipsis")
                lines.append(rendered)
        elif entry.status != "running" and entry.output:
            lines.append(Text("  press o to expand", style="dim"))
        return lines

    def toggle_command_output(self) -> bool:
        if not self._interactive or not self._commands:
            return False
        self._expanded = not self._expanded
        self._update()
        return self._expanded

    def command_history(self) -> tuple[CommandDisplay, ...]:
        return tuple(self._commands)

    def context_status(self, input_estimate: int, budget: int) -> None:
        self._context_percent = min(100, max(0, round(input_estimate * 100 / max(1, budget))))
        self._update()

    def _bottom_status(self) -> Text:
        phase = (self._current_label or "Ready").upper()
        elapsed = int(time.monotonic() - self._started_at)
        parts = [phase, f"{elapsed // 60:02d}:{elapsed % 60:02d}"]
        optional = [f"step {self._step}"] if self._step else []
        if self._context_percent is not None:
            optional.append(f"ctx {self._context_percent}%")
        for item in optional:
            if len(" • ".join(parts + [item])) <= max(12, console.width - 2):
                parts.append(item)
        text = Text(" • ".join(parts), style="reverse dim")
        text.truncate(max(12, console.width - 2), overflow="ellipsis")
        return text

    def cancelled(self) -> None:
        if self._commands and self._commands[-1].status == "running":
            self._commands[-1].status = "cancelled"
            self._commands[-1].summary = "Process stopped"
        self._current_label = "Cancelled"
        self._update()

    def done(self) -> None:
        if self._stopped:
            return
        self._current_label = ""
        self._current_detail = ""
        self._is_generating = False
        self._stages.clear()
        self._stopped = True
        if not self._paused:
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
        summary = redact_secrets(summary)
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
    if isinstance(action, MoveFileAction):
        return "Editing Files", f"Moved {action.source} to {action.destination}"
    if isinstance(action, ListTransactionsAction):
        return "Inspecting Project", "Inspected transaction history"
    if isinstance(
        action,
        (
            UndoTransactionAction,
            RedoTransactionAction,
            RestoreSnapshotAction,
            RecoverTransactionsAction,
        ),
    ):
        return "Recovering", f"Executed {action.type}"
    if isinstance(action, RunShellAction):
        if any(w in action.command.lower() for w in ["test", "pytest", "lint", "check"]):
            return "Running Verification", "Command execution"
        return "Applying Fixes", "Command execution"
    if isinstance(action, StartProcessAction):
        return "Starting Process", "Managed command started"
    if isinstance(action, (StopProcessAction, RestartProcessAction, SendProcessInputAction)):
        return "Managing Process", f"Executed {action.type} for {action.process_id}"
    if isinstance(
        action,
        (ListProcessesAction, InspectProcessAction, ReadProcessLogsAction, ProcessEventsAction),
    ):
        return "Monitoring Process", f"Inspected {getattr(action, 'process_id', 'jobs')}"
    if isinstance(
        action,
        (
            ReadFileAction,
            ListFilesAction,
            SummarizeCodeAction,
            RepoMapAction,
            RankContextAction,
            SymbolIndexAction,
            DependencyGraphAction,
            SearchAction,
            InspectGitDiffAction,
            LspStatusAction,
            LspDefinitionAction,
            LspReferencesAction,
            LspHoverAction,
            LspWorkspaceSymbolsAction,
            LspCompletionAction,
            LspDiagnosticsAction,
            LspRenameAction,
            LspFormattingAction,
            LspCodeActionsAction,
        ),
    ):
        path = getattr(action, "path", "") or "project"
        return "Inspecting Project", f"Inspected {path}"
    if isinstance(action, WebSearchAction):
        return "Understanding Request", "Web search"
    if isinstance(action, (DetectVerificationAction, SuggestVerificationAction)):
        return "Inspecting Project", "Checked verification commands"
    return "Working", f"Executed {action.type}"


def _print_terminal_diagnostic(item: dict[str, object]) -> None:
    item = sanitize_payload(item)
    severity = str(item.get("severity") or "error")
    style = {
        "error": "bold red",
        "warning": "yellow",
        "information": "cyan",
        "hint": "dim",
    }.get(severity, "default")
    path = str(item.get("path") or "")
    line = item.get("line")
    column = item.get("column")
    location = path
    if line is not None:
        location += f":{line}"
    if column is not None:
        location += f":{column}"
    text = Text("  • ")
    if location:
        try:
            target = Path(path).resolve().as_uri()
            text.append(location, style=f"underline {style} link {target}")
        except ValueError:
            text.append(location, style=style)
        text.append(" ")
    rule = str(item.get("rule") or "")
    if rule:
        text.append(f"[{rule}] ", style="magenta")
    text.append(str(item.get("message") or ""), style=style)
    console.print(text)
    snippet = str(item.get("snippet") or "")
    if snippet:
        target_line = item.get("line")
        for snippet_line in snippet.splitlines():
            line_label = snippet_line.split("|", 1)[0].strip()
            snippet_style = "bold red" if line_label == str(target_line) else "dim"
            console.print(Text(f"      {snippet_line}", style=snippet_style))


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
