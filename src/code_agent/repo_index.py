from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


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
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".java",
    ".kt",
    ".kts",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".swift",
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
    "tsconfig.json",
    "vite.config.ts",
    "next.config.js",
    "next.config.mjs",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "CMakeLists.txt",
    "Makefile",
}

LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
}

GRAPH_RELATION_WEIGHTS = {
    "call": 7,
    "test": 7,
    "import": 6,
    "reference": 4,
    "config": 2,
}


@dataclass(frozen=True)
class SymbolRecord:
    path: str
    name: str
    kind: str
    line: int
    end_line: int = 0
    container: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.container}.{self.name}" if self.container else self.name


@dataclass(frozen=True)
class ReferenceRecord:
    name: str
    line: int
    container: str = ""


@dataclass(frozen=True)
class ProjectGraphEdge:
    source_path: str
    target_path: str
    relation: str
    detail: str = ""
    source_symbol: str = ""
    target_symbol: str = ""
    line: int = 0


@dataclass(frozen=True)
class IndexRefreshStats:
    scanned_files: int
    parsed_files: int
    reused_files: int
    deleted_files: int
    graph_edges: int


@dataclass(frozen=True)
class ProjectGraph:
    files: tuple[RepoFile, ...]
    symbols: tuple[SymbolRecord, ...]
    edges: tuple[ProjectGraphEdge, ...]
    stats: IndexRefreshStats | None = None


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
    language: str = ""
    line_count: int = 0
    token_estimate: int = 0
    symbol_records: tuple[SymbolRecord, ...] = ()
    calls: tuple[ReferenceRecord, ...] = ()
    references: tuple[ReferenceRecord, ...] = ()


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
    graph = build_project_graph(workspace, cache=cache)
    files = list(graph.files)
    important = [item for item in files if item.importance >= 6]
    source_count = sum(1 for item in files if item.kind == "source")
    test_count = sum(1 for item in files if item.kind == "test")
    doc_count = sum(1 for item in files if item.kind == "doc")
    languages: dict[str, int] = {}
    for item in files:
        if item.language:
            languages[item.language] = languages.get(item.language, 0) + 1

    lines = [
        "Repository map:",
        f"- files indexed: {len(files)}",
        f"- source files: {source_count}",
        f"- test files: {test_count}",
        f"- docs: {doc_count}",
        f"- graph edges: {len(graph.edges)}",
        "- languages: "
        + (", ".join(f"{name}={count}" for name, count in sorted(languages.items())) or "<none>"),
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
    max_tokens: int = 8000,
    cache: RepoIndexCache | None = None,
) -> str:
    graph = build_project_graph(workspace, cache=cache)
    files = list(graph.files)
    terms = _task_terms(task)
    reasons: dict[str, set[str]] = {item.path: set() for item in files}
    scores: dict[str, int] = {}
    for item in files:
        score = score_file(item, terms)
        symbol_hits = sorted(
            {
                symbol.name
                for symbol in item.symbol_records
                if any(term in symbol.name.lower() for term in terms)
            }
        )
        reference_hits = sorted(
            {
                reference.name
                for reference in (*item.calls, *item.references)
                if any(term in reference.name.lower() for term in terms)
            }
        )
        if symbol_hits:
            score += min(18, 6 * len(symbol_hits))
            reasons[item.path].add("symbols=" + ",".join(symbol_hits[:3]))
        if reference_hits:
            score += min(10, 2 * len(reference_hits))
            reasons[item.path].add("references=" + ",".join(reference_hits[:3]))
        if score > item.importance:
            reasons[item.path].add("task match")
        scores[item.path] = score

    seeded = {path for path, detail in reasons.items() if detail}
    propagated: set[tuple[str, str, str]] = set()
    propagation_counts: dict[tuple[str, str], int] = {}
    for edge in graph.edges:
        weight = GRAPH_RELATION_WEIGHTS.get(edge.relation, 1)
        forward = (edge.target_path, edge.relation, edge.source_path)
        forward_count = (edge.target_path, edge.relation)
        if (
            edge.source_path in seeded
            and edge.target_path in scores
            and forward not in propagated
            and propagation_counts.get(forward_count, 0) < 3
        ):
            scores[edge.target_path] += weight
            reasons[edge.target_path].add(f"{edge.relation} from {edge.source_path}")
            propagated.add(forward)
            propagation_counts[forward_count] = propagation_counts.get(forward_count, 0) + 1
        reverse = (edge.source_path, edge.relation, edge.target_path)
        reverse_count = (edge.source_path, edge.relation)
        if (
            edge.target_path in seeded
            and edge.source_path in scores
            and reverse not in propagated
            and propagation_counts.get(reverse_count, 0) < 3
        ):
            scores[edge.source_path] += max(1, weight - 1)
            reasons[edge.source_path].add(f"{edge.relation} to {edge.target_path}")
            propagated.add(reverse)
            propagation_counts[reverse_count] = propagation_counts.get(reverse_count, 0) + 1

    scored = [(scores[item.path], item) for item in files if scores[item.path] > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1].path))

    lines = [
        "Ranked context:",
        f"- task terms: {', '.join(terms) if terms else '<none>'}",
        f"- token budget: {max_tokens}",
        f"- graph edges considered: {len(graph.edges)}",
        "",
    ]
    if not scored:
        lines.append("<no relevant files found>")
        return "\n".join(lines)

    selected: list[tuple[int, RepoFile]] = []
    allocations: dict[str, int] = {}
    consumed_tokens = 0
    allocation_cap = max(128, max_tokens // min(max_results, 8))
    for score, item in scored:
        if len(selected) >= max_results:
            break
        estimate = max(item.token_estimate, 1)
        allocation = min(estimate, allocation_cap)
        if selected and consumed_tokens + allocation > max_tokens:
            continue
        selected.append((score, item))
        allocations[item.path] = allocation
        consumed_tokens += allocation

    for index, (score, item) in enumerate(selected, start=1):
        ordered_reasons = sorted(
            reasons[item.path],
            key=lambda value: (
                0
                if value == "task match"
                else 1
                if value.startswith(("symbols=", "references="))
                else 2
                if value.startswith("test")
                else 3,
                value,
            ),
        )
        reason = "; ".join(ordered_reasons[:5]) or "repository importance"
        lines.append(
            f"{index}. {item.path} score={score} kind={item.kind} "
            f"tokens~{item.token_estimate} allocation={allocations[item.path]} reason={reason}"
        )
    lines.append("")
    lines.append(f"Selected estimated tokens: {consumed_tokens}/{max_tokens}")
    omitted = len(scored) - len(selected)
    if omitted > 0:
        lines.append(f"<omitted {omitted} lower-ranked or over-budget files>")
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
    graph = build_project_graph(workspace, cache=cache)
    files = [item for item in graph.files if item.kind in {"source", "test"}][:max_files]
    included_paths = {item.path for item in files}
    edges = [edge for edge in graph.edges if edge.source_path in included_paths]
    internal = [edge for edge in edges if edge.relation == "import" and edge.target_path]
    external = [edge for edge in edges if edge.relation == "external_import"]
    calls = [edge for edge in edges if edge.relation == "call"]
    tests = [edge for edge in edges if edge.relation == "test"]
    configs = [edge for edge in edges if edge.relation == "config"]
    references = [edge for edge in edges if edge.relation == "reference"]
    lines = [
        "Dependency graph:",
        f"- files scanned: {len(files)}",
        f"- internal edges: {len(internal)}",
        f"- external imports: {len(external)}",
        f"- call edges: {len(calls)}",
        f"- reference edges: {len(references)}",
        f"- test mappings: {len(tests)}",
        f"- configuration relationships: {len(configs)}",
        "",
        "Internal dependencies:",
    ]
    if internal:
        for edge in internal[:max_edges]:
            lines.append(f"- {edge.source_path} -> {edge.target_path} ({edge.detail})")
    else:
        lines.append("- <none detected>")

    remaining_edges = max_edges - min(len(internal), max_edges)
    lines.extend(["", "External imports:"])
    if external and remaining_edges > 0:
        grouped = _group_project_external_edges(external)
        for source, imports in grouped[:remaining_edges]:
            lines.append(f"- {source}: {', '.join(imports)}")
    elif external:
        lines.append("- <truncated before external imports>")
    else:
        lines.append("- <none detected>")

    relation_groups = (
        ("Call graph", calls),
        ("Reference graph", references),
        ("Test-to-source mapping", tests),
        ("Configuration relationships", configs),
    )
    emitted = min(len(internal), max_edges) + min(len(external), max(remaining_edges, 0))
    for title, relation_edges in relation_groups:
        lines.extend(["", f"{title}:"])
        available = max(max_edges - emitted, 0)
        if not relation_edges:
            lines.append("- <none detected>")
            continue
        if available <= 0:
            lines.append("- <truncated>")
            continue
        for edge in relation_edges[:available]:
            source = edge.source_path
            if edge.source_symbol:
                source += f"::{edge.source_symbol}"
            target = edge.target_path or edge.target_symbol
            if edge.target_symbol and edge.target_path:
                target += f"::{edge.target_symbol}"
            lines.append(f"- {source} -> {target} ({edge.detail or edge.relation})")
        emitted += min(len(relation_edges), available)

    truncated = (
        len(internal)
        + len(external)
        + len(calls)
        + len(references)
        + len(tests)
        + len(configs)
        - max_edges
    )
    if truncated > 0:
        lines.append(f"<truncated {truncated} dependency entries>")
    return "\n".join(lines)


class RepoIndexCache:
    """SQLite-backed repository index cache keyed by workspace and relative path."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_schema()

    def load_workspace(self, workspace: Path) -> dict[str, RepoFile]:
        workspace_key = _workspace_key(workspace)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select path, kind, size, importance, mtime_ns, sha256, module_name,
                       symbols_json, imports_json, language, line_count, token_estimate,
                       symbol_records_json, calls_json, references_json
                from repo_index_files
                where workspace = ?
                """,
                (workspace_key,),
            ).fetchall()
        return {row["path"]: self._row_to_file(row) for row in rows}

    def save_workspace(
        self,
        workspace: Path,
        files: list[RepoFile],
        *,
        changed_paths: set[str] | None = None,
        deleted_paths: set[str] | None = None,
        edges: list[ProjectGraphEdge] | None = None,
        stats: IndexRefreshStats | None = None,
    ) -> None:
        workspace_key = _workspace_key(workspace)
        paths = {item.path for item in files}
        changed = changed_paths if changed_paths is not None else paths
        deleted = deleted_paths if deleted_paths is not None else set()
        changed_files = [item for item in files if item.path in changed]
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                insert into repo_index_files (
                    workspace, path, kind, size, importance, mtime_ns, sha256,
                    module_name, symbols_json, imports_json, language, line_count,
                    token_estimate, symbol_records_json, calls_json, references_json, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
                on conflict(workspace, path) do update set
                    kind = excluded.kind,
                    size = excluded.size,
                    importance = excluded.importance,
                    mtime_ns = excluded.mtime_ns,
                    sha256 = excluded.sha256,
                    module_name = excluded.module_name,
                    symbols_json = excluded.symbols_json,
                    imports_json = excluded.imports_json,
                    language = excluded.language,
                    line_count = excluded.line_count,
                    token_estimate = excluded.token_estimate,
                    symbol_records_json = excluded.symbol_records_json,
                    calls_json = excluded.calls_json,
                    references_json = excluded.references_json,
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
                        item.language,
                        item.line_count,
                        item.token_estimate,
                        json.dumps([_record_payload(record) for record in item.symbol_records]),
                        json.dumps([_record_payload(record) for record in item.calls]),
                        json.dumps([_record_payload(record) for record in item.references]),
                    )
                    for item in changed_files
                ],
            )
            removed = deleted | ({item for item in self._stored_paths(conn, workspace_key) if item not in paths})
            if removed:
                placeholders = ",".join("?" for _ in removed)
                conn.execute(
                    f"""
                    delete from repo_index_files where workspace = ? and path in ({placeholders})
                    """,
                    (workspace_key, *sorted(removed)),
                )
            invalidated = changed | removed
            if invalidated:
                placeholders = ",".join("?" for _ in invalidated)
                conn.execute(
                    f"delete from repo_index_symbols where workspace = ? and path in ({placeholders})",
                    (workspace_key, *sorted(invalidated)),
                )
            conn.executemany(
                """
                insert into repo_index_symbols (
                    workspace, path, name, kind, line, end_line, container
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        workspace_key,
                        record.path,
                        record.name,
                        record.kind,
                        record.line,
                        record.end_line,
                        record.container,
                    )
                    for item in changed_files
                    for record in item.symbol_records
                ],
            )
            if edges is not None:
                self._sync_edges(conn, workspace_key, edges)
            conn.execute(
                """
                insert into repo_index_meta (
                    workspace, refreshed_at, file_count, parsed_files, reused_files,
                    deleted_files, graph_edges
                ) values (?, current_timestamp, ?, ?, ?, ?, ?)
                on conflict(workspace) do update set
                    refreshed_at = current_timestamp,
                    file_count = excluded.file_count,
                    parsed_files = excluded.parsed_files,
                    reused_files = excluded.reused_files,
                    deleted_files = excluded.deleted_files,
                    graph_edges = excluded.graph_edges
                """,
                (
                    workspace_key,
                    len(files),
                    stats.parsed_files if stats else len(changed_files),
                    stats.reused_files if stats else len(files) - len(changed_files),
                    stats.deleted_files if stats else len(removed),
                    stats.graph_edges if stats else len(edges or ()),
                ),
            )

    def refresh(self, workspace: Path) -> list[RepoFile]:
        return index_repo(workspace, cache=self)

    def load_edges(self, workspace: Path) -> list[ProjectGraphEdge]:
        workspace_key = _workspace_key(workspace)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select source_path, target_path, relation, detail, source_symbol,
                       target_symbol, line
                from repo_index_edges
                where workspace = ?
                order by relation, source_path, target_path, source_symbol, target_symbol
                """,
                (workspace_key,),
            ).fetchall()
        return [
            ProjectGraphEdge(
                source_path=row["source_path"],
                target_path=row["target_path"],
                relation=row["relation"],
                detail=row["detail"],
                source_symbol=row["source_symbol"],
                target_symbol=row["target_symbol"],
                line=int(row["line"]),
            )
            for row in rows
        ]

    def load_symbols(self, workspace: Path) -> list[SymbolRecord]:
        workspace_key = _workspace_key(workspace)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select path, name, kind, line, end_line, container
                from repo_index_symbols
                where workspace = ?
                order by path, line, name
                """,
                (workspace_key,),
            ).fetchall()
        return [
            SymbolRecord(
                path=row["path"],
                name=row["name"],
                kind=row["kind"],
                line=int(row["line"]),
                end_line=int(row["end_line"]),
                container=row["container"],
            )
            for row in rows
        ]

    def load_stats(self, workspace: Path) -> IndexRefreshStats | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                select file_count, parsed_files, reused_files, deleted_files, graph_edges
                from repo_index_meta where workspace = ?
                """,
                (_workspace_key(workspace),),
            ).fetchone()
        if row is None:
            return None
        return IndexRefreshStats(
            scanned_files=int(row["file_count"]),
            parsed_files=int(row["parsed_files"]),
            reused_files=int(row["reused_files"]),
            deleted_files=int(row["deleted_files"]),
            graph_edges=int(row["graph_edges"]),
        )

    def invalidate(self, workspace: Path, paths: list[str]) -> None:
        normalized = {Path(path).as_posix() for path in paths if path}
        if not normalized:
            return
        placeholders = ",".join("?" for _ in normalized)
        workspace_key = _workspace_key(workspace)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"delete from repo_index_files where workspace = ? and path in ({placeholders})",
                (workspace_key, *sorted(normalized)),
            )
            conn.execute(
                f"delete from repo_index_symbols where workspace = ? and path in ({placeholders})",
                (workspace_key, *sorted(normalized)),
            )

    @staticmethod
    def _stored_paths(conn: sqlite3.Connection, workspace_key: str) -> set[str]:
        rows = conn.execute(
            "select path from repo_index_files where workspace = ?",
            (workspace_key,),
        ).fetchall()
        return {str(row["path"]) for row in rows}

    @staticmethod
    def _sync_edges(
        conn: sqlite3.Connection,
        workspace_key: str,
        edges: list[ProjectGraphEdge],
    ) -> None:
        columns = (
            "source_path",
            "target_path",
            "relation",
            "detail",
            "source_symbol",
            "target_symbol",
            "line",
        )
        existing = {
            tuple(row[column] for column in columns)
            for row in conn.execute(
                """
                select source_path, target_path, relation, detail, source_symbol,
                       target_symbol, line
                from repo_index_edges where workspace = ?
                """,
                (workspace_key,),
            ).fetchall()
        }
        desired = {
            (
                edge.source_path,
                edge.target_path,
                edge.relation,
                edge.detail,
                edge.source_symbol,
                edge.target_symbol,
                edge.line,
            )
            for edge in edges
        }
        conn.executemany(
            """
            delete from repo_index_edges
            where workspace = ? and source_path = ? and target_path = ? and relation = ?
              and detail = ? and source_symbol = ? and target_symbol = ? and line = ?
            """,
            [(workspace_key, *edge) for edge in sorted(existing - desired)],
        )
        conn.executemany(
            """
            insert into repo_index_edges (
                workspace, source_path, target_path, relation, detail,
                source_symbol, target_symbol, line
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(workspace_key, *edge) for edge in sorted(desired - existing)],
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 10000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
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
                    language text not null default '',
                    line_count integer not null default 0,
                    token_estimate integer not null default 0,
                    symbol_records_json text not null default '[]',
                    calls_json text not null default '[]',
                    references_json text not null default '[]',
                    updated_at text not null default current_timestamp,
                    primary key (workspace, path)
                );

                create index if not exists idx_repo_index_files_workspace
                    on repo_index_files(workspace);

                create table if not exists repo_index_meta (
                    workspace text primary key,
                    refreshed_at text not null default current_timestamp,
                    file_count integer not null,
                    parsed_files integer not null default 0,
                    reused_files integer not null default 0,
                    deleted_files integer not null default 0,
                    graph_edges integer not null default 0
                );

                create table if not exists repo_index_symbols (
                    workspace text not null,
                    path text not null,
                    name text not null,
                    kind text not null,
                    line integer not null,
                    end_line integer not null default 0,
                    container text not null default ''
                );

                create index if not exists idx_repo_index_symbols_lookup
                    on repo_index_symbols(workspace, name);

                create table if not exists repo_index_edges (
                    workspace text not null,
                    source_path text not null,
                    target_path text not null default '',
                    relation text not null,
                    detail text not null default '',
                    source_symbol text not null default '',
                    target_symbol text not null default '',
                    line integer not null default 0
                );

                create index if not exists idx_repo_index_edges_source
                    on repo_index_edges(workspace, source_path, relation);

                create index if not exists idx_repo_index_edges_target
                    on repo_index_edges(workspace, target_path, relation);
                """
            )
            _ensure_sqlite_columns(
                conn,
                "repo_index_files",
                {
                    "language": "text not null default ''",
                    "line_count": "integer not null default 0",
                    "token_estimate": "integer not null default 0",
                    "symbol_records_json": "text not null default '[]'",
                    "calls_json": "text not null default '[]'",
                    "references_json": "text not null default '[]'",
                },
            )
            _ensure_sqlite_columns(
                conn,
                "repo_index_meta",
                {
                    "parsed_files": "integer not null default 0",
                    "reused_files": "integer not null default 0",
                    "deleted_files": "integer not null default 0",
                    "graph_edges": "integer not null default 0",
                },
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
            language=row["language"],
            line_count=int(row["line_count"]),
            token_estimate=int(row["token_estimate"]),
            symbol_records=tuple(
                SymbolRecord(**item) for item in json.loads(row["symbol_records_json"] or "[]")
            ),
            calls=tuple(ReferenceRecord(**item) for item in json.loads(row["calls_json"] or "[]")),
            references=tuple(
                ReferenceRecord(**item) for item in json.loads(row["references_json"] or "[]")
            ),
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
        self._refresh_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "BackgroundIndexRefresh":
        if self._thread and self._thread.is_alive():
            return self
        self._thread = threading.Thread(target=self._run, name="agent47-index-refresh", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._refresh_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def request_refresh(self, paths: list[str] | None = None) -> None:
        if paths:
            self.cache.invalidate(self.workspace, paths)
        self._refresh_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.cache.refresh(self.workspace)
            self._refresh_event.wait(self.interval_seconds)
            self._refresh_event.clear()


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
    changed: list[tuple[Path, str, int, int]] = []
    seen_paths: set[str] = set()
    reused_files = 0
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
        seen_paths.add(relative)
        cached_item = cached.get(relative)
        language = LANGUAGE_EXTENSIONS.get(path.suffix.lower(), "")
        cache_is_current = not language or cached_item is not None and cached_item.language == language
        if (
            cached_item
            and cached_item.size == size
            and cached_item.mtime_ns == mtime_ns
            and cache_is_current
        ):
            files.append(cached_item)
            reused_files += 1
            continue
        changed.append((path, relative, size, mtime_ns))

    workers = min(max(os.cpu_count() or 1, 1), 8, max(len(changed), 1))
    if changed:
        if workers == 1:
            indexed_files = [_index_candidate(candidate) for candidate in changed]
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent47-index") as pool:
                indexed_files = list(pool.map(_index_candidate, changed))
        files.extend(item for item in indexed_files if item is not None)

    files.sort(key=lambda item: (-item.importance, item.path))
    deleted_paths = set(cached) - seen_paths
    if cache and not changed and not deleted_paths:
        edges = cache.load_edges(root)
        edges_changed = False
    else:
        edges = _build_project_graph_edges(files)
        edges_changed = True
    stats = IndexRefreshStats(
        scanned_files=len(files),
        parsed_files=len(changed),
        reused_files=reused_files,
        deleted_files=len(deleted_paths),
        graph_edges=len(edges),
    )
    if cache:
        cache.save_workspace(
            root,
            files,
            changed_paths={relative for _, relative, _, _ in changed},
            deleted_paths=deleted_paths,
            edges=edges if edges_changed else None,
            stats=stats,
        )
    return files


def build_project_graph(
    workspace: Path,
    *,
    cache: RepoIndexCache | None = None,
) -> ProjectGraph:
    files = index_repo(workspace, cache=cache)
    edges = cache.load_edges(workspace) if cache else _build_project_graph_edges(files)
    stats = cache.load_stats(workspace) if cache else IndexRefreshStats(
        scanned_files=len(files),
        parsed_files=len(files),
        reused_files=0,
        deleted_files=0,
        graph_edges=len(edges),
    )
    symbols = tuple(cache.load_symbols(workspace)) if cache else tuple(
        symbol for item in files for symbol in item.symbol_records
    )
    return ProjectGraph(
        files=tuple(files),
        symbols=symbols,
        edges=tuple(edges),
        stats=stats,
    )


def _index_candidate(candidate: tuple[Path, str, int, int]) -> RepoFile | None:
    path, relative, size, mtime_ns = candidate
    return _index_file(path, relative, size=size, mtime_ns=mtime_ns)


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
    language = LANGUAGE_EXTENSIONS.get(path.suffix.lower(), "")
    symbol_records, calls, references = _semantic_facts(path, relative, content, language)
    symbols = tuple(_format_symbol(record) for record in symbol_records)
    return RepoFile(
        path=relative,
        kind=_classify_file(relative),
        size=size,
        importance=_importance(relative),
        mtime_ns=mtime_ns,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        module_name=_module_name_for_path(relative),
        symbols=symbols or tuple(_symbols_for_source(path, content)),
        imports=tuple(_imports_for_source(path, content, relative)),
        language=language,
        line_count=content.count("\n") + (1 if content else 0),
        token_estimate=max(1, (len(content) + 3) // 4),
        symbol_records=tuple(symbol_records),
        calls=tuple(calls),
        references=tuple(references),
    )


def _semantic_facts(
    path: Path,
    relative: str,
    source: str,
    language: str,
) -> tuple[list[SymbolRecord], list[ReferenceRecord], list[ReferenceRecord]]:
    if language == "python":
        return _python_semantic_facts(relative, source)
    if language in {"javascript", "typescript"}:
        return _javascript_semantic_facts(relative, source)
    return _regex_semantic_facts(relative, source, language)


def _python_semantic_facts(
    relative: str,
    source: str,
) -> tuple[list[SymbolRecord], list[ReferenceRecord], list[ReferenceRecord]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], [], []
    symbols: list[SymbolRecord] = []
    calls: list[ReferenceRecord] = []
    references: list[ReferenceRecord] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.containers: list[str] = []

        @property
        def container(self) -> str:
            return ".".join(self.containers)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            symbols.append(
                SymbolRecord(
                    relative,
                    node.name,
                    "class",
                    node.lineno,
                    getattr(node, "end_lineno", 0) or 0,
                    self.container,
                )
            )
            self.containers.append(node.name)
            self.generic_visit(node)
            self.containers.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node, "method" if self.containers else "function")

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node, "async method" if self.containers else "async function")

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
            kind: str,
        ) -> None:
            symbols.append(
                SymbolRecord(
                    relative,
                    node.name,
                    kind,
                    node.lineno,
                    getattr(node, "end_lineno", 0) or 0,
                    self.container,
                )
            )
            self.containers.append(node.name)
            self.generic_visit(node)
            self.containers.pop()

        def visit_Call(self, node: ast.Call) -> None:
            name = _python_call_name(node.func)
            if name:
                calls.append(ReferenceRecord(name, node.lineno, self.container))
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                references.append(ReferenceRecord(node.id, node.lineno, self.container))

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if isinstance(node.ctx, ast.Load):
                references.append(ReferenceRecord(node.attr, node.lineno, self.container))
            self.generic_visit(node)

    Visitor().visit(tree)
    return symbols, _unique_references(calls), _unique_references(references)


