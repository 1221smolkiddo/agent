"""Optional historical experience memory, separate from local and runtime memory."""
from .config import ExperienceMemoryConfig
from .contracts import (
    Experience, ExperienceMemoryProvider, ExperienceMemoryRecall, MemoryProvenance,
    MemoryResult, MemoryStatus, RecallRequest, RecalledExperience, RecalledMemory,
    ReflectDecision, ReflectRequest, ReflectResult, ReflectionHypothesis, ReflectionSupport,
)
from .scope import RepositoryScope, repository_scope
from .service import ExperienceMemoryService

__all__ = [
    "Experience", "ExperienceMemoryConfig", "ExperienceMemoryProvider",
    "ExperienceMemoryService", "ExperienceMemoryRecall", "MemoryProvenance",
    "MemoryResult", "MemoryStatus", "RecallRequest", "RecalledExperience", "RecalledMemory",
    "ReflectDecision", "ReflectRequest", "ReflectResult", "ReflectionHypothesis",
    "ReflectionSupport",
    "RepositoryScope", "repository_scope",
]
