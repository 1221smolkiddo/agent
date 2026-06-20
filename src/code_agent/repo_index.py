from __future__ import annotations

import ast
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


@dataclass(frozen=True)
class DependencyEdge:
    source: str
    target: str
    import_name: str
    kind: str


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


def build_symbol_index(workspace: Path, *, max_files: int = 40, max_symbols: int = 120) -> str:
    files = [item for item in index_repo(workspace) if item.kind in {"source", "test"}]
    lines = ["Symbol index:", f"- files scanned: {min(len(files), max_files)}", ""]
    symbol_count = 0
    for item in files[:max_files]:
        path = workspace / item.path
        symbols = _symbols_for_file(path)
        if not symbols:
            continue
        lines.append(f"{item.path}:")
        for symbol in symbols:
            lines.append(f"- {symbol}")
            symbol_count += 1
            if symbol_count >= max_symbols:
                lines.append(f"<truncated after {max_symbols} symbols>")
                return "\n".join(lines)
        lines.append("")
    if symbol_count == 0:
        lines.append("<no symbols found>")
    return "\n".join(lines).rstrip()


def build_dependency_graph(workspace: Path, *, max_files: int = 60, max_edges: int = 160) -> str:
    files = [item for item in index_repo(workspace) if item.kind in {"source", "test"}]
    module_map = _module_map(files)
    edges: list[DependencyEdge] = []
    for item in files[:max_files]:
        path = workspace / item.path
        edges.extend(_dependency_edges_for_file(path, item.path, module_map))

    internal = [edge for edge in edges if edge.kind == "internal"]
    external = [edge for edge in edges if edge.kind == "external"]
    lines = [
        "Dependency graph:",
        f"- files scanned: {min(len(files), max_files)}",
        f"- internal edges: {len(internal)}",
        f"- external imports: {len(external)}",
        "",
        "Internal dependencies:",
    ]
    if internal:
        for edge in internal[:max_edges]:
            lines.append(f"- {edge.source} -> {edge.target} ({edge.import_name})")
    else:
        lines.append("- <none detected>")

    remaining_edges = max_edges - min(len(internal), max_edges)
    lines.extend(["", "External imports:"])
    if external and remaining_edges > 0:
        grouped = _group_external_edges(external)
        for source, imports in grouped[:remaining_edges]:
            lines.append(f"- {source}: {', '.join(imports)}")
    elif external:
        lines.append("- <truncated before external imports>")
    else:
        lines.append("- <none detected>")

    truncated = len(internal) + len(external) - max_edges
    if truncated > 0:
        lines.append(f"<truncated {truncated} dependency entries>")
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


def _symbols_for_file(path: Path) -> list[str]:
    if path.suffix == ".py":
        return _python_symbols(path)
    if path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        return _javascript_like_symbols(path)
    return []


def _dependency_edges_for_file(
    path: Path,
    relative_path: str,
    module_map: dict[str, str],
) -> list[DependencyEdge]:
    if path.suffix == ".py":
        return _python_dependency_edges(path, relative_path, module_map)
    if path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        return _javascript_dependency_edges(path, relative_path, module_map)
    return []


def _python_dependency_edges(
    path: Path,
    relative_path: str,
    module_map: dict[str, str],
) -> list[DependencyEdge]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    edges: list[DependencyEdge] = []
    current_module = _module_name_for_path(relative_path)
    current_package = current_module.rsplit(".", 1)[0] if "." in current_module else current_module
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_name = alias.name
                edges.append(_dependency_edge(relative_path, import_name, module_map))
        elif isinstance(node, ast.ImportFrom):
            import_name = _resolve_python_from_import(current_package, node)
            if import_name:
                edges.append(_dependency_edge(relative_path, import_name, module_map))
    return edges


def _javascript_dependency_edges(
    path: Path,
    relative_path: str,
    module_map: dict[str, str],
) -> list[DependencyEdge]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    patterns = [
        re.compile(r"\bimport\s+(?:[^'\"]+\s+from\s+)?['\"]([^'\"]+)['\"]"),
        re.compile(r"\bexport\s+[^'\"]+\s+from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"),
    ]
    imports: list[str] = []
    for pattern in patterns:
        imports.extend(match.group(1) for match in pattern.finditer(source))
    return [_dependency_edge(relative_path, item, module_map) for item in sorted(set(imports))]


