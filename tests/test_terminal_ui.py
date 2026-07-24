import pytest
from typer._click.exceptions import Abort

import code_agent.interactive as interactive
from code_agent.agent import AgentRunResult
from code_agent.interactive import DEFAULT_DRY_RUN, read_prompt
from code_agent.interactive import format_model_selection_preview, is_persona_instruction
from code_agent.config import Settings
from code_agent.work_report import should_show_work_report
from code_agent.terminal_ui import (
    SessionHeader,
    format_status_line,
    print_error_card,
    print_session_header,
    print_response,
    print_work_report_panel,
    console,
)
from code_agent.status import StatusReporter


def test_format_work_report_body_produces_rich_panel() -> None:
    result = AgentRunResult(
        message="Updated docs and verified tests.",
        run_id=7,
        task="update the docs",
        changed_paths=["docs/PROGRESS.md"],
        mutation_records=[
            {
                "action": "edit_file",
                "path": "docs/PROGRESS.md",
                "ok": True,
            }
        ],
        verification_results=[
            {"purpose": "test", "command": "uv run pytest", "ok": True, "status": "passed"}
        ],
    )

    with console.capture() as capture:
        print_work_report_panel(result)

    text = capture.get()
    
    assert "Finished Work" in text
    assert "update the docs" not in text
    assert "Modified:" not in text
    assert "docs/PROGRESS.md" in text
    assert "uv run pytest" in text
    assert "Passed" in text


def test_work_report_renders_markdown_without_literal_markers() -> None:
    result = AgentRunResult(
        message=(
            "## Overview\n\n**Agent47** supports:\n\n"
            "- Repository intelligence\n"
            "- Transactional editing\n\n"
            "Run `agent47 --help` for usage."
        ),
        run_id=9,
        changed_paths=["README.md"],
        mutation_records=[{"action": "edit_file", "path": "README.md", "ok": True}],
    )

    with console.capture() as capture:
        print_work_report_panel(result)

    text = capture.get()
    assert "Overview" in text
    assert "Agent47 supports:" in text
    assert "Repository intelligence" in text
    assert "Transactional editing" in text
    assert "##" not in text
    assert "**" not in text


def test_print_response_renders_markdown_as_readable_terminal_content() -> None:
    body = "# Summary\n\n**Done**\n\n- First item\n- Second item\n\n`uv run pytest`"

    with console.capture() as capture:
        print_response("Agent47", body)

    text = capture.get()
    assert "Agent47" in text
    assert "Summary" in text
    assert "Done" in text
    assert "First item" in text
    assert "Second item" in text
    assert "uv run pytest" in text
    assert "# Summary" not in text
    assert "**Done**" not in text


def test_print_error_card_renders_rich_text() -> None:
    with console.capture() as capture:
        print_error_card(
            "Model Request Failed",
            [
                ("What failed:", "The model request could not be completed."),
                ("Reason:", "APIError: ResourceExhausted"),
            ],
            ["Switch models with /model"],
        )

    text = capture.get()
    assert "Model Request Failed" in text
    assert "ResourceExhausted" in text
    assert "Switch models with /model" in text


def test_should_show_work_report_stays_quiet_for_simple_chat() -> None:
    assert not should_show_work_report(AgentRunResult(message="hello", run_id=1))


def test_work_report_renders_managed_process_lifecycle() -> None:
    result = AgentRunResult(
        message="Development server is ready.",
        run_id=8,
        command_records=[
            {
                "kind": "managed_process",
                "action": "start_process",
                "status": "ok",
                "process": {
                    "process_id": "proc-demo",
                    "status": "ready",
                    "pid": 1234,
                    "detected_port": 5173,
                    "ready": True,
                },
            }
        ],
    )

    with console.capture() as capture:
        print_work_report_panel(result)

    text = capture.get()
    assert "Managed Processes" in text
    assert "proc-demo" in text
    assert "port=5173" in text


