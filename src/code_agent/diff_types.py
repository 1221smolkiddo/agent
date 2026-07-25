"""Shared diff view types and enumerations."""

from __future__ import annotations

from enum import Enum


class DiffViewMode(str, Enum):
    """Rendering mode for the interactive diff viewer."""

    UNIFIED = "unified"
    SIDE_BY_SIDE = "side-by-side"

    @classmethod
    def normalize(cls, value: DiffViewMode | str) -> DiffViewMode:
        """Coerce a string or enum value into a valid :class:`DiffViewMode`."""
        if isinstance(value, cls):
            return value
        cleaned = str(value).strip().lower().replace("_", "-")
        if cleaned in {"sbs", "side"}:
            return cls.SIDE_BY_SIDE
        for item in cls:
            if item.value == cleaned:
                return item
        raise ValueError(
            f"Invalid diff view mode: {value!r}. Expected 'unified' or 'side-by-side'."
        )
