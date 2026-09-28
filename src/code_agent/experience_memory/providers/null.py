from ..contracts import (
    Experience, ExperienceMemoryRecall, MemoryResult, MemoryStatus, OperationLookup, RecallRequest,
    ReflectRequest, ReflectResult,
)


class NullExperienceMemoryProvider:
    def __init__(self, status: MemoryStatus = MemoryStatus.DISABLED) -> None:
        self.status = status

    def health(self) -> MemoryResult:
        return MemoryResult(self.status)

    def recall(self, bank_id: str, query: str) -> MemoryResult:
        return self.health()

    def recall_detailed(self, bank_id: str, request: RecallRequest) -> ExperienceMemoryRecall:
        return ExperienceMemoryRecall(self.status)

    def retain(self, bank_id: str, experience: Experience) -> MemoryResult:
        return self.health()

    def reflect(self, bank_id: str, query: str) -> MemoryResult:
        return self.health()

    def reflect_detailed(self, bank_id: str, request: ReflectRequest) -> ReflectResult:
        return ReflectResult(self.status)

    def get_operation(self, bank_id: str, operation_id: str) -> OperationLookup:
        return OperationLookup(self.status)
