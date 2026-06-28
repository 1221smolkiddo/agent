from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import threading
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
    mtime_ns: int = 0
    sha256: str = ""
    module_name: str = ""
    symbols: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()


@dataclass(frozen=True)
class DependencyEdge:
    source: str
    target: str
    import_name: str
    kind: str


def build_repo_map(
    workspace: Path,
    *,
    max_files: int = 80,
    cache: RepoIndexCache | None = None,
) -> str:
    files = index_repo(workspace, cache=cache)
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


def rank_context(
    workspace: Path,
    task: str,
    *,
    max_results: int = 12,
    cache: RepoIndexCache | None = None,
) -> str:
    files = index_repo(workspace, cache=cache)
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


def build_symbol_index(
    workspace: Path,
    *,
    max_files: int = 40,
    max_symbols: int = 120,
    cache: RepoIndexCache | None = None,
) -> str:
    files = [item for item in index_repo(workspace, cache=cache) if item.kind in {"source", "test"}]
    lines = ["Symbol index:", f"- files scanned: {min(len(files), max_files)}", ""]
    symbol_count = 0
    for item in files[:max_files]:
        path = workspace / item.path
        symbols = list(item.symbols) if item.symbols else _symbols_for_file(path)
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


def build_dependency_graph(
    workspace: Path,
    *,
    max_files: int = 60,
    max_edges: int = 160,
    cache: RepoIndexCache | None = None,
) -> str:
    files = [item for item in index_repo(workspace, cache=cache) if item.kind in {"source", "test"}]
    module_map = _module_map(files)
    edges: list[DependencyEdge] = []
    for item in files[:max_files]:
        path = workspace / item.path
        edges.extend(_dependency_edges_for_file(path, item.path, module_map, item.imports))

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


class RepoIndexCache:
    """SQLite-backed repository index cache keyed by workspace and relative path."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def load_workspace(self, workspace: Path) -> dict[str, RepoFile]:
        workspace_key = _workspace_key(workspace)
        with self._connect() as conn:
            rows = conn.execute(
                """
                select path, kind, size, importance, mtime_ns, sha256, module_name,
                       symbols_json, imports_json
                from repo_index_files
                where workspace = ?
                """,
                (workspace_key,),
            ).fetchall()
        return {row["path"]: self._row_to_file(row) for row in rows}

    def save_workspace(self, workspace: Path, files: list[RepoFile]) -> None:
        workspace_key = _workspace_key(workspace)
        paths = {item.path for item in files}
        with self._connect() as conn:
            conn.executemany(
                """
                insert into repo_index_files (
                    workspace, path, kind, size, importance, mtime_ns, sha256,
                    module_name, symbols_json, imports_json, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
                on conflict(workspace, path) do update set
                    kind = excluded.kind,
                    size = excluded.size,
                    importance = excluded.importance,
                    mtime_ns = excluded.mtime_ns,
                    sha256 = excluded.sha256,
                    module_name = excluded.module_name,
                    symbols_json = excluded.symbols_json,
                    imports_json = excluded.imports_json,
                    updated_at = current_timestamp
                """,
                [
                    (
                        workspace_key,
                        item.path,
                        item.kind,
                        item.size,
                        item.importance,
                        item.mtime_ns,
                        item.sha256,
                        item.module_name,
                        json.dumps(list(item.symbols)),
                        json.dumps(list(item.imports)),
                    )
                    for item in files
                ],
            )
            if paths:
                placeholders = ",".join("?" for _ in paths)
                conn.execute(
                    f"""
                    delete from repo_index_files
                    where workspace = ? and path not in ({placeholders})
                    """,
                    (workspace_key, *sorted(paths)),
                )
            else:
                conn.execute("delete from repo_index_files where workspace = ?", (workspace_key,))
            conn.execute(
                """
                insert into repo_index_meta (workspace, refreshed_at, file_count)
                values (?, current_timestamp, ?)
                on conflict(workspace) do update set
                    refreshed_at = current_timestamp,
                    file_count = excluded.file_count
                """,
                (workspace_key, len(files)),
            )

    def refresh(self, workspace: Path) -> list[RepoFile]:
        return index_repo(workspace, cache=self)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists repo_index_files (
                    workspace text not null,
                    path text not null,
                    kind text not null,
                    size integer not null,
                    importance integer not null,
                    mtime_ns integer not null,
                    sha256 text not null,
                    module_name text not null default '',
                    symbols_json text not null default '[]',
                    imports_json text not null default '[]',
                    updated_at text not null default current_timestamp,
                    primary key (workspace, path)
                );

                create index if not exists idx_repo_index_files_workspace
                    on repo_index_files(workspace);

                create table if not exists repo_index_meta (
                    workspace text primary key,
                    refreshed_at text not null default current_timestamp,
                    file_count integer not null
                );
                """
            )

    @staticmethod
    def _row_to_file(row: sqlite3.Row) -> RepoFile:
        return RepoFile(
            path=row["path"],
            kind=row["kind"],
            size=int(row["size"]),
            importance=int(row["importance"]),
            mtime_ns=int(row["mtime_ns"]),
            sha256=row["sha256"],
            module_name=row["module_name"],
            symbols=tuple(json.loads(row["symbols_json"] or "[]")),
            imports=tuple(json.loads(row["imports_json"] or "[]")),
        )


