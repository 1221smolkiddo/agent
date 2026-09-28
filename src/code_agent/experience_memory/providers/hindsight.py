from __future__ import annotations

import asyncio
import importlib
from typing import Any, Literal

from ..config import ExperienceMemoryConfig
from ..contracts import (
    Experience, MemoryResult, MemoryStatus, OperationLookup, OperationState, RecalledExperience,
)
from ..privacy import safe_text, valid_experience, valid_query


class HindsightExperienceMemoryProvider:
    """Sync facade over the optional SDK, with one bounded async client per call.

    Construction performs no imports, network operations, or bank creation. No retries:
    a retain timeout is ambiguous and must not trigger automatic duplicate writes.
    """

    def __init__(self, config: ExperienceMemoryConfig) -> None:
        self._config = config

    def health(self) -> MemoryResult:
        return self._call("health")

    def recall(self, bank_id: str, query: str) -> MemoryResult:
        return self._call("recall", bank_id=bank_id, query=query)

    def retain(self, bank_id: str, experience: Experience) -> MemoryResult:
        return self._call("retain", bank_id=bank_id, experience=experience)

    def reflect(self, bank_id: str, query: str) -> MemoryResult:
        return self._call("reflect", bank_id=bank_id, query=query)

    def get_operation(self, bank_id: str, operation_id: str) -> OperationLookup:
        if self._config.availability != MemoryStatus.OK:
            return OperationLookup(self._config.availability)
        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return OperationLookup(MemoryStatus.UNAVAILABLE)
            sdk = importlib.import_module("hindsight_client")
            return asyncio.run(self._lookup_operation(sdk, bank_id, operation_id))
        except ImportError:
            return OperationLookup(MemoryStatus.MISSING_DEPENDENCY)
        except TimeoutError:
            return OperationLookup(MemoryStatus.TIMEOUT)
        except Exception:
            return OperationLookup(MemoryStatus.UNAVAILABLE)

    async def _lookup_operation(
        self, sdk: Any, bank_id: str, operation_id: str,
    ) -> OperationLookup:
        config = self._config
        deadline = asyncio.get_running_loop().time() + config.timeout_seconds
        async with asyncio.timeout_at(deadline):
            client = sdk.Hindsight(
                base_url=config.endpoint,
                api_key=config.api_key.get_secret_value() if config.api_key else None,
                timeout=config.timeout_seconds, max_attempts=1,
            )
            try:
                try:
                    response = await client.operations.get_operation_status(
                        bank_id, operation_id, include_payload=False,
                        _request_timeout=config.timeout_seconds,
                    )
                except Exception as exc:
                    if getattr(exc, "status", None) == 404:
                        return OperationLookup(MemoryStatus.OK, OperationState.NOT_FOUND)
                    raise
                if response.operation_id != operation_id:
                    return OperationLookup(MemoryStatus.UNAVAILABLE)
                return OperationLookup(
                    MemoryStatus.OK, OperationState(response.status),
                    "provider_failed" if response.status == "failed" else "",
                )
            finally:
                remaining = max(0.001, deadline - asyncio.get_running_loop().time())
                async with asyncio.timeout(remaining):
                    await client.aclose()

    def _call(
        self, operation: Literal["health", "recall", "retain", "reflect"], *,
        bank_id: str = "", query: str = "", experience: Experience | None = None,
    ) -> MemoryResult:
        status = self._config.availability
        if status != MemoryStatus.OK:
            return MemoryResult(status)
        try:
            if operation in {"recall", "reflect"} and not valid_query(
                bank_id, query, self._config,
            ):
                return MemoryResult(MemoryStatus.INVALID_REQUEST)
            if operation == "retain" and (
                experience is None or not valid_experience(bank_id, experience, self._config)
            ):
                return MemoryResult(MemoryStatus.INVALID_REQUEST)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                return MemoryResult(MemoryStatus.UNAVAILABLE)
            try:
                sdk = importlib.import_module("hindsight_client")
            except ImportError:
                return MemoryResult(MemoryStatus.MISSING_DEPENDENCY)
            return asyncio.run(self._execute(sdk, operation, bank_id, query, experience))
        except TimeoutError:
            return MemoryResult(MemoryStatus.TIMEOUT)
        except Exception:
            # Never propagate exception messages, URLs, request bodies, or SDK objects.
            return MemoryResult(MemoryStatus.UNAVAILABLE)

    async def _execute(
        self, sdk: Any, operation: str, bank_id: str, query: str,
        experience: Experience | None,
    ) -> MemoryResult:
        config = self._config
        deadline = asyncio.get_running_loop().time() + config.timeout_seconds
        async with asyncio.timeout_at(deadline):
            client = sdk.Hindsight(
                base_url=config.endpoint,
                api_key=config.api_key.get_secret_value() if config.api_key else None,
                timeout=config.timeout_seconds, max_attempts=1,
            )
            try:
                if operation == "health":
                    response = await client.monitoring.health_endpoint_health_get(
                        _request_timeout=config.timeout_seconds,
                    )
                    if not isinstance(response, dict) or response.get("status") != "healthy":
                        return MemoryResult(MemoryStatus.UNAVAILABLE)
                    return MemoryResult(MemoryStatus.OK)
                if operation == "retain":
                    assert experience is not None
                    episode = experience.kind == "engineering_episode"
                    metadata = {
                        "source": "agent47",
                        "memory_kind": "engineering_episode" if episode else "historical_experience",
                    }
                    if experience.branch:
                        metadata["branch"] = experience.branch
                    if experience.head:
                        metadata["head"] = experience.head
                    if episode:
                        metadata.update({
                            "outcome": experience.outcome,
                            "agent_version": experience.agent_version,
                        })
                    response = await client.aretain(
                        bank_id=bank_id, content=experience.summary,
                        metadata=metadata, document_id=experience.document_id,
                        update_mode="replace" if episode else None,
                        retain_async=episode, operation_id=experience.operation_id,
                    )
                    success = response.success is True and (
                        response.var_async is True
                        and response.operation_id == experience.operation_id
                        if episode else response.var_async is False
                    )
                    return MemoryResult(MemoryStatus.OK if success else MemoryStatus.UNAVAILABLE)
                if operation == "recall":
                    response = await client.arecall(
                        bank_id=bank_id, query=query, max_tokens=config.recall_max_tokens,
                        budget=config.budget,
                    )
                    remaining = config.recall_max_tokens  # conservative UTF-8 byte ceiling
                    memories: list[RecalledExperience] = []
                    for item in response.results[:config.recall_max_results]:
                        text = safe_text(item.text, config).encode("utf-8")[:remaining]
                        remaining -= len(text)
                        if text:
                            memories.append(RecalledExperience(text.decode("utf-8", errors="ignore")))
                        if remaining <= 0:
                            break
                    return MemoryResult(MemoryStatus.OK, tuple(memories))
                response = await client.areflect(
                    bank_id=bank_id, query=query, max_tokens=config.recall_max_tokens,
                    budget=config.budget,
                )
                text = safe_text(response.text, config).encode("utf-8")[:config.recall_max_tokens]
                return MemoryResult(MemoryStatus.OK, text=text.decode("utf-8", errors="ignore"))
            finally:
                remaining = max(0.001, deadline - asyncio.get_running_loop().time())
                async with asyncio.timeout(remaining):
                    await client.aclose()
