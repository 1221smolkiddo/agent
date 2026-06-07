from code_agent.schema import LocalTimeAction
from code_agent.time_tools import local_time_for
from code_agent.tools import ToolRegistry


def test_local_time_for_known_city() -> None:
    output = local_time_for("Kyoto")

    assert output.startswith("Kyoto:")
    assert "Asia/Tokyo" in output


def test_local_time_for_common_typo() -> None:
    output = local_time_for("wuhsn")

    assert output.startswith("Wuhan:")
    assert "Asia/Shanghai" in output


def test_local_time_tool_returns_time_without_permission(tmp_path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: False)

    result = tools.run(LocalTimeAction(type="local_time", location="Chhongcbin"))

    assert result.ok
    assert result.output.startswith("Chongqing:")
    assert "Asia/Shanghai" in result.output
