"""Safe operator diagnostics and opt-in live qualification for experience memory."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from pathlib import Path

import typer
from pydantic import ValidationError

from .config import Settings
from .experience_memory.config import ExperienceMemoryConfig
from .experience_memory.contracts import MemoryStatus, OperationState, RecallRequest
from .experience_memory.episode_sanitizer import MemorySanitizer
from .experience_memory.outbox import ExperienceMemoryOutbox, OutboxEntry, SAFE_ERROR_CODES
from .experience_memory.service import ExperienceMemoryService

memory_app = typer.Typer(help="Inspect Hindsight health and durable retention.")


def _service() -> tuple[Settings, ExperienceMemoryService]:
    try:
        settings = Settings()
        return settings, ExperienceMemoryService(settings.experience_memory_config, Path.cwd())
    except (ValidationError, ValueError):
        typer.echo(json.dumps({"health": "configuration_error", "status": "missing_config"}))
        raise typer.Exit(code=1) from None


def _outbox(settings: Settings, service: ExperienceMemoryService) -> ExperienceMemoryOutbox | None:
    if not settings.agent_db_path.is_file():
        return None
    try:
        return ExperienceMemoryOutbox(settings.agent_db_path, service)
    except (sqlite3.Error, OSError):
        typer.echo("Outbox unavailable.", err=True)
        raise typer.Exit(code=1) from None


def _entry_view(entry: OutboxEntry, *, config: ExperienceMemoryConfig | None = None) -> dict[str, object]:
    # No content, branch, HEAD, provider payload, or exception text.
    prepared = entry.prepared
    sanitizer = MemorySanitizer(config or ExperienceMemoryConfig())
    operation_id = prepared.operation_id
    document_id = prepared.document_id
    if sanitizer.sanitize_text(operation_id) != operation_id:
        operation_id = "<redacted>"
    if sanitizer.sanitize_text(document_id) != document_id:
        document_id = "<redacted>"
    review = (entry.state == "unknown" and entry.next_attempt_at is None) or entry.error_code in {
        "operation_missing_after_ack", "ambiguity_window_expired", "retry_limit",
    }
    return {
        "operation_id": operation_id if re.fullmatch(r"[0-9a-f-]{36}", operation_id) else "<invalid>",
        "document_id": document_id if re.fullmatch(r"agent47:[A-Za-z0-9_-]{1,100}:[A-Za-z0-9_-]{1,100}:episode:v1", document_id) else "<invalid>",
        "state": entry.state if entry.state in {"prepared", "submitted", "processing", "completed", "failed", "cancelled", "unknown"} else "unknown",
        "queue_state": "queued" if entry.state == "prepared" else entry.state if entry.state in {"submitted", "processing", "completed", "failed", "cancelled", "unknown"} else "unknown",
        "operator_review": review,
        "error_code": entry.error_code if entry.error_code in SAFE_ERROR_CODES else "provider_failed",
        "attempts": entry.attempts,
        "submitted": entry.submitted,
        "created_at": entry.created_at,
        "next_attempt_at": entry.next_attempt_at,
        "updated_at": entry.updated_at,
        "lease_until": entry.lease_until,
        "lease_active": entry.lease_until is not None and entry.lease_until > time.time(),
        "age_seconds": max(0.0, time.time() - entry.created_at),
        "seconds_since_update": max(0.0, time.time() - entry.updated_at),
        "network_requests": entry.network_requests,
        "wall_seconds": entry.wall_seconds,
    }


@memory_app.command("status")
def memory_status() -> None:
    settings, service = _service()
    outbox = _outbox(settings, service)
    typer.echo(json.dumps({
        "availability": service.availability.value,
        "outbox": outbox.diagnostics() if outbox else {"total": 0, "counts": {}},
    }, sort_keys=True))


@memory_app.command("health")
def memory_health() -> None:
    _, service = _service()
    if service.availability != MemoryStatus.OK:
        status = service.availability
    else:
        try:
            status = MemoryStatus(service.health().status)
        except Exception:
            status = MemoryStatus.UNAVAILABLE
    label = "healthy" if status == MemoryStatus.OK else (
        "configuration_error" if status == MemoryStatus.MISSING_CONFIG else
        "provider_unavailable" if status in {MemoryStatus.UNAVAILABLE, MemoryStatus.TIMEOUT} else
        status.value
    )
    typer.echo(json.dumps({"health": label, "status": status.value}, sort_keys=True))
    if status not in {MemoryStatus.OK, MemoryStatus.DISABLED}:
        raise typer.Exit(code=1)


@memory_app.command("outbox")
def memory_outbox(limit: int = typer.Option(50, min=1, max=200)) -> None:
    settings, service = _service()
    outbox = _outbox(settings, service)
    typer.echo(json.dumps(
        [_entry_view(entry, config=service.config) for entry in outbox.list_entries(limit=limit)] if outbox else [],
        sort_keys=True,
    ))


@memory_app.command("inspect")
def memory_inspect(operation_id: str) -> None:
    settings, service = _service()
    outbox = _outbox(settings, service)
    entry = outbox.get(operation_id) if outbox else None
    if entry is None:
        typer.echo("Operation not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(_entry_view(entry, config=service.config), sort_keys=True))


@memory_app.command("retry")
def memory_retry(operation_id: str) -> None:
    settings, service = _service()
    outbox = _outbox(settings, service)
    if outbox is None:
        typer.echo("Outbox not found.", err=True)
        raise typer.Exit(code=1)
    try:
        entry = outbox.retry(operation_id)
    except ValueError:
        typer.echo("Retry refused: operation requires review, is terminal, or provider is unavailable.", err=True)
        raise typer.Exit(code=1) from None
    if entry is None:
        typer.echo("Operation not found or currently leased.", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(_entry_view(entry, config=service.config), sort_keys=True))


def run_live_smoke(
    service: ExperienceMemoryService, bank_id: str, *, timeout_seconds: float = 60.0,
    poll_seconds: float = 2.0, include_reflect: bool = False,
) -> dict[str, object]:
    """Explicit synthetic retain and identity check, with a whole-run deadline."""
    if not re.fullmatch(r"agent47-repo-[0-9a-f]{64}", bank_id):
        return {"stage": "validation", "passed": False}
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
        return {"stage": "validation", "passed": False}
    if not math.isfinite(poll_seconds) or poll_seconds <= 0:
        return {"stage": "validation", "passed": False}
    from .experience_memory.episode_sanitizer import PreparedEpisode
    from .experience_memory.providers.hindsight import HindsightExperienceMemoryProvider
    import hashlib

    deadline = time.monotonic() + timeout_seconds

    def bounded_service() -> ExperienceMemoryService:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        config = service.config.model_copy(update={
            "timeout_seconds": min(service.config.timeout_seconds, remaining),
        })
        # Production SDK calls each receive the remaining whole-run deadline.
        provider = service._provider
        if isinstance(provider, HindsightExperienceMemoryProvider):
            provider = HindsightExperienceMemoryProvider(config)
        return ExperienceMemoryService(config, service.workspace, provider=provider)

    marker = uuid.uuid4().hex
    document_id = f"agent47:qualification:{marker}:episode:v1"
    content = json.dumps({
        "kind": "engineering_episode", "source": "agent47_qualification",
        "goal": f"Synthetic qualification marker {marker}", "outcome": "unverified",
        "lesson": "Resolve synthetic test fixtures relative to their module, not the process working directory.",
    }, sort_keys=True)
    operation_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL, document_id + ":" + hashlib.sha256(content.encode()).hexdigest(),
    ))
    prepared = PreparedEpisode(
        content, document_id, operation_id, None, None, "unverified", "qualification",
    )
    identity = {"document_id": document_id, "operation_id": operation_id}
    metrics: dict[str, object] = {}
    stage = "health"
    try:
        health = bounded_service().health()
        metrics["health_status"] = MemoryStatus(health.status).value
        if health.status != MemoryStatus.OK:
            return {"stage": stage, "passed": False, "status": health.status.value}
        stage = "retain"
        started = time.monotonic()
        retained = bounded_service().submit_retention(bank_id, prepared)
        metrics["retain_submission_latency_seconds"] = max(0.0, time.monotonic() - started)
        indexing_started = time.monotonic()
        if retained.status != MemoryStatus.OK:
            return {"stage": stage, "passed": False, "status": MemoryStatus(retained.status).value, **identity}
        stage = "poll"
        while time.monotonic() < deadline:
            result = bounded_service().get_operation(bank_id, operation_id)
            if result.status != MemoryStatus.OK:
                return {"stage": stage, "passed": False, "status": MemoryStatus(result.status).value, **identity}
            if result.state == OperationState.COMPLETED:
                break
            if result.state in {OperationState.FAILED, OperationState.CANCELLED, OperationState.NOT_FOUND}:
                return {"stage": stage, "passed": False, "state": result.state.value, **identity}
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        else:
            return {"stage": stage, "passed": False, "state": "timeout", **identity}
        metrics["indexing_completion_latency_seconds"] = max(0.0, time.monotonic() - indexing_started)
        stage = "recall_identity"
        bounded = bounded_service()
        request = RecallRequest(
            query=f"Synthetic qualification marker {marker}",
            max_tokens=min(1024, bounded.config.recall_max_tokens),
            max_results=min(5, bounded.config.recall_max_results),
            source_fact_tokens=min(256, bounded.config.automatic_recall_source_facts_max_tokens),
            timeout_seconds=min(3.0, bounded.config.timeout_seconds),
        )
        started = time.monotonic()
        recall = bounded._provider.recall_detailed(bank_id, request)
        metrics["recall_latency_seconds"] = max(0.0, time.monotonic() - started)
        metrics["recall_returned_count"] = len(recall.memories)
        match = recall.status == MemoryStatus.OK and any(
            marker in item.text and any(
                provenance.document_id == document_id and provenance.repository_bank_id == bank_id
                for provenance in (item.provenance, *item.source_facts)
            )
            for item in recall.memories
        )
        metrics["recall_status"] = MemoryStatus(recall.status).value
        if match and include_reflect:
            stage = "reflect"
            bounded = bounded_service()
            # Provider-only check: no recall-policy or agent recovery behavior is involved.
            config = bounded.config.model_copy(update={
                "budget": "low", "recall_max_tokens": min(512, bounded.config.recall_max_tokens),
            })
            provider = bounded._provider
            if isinstance(provider, HindsightExperienceMemoryProvider):
                provider = HindsightExperienceMemoryProvider(config)
            started = time.monotonic()
            reflected = provider.reflect(bank_id, f"Explain only the synthetic qualification marker {marker}.")
            metrics["reflect_latency_seconds"] = max(0.0, time.monotonic() - started)
            metrics["reflect_status"] = MemoryStatus(reflected.status).value
            metrics["reflect_returned_count"] = int(bool(reflected.text))
            match = reflected.status == MemoryStatus.OK and bool(reflected.text and marker in reflected.text)
        return {
            "stage": "complete" if match else stage, "passed": match,
            **identity, **metrics,
        }
    except TimeoutError:
        return {"stage": stage, "passed": False, "status": "timeout", **identity, **metrics}
    except Exception:
        return {"stage": stage, "passed": False, "status": "unavailable", **identity, **metrics}


@memory_app.command("live-smoke")
def memory_live_smoke(
    bank_id: str = typer.Option(..., help="Existing repository bank ID."),
    timeout_seconds: float = typer.Option(60.0, min=1.0, max=300.0),
    include_reflect: bool = typer.Option(True, "--reflect/--no-reflect"),
) -> None:
    _, service = _service()
    if service.availability != MemoryStatus.OK:
        typer.echo(json.dumps({"passed": False, "stage": service.availability.value}))
        raise typer.Exit(code=1)
    result = run_live_smoke(service, bank_id, timeout_seconds=timeout_seconds, include_reflect=include_reflect)
    typer.echo(json.dumps(result, sort_keys=True))
    if not result["passed"]:
        raise typer.Exit(code=1)


@memory_app.command("evaluate")
def memory_evaluate(
    manifest: Path = typer.Option(..., exists=True, dir_okay=False),
    runner: str = typer.Option(..., help="Runner command that writes AGENT47_EVAL_METRICS JSON."),
    output: Path = typer.Option(Path("memory-eval-results.json")),
    timeout_seconds: float = typer.Option(600.0, min=1.0),
    trials: int = typer.Option(1, min=1, max=100),
    include_reflect: bool = typer.Option(False, "--include-reflect", help="Add C on a reviewed final-core external runner."),
) -> None:
    import shlex
    from .memory_eval import human_report, run_evaluation

    try:
        report = run_evaluation(
            manifest, shlex.split(runner), output, timeout_seconds=timeout_seconds, trials=trials,
            include_reflect=include_reflect,
        )
    except (OSError, ValueError):
        typer.echo("Evaluation failed: invalid manifest, fixture, or runner.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(human_report(report))
    typer.echo(f"JSON report: {output}")


@memory_app.command("evaluate-smoke")
def memory_evaluate_smoke(
    output: Path = typer.Option(Path("memory-smoke-results.json")),
    trials: int = typer.Option(1, min=1, max=100),
) -> None:
    import sys
    import tempfile
    from .memory_eval import human_report, run_evaluation
    from .memory_eval_runner import write_smoke_manifest

    with tempfile.TemporaryDirectory(prefix="agent47-p6-fixtures-") as temp:
        manifest = write_smoke_manifest(Path(temp))
        report = run_evaluation(
            manifest, [sys.executable, "-m", "code_agent.memory_eval_runner"],
            output.resolve(), trials=trials,
        )
    typer.echo(human_report(report))
    if any(arm["error"] for row in report["results"] for arm in row["arms"].values()):
        raise typer.Exit(code=1)


@memory_app.command("benchmark")
def memory_benchmark(
    output: Path = typer.Option(Path("memory-benchmark.json")),
    iterations: int = typer.Option(10000, min=1, max=100000),
    samples: int = typer.Option(7, min=3, max=30),
) -> None:
    from .memory_benchmark import benchmark_disabled

    result = benchmark_disabled(iterations=iterations, samples=samples)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    typer.echo(json.dumps(result, sort_keys=True))
