from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .storage import AgentStorage


ApprovalCallback = Callable[[str, str], bool | str]


@dataclass(frozen=True)
class GitSnapshot:
    cwd: Path
    branch: str
    status_lines: list[str]
    staged_files: list[str] = field(default_factory=list)
    unstaged_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    diff_stat: str = ""
    staged_diff_stat: str = ""

    @property
    def changed_files(self) -> list[str]:
        return sorted({*self.staged_files, *self.unstaged_files, *self.untracked_files})

    @property
    def has_changes(self) -> bool:
        return bool(self.status_lines)


@dataclass(frozen=True)
class CollaborationContext:
    snapshot: GitSnapshot
    run_id: int | None = None
    task: str = ""
    work_report: dict[str, Any] | None = None
    mutation_records: list[dict[str, Any]] = field(default_factory=list)
    verification_results: list[dict[str, Any]] = field(default_factory=list)
    command_records: list[dict[str, Any]] = field(default_factory=list)
    failed_actions: list[dict[str, Any]] = field(default_factory=list)
    denied_actions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed_paths(self) -> list[str]:
        paths = {
            str(record.get("path"))
            for record in self.mutation_records
            if record.get("ok") is True and record.get("path")
        }
        if paths:
            return sorted(paths)
        return self.snapshot.changed_files


@dataclass(frozen=True)
class BranchResult:
    ok: bool
    branch: str
    output: str


@dataclass(frozen=True)
class CommitResult:
    ok: bool
    message: str
    output: str
    committed: bool = False


def build_collaboration_context(
    cwd: Path,
    storage: AgentStorage | None = None,
    *,
    run_id: int | None = None,
) -> CollaborationContext:
    snapshot = inspect_git_snapshot(cwd)
    if storage is None or run_id is None:
        return CollaborationContext(snapshot=snapshot)

    run = storage.get_run(run_id)
    if run is None:
        raise ValueError(f"No run found with id {run_id}.")
    steps = storage.run_steps_payloads(run_id)
    return CollaborationContext(
        snapshot=snapshot,
        run_id=run_id,
        task=str(run["task"]),
        work_report=storage.get_work_report(run_id),
        mutation_records=_collect_records(steps, "mutation_records"),
        verification_results=_collect_verification(steps),
        command_records=_collect_command_records(steps),
        failed_actions=_collect_failed_actions(steps),
        denied_actions=_collect_denied_actions(steps),
    )


def inspect_git_snapshot(cwd: Path) -> GitSnapshot:
    root = _git_root(cwd)
    status_output = _git(root, ["status", "--short", "--untracked-files=all"]).stdout.rstrip()
    status_lines = status_output.splitlines() if status_output else []
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in status_lines:
        if len(line) < 4:
            continue
        index_status = line[0]
        worktree_status = line[1]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if line.startswith("??"):
            untracked.append(path)
            continue
        if index_status != " ":
            staged.append(path)
        if worktree_status != " ":
            unstaged.append(path)
    branch = _git(root, ["branch", "--show-current"]).stdout.strip() or "<detached>"
    diff_stat = _git(root, ["diff", "--stat"]).stdout.strip()
    staged_diff_stat = _git(root, ["diff", "--cached", "--stat"]).stdout.strip()
    return GitSnapshot(
        cwd=root,
        branch=branch,
        status_lines=status_lines,
        staged_files=sorted(set(staged)),
        unstaged_files=sorted(set(unstaged)),
        untracked_files=sorted(set(untracked)),
        diff_stat=diff_stat,
        staged_diff_stat=staged_diff_stat,
    )


def format_collaboration_status(context: CollaborationContext) -> str:
    snapshot = context.snapshot
    lines = [
        "Agent47 collaboration status:",
        f"- Workspace: {snapshot.cwd}",
        f"- Branch: {snapshot.branch}",
        f"- Changed files: {len(snapshot.changed_files)}",
    ]
    if context.run_id is not None:
        lines.append(f"- Run: {context.run_id}")
        if context.task:
            lines.append(f"- Task: {_single_line(context.task)}")
    if snapshot.changed_files:
        lines.append("- Files:")
        lines.extend(f"  - {path}" for path in snapshot.changed_files[:40])
    if context.verification_results:
        lines.append("- Verification:")
        lines.extend(f"  - {_verification_line(item)}" for item in context.verification_results)
    if context.failed_actions or context.denied_actions:
        lines.append(
            f"- Risks: failed_actions={len(context.failed_actions)} denied_actions={len(context.denied_actions)}"
        )
    return "\n".join(lines)


def build_commit_message(context: CollaborationContext) -> str:
    scope = _scope_from_paths(context.changed_paths)
    subject = _subject_from_context(context, scope)
    body_lines = _summary_bullets(context)
    verification = [_verification_line(item) for item in context.verification_results]
    risk_lines = _risk_lines(context)

    lines = [subject, ""]
    if body_lines:
        lines.extend(["Summary:", *[f"- {line}" for line in body_lines], ""])
    if verification:
        lines.extend(["Verification:", *[f"- {line}" for line in verification], ""])
    if risk_lines:
        lines.extend(["Risks:", *[f"- {line}" for line in risk_lines], ""])
    return "\n".join(lines).rstrip()