def test_interactive_mode_starts_write_enabled() -> None:
    assert DEFAULT_DRY_RUN is False


def test_interactive_onboarding_requires_login_before_starting(monkeypatch) -> None:
    class UnsignedSession:
        def signed_in(self) -> bool:
            return False

    monkeypatch.setattr(interactive.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(interactive, "LocalSession", UnsignedSession)
    monkeypatch.setattr(interactive.Prompt, "ask", lambda *_args, **_kwargs: "no")

    assert interactive.require_interactive_onboarding(Settings(_env_file=None)) is interactive._ONBOARDING_BLOCKED


def test_interactive_onboarding_runs_login_then_requires_secure_provider_key(monkeypatch) -> None:
    class Session:
        signed_in_count = 0

        def signed_in(self) -> bool:
            type(self).signed_in_count += 1
            return type(self).signed_in_count > 1

    class Store:
        keys: set[str] = set()

        def get_provider_key(self, provider: str) -> str | None:
            return "secure-key" if provider in type(self).keys else None

    prompts = iter(["yes", "1"])
    login_calls: list[bool] = []
    setup_calls: list[str] = []

    monkeypatch.setattr(interactive.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(interactive, "LocalSession", Session)
    monkeypatch.setattr(interactive, "CredentialStore", Store)
    monkeypatch.setattr(interactive.Prompt, "ask", lambda *_args, **_kwargs: next(prompts))
    monkeypatch.setattr(interactive, "_start_auth_login", lambda: login_calls.append(True))

    def add_key(provider) -> None:
        setup_calls.append(provider.name)
        Store.keys.add(provider.name)

    monkeypatch.setattr(interactive, "_start_key_setup", add_key)

    model = interactive.require_interactive_onboarding(Settings(_env_file=None))

    assert login_calls == [True]
    assert setup_calls == ["openai"]
    assert model == "gpt-4.1"


def test_read_prompt_propagates_click_abort(monkeypatch) -> None:
    monkeypatch.setattr(interactive.typer, "echo", lambda *_args, **_kwargs: None)

    class DummySession:
        def prompt(self, *args, **kwargs):
            raise Abort()

    with pytest.raises(Abort):
        read_prompt(DummySession())


def test_model_selection_preview_lists_presets_and_shortcut_note() -> None:
    preview = format_model_selection_preview("current-model")

    assert "Current model: current-model" in preview
    assert "qwen-coder" in preview
    assert "gemini-flash" in preview
    assert "Ctrl+M" in preview


def test_prompt_model_selection_accepts_number(monkeypatch) -> None:
    monkeypatch.setattr(interactive.Prompt, "ask", lambda *_args, **_kwargs: "2")

    selected = interactive.prompt_model_selection("current-model")

    assert selected == "gemini-3.5-flash"


def test_prompt_model_selection_accepts_preset_name(monkeypatch) -> None:
    monkeypatch.setattr(interactive.Prompt, "ask", lambda *_args, **_kwargs: "deepseek-pro")

    selected = interactive.prompt_model_selection("current-model")

    assert selected == "deepseek-v4-pro"


def test_current_model_name_uses_preset_or_override() -> None:
    settings = Settings(_env_file=None, agent_model_preset="glm-5.2", agent_model="fallback")

    assert interactive.current_model_name(settings, None) == "z-ai/glm-5.2"
    assert interactive.current_model_name(settings, "custom-model") == "custom-model"
    assert interactive.current_provider_name(settings, None) == "nvidia"
    assert interactive.model_display_name("z-ai/glm-5.2") == "glm-5.2"


def test_current_provider_name_uses_registered_agent_model_provider() -> None:
    settings = Settings(
        _env_file=None,
        agent_provider="nvidia",
        agent_model_preset=None,
        agent_model="qwen/qwen3-coder",
    )

    assert interactive.current_provider_name(settings, None) == "openrouter"


def test_model_choice_accepts_preset_names() -> None:
    assert interactive.resolve_model_choice("qwen-coder") == "qwen/qwen3-coder"
    assert interactive.resolve_model_choice("custom/model") == "custom/model"


def test_model_switch_validates_inferred_provider_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    missing_key = Settings(
        _env_file=None,
        agent_provider="nvidia",
        agent_model_preset=None,
        agent_model="z-ai/glm-5.2",
        nvidia_api_key="nvidia-key",
        openrouter_api_key=None,
    )
    ready = Settings(
        _env_file=None,
        agent_provider="nvidia",
        agent_model_preset=None,
        agent_model="z-ai/glm-5.2",
        nvidia_api_key="nvidia-key",
        openrouter_api_key="router-key",
    )

    assert "OPENROUTER_API_KEY is required" in (
        interactive.model_switch_error(missing_key, "qwen/qwen3-coder") or ""
    )
    assert interactive.model_switch_error(ready, "qwen/qwen3-coder") is None


def test_persist_model_selection_updates_non_secret_env_fields(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "AGENT_PROVIDER=nvidia\n"
        "AGENT_MODEL_PRESET=glm-5.2\n"
        "NVIDIA_API_KEY=keep-this-secret\n"
        "AGENT_MODEL=z-ai/glm-5.2\n",
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        agent_provider="nvidia",
        agent_model_preset="glm-5.2",
        agent_model="z-ai/glm-5.2",
    )

    interactive.persist_model_selection(settings, "gemini-3.5-flash", env_path=env_path)

    text = env_path.read_text(encoding="utf-8")
    assert "AGENT_PROVIDER=gemini" in text
    assert "AGENT_MODEL_PRESET=gemini-flash" in text
    assert "AGENT_MODEL=gemini-3.5-flash" in text
    assert "NVIDIA_API_KEY=keep-this-secret" in text
    assert settings.agent_provider == "gemini"
    assert settings.agent_model_preset == "gemini-flash"
    assert settings.agent_model == "gemini-3.5-flash"


def test_persist_custom_model_clears_preset_and_keeps_provider(tmp_path) -> None:
    env_path = tmp_path / ".env"
    settings = Settings(
        _env_file=None,
        agent_provider="gemini",
        agent_model_preset="gemini-flash",
        agent_model="gemini-3.5-flash",
    )

    interactive.persist_model_selection(settings, "custom/model", env_path=env_path)

    text = env_path.read_text(encoding="utf-8")
    assert "AGENT_PROVIDER=gemini" in text
    assert "AGENT_MODEL_PRESET=\n" in text
    assert "AGENT_MODEL=custom/model" in text
    assert settings.agent_model_preset is None


def test_interactive_prompt_contains_repo_model_and_profile(tmp_path) -> None:
    settings = Settings(_env_file=None, agent_model_preset="glm-5.2", agent_profile="coder")

    prompt = interactive.format_interactive_prompt(settings, tmp_path, None, None)

    assert prompt.startswith(f"agent47({tmp_path.name})")
    assert "[glm-5.2|coder]" in prompt


def test_model_command_without_value_reports_current_model(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        agent_model_preset="glm-5.2",
        agent_model="fallback",
        agent_db_path=tmp_path / "agent.db",
    )

    state = interactive.handle_command(
        "/model",
        settings,
        tmp_path,
        tmp_path,
        model=None,
        profile=None,
        dry_run=False,
        stream_model=True,
        sandbox_enabled=False,
        max_steps=12,
        max_failures=None,
    )

    assert state.model is None


def test_model_selection_table_shows_capabilities() -> None:
    table = interactive.build_model_selection_table("z-ai/glm-5.2")

    columns = [column.header for column in table.columns]
    assert "Ctx" in columns
    assert "Caps" in columns
    assert "Score" in columns
    assert "Best for" not in columns


def test_compact_model_labels_are_readable() -> None:
    model = interactive.REGISTERED_MODELS["deepseek-flash"]

    assert interactive.compact_capabilities(model.capabilities) == "T S U"
    assert interactive.compact_model_score(model) == "Q4 F5 $5"


def test_stop_command_requests_exit_between_turns(tmp_path) -> None:
    settings = Settings(_env_file=None, agent_db_path=tmp_path / "agent.db")

    state = interactive.handle_command(
        "/stop",
        settings,
        tmp_path,
        tmp_path,
        model=None,
        profile=None,
        dry_run=False,
        stream_model=True,
        sandbox_enabled=False,
        max_steps=12,
        max_failures=None,
    )

    assert state.exit_requested is True


def test_read_interactive_line_uses_plain_input_when_not_tty(monkeypatch) -> None:
    monkeypatch.setattr(interactive.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt: f"typed after {prompt}")

    assert interactive.read_interactive_line("prompt> ") == "typed after prompt> "


def test_steer_command_updates_session_state(tmp_path) -> None:
    settings = Settings(_env_file=None, agent_provider="openrouter", agent_db_path=tmp_path / "agent.db")
    session_state = interactive.SessionState()

    interactive.handle_command(
        "/steer be concise and ask before broad refactors",
        settings,
        tmp_path,
        tmp_path,
        model=None,
        profile=None,
        dry_run=False,
        stream_model=True,
        sandbox_enabled=False,
        max_steps=12,
        max_failures=None,
        session_state=session_state,
    )

    assert session_state.conversation_steering == "be concise and ask before broad refactors"

    interactive.handle_command(
        "/steer clear",
        settings,
        tmp_path,
        tmp_path,
        model=None,
        profile=None,
        dry_run=False,
        stream_model=True,
        sandbox_enabled=False,
        max_steps=12,
        max_failures=None,
        session_state=session_state,
    )

    assert session_state.conversation_steering is None


def test_lightweight_chat_prompt_avoids_workspace_work() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.messages = []

        def complete(self, messages):
            self.messages = messages
            return "hi"

    client = FakeClient()

    assert interactive.run_lightweight_chat("hii", client) == "hi"
    assert "Do not inspect files" in client.messages[0]["content"]
    assert client.messages[1] == {"role": "user", "content": "hii"}


def test_friendly_model_error_summarizes_provider_capacity() -> None:
    message = interactive.friendly_model_error(
        RuntimeError("ResourceExhausted: Worker local total request limit reached (32/32)")
    )

    assert "capacity-limited" in message
    assert "RuntimeError" in message


def test_model_failure_result_uses_error_card(monkeypatch) -> None:
    calls = []
    result = AgentRunResult(
        message="Stopped after a model failure",
        run_id=12,
        failed_actions=[
            {
                "type": "model_failure",
                "output": "APIError: ResourceExhausted: Worker local total request limit reached (32/32)",
            }
        ],
        blocked=True,
    )

    monkeypatch.setattr(
        interactive,
        "print_error_card",
        lambda title, lines, suggestions: calls.append((title, lines, suggestions)),
    )

    assert interactive.is_model_failure_result(result)
    interactive.print_model_failure_card(result)

    assert calls[0][0] == "Model Request Failed"
    assert "capacity-limited" in calls[0][1][1][1]
    assert "Switch models with /model" in calls[0][2]


def test_status_line_and_header_are_compact() -> None:
    line = format_status_line(
        model="glm-5.2",
        provider="nvidia",
        approval="auto_read",
        sandbox=True,
        git_status="Clean",
        tokens="8.4k",
        cost="$0.02",
        elapsed="14s",
    )

    assert "glm-5.2 | nvidia | 8.4k tokens | $0.02 | 14s | Clean" in line
    assert "Sandbox ON" in line

    with console.capture() as capture:
        print_session_header(
            SessionHeader(
                version="0.1.0",
                profile="coder",
                model="glm-5.2",
                provider="nvidia",
                workspace="repo",
                sandbox="enabled",
                approval="auto_read",
                git_branch="main",
            ),
            mode="write-enabled",
        )

    rendered = capture.get()
    assert "AGENT47" in rendered or "___  ____" in rendered or "/ _ \\" in rendered
    assert "Tips for getting started" in rendered
    assert "1. Ask questions" in rendered


def test_status_reporter_uses_single_thinking_spinner(monkeypatch) -> None:
    class FakeLive:
        def __init__(self, *args, **kwargs) -> None:
            self.renderable = None
            self.stopped = False

        def start(self) -> None:
            pass

        def update(self, renderable) -> None:
            self.renderable = renderable

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr("code_agent.status.Live", FakeLive)
    reporter = StatusReporter()

    reporter.thinking(1)
    with console.capture() as capture:
        console.print(reporter._render())
    rendered = capture.get()

    assert "Thinking" in rendered
    assert "Generating output" not in rendered
    assert "Gathering context" not in rendered


def test_status_reporter_done_clears_live_state(monkeypatch) -> None:
    class FakeLive:
        def __init__(self, *args, **kwargs) -> None:
            self.stopped = False
            self.update_count = 0

        def start(self) -> None:
            pass

        def update(self, _renderable) -> None:
            self.update_count += 1

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr("code_agent.status.Live", FakeLive)
    reporter = StatusReporter()

    reporter.thinking(1)
    reporter.model_stream_start(1)
    reporter.model_stream_end()
    reporter.done()
    reporter.done()

    assert reporter._live.stopped is True
    assert reporter._current_label == ""
    assert reporter._current_detail == ""
    assert reporter._is_generating is False
    assert reporter._stages == []


def test_status_reporter_suspends_live_display_while_prompting(monkeypatch) -> None:
    live_instances = []

    class FakeLive:
        def __init__(self, *args, **kwargs) -> None:
            self.started = False
            self.stopped = False
            live_instances.append(self)

        def start(self) -> None:
            self.started = True

        def update(self, _renderable) -> None:
            pass

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr("code_agent.status.Live", FakeLive)
    reporter = StatusReporter()

    def prompt() -> str:
        assert reporter._paused is True
        assert live_instances[0].stopped is True
        return "y"

    guarded = reporter.guard_prompt(prompt)

    assert guarded() == "y"
    assert reporter._paused is False
    assert len(live_instances) == 2
    assert live_instances[1].started is True


def test_status_reporter_restores_live_display_after_prompt_error(monkeypatch) -> None:
    live_instances = []

    class FakeLive:
        def __init__(self, *args, **kwargs) -> None:
            live_instances.append(self)

        def start(self) -> None:
            pass

        def update(self, _renderable) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr("code_agent.status.Live", FakeLive)
    reporter = StatusReporter()

    def prompt() -> str:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        reporter.guard_prompt(prompt)()

    assert reporter._paused is False
    assert len(live_instances) == 2


def test_status_reporter_action_uses_one_live_update(monkeypatch) -> None:
    class FakeLive:
        def __init__(self, *args, **kwargs) -> None:
            self.update_count = 0

        def start(self) -> None:
            pass

        def update(self, _renderable) -> None:
            self.update_count += 1

        def stop(self) -> None:
            pass

    monkeypatch.setattr("code_agent.status.Live", FakeLive)
    reporter = StatusReporter()
    reporter._current_label = "Thinking"

    from code_agent.schema import ReadFileAction

    reporter.action(ReadFileAction(type="read_file", path="README.md"))

    assert reporter._live.update_count == 1


def test_is_persona_instruction() -> None:
    assert is_persona_instruction("You are a senior frontend engineer")
    assert is_persona_instruction("Act as a concise reviewer")
    assert not is_persona_instruction("You are a senior frontend engineer; update the app")
