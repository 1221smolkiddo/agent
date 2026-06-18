from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


IGNORED_NAMES = {
    ".code-agent",
    ".env",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
}

TEXT_EXTENSIONS = {
    ".css",
    ".go",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".py",
    ".rs",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

IMPORTANT_FILENAMES = {
    "README.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "uv.lock",
    "requirements.txt",
    "pytest.ini",
}


@dataclass(frozen=True)
class RepoFile:
    path: str
    kind: str
    size: int
    importance: int


def build_repo_map(workspace: Path, *, max_files: int = 80) -> str:
    files = index_repo(workspace)
    important = [item for item in files if item.importance >= 6]
    source_count = sum(1 for item in files if item.kind == "source")
    test_count = sum(1 for item in files if item.kind == "test")
    doc_count = sum(1 for item in files if item.kind == "doc")

    lines = [
        "Repository map:",
        f"- files indexed: {len(files)}",
        f"- source files: {source_count}",
        f"- test files: {test_count}",
        f"- docs: {doc_count}",
        "",
        "Important files:",
    ]
    if important:
        lines.extend(f"- {item.path} ({item.kind})" for item in important[:max_files])
    else:
        lines.append("- <none detected>")

    lines.extend(["", "Top-level layout:"])
    lines.extend(_top_level_layout(files))

    lines.extend(["", "Representative files:"])
    for item in files[:max_files]:
        lines.append(f"- {item.path} ({item.kind}, {item.size} bytes)")
    if len(files) > max_files:
        lines.append(f"- <truncated {len(files) - max_files} files>")
    return "\n".join(lines)


def rank_context(workspace: Path, task: str, *, max_results: int = 12) -> str:
    files = index_repo(workspace)
    terms = _task_terms(task)
    scored = [(score_file(item, terms), item) for item in files]
    scored = [(score, item) for score, item in scored if score > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1].path))

    lines = [
        "Ranked context:",
        f"- task terms: {', '.join(terms) if terms else '<none>'}",
        "",
    ]
    if not scored:
        lines.append("<no relevant files found>")
        return "\n".join(lines)

    for index, (score, item) in enumerate(scored[:max_results], start=1):
        lines.append(f"{index}. {item.path} score={score} kind={item.kind}")
    if len(scored) > max_results:
        lines.append(f"<truncated {len(scored) - max_results} lower-ranked files>")
    return "\n".join(lines)


def index_repo(workspace: Path) -> list[RepoFile]:
    root = workspace.resolve()
    files: list[RepoFile] = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
        if _is_ignored_path(path, root) or not path.is_file():
            continue
        if path.suffix and path.suffix not in TEXT_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
            relative = path.relative_to(root).as_posix()
        except OSError:
            continue
        files.append(
            RepoFile(
                path=relative,
                kind=_classify_file(relative),
                size=size,
                importance=_importance(relative),
            )
        )
    files.sort(key=lambda item: (-item.importance, item.path))
    return files


def score_file(file: RepoFile, terms: list[str]) -> int:
    path_lower = file.path.lower()
    score = file.importance
    if file.kind == "test":
        score += 1
    for term in terms:
        if term in path_lower:
            score += 8
        elif any(part.startswith(term) for part in _path_parts(path_lower)):
            score += 4
    if "test" in terms and file.kind == "test":
        score += 8
    if any(term in {"doc", "docs", "readme"} for term in terms) and file.kind == "doc":
        score += 8
    if any(term in {"cli", "command", "terminal"} for term in terms) and "cli" in path_lower:
        score += 8
    return score


def _task_terms(task: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z0-9_./-]+", task.lower())
    stop_words = {
        "a",
        "add",
        "and",
        "build",
        "change",
        "fix",
        "for",
        "in",
        "it",
        "make",
        "of",
        "on",
        "the",
        "this",
        "to",
        "update",
        "with",
    }
    terms: list[str] = []
    for raw in raw_terms:
        for part in re.split(r"[/_.-]+", raw):
            if len(part) < 3 or part in stop_words or part in terms:
                continue
            terms.append(part)
    return terms[:20]


def _top_level_layout(files: list[RepoFile]) -> list[str]:
    counts: dict[str, int] = {}
    for item in files:
        top = item.path.split("/", 1)[0]
        counts[top] = counts.get(top, 0) + 1
    if not counts:
        return ["- <empty>"]
    return [f"- {name}: {count} files" for name, count in sorted(counts.items())]


def _classify_file(path: str) -> str:
    lower = path.lower()
    name = Path(path).name
    if name in IMPORTANT_FILENAMES:
        return "config"
    if lower.startswith("tests/") or "/test_" in lower or lower.endswith("_test.py"):
        return "test"
    if lower.startswith("docs/") or lower.endswith(".md") or lower.endswith(".txt"):
        return "doc"
    if lower.startswith("src/") or lower.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go")):
        return "source"
    return "other"


def _importance(path: str) -> int:
    name = Path(path).name
    lower = path.lower()
    score = 0
    if name in IMPORTANT_FILENAMES:
        score += 10
    if lower.startswith("src/"):
        score += 5
    if lower.startswith("tests/"):
        score += 4
    if lower.startswith("docs/"):
        score += 3
    if name in {"agent.py", "tools.py", "schema.py", "cli.py", "interactive.py"}:
        score += 4
    return score


def _path_parts(path: str) -> list[str]:
    return [part for part in re.split(r"[/_.-]+", path.lower()) if part]


def _is_ignored_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in IGNORED_NAMES for part in relative.parts)
