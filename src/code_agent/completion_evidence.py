"""Conservative completion evidence for autonomous workspace mutations.

This is a finalization guard, not an eligibility model call or a planner.
Repository contents and recorded tool outcomes remain authoritative.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_MUTATION = re.compile(
    r"^(?:(?:please|can you|could you|i want you to|i need you to)\s+)?"
    r"(?:create|build|implement|fix|edit|write|update|add|remove|delete|rename|move|refactor|repair)\b", re.I
)
_FUTURE = re.compile(
    r"would you like me to (?:proceed|start|begin)|(?:i(?:'ll| will)|we(?:'ll| will)) "
    r"(?:start|begin|initialize|create|build|implement)|(?:i|we) can (?:create|build|implement|begin|start)|"
    r"(?:shall|should|can) (?:i|we) (?:proceed|start|begin)|do you want me to (?:proceed|start|begin)", re.I
)
_SOURCE = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".vue", ".svelte",
           ".rs", ".go", ".java", ".c", ".cpp", ".cs", ".rb", ".sh"}


def mutation_intent(task: str) -> bool:
    return bool(_MUTATION.search(task.strip()))


def waiting_reason(task: str, message: str, denied: list[dict[str, Any]]) -> str | None:
    """Only concrete required input or an observed denial permits a waiting final."""
    if not mutation_intent(task) or _FUTURE.search(message):
        return None
    if denied and re.search(r"permission|approval|write mode|enable .?write|/write|access|dry-run", message, re.I) and re.search(
        r"cannot|can't|could not|blocked|denied|need|required|waiting|enable|disabled", message, re.I
    ):
        return "waiting_permission"
    # Optional preferences/framework choices do not justify pausing an autonomous task.
    required = r"(?:required|missing|need|cannot|can't|blocked)"
    information = r"(?:credentials?|api key|access token|target (?:url|repository)|required assets?|private (?:schema|specification))"
    request = r"(?:please provide|please supply|please specify|what is|which is|provide the)"
    if re.search(required, message, re.I) and re.search(information, message, re.I) and re.search(request, message, re.I):
        return "waiting_user"
    return None


def substantive_content(path: str, content: str) -> bool:
    """Exclude placeholders and comments; size alone is never sufficient."""
    name = Path(path).name.lower()
    if name in {".gitkeep", ".gitignore", ".keep"} or not content.strip():
        return False
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        try:
            body = ast.parse(content).body
        except SyntaxError:
            return bool(content.strip())  # Verification must establish validity.
        return any(not isinstance(node, ast.Pass) and not (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ) for node in body)
    if suffix == ".json":
        try:
            value = json.loads(content)
        except ValueError:
            return False
        return bool(value)
    cleaned = re.sub(r"/\*.*?\*/|<!--.*?-->", "", content, flags=re.S)
    cleaned = re.sub(r"(?m)^\s*(?://|#).*$", "", cleaned).strip()
    return bool(cleaned) and cleaned.lower() not in {"todo", "placeholder", "pass", "{}", "[]"}


def completion_blocker(
    task: str, message: str, records: list[dict[str, Any]],
    verification: list[dict[str, Any]], workspace: Path,
) -> str | None:
    if not mutation_intent(task):
        return None
    if _FUTURE.search(message):
        return "The user already requested implementation. Make a reasonable choice and continue; a promise or proceed question cannot finish the task."
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("ok") is True and record.get("verified") is True:
            latest[str(record.get("path", ""))] = record
    verified = []
    meaningful = []
    root = workspace.resolve()
    for path, record in latest.items():
        try:
            target = (root / path).resolve()
            target.relative_to(root)
            if record.get("action") == "make_directory":
                if target.is_dir():
                    verified.append(record)
                continue
            if not record.get("exists_after", True):
                if not target.exists():
                    verified.append(record)
                continue
            raw = target.read_bytes()
            content = raw.decode("utf-8")
            current_hash = hashlib.sha256(raw).hexdigest()
            # Transactions hash bytes; legacy in-memory records hash normalized text.
            legacy_hash = hashlib.sha256(content.replace("\r\n", "\n").encode("utf-8")).hexdigest()
            if current_hash != record.get("after_sha256") and (
                record.get("transaction_id") or legacy_hash != record.get("after_sha256")
            ):
                continue
            verified.append(record)
            if substantive_content(path, content):
                meaningful.append(record)
        except (OSError, ValueError, UnicodeError):
            continue
    explicit_empty = bool(re.search(r"\b(?:empty|blank) (?:file|directory|folder)\b", task, re.I))
    if explicit_empty:
        wants_directory = bool(re.search(r"\b(?:empty|blank) (?:directory|folder)\b", task, re.I))
        if any((r.get("action") == "make_directory") == wants_directory for r in verified):
            return None
    file_operation = bool(re.match(r"^(?:please\s+)?(?:delete|remove|rename|move)\b", task, re.I))
    if file_operation and any(r.get("content_changed") for r in verified):
        return None
    if not meaningful:
        return "There is no substantive verified artifact for this request. Directories and empty placeholders are scaffolding only. Continue implementing the requested work."
    functional = bool(re.search(r"\b(?:build|implement|fix|refactor|repair|app|frontend|backend|website|feature|functionality)\b", task, re.I))
    if functional:
        source = [r for r in meaningful if Path(str(r["path"])).suffix.lower() in _SOURCE
                  and not Path(str(r["path"])).name.startswith("test_")
                  and "tests" not in Path(str(r["path"])).parts]
        needs_source = bool(re.search(r"\b(?:app|frontend|backend|website|functionality)\b", task, re.I))
        if needs_source and not source:
            return "Functional implementation requires actual source or behavior artifacts; configuration or scaffolding alone is insufficient."
        docs_only = all(Path(str(r["path"])).suffix.lower() in {".md", ".txt", ".rst"} for r in meaningful)
        if docs_only and not needs_source:
            return None
        last_change = max(int(r.get("step", 0)) for r in verified if r.get("content_changed")) if any(r.get("content_changed") for r in verified) else 0
        relevant = [v for v in verification if v.get("purpose") in {"test", "build", "lint", "typecheck"}
                    and int(v.get("step", -1)) >= last_change
                    and not re.match(r"\s*(?:echo|printf)\b", str(v.get("command", "")), re.I)]
        relevant = list({str(v.get("command", "")): v for v in relevant}.values())
        if not relevant or not any(v.get("ok") is True for v in relevant) or any(v.get("ok") is not True for v in relevant):
            return "The latest implementation has no successful relevant verification. Run an appropriate build, test, or check and resolve failures before finishing."
    return None
