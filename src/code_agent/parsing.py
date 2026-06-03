from __future__ import annotations

from pathlib import Path


def summarize_code_file(path: Path) -> str:
    """Return a compact tree-sitter based summary when optional parsing deps exist."""
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return "Tree-sitter support is not installed. Install with: pip install -e .[parsing]"

    language = _language_from_suffix(path.suffix)
    if language is None:
        return f"No tree-sitter language mapping for {path.suffix or '<no suffix>'}."

    source = path.read_bytes()
    parser = get_parser(language)
    tree = parser.parse(source)
    captures = _interesting_nodes(tree.root_node, source)
    return "\n".join(captures) or "No high-level declarations found."


def _language_from_suffix(suffix: str) -> str | None:
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".go": "go",
        ".rs": "rust",
    }.get(suffix)


def _interesting_nodes(root: object, source: bytes) -> list[str]:
    wanted = {
        "class_definition",
        "function_definition",
        "function_declaration",
        "method_definition",
        "lexical_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "struct_item",
        "enum_item",
        "impl_item",
        "function_item",
    }
    lines: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        node_type = getattr(node, "type", "")
        if node_type in wanted:
            start = getattr(node, "start_byte")
            end = getattr(node, "end_byte")
            text = source[start:end].decode("utf-8", errors="replace").splitlines()[0].strip()
            row = getattr(node, "start_point")[0] + 1
            lines.append(f"{row}: {text}")
        stack.extend(reversed(getattr(node, "children", [])))
    return lines
