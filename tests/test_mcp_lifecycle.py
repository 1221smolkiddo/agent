import pytest
import json
from unittest.mock import MagicMock

from code_agent.mcp import McpManager, McpServerConfig, McpStdioClient, McpError
from code_agent.platform_runtime import PlatformRuntime
from code_agent.extensions import DynamicToolRegistry


@pytest.fixture
def registry():
    return DynamicToolRegistry()


@pytest.fixture
def manager(registry):
    return McpManager(registry)


@pytest.fixture
def basic_config():
    return McpServerConfig(
        name="test_server",
        command=("dummy",),
        enabled=True,
    )


def test_start_server(manager, basic_config, monkeypatch):
    manager.add(basic_config)
    
    connect_mock = MagicMock(return_value={"capabilities": {}, "serverInfo": {}})
    register_tools_mock = MagicMock(return_value=["tool1", "tool2"])
    
    monkeypatch.setattr(McpStdioClient, "connect", connect_mock)
    monkeypatch.setattr(McpStdioClient, "register_tools", register_tools_mock)
    
    result = manager.start("test_server")
    assert result["server"] == "test_server"
    assert "handshake" in result
    assert result["tools"] == ["tool1", "tool2"]
    
    connect_mock.assert_called_once()
    register_tools_mock.assert_called_once()


def test_duplicate_start(manager, basic_config, monkeypatch):
    client = manager.add(basic_config)
    
    connect_mock = MagicMock(return_value={})
    register_tools_mock = MagicMock(return_value=[])
    monkeypatch.setattr(McpStdioClient, "connect", connect_mock)
    monkeypatch.setattr(McpStdioClient, "register_tools", register_tools_mock)
    client.process = MagicMock()
    client.process.poll.return_value = None
    
    with pytest.raises(ValueError, match="already running"):
        manager.start("test_server")


def test_start_failure(manager, basic_config, monkeypatch):
    manager.add(basic_config)
    
    def mock_connect(*args, **kwargs):
        raise McpError("failed to start")
        
    monkeypatch.setattr(McpStdioClient, "connect", mock_connect)
    
    with pytest.raises(McpError, match="failed to start"):
        manager.start("test_server")


def test_stop_server(manager, basic_config, monkeypatch):
    client = manager.add(basic_config)
    client.process = MagicMock()
    client.process.poll.return_value = None
    
    close_mock = MagicMock()
    monkeypatch.setattr(client, "close", close_mock)
    
    manager.registry.register = MagicMock()
    manager.registry.discover = MagicMock(return_value=[{"name": "mcp-test-server.tool1"}])
    manager.registry.unregister = MagicMock()
    
    was_connected = manager.stop("test_server")
    assert was_connected is True
    
    close_mock.assert_called_once()
    manager.registry.unregister.assert_called_once_with("mcp-test-server.tool1")


def test_duplicate_stop(manager, basic_config, monkeypatch):
    client = manager.add(basic_config)
    client.process = None
    
    close_mock = MagicMock()
    monkeypatch.setattr(client, "close", close_mock)
    
    was_connected = manager.stop("test_server")
    assert was_connected is False


def test_restart_server(manager, basic_config, monkeypatch):
    manager.add(basic_config)
    
    stop_mock = MagicMock()
    start_mock = MagicMock(return_value={"server": "test_server"})
    monkeypatch.setattr(manager, "stop", stop_mock)
    monkeypatch.setattr(manager, "start", start_mock)
    
    result = manager.restart("test_server")
    assert result["server"] == "test_server"
    
    stop_mock.assert_called_once_with("test_server")
    start_mock.assert_called_once_with("test_server")


def test_reload_reconcile(manager, monkeypatch):
    config_a1 = McpServerConfig("server_a", ("cmd1",))
    config_a2 = McpServerConfig("server_a", ("cmd2",))
    config_b = McpServerConfig("server_b", ("cmd",))
    config_c = McpServerConfig("server_c", ("cmd",))
    config_d = McpServerConfig("server_d", ("cmd",))
    
    client_a = manager.add(config_a1)
    client_b = manager.add(config_b)
    client_d = manager.add(config_d)
    
    for client in (client_a, client_b, client_d):
        client.process = MagicMock()
        client.process.poll.return_value = None
    
    stop_mock = MagicMock()
    monkeypatch.setattr(manager, "stop", stop_mock)
    
    new_configs = {
        "server_a": config_a2,
        "server_c": config_c,
        "server_d": config_d,
    }
    
    result = manager.reconcile(new_configs)
    
    assert result.added == ["server_c"]
    assert result.removed == ["server_b"]
    assert result.modified == ["server_a"]
    assert result.unchanged == ["server_d"]
    
    stop_mock.assert_any_call("server_a")
    stop_mock.assert_any_call("server_b")
    assert stop_mock.call_count == 2
    
    assert manager.clients["server_d"] is client_d
    
    assert manager.clients["server_a"] is not client_a
    assert manager.clients["server_a"].config.command == ("cmd2",)
    
    assert "server_c" in manager.clients
    assert "server_b" not in manager.clients


def test_reload_rollback_on_failure(manager, monkeypatch):
    config_a = McpServerConfig("server_a", ("cmd",))
    client_a = manager.add(config_a)
    
    config_b = McpServerConfig("server_b", ("cmd",))
    new_configs = {
        "server_a": config_a,
        "server_b": config_b,
    }
    
    def mock_add(*args, **kwargs):
        raise RuntimeError("simulated failure")
        
    monkeypatch.setattr(manager, "add", mock_add)
    
    with pytest.raises(RuntimeError, match="simulated failure"):
        manager.reconcile(new_configs)
        
    assert list(manager.clients.keys()) == ["server_a"]
    assert manager.clients["server_a"] is client_a


def test_platform_runtime_reload_malformed(tmp_path):
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text("invalid json")
    
    runtime = PlatformRuntime.create(tmp_path)
    
    with pytest.raises(json.JSONDecodeError):
        runtime.reload_mcp()


def test_platform_runtime_reload_invalid_schema(tmp_path):
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "mcp.json").write_text(json.dumps({"servers": ["not a dict"]}))
    
    runtime = PlatformRuntime.create(tmp_path)
    
    with pytest.raises(ValueError, match="servers must be an object"):
        runtime.reload_mcp()