class BackgroundIndexRefresh:
    """Small idle-refresh worker for frontends that keep Agent47 alive."""

    def __init__(
        self,
        workspace: Path,
        cache: RepoIndexCache,
        *,
        interval_seconds: float = 30.0,
    ) -> None:
        self.workspace = workspace
        self.cache = cache
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "BackgroundIndexRefresh":
        if self._thread and self._thread.is_alive():
            return self
        self._thread = threading.Thread(target=self._run, name="agent47-index-refresh", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.cache.refresh(self.workspace)
            self._stop_event.wait(self.interval_seconds)


def start_background_index_refresh(
    workspace: Path,
    cache: RepoIndexCache,
    *,
    interval_seconds: float = 30.0,
) -> BackgroundIndexRefresh:
    return BackgroundIndexRefresh(
        workspace,
        cache,
        interval_seconds=interval_seconds,
    ).start()


def index_repo(workspace: Path, cache: RepoIndexCache | None = None) -> list[RepoFile]:
    root = workspace.resolve()
    cached = cache.load_workspace(root) if cache else {}
    files: list[RepoFile] = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
        if _is_ignored_path(path, root) or not path.is_file():
            continue
        if path.suffix and path.suffix not in TEXT_EXTENSIONS:
            continue
        try:
            stat = path.stat()
            size = stat.st_size
            mtime_ns = stat.st_mtime_ns
            relative = path.relative_to(root).as_posix()
        except OSError:
            continue
        cached_item = cached.get(relative)
        if cached_item and cached_item.size == size and cached_item.mtime_ns == mtime_ns:
            files.append(cached_item)
            continue
        indexed = _index_file(path, relative, size=size, mtime_ns=mtime_ns)
        if indexed:
            files.append(indexed)
    files.sort(key=lambda item: (-item.importance, item.path))
    if cache:
        cache.save_workspace(root, files)
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


def _index_file(path: Path, relative: str, *, size: int, mtime_ns: int) -> RepoFile | None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return RepoFile(
        path=relative,
        kind=_classify_file(relative),
        size=size,
        importance=_importance(relative),
        mtime_ns=mtime_ns,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        module_name=_module_name_for_path(relative),
        symbols=tuple(_symbols_for_source(path, content)),
        imports=tuple(_imports_for_source(path, content, relative)),
    )


def _symbols_for_source(path: Path, source: str) -> list[str]:
    if path.suffix == ".py":
        return _python_symbols_from_source(source)
    if path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        return _javascript_like_symbols_from_lines(source.splitlines())
    return []


def _imports_for_source(path: Path, source: str, relative: str) -> list[str]:
    if path.suffix == ".py":
        return _python_imports(source, relative)
    if path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        return _javascript_imports_from_source(source)
    return []


def _dependency_edges_for_file(
    path: Path,
    relative_path: str,
    module_map: dict[str, str],
    imports: tuple[str, ...] = (),
) -> list[DependencyEdge]:
    if imports:
        return [_dependency_edge(relative_path, item, module_map) for item in imports]
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
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    return [_dependency_edge(relative_path, item, module_map) for item in _python_imports(source, relative_path)]


def _javascript_dependency_edges(
    path: Path,
    relative_path: str,
    module_map: dict[str, str],
) -> list[DependencyEdge]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return [_dependency_edge(relative_path, item, module_map) for item in _javascript_imports_from_source(source)]


def _javascript_imports_from_source(source: str) -> list[str]:

    patterns = [
        re.compile(r"\bimport\s+(?:[^'\"]+\s+from\s+)?['\"]([^'\"]+)['\"]"),
        re.compile(r"\bexport\s+[^'\"]+\s+from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"),
    ]
    imports: list[str] = []
    for pattern in patterns:
        imports.extend(match.group(1) for match in pattern.finditer(source))
    return sorted(set(imports))


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
        module = item.module_name or _module_name_for_path(item.path)
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
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    return _python_symbols_from_source(source)


def _python_symbols_from_source(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
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
    return _javascript_like_symbols_from_lines(lines)


def _javascript_like_symbols_from_lines(lines: list[str]) -> list[str]:
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


def _python_imports(source: str, relative_path: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    imports: list[str] = []
    current_module = _module_name_for_path(relative_path)
    current_package = current_module.rsplit(".", 1)[0] if "." in current_module else current_module
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            import_name = _resolve_python_from_import(current_package, node)
            if import_name:
                imports.append(import_name)
    return sorted(set(imports))


def _workspace_key(workspace: Path) -> str:
    return str(workspace.resolve())


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
