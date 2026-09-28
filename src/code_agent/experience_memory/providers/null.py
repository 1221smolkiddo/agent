from ..contracts import Experience, MemoryResult, MemoryStatus, OperationLookup


class NullExperienceMemoryProvider:
    def __init__(self, status: MemoryStatus = MemoryStatus.DISABLED) -> None:
        self.status = status

    def health(self) -> MemoryResult:
        return MemoryResult(self.status)

    def recall(self, bank_id: str, query: str) -> MemoryResult:
        return self.health()

    def retain(self, bank_id: str, experience: Experience) -> MemoryResult:
        return self.health()

    def reflect(self, bank_id: str, query: str) -> MemoryResult:
        return self.health()

    def get_operation(self, bank_id: str, operation_id: str) -> OperationLookup:
        return OperationLookup(self.status)
