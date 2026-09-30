from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

from code_agent.repo_index import BackgroundIndexRefresh, RepoIndexCache
from code_agent.storage import AgentStorage


def test_history_writes_and_repeated_index_refreshes_use_independent_databases(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "app.py").write_text("def app(): return 1\n", encoding="utf-8")
    storage = AgentStorage(workspace / ".code-agent" / "agent.db")
    cache = RepoIndexCache(workspace / ".code-agent" / "repo-index.db")
    run = storage.create_run("build app", "fake", workspace)
    assert storage.db_path != cache.db_path
    errors = []
    monkey_cache = RepoIndexCache(cache.db_path)
    worker = BackgroundIndexRefresh(workspace, cache, interval_seconds=0.01).start()
    def history():
        for index in range(40):
            storage.add_step(run, "tool", {"type": "test_event", "index": index})
            worker.request_refresh(["app.py"])
    def refresh(selected):
        for _ in range(12):
            selected.refresh(workspace)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for future in [pool.submit(history), pool.submit(refresh, cache), pool.submit(refresh, monkey_cache)]:
                try:
                    future.result(timeout=30)
                except Exception as exc:
                    errors.append(type(exc).__name__)
        assert worker.last_error is None
    finally:
        worker.stop()
    assert errors == []
    assert len(storage.run_steps(run)) == 40
    with sqlite3.connect(cache.db_path) as connection:
        assert connection.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_background_failure_is_contained_retried_and_never_leaks_traceback(tmp_path, monkeypatch, capsys):
    succeeded = threading.Event()
    leaked = []
    monkeypatch.setattr(threading, "excepthook", lambda event: leaked.append(event))
    class Cache:
        calls = 0
        def refresh(self, workspace):
            self.calls += 1
            if self.calls == 1:
                raise sqlite3.OperationalError("database is locked API_KEY=private-value")
            succeeded.set()
        def invalidate(self, workspace, paths):
            raise sqlite3.OperationalError("database is locked")
    cache = Cache()
    worker = BackgroundIndexRefresh(tmp_path, cache, interval_seconds=0.05).start()
    try:
        worker.request_refresh(["app.py"])
        assert succeeded.wait(3)
        assert worker._thread.is_alive()
        assert cache.calls >= 2
    finally:
        worker.stop()
    assert leaked == []
    output = capsys.readouterr()
    assert "Traceback" not in output.out + output.err
    assert "private-value" not in str(worker.last_error) + output.out + output.err


def test_cache_failures_do_not_break_scan_graph_or_completed_mutation(tmp_path):
    from code_agent.repo_index import build_project_graph, index_repo
    from code_agent.schema import WriteFileAction
    from code_agent.tools import ToolRegistry

    (tmp_path / "app.py").write_text("def app(): return 1\n", encoding="utf-8")
    class Cache:
        def load_workspace(self, workspace):
            raise sqlite3.OperationalError("locked")
        def load_edges(self, workspace):
            raise sqlite3.OperationalError("locked")
        def save_workspace(self, *args, **kwargs):
            raise sqlite3.OperationalError("locked")
        def invalidate(self, *args):
            raise sqlite3.OperationalError("locked")
    cache = Cache()
    assert any(f.path == "app.py" for f in index_repo(tmp_path, cache=cache))
    assert any(f.path == "app.py" for f in build_project_graph(tmp_path, cache=cache).files)
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, index_cache=cache,
                         approval_callback=lambda *_: True)
    result = tools.run(WriteFileAction(type="write_file", path="new.py", content="print(1)"))
    assert result.ok
    assert (tmp_path / "new.py").read_text() == "print(1)"


def test_factory_cache_initialization_failure_preserves_authoritative_history(tmp_path, monkeypatch):
    import pytest
    from code_agent.config import Settings
    from code_agent.factory import create_agent
    from test_agent_recovery import FakeModel

    def fail_cache(path):
        assert path == tmp_path / ".code-agent/repo-index.db"
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr("code_agent.factory.RepoIndexCache", fail_cache)
    monkeypatch.setattr("code_agent.factory.create_fallback_client", lambda *args: FakeModel([
        '{"type":"final","message":"Project inspected."}'
    ]))
    settings = Settings(_env_file=None, agent_db_path=tmp_path / ".code-agent/agent.db",
                        agent_fallback_models="", agent_reviewer_pass=False, agent_stream=False)
    with pytest.warns(RuntimeWarning, match="no fallback model"):
        agent = create_agent(settings=settings, cwd=tmp_path, model=None, dry_run=True, max_steps=2)
    assert agent.tools.index_cache is None
    result = agent.run_detailed("inspect project")
    assert not result.blocked
    assert agent.storage.run_steps(result.run_id)
