from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .command_registry import CommandMeta, CommandRegistry


def levenshtein(a: str, b: str) -> int:
    """Calculate the Levenshtein distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    v0 = list(range(len(b) + 1))
    v1 = [0] * (len(b) + 1)

    for i in range(len(a)):
        v1[0] = i + 1
        for j in range(len(b)):
            cost = 0 if a[i] == b[j] else 1
            v1[j + 1] = min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost)
        v0, v1 = v1, v0

    return v0[len(b)]


def search_commands(
    query: str,
    registry: CommandRegistry,
    *,
    limit: int = 5,
    include_hidden: bool = False,
) -> list[CommandMeta]:
    """Search commands using a deterministic 5-tier ranking pipeline.
    
    Tiers:
    1. Exact prefix match
    2. Alias match
    3. Keyword match
    4. Substring match
    5. Levenshtein edit distance match
    """
    clean_query = query.strip().lower()
    if not clean_query:
        return registry.iter_visible()[:limit]

    # Normalize slash
    query_no_slash = clean_query.lstrip("/")
    query_with_slash = "/" + query_no_slash

    candidates = registry.all_commands() if include_hidden else registry.iter_visible()

    # Tier buckets
    tier1_prefix: list[CommandMeta] = []
    tier2_alias: list[CommandMeta] = []
    tier3_keyword: list[CommandMeta] = []
    tier4_substring: list[CommandMeta] = []
    tier5_levenshtein: list[tuple[int, CommandMeta]] = []

    seen: set[str] = set()

    for cmd in candidates:
        name_lower = cmd.name.lower()
        name_no_slash = name_lower.lstrip("/")

        # Tier 1: Exact prefix match on canonical name
        if name_lower.startswith(query_with_slash) or name_no_slash.startswith(query_no_slash):
            tier1_prefix.append(cmd)
            seen.add(cmd.name)
            continue

        # Tier 2: Alias prefix/exact match
        if any(
            alias.lower().startswith(query_with_slash)
            or alias.lower().lstrip("/").startswith(query_no_slash)
            for alias in cmd.aliases
        ):
            tier2_alias.append(cmd)
            seen.add(cmd.name)
            continue

        # Tier 3: Keyword match
        all_keywords = set(cmd.keywords) | {cmd.category.lower()}
        if any(kw.startswith(query_no_slash) for kw in all_keywords):
            tier3_keyword.append(cmd)
            seen.add(cmd.name)
            continue

        # Tier 4: Substring match in name or description
        if (
            query_no_slash in name_no_slash
            or query_no_slash in cmd.description.lower()
        ):
            tier4_substring.append(cmd)
            seen.add(cmd.name)
            continue

        # Tier 5: Levenshtein distance match (fuzzy)
        # Max allowed distance: 3, or 40% of query length
        max_dist = min(3, max(1, int(len(query_no_slash) * 0.4)))
        dist = levenshtein(query_no_slash, name_no_slash)
        if dist <= max_dist:
            tier5_levenshtein.append((dist, cmd))
            seen.add(cmd.name)

    # Sort each tier by command rank (and Levenshtein distance for Tier 5)
    tier1_prefix.sort(key=lambda c: (c.rank, c.name))
    tier2_alias.sort(key=lambda c: (c.rank, c.name))
    tier3_keyword.sort(key=lambda c: (c.rank, c.name))
    tier4_substring.sort(key=lambda c: (c.rank, c.name))
    tier5_levenshtein.sort(key=lambda item: (item[0], item[1].rank, item[1].name))

    results: list[CommandMeta] = []
    results.extend(tier1_prefix)
    results.extend(tier2_alias)
    results.extend(tier3_keyword)
    results.extend(tier4_substring)
    results.extend([item[1] for item in tier5_levenshtein])

    return results[:limit]


def suggest_commands(query: str, registry: CommandRegistry) -> list[CommandMeta]:
    """Suggest corrections/matches for an unknown command or natural query."""
    return search_commands(query, registry, limit=5, include_hidden=True)