def _dependency_edge(source: str, import_name: str, module_map: dict[str, str]) -> DependencyEdge:
    target = _resolve_import_target(source, import_name, module_map)
    if target:
        return DependencyEdge(source=source, target=target, import_name=import_name, kind="internal")
    return DependencyEdge(
        source=source,
        target=_external_import_name(import_name),
        import_name=import_name,
        kind="external",
    )


def _resolve_import_target(source: str, import_name: str, module_map: dict[str, str]) -> str | None:
    if import_name.startswith("."):
        return _resolve_relative_javascript_import(source, import_name, module_map)
    parts = import_name.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in module_map:
            return module_map[candidate]
    return None


def _resolve_relative_javascript_import(
    source: str,
    import_name: str,
    module_map: dict[str, str],
) -> str | None:
    base = Path(source).parent
    normalized = (base / import_name).as_posix()
    candidates = [
        normalized,
        f"{normalized}.js",
        f"{normalized}.jsx",
        f"{normalized}.ts",
        f"{normalized}.tsx",
        f"{normalized}/index.js",
        f"{normalized}/index.jsx",
        f"{normalized}/index.ts",
        f"{normalized}/index.tsx",
    ]
    path_lookup = {path: path for path in module_map.values()}
    for candidate in candidates:
        if candidate in path_lookup:
            return path_lookup[candidate]
    return None


def _resolve_python_from_import(current_package: str, node: ast.ImportFrom) -> str:
    module = node.module or ""
    if node.level <= 0:
        return module
    package_parts = current_package.split(".") if current_package else []
    keep = max(len(package_parts) - node.level + 1, 0)
    prefix = ".".join(package_parts[:keep])
    if prefix and module:
        return f"{prefix}.{module}"
    return prefix or module


def _module_map(files: list[RepoFile]) -> dict[str, str]:
    modules: dict[str, str] = {}
    for item in files:
        module = _module_name_for_path(item.path)
        if module:
            modules[module] = item.path
    return modules


def _module_name_for_path(path: str) -> str:
    item = Path(path)
    if item.suffix == ".py":
        without_suffix = item.with_suffix("")
        parts = list(without_suffix.parts)
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)
    if item.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        return item.with_suffix("").as_posix()
    return ""


def _external_import_name(import_name: str) -> str:
    if import_name.startswith("@"):
        parts = import_name.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else import_name
    return re.split(r"[./]", import_name, maxsplit=1)[0]


def _group_external_edges(edges: list[DependencyEdge]) -> list[tuple[str, list[str]]]:
    grouped: dict[str, set[str]] = {}
    for edge in edges:
        grouped.setdefault(edge.source, set()).add(edge.target)
    return [(source, sorted(imports)) for source, imports in sorted(grouped.items())]


def _python_symbols(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    symbols: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(f"L{node.lineno} class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            symbols.append(f"L{node.lineno} {prefix} {node.name}")
    return sorted(symbols, key=_symbol_line_number)


def _javascript_like_symbols(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    patterns = [
        (re.compile(r"^\s*export\s+default\s+function\s+([A-Za-z_$][\w$]*)"), "function"),
        (re.compile(r"^\s*export\s+function\s+([A-Za-z_$][\w$]*)"), "function"),
        (re.compile(r"^\s*function\s+([A-Za-z_$][\w$]*)"), "function"),
        (re.compile(r"^\s*export\s+class\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"^\s*class\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"^\s*export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="), "binding"),
        (re.compile(r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="), "binding"),
    ]
    symbols: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        for pattern, kind in patterns:
            match = pattern.search(line)
            if match:
                symbols.append(f"L{line_number} {kind} {match.group(1)}")
                break
    return symbols


def _symbol_line_number(symbol: str) -> int:
    match = re.match(r"L(\d+)", symbol)
    return int(match.group(1)) if match else 0


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