def build_pr_summary(context: CollaborationContext, template: str | None = None) -> str:
    what_changed = _summary_bullets(context) or [
        f"Updated {len(context.changed_paths)} file(s): " + ", ".join(context.changed_paths[:8])
    ]
    tested = [_verification_line(item) for item in context.verification_results]
    if not tested:
        tested = ["Not recorded."]
    notes = _risk_lines(context) or ["No follow-up risks recorded."]

    if template:
        return _fill_pr_template(template, what_changed=what_changed, tested=tested, notes=notes)
    return "\n".join(
        [
            "## What Changed",
            *[f"- {line}" for line in what_changed],
            "",
            "## How I Tested",
            *[f"- {line}" for line in tested],
            "",
            "## Notes / Follow-Up",
            *[f"- {line}" for line in notes],
        ]
    )


def build_changelog_entry(context: CollaborationContext, *, version: str = "Unreleased") -> str:
    summary = _summary_bullets(context) or [
        f"Updated {len(context.changed_paths)} file(s): " + ", ".join(context.changed_paths[:8])
    ]
    lines = [f"## {version}", "", "### Changed"]
    lines.extend(f"- {line}" for line in summary)
    verification = [_verification_line(item) for item in context.verification_results]
    if verification:
        lines.extend(["", "### Verified", *[f"- {line}" for line in verification]])
    risks = _risk_lines(context)
    if risks:
        lines.extend(["", "### Notes", *[f"- {line}" for line in risks]])
    return "\n".join(lines)


def create_branch(
    cwd: Path,
    branch: str,
    *,
    approval_callback: ApprovalCallback | None = None,
    apply: bool = False,
) -> BranchResult:
    root = _git_root(cwd)
    _validate_branch_name(root, branch)
    detail = f"Create and switch to git branch `{branch}` in {root}."
    if not apply:
        return BranchResult(ok=True, branch=branch, output=f"Preview: {detail}")
    if approval_callback is not None and not _approved(approval_callback("git_branch", detail)):
        return BranchResult(ok=False, branch=branch, output="Permission denied for git_branch.")
    completed = _git(root, ["switch", "-c", branch], check=False)
    output = _combined_output(completed) or f"Switched to a new branch '{branch}'"
    return BranchResult(ok=completed.returncode == 0, branch=branch, output=output)


def commit_changes(
    cwd: Path,
    message: str,
    *,
    paths: list[str] | None = None,
    approval_callback: ApprovalCallback | None = None,
    commit: bool = False,
) -> CommitResult:
    root = _git_root(cwd)
    selected_paths = paths or inspect_git_snapshot(root).changed_files
    if not selected_paths:
        return CommitResult(ok=False, message=message, output="No changed files to commit.")
    detail = "\n".join(
        [
            "Create git commit with:",
            "",
            message,
            "",
            "Paths:",
            *[f"- {path}" for path in selected_paths],
        ]
    )
    if not commit:
        return CommitResult(ok=True, message=message, output="Preview:\n" + detail)
    if approval_callback is not None and not _approved(approval_callback("git_commit", detail)):
        return CommitResult(ok=False, message=message, output="Permission denied for git_commit.")
    add = _git(root, ["add", "--", *selected_paths], check=False)
    if add.returncode != 0:
        return CommitResult(ok=False, message=message, output=_combined_output(add))
    completed = _git(root, ["commit", "-m", message], check=False)
    return CommitResult(
        ok=completed.returncode == 0,
        message=message,
        output=_combined_output(completed),
        committed=completed.returncode == 0,
    )


def load_pr_template(cwd: Path) -> str | None:
    root = _git_root(cwd)
    path = root / ".github" / "PULL_REQUEST_TEMPLATE.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _fill_pr_template(
    template: str,
    *,
    what_changed: list[str],
    tested: list[str],
    notes: list[str],
) -> str:
    replacements = {
        "## What Changed": what_changed,
        "## How I Tested": tested,
        "## Notes / Follow-Up": notes,
    }
    lines = template.rstrip().splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        output.append(line)
        if line.strip() in replacements:
            index += 1
            while index < len(lines) and not lines[index].startswith("## "):
                index += 1
            output.extend(["", *[f"- {item}" for item in replacements[line.strip()]], ""])
            continue
        index += 1
    return "\n".join(output).rstrip() + "\n"


