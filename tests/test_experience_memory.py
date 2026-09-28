from __future__ import annotations

import asyncio
import builtins
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from code_agent.config import Settings
from code_agent.experience_memory import (
    Experience, ExperienceMemoryConfig, ExperienceMemoryProvider, ExperienceMemoryService,
    MemoryResult, MemoryStatus, RecalledExperience, repository_scope,
)
from code_agent.experience_memory.providers.hindsight import HindsightExperienceMemoryProvider
from code_agent.experience_memory.providers.null import NullExperienceMemoryProvider
from code_agent.experience_memory.scope import normalize_origin

BANK = "agent47-repo-" + "a" * 64
KEY = "opaque-memory-credential"


@pytest.fixture(autouse=True)
def clean_memory_env(monkeypatch):
    import os

    for key in os.environ:
        if key.startswith("AGENT_EXPERIENCE_MEMORY_") or key.startswith("HINDSIGHT_"):
            monkeypatch.delenv(key)


def config(**kwargs):
    return ExperienceMemoryConfig(enabled=True, api_key=KEY, **kwargs)


@pytest.fixture
def sdk(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
            self.monitoring = self

        async def health_endpoint_health_get(self, **kwargs):
            calls.append(("health", kwargs))
            return {"status": "healthy"}

        async def arecall(self, **kwargs):
            calls.append(("recall", kwargs))
            return SimpleNamespace(results=[
                SimpleNamespace(text="Useful historical fix.", metadata={"secret": KEY}),
                SimpleNamespace(text="TOKEN=other-secret and " + KEY),
                SimpleNamespace(text="Third memory"),
            ])

        async def aretain(self, **kwargs):
            calls.append(("retain", kwargs))
            return SimpleNamespace(success=True, var_async=False)

        async def areflect(self, **kwargs):
            calls.append(("reflect", kwargs))
            return SimpleNamespace(text="Historical answer " + KEY)

        async def aclose(self):
            calls.append(("close", {}))

    monkeypatch.setattr(
        "code_agent.experience_memory.providers.hindsight.importlib.import_module",
        lambda name: SimpleNamespace(Hindsight=Client),
    )
    return Client, calls


def test_null_contract():
    provider = NullExperienceMemoryProvider()
    assert isinstance(provider, ExperienceMemoryProvider)
    for result in [provider.health(), provider.recall(BANK, "why"),
                   provider.retain(BANK, Experience("fix")), provider.reflect(BANK, "why")]:
        assert result.status == MemoryStatus.DISABLED
        assert not result.ok and result.untrusted
        assert result.memories == () and result.text == ""


@pytest.mark.parametrize("kwargs,status", [
    ({}, MemoryStatus.DISABLED),
    ({"enabled": True, "provider": "none"}, MemoryStatus.DISABLED),
    ({"enabled": True}, MemoryStatus.MISSING_CONFIG),
    ({"enabled": True, "deployment": "self_hosted"}, MemoryStatus.MISSING_CONFIG),
])
def test_disabled_and_missing_config_do_not_inspect_git_or_import(tmp_path, monkeypatch, kwargs, status):
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled memory must do no work")

    monkeypatch.setattr("code_agent.experience_memory.service.repository_scope", forbidden)
    monkeypatch.setattr(
        "code_agent.experience_memory.providers.hindsight.importlib.import_module", forbidden,
    )
    service = ExperienceMemoryService(ExperienceMemoryConfig(**kwargs), tmp_path)
    assert service.health().status == status
    assert service.recall("why").status == status
    assert service.reflect("why").status == status
    assert service.retain("A reviewed summary.").status == status


def test_missing_optional_dependency_is_safe(tmp_path, monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(KEY)

    monkeypatch.setattr(
        "code_agent.experience_memory.providers.hindsight.importlib.import_module", missing,
    )
    service = ExperienceMemoryService(config(), tmp_path)
    result = service.health()
    assert result.status == MemoryStatus.MISSING_DEPENDENCY
    assert KEY not in repr(result)


def test_sdk_translation_budgets_and_cleanup(sdk):
    _, calls = sdk
    provider = HindsightExperienceMemoryProvider(config(recall_max_results=2, recall_max_tokens=100))
    assert provider.health().ok
    recalled = provider.recall(BANK, "What worked?")
    assert recalled.ok and len(recalled.memories) == 2 and recalled.untrusted
    assert KEY not in str(recalled.memories)
    assert "other-secret" not in recalled.memories[1].text
    assert "[REDACTED]" in recalled.memories[1].text
    assert provider.retain(BANK, Experience("A verified fix.", "feature/a", "a" * 40)).ok
    reflected = provider.reflect(BANK, "Why?")
    assert reflected.ok and KEY not in reflected.text and reflected.untrusted
    assert len([x for x in calls if x[0] == "close"]) == 4
    init = calls[0][1]
    assert init["timeout"] == 10 and init["max_attempts"] == 1
    recall = next(x[1] for x in calls if x[0] == "recall")
    assert recall == {"bank_id": BANK, "query": "What worked?", "max_tokens": 100, "budget": "low"}
    retain = next(x[1] for x in calls if x[0] == "retain")
    assert retain["metadata"] == {
        "source": "agent47", "memory_kind": "historical_experience",
        "branch": "feature/a", "head": "a" * 40,
    }
    assert retain["retain_async"] is False


@pytest.mark.parametrize("method", ["health", "recall", "retain", "reflect"])
@pytest.mark.parametrize("error,status", [
    (RuntimeError(KEY), MemoryStatus.UNAVAILABLE),
    (TimeoutError(KEY), MemoryStatus.TIMEOUT),
])
def test_sdk_failures_safe_and_closed(sdk, monkeypatch, method, error, status):
    client, calls = sdk

    async def fail(self, **kwargs):
        raise error

    name = "health_endpoint_health_get" if method == "health" else "a" + method
    monkeypatch.setattr(client, name, fail)
    provider = HindsightExperienceMemoryProvider(config())
    args = () if method == "health" else (
        BANK, Experience("A fix.") if method == "retain" else "why",
    )
    result = getattr(provider, method)(*args)
    assert result.status == status and KEY not in repr(result)
    assert calls[-1][0] == "close"
    assert len([x for x in calls if x[0] == "init"]) == 1


def test_deadline_cancels_request_and_closes(sdk, monkeypatch):
    client, calls = sdk

    async def hang(self, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(client, "arecall", hang)
    result = HindsightExperienceMemoryProvider(config(timeout_seconds=0.01)).recall(BANK, "why")
    assert result.status == MemoryStatus.TIMEOUT
    assert calls[-1][0] == "close"


def test_existing_event_loop_fails_safely_without_import(sdk):
    _, calls = sdk

    async def run():
        return HindsightExperienceMemoryProvider(config()).health()

    assert asyncio.run(run()).status == MemoryStatus.UNAVAILABLE
    assert calls == []


@pytest.mark.parametrize("summary", [
    "API_KEY=secret", "TOKEN=secret", "PATH=/private", "export PATH=/private",
    "def example(): return 1", "```python code```", "one\ntwo", KEY,
    "x" * 1001, "", "-----BEGIN RSA PRIVATE KEY-----",
])
def test_unsafe_retention_never_reaches_sdk(sdk, summary):
    _, calls = sdk
    result = HindsightExperienceMemoryProvider(config()).retain(BANK, Experience(summary))
    assert result.status == MemoryStatus.INVALID_REQUEST
    assert calls == []


@pytest.mark.parametrize("query", [KEY, "API_KEY=secret", "x" * 2001, ""])
def test_unsafe_queries_never_reach_sdk(sdk, query):
    _, calls = sdk
    provider = HindsightExperienceMemoryProvider(config())
    assert provider.recall(BANK, query).status == MemoryStatus.INVALID_REQUEST
    assert provider.reflect(BANK, query).status == MemoryStatus.INVALID_REQUEST
    assert calls == []


def test_response_size_limit_and_malformed_response(sdk, monkeypatch):
    client, _ = sdk

    async def large(self, **kwargs):
        return SimpleNamespace(results=[SimpleNamespace(text="é" * 100)] * 100)

    monkeypatch.setattr(client, "arecall", large)
    result = HindsightExperienceMemoryProvider(config(recall_max_tokens=11)).recall(BANK, "why")
    assert result.ok and sum(len(x.text.encode()) for x in result.memories) <= 11

    async def malformed(self, **kwargs):
        return SimpleNamespace(results=None)

    monkeypatch.setattr(client, "arecall", malformed)
    assert HindsightExperienceMemoryProvider(config()).recall(BANK, "why").status == MemoryStatus.UNAVAILABLE


def test_unhealthy_and_unconfirmed_retain(sdk, monkeypatch):
    client, _ = sdk

    async def unhealthy(self, **kwargs):
        return {"status": "unhealthy", "error": KEY}

    async def unconfirmed(self, **kwargs):
        return SimpleNamespace(success=True, var_async=True)

    monkeypatch.setattr(client, "health_endpoint_health_get", unhealthy)
    monkeypatch.setattr(client, "aretain", unconfirmed)
    provider = HindsightExperienceMemoryProvider(config())
    assert provider.health().status == MemoryStatus.UNAVAILABLE
    assert provider.retain(BANK, Experience("A fix.")).status == MemoryStatus.UNAVAILABLE


def test_config_and_settings_secret_repr_and_export():
    settings = Settings(_env_file=None, hindsight_api_key=KEY)
    for obj in [settings, settings.experience_memory_config,
                HindsightExperienceMemoryProvider(config())]:
        assert KEY not in repr(obj)
    assert KEY not in settings.model_dump_json()
    assert "hindsight_api_key" not in settings.model_dump()
    assert "api_key" not in config().model_dump()


@pytest.mark.parametrize("kwargs", [
    {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"timeout_seconds": float("inf")}, {"recall_max_results": 0},
    {"recall_max_tokens": 16385}, {"provider": KEY}, {"budget": KEY},
    {"base_url": "https://user:" + KEY + "@example.com"},
    {"base_url": "https://example.com?api_key=" + KEY},
    {"base_url": "https://example.com#" + KEY},
    {"base_url": "http://example.com"}, {"base_url": "not a URL " + KEY},
])
def test_invalid_config_errors_do_not_echo_input(kwargs):
    with pytest.raises(ValidationError) as error:
        ExperienceMemoryConfig(**kwargs)
    assert KEY not in str(error.value) and KEY not in repr(error.value)


def test_cloud_and_self_hosted_config():
    assert config().endpoint == "https://api.hindsight.vectorize.io"
    local = ExperienceMemoryConfig(
        enabled=True, deployment="self_hosted", base_url="http://127.0.0.1:8888/", api_key="",
    )
    assert local.availability == MemoryStatus.OK and local.endpoint == "http://127.0.0.1:8888"
    with pytest.raises(ValidationError):
        ExperienceMemoryConfig(deployment="self_hosted", base_url="http://localhost", api_key=KEY)


def test_settings_env_parsing(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "AGENT_EXPERIENCE_MEMORY_ENABLED=true\n"
        "AGENT_EXPERIENCE_MEMORY_DEPLOYMENT=self_hosted\n"
        "HINDSIGHT_BASE_URL=http://localhost:8888\nHINDSIGHT_API_KEY=\n"
        "AGENT_EXPERIENCE_MEMORY_RECALL_MAX_RESULTS=3\n"
        "AGENT_EXPERIENCE_MEMORY_RECALL_MAX_TOKENS=100\n"
        "AGENT_EXPERIENCE_MEMORY_TIMEOUT_SECONDS=2.5\n"
        "AGENT_EXPERIENCE_MEMORY_BUDGET=mid\n", encoding="utf-8",
    )
    result = Settings(_env_file=env).experience_memory_config
    assert result.availability == MemoryStatus.OK
    assert result.recall_max_results == 3 and result.recall_max_tokens == 100
    assert result.timeout_seconds == 2.5 and result.budget == "mid"


@pytest.mark.parametrize("kwargs", [
    {"agent_experience_memory_timeout_seconds": 0},
    {"agent_experience_memory_provider": KEY},
    {"hindsight_base_url": "https://user:" + KEY + "@example.com"},
])
def test_settings_validation(kwargs):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, **kwargs)
    assert KEY not in str(error.value)


def git(path, *args):
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize("origin", [
    "https://github.com/Org/Repo.git/", "git@github.com:org/repo.git",
    "ssh://git@github.com:22/org/repo", "https://user:secret@github.com/org/repo?token=secret",
])
def test_origin_normalization(tmp_path, origin):
    assert normalize_origin(origin, tmp_path) == "remote:github.com/org/repo"


def test_repo_stability_branches_worktrees_and_fallback(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "initial")
    worktree = tmp_path / "tree"
    git(repo, "worktree", "add", "-b", "feature/test", str(worktree))
    fallback = repository_scope(repo)
    assert fallback.bank_id == repository_scope(repo).bank_id == repository_scope(worktree).bank_id
    assert fallback.branch != repository_scope(worktree).branch
    git(repo, "remote", "add", "origin", "git@github.com:org/repo.git")
    scope = repository_scope(repo)
    assert scope.bank_id == repository_scope(worktree).bank_id
    nested = repo / "nested"
    nested.mkdir()
    assert repository_scope(nested).bank_id == scope.bank_id
    assert str(repo) not in repr(scope) and "github" not in scope.bank_id
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init")
    git(other, "remote", "add", "origin", "https://github.com/org/different.git")
    assert repository_scope(other).bank_id != scope.bank_id
    git(other, "remote", "set-url", "origin", "https://github.com/org/repo.git")
    assert repository_scope(other).bank_id == scope.bank_id


def test_non_git_and_missing_git_fallback(tmp_path, monkeypatch):
    first = repository_scope(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    assert first.bank_id != repository_scope(child).bank_id

    def no_git(*args, **kwargs):
        raise FileNotFoundError(KEY)

    monkeypatch.setattr("code_agent.experience_memory.scope.subprocess.run", no_git)
    assert repository_scope(tmp_path) == first


def test_service_contains_third_party_provider_errors_and_redacts(tmp_path):
    class Provider(NullExperienceMemoryProvider):
        def health(self):
            raise RuntimeError(KEY)

        def recall(self, bank_id, query):
            return MemoryResult(MemoryStatus.OK, (RecalledExperience(KEY),), text="TOKEN=secret")

    service = ExperienceMemoryService(config(), tmp_path, provider=Provider())
    assert service.health().status == MemoryStatus.UNAVAILABLE
    result = service.recall("why")
    assert result.ok and result.memories[0].text == "[REDACTED]"
    assert KEY not in result.text and "secret" not in result.text


@pytest.mark.parametrize("enabled", [False, True])
def test_default_factory_startup_and_run_have_no_memory_operations(tmp_path, monkeypatch, enabled):
    from code_agent.factory import create_agent

    class Model:
        model = "test-model"

        def complete(self, messages):
            return '{"type":"final","message":"Hello."}'

    imported = builtins.__import__

    def guard(name, *args, **kwargs):
        assert not name.startswith("hindsight_client")
        return imported(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("P1 must not invoke memory operations")

    monkeypatch.setattr(builtins, "__import__", guard)
    for method in ["scope", "health", "recall", "retain", "reflect"]:
        monkeypatch.setattr(ExperienceMemoryService, method, forbidden)
    monkeypatch.setattr("code_agent.factory.create_fallback_client", lambda *args: Model())
    monkeypatch.setattr("code_agent.credentials.keyring.CredentialStore.get_provider_key", lambda *args: None)
    settings = Settings(
        _env_file=None, openrouter_api_key="model-key", agent_model="test-model",
        agent_db_path=tmp_path / "agent.db", agent_execution_db_path=tmp_path / "execution.db",
        agent_reviewer_pass=False, agent_stream=False,
        agent_experience_memory_enabled=enabled, hindsight_api_key=KEY,
    )
    agent = create_agent(settings, tmp_path, None, True, 2)
    assert agent.experience_memory is not None
    result = agent.run_detailed("Inspect repository")
    assert result.message == "Hello."
    assert agent.storage.get_work_report(result.run_id) is not None
    assert KEY.encode() not in (tmp_path / "agent.db").read_bytes()
    assert not (tmp_path / ".code-agent/memory/project.md").exists()


def test_cleanup_cannot_extend_deadline(sdk, monkeypatch):
    client, calls = sdk

    async def hang_close(self):
        calls.append(("close", {}))
        await asyncio.sleep(10)

    monkeypatch.setattr(client, "aclose", hang_close)
    result = HindsightExperienceMemoryProvider(config(timeout_seconds=0.01)).recall(BANK, "why")
    assert result.status == MemoryStatus.TIMEOUT
    assert calls[-1][0] == "close"


def test_metadata_rejects_credentials_before_dispatch(sdk):
    _, calls = sdk
    provider = HindsightExperienceMemoryProvider(config())
    assert provider.retain(BANK, Experience("A fix.", KEY)).status == MemoryStatus.INVALID_REQUEST
    assert provider.retain(BANK, Experience("A fix.", "feature", KEY)).status == MemoryStatus.INVALID_REQUEST
    assert calls == []


def test_empty_secretstr_is_missing_cloud_config():
    from pydantic import SecretStr

    assert ExperienceMemoryConfig(enabled=True, api_key=SecretStr("")).availability == MemoryStatus.MISSING_CONFIG
