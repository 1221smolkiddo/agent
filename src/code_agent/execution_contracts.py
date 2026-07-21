from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


ADAPTER_API_VERSION = "1"


class GuaranteeLevel(str, Enum):
    UNSUPPORTED = "unsupported"
    BEST_EFFORT = "best_effort"
    ADAPTER = "adapter"
    PROVIDER = "provider"
    ENGINE = "engine"


class IsolationLevel(str, Enum):
    NONE = "none"
    WORKSPACE = "workspace"
    PROCESS = "process"
    CONTAINER = "container"
    VM = "vm"


@dataclass(frozen=True)
class AdapterCapabilities:
    name: str
    adapter_api_version: str = ADAPTER_API_VERSION
    effect_kinds: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    isolation_level: IsolationLevel = IsolationLevel.WORKSPACE
    idempotency: GuaranteeLevel = GuaranteeLevel.UNSUPPORTED
    reconciliation: GuaranteeLevel = GuaranteeLevel.UNSUPPORTED
    compensation: GuaranteeLevel = GuaranteeLevel.UNSUPPORTED
    verification: GuaranteeLevel = GuaranteeLevel.ADAPTER
    cancellation: GuaranteeLevel = GuaranteeLevel.BEST_EFFORT
    timeout_support: bool = True
    streaming: bool = False
    concurrency_safe: bool = False
    retry_safe: bool = False
    durable: bool = False
    resource_accounting: tuple[str, ...] = ()
    compatibility_versions: tuple[str, ...] = ("1",)

    def validate(self) -> None:
        if not self.name or self.adapter_api_version != ADAPTER_API_VERSION:
            raise ValueError("Adapter uses an unsupported API contract.")
        if not self.effect_kinds:
            raise ValueError(f"Adapter {self.name} must declare at least one effect kind.")
        if self.retry_safe and self.idempotency == GuaranteeLevel.UNSUPPORTED:
            raise ValueError("Retry-safe adapters must provide idempotency.")
        if self.durable and self.reconciliation == GuaranteeLevel.UNSUPPORTED:
            raise ValueError("Durable adapters must provide reconciliation.")

    def supports(self, requirement: "CapabilityRequirement") -> tuple[bool, list[str]]:
        missing: list[str] = []
        if requirement.effect_kind not in self.effect_kinds and "*" not in self.effect_kinds:
            missing.append(f"effect kind {requirement.effect_kind}")
        if requirement.compatibility_version not in self.compatibility_versions:
            missing.append(f"compatibility {requirement.compatibility_version}")
        if set(requirement.permissions) - set(self.permissions):
            missing.append("permissions: " + ", ".join(sorted(set(requirement.permissions) - set(self.permissions))))
        for name in ("idempotency", "reconciliation", "compensation", "verification", "cancellation"):
            required = getattr(requirement, name)
            actual = getattr(self, name)
            if required and actual == GuaranteeLevel.UNSUPPORTED:
                missing.append(name)
        if requirement.timeout_support and not self.timeout_support:
            missing.append("timeout support")
        if requirement.concurrency_safe and not self.concurrency_safe:
            missing.append("concurrency safety")
        return not missing, missing

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for name in (
            "isolation_level", "idempotency", "reconciliation", "compensation",
            "verification", "cancellation",
        ):
            value[name] = getattr(self, name).value
        return value


@dataclass(frozen=True)
class CapabilityRequirement:
    effect_kind: str
    compatibility_version: str = "1"
    permissions: tuple[str, ...] = ()
    idempotency: bool = True
    reconciliation: bool = True
    compensation: bool = False
    verification: bool = True
    cancellation: bool = False
    timeout_support: bool = True
    concurrency_safe: bool = False


@dataclass(frozen=True)
class AdapterContext:
    execution_id: str
    task_id: str
    compatibility_version: str
    actor: str
    idempotency_key: str
    timeout_seconds: float
    permissions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedEffect:
    adapter: str
    kind: str
    request: dict[str, Any]
    context: AdapterContext
    fingerprint: str


@dataclass(frozen=True)
class EffectOutcome:
    ok: bool
    status: str
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)
    external_id: str | None = None


@dataclass(frozen=True)
class AdapterEvidence:
    kind: str
    summary: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CompensationOutcome:
    ok: bool
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TransactionalAdapter(Protocol):
    capabilities: AdapterCapabilities

    def prepare(self, request: dict[str, Any], context: AdapterContext) -> PreparedEffect: ...

    def execute(self, prepared: PreparedEffect) -> EffectOutcome: ...

    def reconcile(self, prepared: PreparedEffect) -> EffectOutcome: ...

    def compensate(
        self, prepared: PreparedEffect, outcome: EffectOutcome
    ) -> CompensationOutcome: ...

    def verify(
        self, prepared: PreparedEffect, outcome: EffectOutcome
    ) -> list[AdapterEvidence]: ...

    def cancel(self, prepared: PreparedEffect) -> bool: ...


class CapabilityNegotiationError(RuntimeError):
    pass


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, TransactionalAdapter] = {}

    def register(self, adapter: TransactionalAdapter, *, replace: bool = False) -> None:
        adapter.capabilities.validate()
        name = adapter.capabilities.name
        if name in self._adapters and not replace:
            raise ValueError(f"Adapter already registered: {name}")
        self._adapters[name] = adapter

    def resolve(
        self, requirement: CapabilityRequirement, *, preferred: str | None = None
    ) -> TransactionalAdapter:
        candidates = (
            [self._adapters[preferred]]
            if preferred and preferred in self._adapters
            else [self._adapters[name] for name in sorted(self._adapters)]
        )
        failures: list[str] = []
        for adapter in candidates:
            supported, missing = adapter.capabilities.supports(requirement)
            if supported:
                return adapter
            failures.append(f"{adapter.capabilities.name}: {', '.join(missing)}")
        raise CapabilityNegotiationError(
            f"No adapter satisfies {requirement.effect_kind}: " + "; ".join(failures)
        )

    def discover(self) -> list[dict[str, Any]]:
        return [self._adapters[name].capabilities.as_dict() for name in sorted(self._adapters)]