def _collect_records(steps: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for step in steps:
        payload = step.get("payload", {})
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            records.extend(item for item in payload[key] if isinstance(item, dict))
    return records


def _collect_verification(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = _collect_records(steps, "automatic_verification_results")
    for step in steps:
        payload = step.get("payload", {})
        if isinstance(payload, dict) and isinstance(payload.get("verification_result"), dict):
            records.append(payload["verification_result"])
        if isinstance(payload, dict) and payload.get("type") == "automatic_verification_result":
            records.append(payload)
    return _dedupe_verification(records)


def _collect_command_records(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for step in steps:
        payload = step.get("payload", {})
        if isinstance(payload, dict) and payload.get("type") == "tool_result":
            verification = payload.get("verification_result")
            if isinstance(verification, dict):
                records.append(verification)
    return records


def _collect_failed_actions(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for step in steps:
        payload = step.get("payload", {})
        if isinstance(payload, dict) and payload.get("type") == "tool_result" and payload.get("ok") is False:
            records.append(payload)
    return records


def _collect_denied_actions(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record
        for record in _collect_failed_actions(steps)
        if "Permission denied" in str(record.get("output", ""))
    ]


def _dedupe_verification(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        key = (str(record.get("purpose", "")), str(record.get("command", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _summary_bullets(context: CollaborationContext) -> list[str]:
    report_sections = {}
    if context.work_report and isinstance(context.work_report.get("payload"), dict):
        report_sections = context.work_report["payload"].get("sections", {})
    changes = report_sections.get("change_summary") if isinstance(report_sections, dict) else None
    if isinstance(changes, list) and changes:
        return [
            f"{item.get('action', 'Changed')} {item.get('path', '<unknown>')}"
            for item in changes
            if isinstance(item, dict)
        ][:8]
    if context.mutation_records:
        return [
            f"{_friendly_action(record)} {record.get('path', '<unknown>')}"
            for record in context.mutation_records
            if record.get("ok") is True
        ][:8]
    if context.changed_paths:
        return [f"Updated {path}" for path in context.changed_paths[:8]]
    return []


def _risk_lines(context: CollaborationContext) -> list[str]:
    risks: list[str] = []
    if context.failed_actions:
        risks.append(f"{len(context.failed_actions)} failed action(s) recorded.")
    if context.denied_actions:
        risks.append(f"{len(context.denied_actions)} denied action(s) recorded.")
    failed_checks = [item for item in context.verification_results if item.get("ok") is False]
    if failed_checks:
        risks.append(f"{len(failed_checks)} verification check(s) failed in the recorded run.")
    if not context.verification_results:
        risks.append("No verification results were recorded.")
    return risks


def _subject_from_context(context: CollaborationContext, scope: str) -> str:
    if context.task:
        verb = "update"
        lowered = context.task.lower()
        if any(token in lowered for token in ["fix", "bug", "failing", "regression"]):
            verb = "fix"
        elif any(token in lowered for token in ["add", "create", "implement", "build"]):
            verb = "feat"
        subject = _single_line(context.task, max_chars=58)
        return f"{verb}({scope}): {subject}" if scope else f"{verb}: {subject}"
    return f"chore({scope}): update project files" if scope else "chore: update project files"


def _scope_from_paths(paths: list[str]) -> str:
    if not paths:
        return ""
    first_parts = {path.split("/", 1)[0].split("\\", 1)[0] for path in paths}
    if len(first_parts) == 1:
        return next(iter(first_parts)).replace(".", "")
    if any(path.startswith("tests/") or path.startswith("tests\\") for path in paths):
        return "tests"
    if any(path.startswith("docs/") or path.startswith("docs\\") for path in paths):
        return "docs"
    return "repo"


def _verification_line(item: dict[str, Any]) -> str:
    purpose = str(item.get("purpose") or "check")
    command = str(item.get("command") or "<unknown>")
    status = str(item.get("status") or ("passed" if item.get("ok") is True else "failed"))
    return f"{purpose} `{command}`: {status}"


def _friendly_action(record: dict[str, Any]) -> str:
    return {
        "write_file": "Created",
        "edit_file": "Modified",
        "apply_patch": "Patched",
        "delete_file": "Deleted",
    }.get(str(record.get("action", "")), "Changed")


def _validate_branch_name(root: Path, branch: str) -> None:
    if not branch.strip() or branch.startswith("-") or ".." in branch:
        raise ValueError(f"Unsafe branch name: {branch}")
    completed = _git(root, ["check-ref-format", "--branch", branch], check=False)
    if completed.returncode != 0:
        raise ValueError(_combined_output(completed) or f"Invalid branch name: {branch}")


def _git_root(cwd: Path) -> Path:
    completed = _git(cwd.resolve(), ["rev-parse", "--show-toplevel"], check=False)
    if completed.returncode != 0:
        raise ValueError(f"Not a git repository: {cwd}")
    return Path(completed.stdout.strip()).resolve()


def _git(cwd: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if check and completed.returncode != 0:
        raise ValueError(_combined_output(completed))
    return completed


def _combined_output(completed: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()


def _approved(value: bool | str) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"y", "yes", "approve", "approved"}
    return bool(value)


def _single_line(value: str, max_chars: int = 120) -> str:
    rendered = " ".join(value.split())
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars].rstrip() + " <truncated>"