def _python_call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _javascript_semantic_facts(
    relative: str,
    source: str,
) -> tuple[list[SymbolRecord], list[ReferenceRecord], list[ReferenceRecord]]:
    symbols: list[SymbolRecord] = []
    symbol_patterns = [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"), "interface"),
        (re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)"), "type"),
        (
            re.compile(
                r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
            ),
            "function",
        ),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="), "binding"),
    ]
    for line_number, line in enumerate(source.splitlines(), start=1):
        for pattern, kind in symbol_patterns:
            match = pattern.search(line)
            if match:
                symbols.append(SymbolRecord(relative, match.group(1), kind, line_number))
                break
    return symbols, _regex_calls(source), _regex_references(source)


def _regex_semantic_facts(
    relative: str,
    source: str,
    language: str,
) -> tuple[list[SymbolRecord], list[ReferenceRecord], list[ReferenceRecord]]:
    patterns: dict[str, list[tuple[re.Pattern[str], str]]] = {
        "go": [
            (re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("), "function"),
            (re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b"), "type"),
        ],
        "rust": [
            (re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"), "function"),
            (re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)"), "type"),
        ],
        "java": _class_and_method_patterns(),
        "kotlin": _class_and_method_patterns(kotlin=True),
        "csharp": _class_and_method_patterns(),
        "swift": [
            (re.compile(r"^\s*(?:public\s+|private\s+)?(?:class|struct|enum|protocol)\s+([A-Za-z_]\w*)"), "type"),
            (re.compile(r"^\s*(?:public\s+|private\s+)?func\s+([A-Za-z_]\w*)"), "function"),
        ],
        "c": _c_family_patterns(),
        "cpp": _c_family_patterns(),
        "ruby": [
            (re.compile(r"^\s*class\s+([A-Za-z_]\w*)"), "class"),
            (re.compile(r"^\s*def\s+([A-Za-z_]\w*[!?=]?)"), "function"),
        ],
        "php": [
            (re.compile(r"^\s*(?:final\s+|abstract\s+)?class\s+([A-Za-z_]\w*)"), "class"),
            (re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?function\s+([A-Za-z_]\w*)"), "function"),
        ],
    }
    symbols: list[SymbolRecord] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        for pattern, kind in patterns.get(language, []):
            match = pattern.search(line)
            if match:
                symbols.append(SymbolRecord(relative, match.group(1), kind, line_number))
                break
    return symbols, _regex_calls(source), _regex_references(source)


def _class_and_method_patterns(*, kotlin: bool = False) -> list[tuple[re.Pattern[str], str]]:
    method = (
        r"^\s*(?:public|private|protected|internal|static|final|abstract|synchronized|override|open|suspend|\s)+"
        r"(?:fun\s+)?(?:[\w<>,.?\[\]]+\s+)?([A-Za-z_]\w*)\s*\("
        if kotlin
        else r"^\s*(?:public|private|protected|internal|static|final|abstract|synchronized|override|\s)+[\w<>,.?\[\]]+\s+([A-Za-z_]\w*)\s*\("
    )
    return [
        (re.compile(r"^\s*(?:public\s+|private\s+)?(?:class|interface|enum|record|object)\s+([A-Za-z_]\w*)"), "type"),
        (re.compile(method), "method"),
    ]


def _c_family_patterns() -> list[tuple[re.Pattern[str], str]]:
    return [
        (re.compile(r"^\s*(?:class|struct|enum)\s+([A-Za-z_]\w*)"), "type"),
        (
            re.compile(
                r"^\s*(?!if\b|for\b|while\b|switch\b)(?:[A-Za-z_]\w*[\w\s:*&<>]*\s+)+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:\{|$)"
            ),
            "function",
        ),
    ]


def _regex_calls(source: str) -> list[ReferenceRecord]:
    excluded = {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "sizeof",
        "function",
        "func",
        "fn",
        "def",
    }
    calls: list[ReferenceRecord] = []
    pattern = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
    for line_number, line in enumerate(source.splitlines(), start=1):
        for match in pattern.finditer(line):
            if match.group(1) not in excluded:
                calls.append(ReferenceRecord(match.group(1), line_number))
    return _unique_references(calls)


def _regex_references(source: str) -> list[ReferenceRecord]:
    references: list[ReferenceRecord] = []
    seen: set[str] = set()
    for line_number, line in enumerate(source.splitlines(), start=1):
        for name in re.findall(r"\b[A-Za-z_$][\w$]*\b", line):
            if name in seen or len(name) < 3:
                continue
            seen.add(name)
            references.append(ReferenceRecord(name, line_number))
            if len(references) >= 400:
                return references
    return references


def _unique_references(records: list[ReferenceRecord]) -> list[ReferenceRecord]:
    unique: dict[tuple[str, str], ReferenceRecord] = {}
    for record in records:
        unique.setdefault((record.name, record.container), record)
    return list(unique.values())


def _format_symbol(record: SymbolRecord) -> str:
    kind = record.kind
    if kind == "function":
        kind = "def" if record.path.endswith(".py") else "function"
    elif kind == "async function":
        kind = "async def"
    elif kind == "method":
        kind = "def" if record.path.endswith(".py") else "method"
    elif kind == "async method":
        kind = "async def"
    return f"L{record.line} {kind} {record.name}"


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
    language = LANGUAGE_EXTENSIONS.get(path.suffix.lower(), "")
    patterns: dict[str, list[re.Pattern[str]]] = {
        "go": [re.compile(r"(?:^|\n)\s*import\s+(?:[A-Za-z_.]+\s+)?[\"`]([^\"`]+)[\"`]")],
        "rust": [
            re.compile(r"(?:^|\n)\s*use\s+([^;\s]+)"),
            re.compile(r"(?:^|\n)\s*(?:pub\s+)?mod\s+([A-Za-z_]\w*)"),
        ],
        "java": [re.compile(r"(?:^|\n)\s*import\s+(?:static\s+)?([\w.]+)")],
        "kotlin": [re.compile(r"(?:^|\n)\s*import\s+([\w.]+)")],
        "csharp": [re.compile(r"(?:^|\n)\s*using\s+([\w.]+)\s*;")],
        "swift": [re.compile(r"(?:^|\n)\s*import\s+([A-Za-z_]\w*)")],
        "c": [re.compile(r"(?:^|\n)\s*#\s*include\s*[<\"]([^>\"]+)[>\"]")],
        "cpp": [re.compile(r"(?:^|\n)\s*#\s*include\s*[<\"]([^>\"]+)[>\"]")],
        "ruby": [re.compile(r"(?:^|\n)\s*require(?:_relative)?\s*[\"']([^\"']+)[\"']")],
        "php": [re.compile(r"(?:^|\n)\s*(?:use|require|include)(?:_once)?\s*[\"']?([^;\"']+)")],
    }
    imports: list[str] = []
    for pattern in patterns.get(language, []):
        imports.extend(match.group(1).strip() for match in pattern.finditer(source))
    if language == "go":
        block = re.search(r"(?:^|\n)\s*import\s*\((.*?)\)", source, re.DOTALL)
        if block:
            imports.extend(re.findall(r"[\"`]([^\"`]+)[\"`]", block.group(1)))
    return sorted(set(imports))


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


def _build_project_graph_edges(files: list[RepoFile]) -> list[ProjectGraphEdge]:
    module_map = _module_map(files)
    symbols_by_name: dict[str, list[SymbolRecord]] = {}
    for item in files:
        for symbol in item.symbol_records:
            symbols_by_name.setdefault(symbol.name, []).append(symbol)

    edges: list[ProjectGraphEdge] = []
    imported_targets: dict[str, set[str]] = {}
    for item in files:
        for import_name in item.imports:
            target = _resolve_import_target(item.path, import_name, module_map)
            if target:
                imported_targets.setdefault(item.path, set()).add(target)
                edges.append(
                    ProjectGraphEdge(
                        source_path=item.path,
                        target_path=target,
                        relation="import",
                        detail=import_name,
                    )
                )
            else:
                edges.append(
                    ProjectGraphEdge(
                        source_path=item.path,
                        target_path="",
                        relation="external_import",
                        detail=_external_import_name(import_name),
                    )
                )

    for item in files:
        imported = imported_targets.get(item.path, set())
        for record in item.calls:
            target = _select_symbol_target(item.path, record.name, imported, symbols_by_name)
            if target is None or (target.path == item.path and target.line == record.line):
                continue
            edges.append(
                ProjectGraphEdge(
                    source_path=item.path,
                    target_path=target.path,
                    relation="call",
                    detail=record.name,
                    source_symbol=record.container,
                    target_symbol=target.qualified_name,
                    line=record.line,
                )
            )
        for record in item.references:
            target = _select_symbol_target(item.path, record.name, imported, symbols_by_name)
            if target is None or (target.path == item.path and target.line == record.line):
                continue
            edges.append(
                ProjectGraphEdge(
                    source_path=item.path,
                    target_path=target.path,
                    relation="reference",
                    detail=record.name,
                    source_symbol=record.container,
                    target_symbol=target.qualified_name,
                    line=record.line,
                )
            )

    source_files = [item for item in files if item.kind == "source"]
    for test in (item for item in files if item.kind == "test"):
        mapped = set(imported_targets.get(test.path, set()))
        test_key = _test_name_key(test.path)
        for source in source_files:
            if test_key and test_key == _source_name_key(source.path):
                mapped.add(source.path)
        for target in sorted(mapped):
            if target != test.path:
                edges.append(
                    ProjectGraphEdge(
                        source_path=test.path,
                        target_path=target,
                        relation="test",
                        detail="import or naming convention",
                    )
                )

    for config in (item for item in files if item.kind == "config"):
        languages = _config_languages(config.path)
        for source in source_files:
            if source.language in languages:
                edges.append(
                    ProjectGraphEdge(
                        source_path=config.path,
                        target_path=source.path,
                        relation="config",
                        detail=Path(config.path).name,
                    )
                )

    unique: dict[tuple[Any, ...], ProjectGraphEdge] = {}
    for edge in edges:
        key = (
            edge.source_path,
            edge.target_path,
            edge.relation,
            edge.detail,
            edge.source_symbol,
            edge.target_symbol,
            edge.line,
        )
        unique.setdefault(key, edge)
    return sorted(
        unique.values(),
        key=lambda edge: (
            edge.relation,
            edge.source_path,
            edge.target_path,
            edge.source_symbol,
            edge.target_symbol,
            edge.line,
        ),
    )


def _select_symbol_target(
    source_path: str,
    name: str,
    imported_targets: set[str],
    symbols_by_name: dict[str, list[SymbolRecord]],
) -> SymbolRecord | None:
    candidates = symbols_by_name.get(name, [])
    if not candidates:
        return None
    same_file = [symbol for symbol in candidates if symbol.path == source_path]
    if len(same_file) == 1:
        return same_file[0]
    imported = [symbol for symbol in candidates if symbol.path in imported_targets]
    if len(imported) == 1:
        return imported[0]
    return candidates[0] if len(candidates) == 1 else None


def _test_name_key(path: str) -> str:
    stem = Path(path).stem.lower()
    stem = re.sub(r"^(?:test_|spec_)", "", stem)
    stem = re.sub(r"(?:_test|_spec)$", "", stem)
    return re.sub(r"[^a-z0-9]", "", stem)


def _source_name_key(path: str) -> str:
    return re.sub(r"[^a-z0-9]", "", Path(path).stem.lower())


def _config_languages(path: str) -> set[str]:
    name = Path(path).name.lower()
    if name in {"pyproject.toml", "requirements.txt", "pytest.ini", "uv.lock"}:
        return {"python"}
    if name in {"package.json", "tsconfig.json", "vite.config.ts", "next.config.js", "next.config.mjs"}:
        return {"javascript", "typescript"}
    if name in {"cargo.toml", "cargo.lock"}:
        return {"rust"}
    if name in {"go.mod", "go.sum"}:
        return {"go"}
    if name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
        return {"java", "kotlin"}
    if name in {"cmakelists.txt", "makefile"}:
        return {"c", "cpp"}
    return set()


def _resolve_import_target(source: str, import_name: str, module_map: dict[str, str]) -> str | None:
    if import_name.startswith("."):
        return _resolve_relative_javascript_import(source, import_name, module_map)
    path_lookup = {path: path for path in module_map.values()}
    source_path = Path(source)
    sibling_base = source_path.parent / import_name
    sibling_candidates = [
        sibling_base.as_posix(),
        sibling_base.with_suffix(source_path.suffix).as_posix(),
        (sibling_base / f"mod{source_path.suffix}").as_posix(),
        (sibling_base / f"index{source_path.suffix}").as_posix(),
    ]
    for candidate in sibling_candidates:
        if candidate in path_lookup:
            return candidate
    normalized = import_name.strip().removesuffix(".*")
    if normalized.startswith(("crate::", "self::", "super::")):
        normalized = re.sub(r"^(?:crate|self|super)::", "", normalized)
    normalized = normalized.replace("::", ".").replace("/", ".")
    normalized = re.sub(r"\.(?:h|hpp|c|cc|cpp)$", "", normalized)
    direct_candidates = [
        normalized,
        normalized.replace(".", "/"),
        Path(normalized).stem,
    ]
    for candidate in direct_candidates:
        if candidate in module_map:
            return module_map[candidate]
    parts = normalized.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in module_map:
            return module_map[candidate]
    for start in range(1, len(parts)):
        candidate = ".".join(parts[start:])
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
            modules.setdefault(module, item.path)
        path_without_suffix = Path(item.path).with_suffix("").as_posix()
        aliases = {
            path_without_suffix,
            path_without_suffix.replace("/", "."),
            Path(item.path).stem,
        }
        if path_without_suffix.startswith("src/"):
            aliases.add(path_without_suffix.removeprefix("src/").replace("/", "."))
        for alias in aliases:
            modules.setdefault(alias, item.path)
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
    if item.suffix.lower() in LANGUAGE_EXTENSIONS:
        without_suffix = item.with_suffix("")
        parts = list(without_suffix.parts)
        if parts and parts[0] in {"src", "lib", "app"}:
            parts = parts[1:]
        return ".".join(parts)
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


def _group_project_external_edges(
    edges: list[ProjectGraphEdge],
) -> list[tuple[str, list[str]]]:
    grouped: dict[str, set[str]] = {}
    for edge in edges:
        grouped.setdefault(edge.source_path, set()).add(edge.detail)
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
    if lower.startswith("src/") or Path(lower).suffix in LANGUAGE_EXTENSIONS:
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


def _record_payload(record: SymbolRecord | ReferenceRecord) -> dict[str, Any]:
    return {
        field: getattr(record, field)
        for field in record.__dataclass_fields__
    }


def _ensure_sqlite_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute(f"pragma table_info({table})").fetchall()
    }
    for name, declaration in columns.items():
        if name not in existing:
            conn.execute(f"alter table {table} add column {name} {declaration}")
