# Runtime timeout source inventory

Generated for the long-lived runtime implementation on 2026-09-29. This replaces the original 300-second audit inventory.
Scope: all tracked production Python sources. Matches include declarations, error handling, comments and inactive metadata; a match is not proof of an active timer.
Normal overall run timeouts are removed. The deprecated setting/argument are accepted but ignored. See PRE_RELEASE_RUNTIME_AUDIT.md for active operation-level semantics.

Production source references: 422.

| Source | Enclosing scope | Expression |
|---|---|---|
| `src/code_agent/agent.py:88` | `__init__` | `model_timeout_seconds: float = 60.0,` |
| `src/code_agent/agent.py:89` | `__init__` | `run_timeout_seconds: float &#124; None = None,` |
| `src/code_agent/agent.py:115` | `__init__` | `self.model_timeout_seconds = model_timeout_seconds` |
| `src/code_agent/agent.py:116` | `__init__` | `self.run_timeout_seconds = run_timeout_seconds  # Deprecated; deliberately ignored.` |
| `src/code_agent/agent.py:340` | `_run_detailed` | `timeout_seconds=self.model_timeout_seconds,` |
| `src/code_agent/agent.py:800` | `_run_detailed` | `timeout_seconds=self.model_timeout_seconds,` |
| `src/code_agent/agent.py:1197` | `_complete_model` | `timeout_seconds: float,` |
| `src/code_agent/agent.py:1228` | `checked` | `if perf_counter() - started >= timeout_seconds:` |
| `src/code_agent/agent.py:1229` | `checked` | `raise TimeoutError("Model request timed out; late response discarded.")` |
| `src/code_agent/agent.py:1238` | `_complete_model` | `complete_with_timeout = getattr(self.model_client, "complete_with_timeout", None)` |
| `src/code_agent/agent.py:1239` | `_complete_model` | `if complete_with_timeout is not None:` |
| `src/code_agent/agent.py:1240` | `_complete_model` | `return checked(complete_with_timeout(messages, timeout_seconds))` |
| `src/code_agent/agent.py:1246` | `_complete_model` | `stream_with_timeout = getattr(self.model_client, "stream_complete_with_timeout", None)` |
| `src/code_agent/agent.py:1247` | `_complete_model` | `if stream_with_timeout is not None:` |
| `src/code_agent/agent.py:1248` | `_complete_model` | `return checked(stream_with_timeout(` |
| `src/code_agent/agent.py:1251` | `_complete_model` | `timeout_seconds,` |
| `src/code_agent/agent.py:1317` | `_reflection_recovery_context` | `if remaining_seconds <= min(config.timeout_seconds, config.automatic_reflect_timeout_seconds):` |
| `src/code_agent/auth/config.py:16` | `GoogleOAuthConfig` | `timeout_seconds: float = 180.0` |
| `src/code_agent/auth/config.py:28` | `from_environment` | `raw_timeout = os.environ.get("GOOGLE_OAUTH_TIMEOUT_SECONDS", "180")` |
| `src/code_agent/auth/config.py:30` | `from_environment` | `timeout = float(raw_timeout)` |
| `src/code_agent/auth/config.py:32` | `from_environment` | `raise OAuthConfigurationError("GOOGLE_OAUTH_TIMEOUT_SECONDS must be a number.") from exc` |
| `src/code_agent/auth/config.py:33` | `from_environment` | `if not 1 <= timeout <= 900:` |
| `src/code_agent/auth/config.py:34` | `from_environment` | `raise OAuthConfigurationError("GOOGLE_OAUTH_TIMEOUT_SECONDS must be between 1 and 900.")` |
| `src/code_agent/auth/config.py:41` | `from_environment` | `return cls(client_id=client_id, client_secret=secret, timeout_seconds=timeout)` |
| `src/code_agent/auth/google.py:57` | `login` | `4. Wait for the callback with timeout.` |
| `src/code_agent/auth/google.py:85` | `login` | `callback = callback_server.wait(self.config.timeout_seconds)` |
| `src/code_agent/auth/oauth.py:105` | `fetch_profile` | `with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed Google endpoint` |
| `src/code_agent/auth/oauth.py:140` | `_post_token` | `with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed Google endpoint` |
| `src/code_agent/cli.py:512` | `keys_add_command` | `submission = server.wait(timeout_seconds=300.0)` |
| `src/code_agent/cli.py:1812` | `processes_start_command` | `timeout_seconds: int = typer.Option(0, "--timeout", min=0, help="Zero disables timeout."),` |
| `src/code_agent/cli.py:1829` | `processes_start_command` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/collaboration.py:755` | `_git` | `timeout=60,` |
| `src/code_agent/command_diagnostics.py:637` | `_execution_diagnostics` | `message=f"Command timed out after {execution.get('timeout_seconds', '?')} seconds.",` |
| `src/code_agent/command_diagnostics.py:638` | `_execution_diagnostics` | `raw="timeout",` |
| `src/code_agent/command_diagnostics.py:979` | `_root_cause` | `return "The process exceeded its configured timeout before completing."` |
| `src/code_agent/command_registry.py:485` | `build_default_registry` | `rank=300,` |
| `src/code_agent/config.py:51` | `Settings` | `agent_model_timeout_seconds: float = 60.0` |
| `src/code_agent/config.py:53` | `Settings` | `agent_run_timeout_seconds: float &#124; None = Field(default=None, ge=0, deprecated=True)` |
| `src/code_agent/config.py:57` | `Settings` | `agent_model_retry_max_seconds: float = Field(default=4.0, ge=0, le=300)` |
| `src/code_agent/container_manager.py:206` | `ensure_running` | `timeout=30,` |
| `src/code_agent/container_manager.py:293` | `terminate_exec` | `timeout=5,` |
| `src/code_agent/container_manager.py:346` | `resolve_command` | `timeout=5,` |
| `src/code_agent/container_manager.py:439` | `stop` | `timeout=15,` |
| `src/code_agent/container_manager.py:566` | `_inspect` | `timeout=10,` |
| `src/code_agent/container_manager.py:591` | `_remove` | `timeout=15,` |
| `src/code_agent/credentials/providers.py:67` | `validate_provider_key` | `with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed provider endpoint or explicit user URL` |
| `src/code_agent/credentials/providers.py:68` | `validate_provider_key` | `if 200 <= response.status < 300:` |
| `src/code_agent/doctor.py:284` | `_check_model_deadlines` | `f"turn={settings.agent_model_timeout_seconds:g}s; no overall run timeout; Agent47-owned retries",` |
| `src/code_agent/doctor.py:587` | `_check_provider_reachability` | `sock = socket.create_connection((host, 443), timeout=5)` |
| `src/code_agent/doctor.py:590` | `_check_provider_reachability` | `except (socket.timeout, OSError):` |
| `src/code_agent/durable_execution.py:372` | `_connect` | `conn = sqlite3.connect(self.path, timeout=30)` |
| `src/code_agent/durable_execution.py:375` | `_connect` | `conn.execute("pragma busy_timeout=30000")` |
| `src/code_agent/eval_reports.py:535` | `_case_traces` | `or failure_class in {"provider_blocked", "safety_blocked", "timeout"},` |
| `src/code_agent/eval_reports.py:644` | `_classify_case_failure` | `if category == "timeout" or "timed out" in lowered or "timeout" in lowered:` |
| `src/code_agent/eval_reports.py:645` | `_classify_case_failure` | `return "timeout"` |
| `src/code_agent/eval_reports.py:661` | `_case_diagnostic` | `if failure_class == "timeout":` |
| `src/code_agent/evals.py:464` | `live_eval_cases` | `task="Add a changelog entry for the new timeout feature based on the code and tests.",` |
| `src/code_agent/evals.py:467` | `live_eval_cases` | `"timeouts.py": "DEFAULT_TIMEOUT_SECONDS = 30\n",` |
| `src/code_agent/evals.py:468` | `live_eval_cases` | `"tests/test_timeouts.py": (` |
| `src/code_agent/evals.py:469` | `live_eval_cases` | `"from timeouts import DEFAULT_TIMEOUT_SECONDS\n\n"` |
| `src/code_agent/evals.py:470` | `live_eval_cases` | `"def test_default_timeout():\n"` |
| `src/code_agent/evals.py:471` | `live_eval_cases` | `"    assert DEFAULT_TIMEOUT_SECONDS == 30\n"` |
| `src/code_agent/evals.py:475` | `live_eval_cases` | `file_contains("CHANGELOG.md", "timeout"),` |
| `src/code_agent/evals.py:1486` | `validate` | `timeout=60,` |
| `src/code_agent/evals.py:1541` | `_failure_category` | `if "timeout" in text or "timed out" in text:` |
| `src/code_agent/evals.py:1542` | `_failure_category` | `return "timeout"` |
| `src/code_agent/execution_adapters.py:245` | `__init__` | `timeout_support=True, streaming=True, concurrency_safe=True,` |
| `src/code_agent/execution_adapters.py:279` | `run` | `timeout_seconds: float = 30,` |
| `src/code_agent/execution_adapters.py:287` | `run` | `idempotency_key, timeout_seconds, requirement.permissions,` |
| `src/code_agent/execution_contracts.py:39` | `AdapterCapabilities` | `timeout_support: bool = True` |
| `src/code_agent/execution_contracts.py:70` | `supports` | `if requirement.timeout_support and not self.timeout_support:` |
| `src/code_agent/execution_contracts.py:71` | `supports` | `missing.append("timeout support")` |
| `src/code_agent/execution_contracts.py:96` | `CapabilityRequirement` | `timeout_support: bool = True` |
| `src/code_agent/execution_contracts.py:107` | `AdapterContext` | `timeout_seconds: float` |
| `src/code_agent/execution_host.py:125` | `__init__` | `timeout_seconds: float = 60,` |
| `src/code_agent/execution_host.py:129` | `__init__` | `self.timeout_seconds = timeout_seconds` |
| `src/code_agent/execution_host.py:171` | `plan_with_context` | `+ context.repository_context[:3000]` |
| `src/code_agent/execution_host.py:174` | `plan_with_context` | `complete_with_timeout = getattr(self.client, "complete_with_timeout", None)` |
| `src/code_agent/execution_host.py:176` | `plan_with_context` | `complete_with_timeout(messages, self.timeout_seconds)` |
| `src/code_agent/execution_host.py:177` | `plan_with_context` | `if callable(complete_with_timeout)` |
| `src/code_agent/execution_host.py:478` | `execute_action` | `timeout_seconds: float = 30,` |
| `src/code_agent/execution_host.py:507` | `execute_action` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/execution_profiles.py:108` | `ToolPolicy` | `timeout_seconds: float = 300.0` |
| `src/code_agent/execution_profiles.py:115` | `__post_init__` | `if self.timeout_seconds <= 0:` |
| `src/code_agent/execution_profiles.py:116` | `__post_init__` | `raise ValueError("Tool timeout must be positive.")` |
| `src/code_agent/execution_profiles.py:339` | `<module>` | `tools=ToolPolicy(max_fanout=1, concurrency=1, timeout_seconds=120.0),` |
| `src/code_agent/execution_profiles.py:351` | `<module>` | `tools=ToolPolicy(max_fanout=4, concurrency=4, timeout_seconds=300.0),` |
| `src/code_agent/execution_profiles.py:363` | `<module>` | `tools=ToolPolicy(max_fanout=8, concurrency=8, timeout_seconds=600.0),` |
| `src/code_agent/experience_memory/config.py:21` | `ExperienceMemoryConfig` | `timeout_seconds: float = Field(default=10.0, gt=0, le=120, allow_inf_nan=False)` |
| `src/code_agent/experience_memory/config.py:26` | `ExperienceMemoryConfig` | `automatic_recall_timeout_seconds: float = Field(default=3.0, gt=0, le=10, allow_inf_nan=False)` |
| `src/code_agent/experience_memory/config.py:32` | `ExperienceMemoryConfig` | `automatic_reflect_timeout_seconds: float = Field(default=5.0, gt=0, le=15, allow_inf_nan=False)` |
| `src/code_agent/experience_memory/config.py:35` | `ExperienceMemoryConfig` | `automatic_reflect_context_max_chars: int = Field(default=3000, ge=500, le=6000)` |
| `src/code_agent/experience_memory/config.py:101` | `ExperienceMemorySettings` | `agent_experience_memory_timeout_seconds: float = Field(` |
| `src/code_agent/experience_memory/config.py:108` | `ExperienceMemorySettings` | `agent_experience_memory_automatic_recall_timeout_seconds: float = Field(` |
| `src/code_agent/experience_memory/config.py:116` | `ExperienceMemorySettings` | `agent_experience_memory_automatic_reflect_timeout_seconds: float = Field(` |
| `src/code_agent/experience_memory/config.py:123` | `ExperienceMemorySettings` | `agent_experience_memory_automatic_reflect_context_max_chars: int = Field(default=3000, ge=500, le=6000)` |
| `src/code_agent/experience_memory/config.py:148` | `experience_memory_config` | `timeout_seconds=self.agent_experience_memory_timeout_seconds,` |
| `src/code_agent/experience_memory/config.py:153` | `experience_memory_config` | `automatic_recall_timeout_seconds=self.agent_experience_memory_automatic_recall_timeout_seconds,` |
| `src/code_agent/experience_memory/config.py:161` | `experience_memory_config` | `automatic_reflect_timeout_seconds=self.agent_experience_memory_automatic_reflect_timeout_seconds,` |
| `src/code_agent/experience_memory/contracts.py:15` | `MemoryStatus` | `TIMEOUT = "timeout"` |
| `src/code_agent/experience_memory/contracts.py:56` | `RecallRequest` | `timeout_seconds: float = 3.0` |
| `src/code_agent/experience_memory/contracts.py:98` | `ReflectRequest` | `timeout_seconds: float = 5.0` |
| `src/code_agent/experience_memory/outbox.py:26` | `<module>` | `"", "provider_failed", "timeout", "unavailable", "missing_config",` |
| `src/code_agent/experience_memory/outbox.py:58` | `_connect` | `conn = sqlite3.connect(self.db_path, timeout=5)` |
| `src/code_agent/experience_memory/outbox.py:60` | `_connect` | `conn.execute("pragma busy_timeout = 5000")` |
| `src/code_agent/experience_memory/outbox.py:204` | `_claim` | `""", (owner, now + max(10.0, self.service.config.timeout_seconds + 5.0), operation_id))` |
| `src/code_agent/experience_memory/providers/hindsight.py:23` | `HindsightExperienceMemoryProvider` | `a retain timeout is ambiguous and must not trigger automatic duplicate writes.` |
| `src/code_agent/experience_memory/providers/hindsight.py:49` | `recall_detailed` | `except TimeoutError:` |
| `src/code_agent/experience_memory/providers/hindsight.py:50` | `recall_detailed` | `return ExperienceMemoryRecall(MemoryStatus.TIMEOUT)` |
| `src/code_agent/experience_memory/providers/hindsight.py:58` | `_execute_detailed_recall` | `timeout = min(config.timeout_seconds, request.timeout_seconds)` |
| `src/code_agent/experience_memory/providers/hindsight.py:59` | `_execute_detailed_recall` | `deadline = asyncio.get_running_loop().time() + timeout` |
| `src/code_agent/experience_memory/providers/hindsight.py:60` | `_execute_detailed_recall` | `async with asyncio.timeout_at(deadline):` |
| `src/code_agent/experience_memory/providers/hindsight.py:64` | `_execute_detailed_recall` | `timeout=timeout, max_attempts=1,` |
| `src/code_agent/experience_memory/providers/hindsight.py:120` | `_execute_detailed_recall` | `async with asyncio.timeout(remaining_time):` |
| `src/code_agent/experience_memory/providers/hindsight.py:198` | `reflect_detailed` | `except TimeoutError:` |
| `src/code_agent/experience_memory/providers/hindsight.py:199` | `reflect_detailed` | `return ReflectResult(MemoryStatus.TIMEOUT)` |
| `src/code_agent/experience_memory/providers/hindsight.py:207` | `_execute_detailed_reflect` | `timeout = min(config.timeout_seconds, request.timeout_seconds)` |
| `src/code_agent/experience_memory/providers/hindsight.py:208` | `_execute_detailed_reflect` | `deadline = asyncio.get_running_loop().time() + timeout` |
| `src/code_agent/experience_memory/providers/hindsight.py:227` | `_execute_detailed_reflect` | `async with asyncio.timeout_at(deadline):` |
| `src/code_agent/experience_memory/providers/hindsight.py:231` | `_execute_detailed_reflect` | `timeout=timeout, max_attempts=1,` |
| `src/code_agent/experience_memory/providers/hindsight.py:280` | `_execute_detailed_reflect` | `async with asyncio.timeout(remaining):` |
| `src/code_agent/experience_memory/providers/hindsight.py:297` | `get_operation` | `except TimeoutError:` |
| `src/code_agent/experience_memory/providers/hindsight.py:298` | `get_operation` | `return OperationLookup(MemoryStatus.TIMEOUT)` |
| `src/code_agent/experience_memory/providers/hindsight.py:306` | `_lookup_operation` | `deadline = asyncio.get_running_loop().time() + config.timeout_seconds` |
| `src/code_agent/experience_memory/providers/hindsight.py:307` | `_lookup_operation` | `async with asyncio.timeout_at(deadline):` |
| `src/code_agent/experience_memory/providers/hindsight.py:311` | `_lookup_operation` | `timeout=config.timeout_seconds, max_attempts=1,` |
| `src/code_agent/experience_memory/providers/hindsight.py:317` | `_lookup_operation` | `_request_timeout=config.timeout_seconds,` |
| `src/code_agent/experience_memory/providers/hindsight.py:331` | `_lookup_operation` | `async with asyncio.timeout(remaining):` |
| `src/code_agent/experience_memory/providers/hindsight.py:361` | `_call` | `except TimeoutError:` |
| `src/code_agent/experience_memory/providers/hindsight.py:362` | `_call` | `return MemoryResult(MemoryStatus.TIMEOUT)` |
| `src/code_agent/experience_memory/providers/hindsight.py:372` | `_execute` | `deadline = asyncio.get_running_loop().time() + config.timeout_seconds` |
| `src/code_agent/experience_memory/providers/hindsight.py:373` | `_execute` | `async with asyncio.timeout_at(deadline):` |
| `src/code_agent/experience_memory/providers/hindsight.py:377` | `_execute` | `timeout=config.timeout_seconds, max_attempts=1,` |
| `src/code_agent/experience_memory/providers/hindsight.py:382` | `_execute` | `_request_timeout=config.timeout_seconds,` |
| `src/code_agent/experience_memory/providers/hindsight.py:451` | `_execute` | `async with asyncio.timeout(remaining):` |
| `src/code_agent/experience_memory/recall.py:128` | `before_planning` | `timeout_seconds=min(config.automatic_recall_timeout_seconds, config.timeout_seconds),` |
| `src/code_agent/experience_memory/recall_policy.py:14` | `<module>` | `_REPAIR = re.compile(r"\b(fix&#124;repair&#124;debug&#124;bug&#124;error&#124;failing&#124;failed&#124;failure&#124;crash&#124;timeout&#124;broken)\b", re.I)` |
| `src/code_agent/experience_memory/reflection.py:146` | `escalate` | `timeout_seconds=min(config.timeout_seconds, config.automatic_reflect_timeout_seconds),` |
| `src/code_agent/experience_memory/retention.py:60` | `after_run` | `"timeout_seconds": min(self.service.config.timeout_seconds, 2.0),` |
| `src/code_agent/experience_memory/scope.py:29` | `_git` | `encoding="utf-8", errors="replace", timeout=2, check=False, env=env,` |
| `src/code_agent/experience_memory/service.py:81` | `recall_detailed` | `or request.timeout_seconds <= 0` |
| `src/code_agent/experience_memory/service.py:82` | `recall_detailed` | `or request.timeout_seconds > min(` |
| `src/code_agent/experience_memory/service.py:83` | `recall_detailed` | `self._config.timeout_seconds, self._config.automatic_recall_timeout_seconds,` |
| `src/code_agent/experience_memory/service.py:104` | `recall_detailed` | `except TimeoutError:` |
| `src/code_agent/experience_memory/service.py:105` | `recall_detailed` | `return ExperienceMemoryRecall(MemoryStatus.TIMEOUT)` |
| `src/code_agent/experience_memory/service.py:126` | `reflect_detailed` | `or not 0 < request.timeout_seconds <= min(` |
| `src/code_agent/experience_memory/service.py:127` | `reflect_detailed` | `config.timeout_seconds, config.automatic_reflect_timeout_seconds,` |
| `src/code_agent/experience_memory/service.py:139` | `reflect_detailed` | `except TimeoutError:` |
| `src/code_agent/experience_memory/service.py:140` | `reflect_detailed` | `return ReflectResult(MemoryStatus.TIMEOUT)` |
| `src/code_agent/experience_memory/service.py:207` | `get_operation` | `except TimeoutError:` |
| `src/code_agent/experience_memory/service.py:208` | `get_operation` | `return OperationLookup(MemoryStatus.TIMEOUT)` |
| `src/code_agent/experience_memory/service.py:227` | `_safe` | `except TimeoutError:` |
| `src/code_agent/experience_memory/service.py:228` | `_safe` | `return MemoryResult(MemoryStatus.TIMEOUT)` |
| `src/code_agent/extensions.py:270` | `emit` | `thread.join(timeout=5)` |
| `src/code_agent/factory.py:164` | `create_agent` | `timeout_seconds=settings.agent_model_timeout_seconds,` |
| `src/code_agent/factory.py:202` | `create_agent` | `"timeout_seconds": min(settings.experience_memory_config.timeout_seconds, 2.0),` |
| `src/code_agent/factory.py:222` | `create_agent` | `model_timeout_seconds=settings.agent_model_timeout_seconds,` |
| `src/code_agent/factory.py:296` | `model_provider_config` | `timeout_seconds=runtime.timeout_seconds or settings.agent_model_timeout_seconds,` |
| `src/code_agent/failure_types.py:59` | `FailureCategory` | `TIMEOUT = "timeout"` |
| `src/code_agent/failure_types.py:151` | `<module>` | `FailureCategory.TIMEOUT: RecoveryStrategy.RETRY_WITH_BACKOFF,` |
| `src/code_agent/failure_types.py:231` | `classify_failure` | `if metadata.get("timeout") is True:` |
| `src/code_agent/failure_types.py:232` | `classify_failure` | `return FailureCategory.TIMEOUT` |
| `src/code_agent/failure_types.py:273` | `classify_failure` | `if any(phrase in lowered for phrase in ("timed out", "timeout", "deadline exceeded")):` |
| `src/code_agent/failure_types.py:274` | `classify_failure` | `return FailureCategory.TIMEOUT` |
| `src/code_agent/failure_types.py:312` | `_classify_from_error_code` | `"timeout": FailureCategory.TIMEOUT,` |
| `src/code_agent/interactive.py:1511` | `friendly_error_message` | `if isinstance(exc, (ConnectionError, TimeoutError)):` |
| `src/code_agent/local_server.py:96` | `render_timeout` | `def render_timeout(self) -> str: ...` |
| `src/code_agent/local_server.py:105` | `_BasePageRenderer` | `"""Shared rendering logic for success, error, and timeout pages.` |
| `src/code_agent/local_server.py:107` | `_BasePageRenderer` | `Subclasses override &#96;&#96;timeout_title&#96;&#96; and &#96;&#96;timeout_message&#96;&#96; to` |
| `src/code_agent/local_server.py:112` | `_BasePageRenderer` | `timeout_title: str = "Timeout"` |
| `src/code_agent/local_server.py:113` | `_BasePageRenderer` | `timeout_message: str = "The operation timed out."` |
| `src/code_agent/local_server.py:122` | `render_timeout` | `def render_timeout(self) -> str:` |
| `src/code_agent/local_server.py:123` | `render_timeout` | `return self.render_error(self.timeout_title, self.timeout_message)` |
| `src/code_agent/local_server.py:129` | `OAuthHandler` | `timeout_title = "Authentication Timeout"` |
| `src/code_agent/local_server.py:130` | `OAuthHandler` | `timeout_message = "The local server took too long to exchange the authentication token. Please try signing in again."` |
| `src/code_agent/local_server.py:159` | `FormSubmissionHandler` | `timeout_title = "Setup Timeout"` |
| `src/code_agent/local_server.py:160` | `FormSubmissionHandler` | `timeout_message = "The local server took too long to validate your submission."` |
| `src/code_agent/local_server.py:263` | `_process_payload` | `# Wait up to response_timeout seconds for validation/processing` |
| `src/code_agent/local_server.py:264` | `_process_payload` | `if self.server.response_event.wait(timeout=self.server.response_timeout):` |
| `src/code_agent/local_server.py:272` | `_process_payload` | `html = self.server.handler.render_timeout()` |
| `src/code_agent/local_server.py:314` | `CallbackHTTPServer` | `response_timeout: float` |
| `src/code_agent/local_server.py:337` | `__init__` | `response_timeout: float = 30.0,` |
| `src/code_agent/local_server.py:354` | `__init__` | `self._server.response_timeout = response_timeout` |
| `src/code_agent/local_server.py:378` | `wait` | `def wait(self, timeout_seconds: float) -> T &#124; None:` |
| `src/code_agent/local_server.py:379` | `wait` | `if not self._server.received.wait(timeout_seconds):` |
| `src/code_agent/local_server.py:411` | `close` | `self._thread.join(timeout=2)` |
| `src/code_agent/lsp.py:112` | `__init__` | `request_timeout: float = 10.0,` |
| `src/code_agent/lsp.py:120` | `__init__` | `self.request_timeout = request_timeout` |
| `src/code_agent/lsp.py:207` | `request` | `def request(self, method: str, params: Any, *, timeout: float &#124; None = None) -> Any:` |
| `src/code_agent/lsp.py:218` | `request` | `response = response_queue.get(timeout=timeout or self.request_timeout)` |
| `src/code_agent/lsp.py:276` | `wait_for_notification` | `timeout: float,` |
| `src/code_agent/lsp.py:278` | `wait_for_notification` | `deadline = time.monotonic() + timeout` |
| `src/code_agent/lsp.py:297` | `close` | `self.request("shutdown", None, timeout=2.0)` |
| `src/code_agent/lsp.py:306` | `close` | `process.wait(timeout=2.0)` |
| `src/code_agent/lsp.py:307` | `close` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/lsp.py:310` | `close` | `process.wait(timeout=2.0)` |
| `src/code_agent/lsp.py:311` | `close` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/lsp.py:317` | `close` | `thread.join(timeout=1.0)` |
| `src/code_agent/lsp.py:435` | `__init__` | `request_timeout: float = 10.0,` |
| `src/code_agent/lsp.py:450` | `__init__` | `self.request_timeout = request_timeout` |
| `src/code_agent/lsp.py:656` | `diagnostics` | `timeout=self.diagnostics_wait if wait is None else wait,` |
| `src/code_agent/lsp.py:744` | `_client_for_spec` | `client_options: dict[str, Any] = {"request_timeout": self.request_timeout}` |
| `src/code_agent/managed_processes.py:44` | `ManagedProcessSpec` | `timeout_seconds: int = 0` |
| `src/code_agent/managed_processes.py:84` | `start` | `timeout_seconds: int = 0,` |
| `src/code_agent/managed_processes.py:131` | `start` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/managed_processes.py:417` | `check_local_port` | `def check_local_port(port: int, timeout: float = 0.25) -> bool:` |
| `src/code_agent/managed_processes.py:419` | `check_local_port` | `with socket.create_connection(("127.0.0.1", port), timeout=timeout):` |
| `src/code_agent/mcp.py:31` | `McpServerConfig` | `timeout_seconds: float = 30.0` |
| `src/code_agent/mcp.py:109` | `close` | `process.wait(timeout=3)` |
| `src/code_agent/mcp.py:110` | `close` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/mcp.py:215` | `_read_response` | `deadline = time.monotonic() + self.config.timeout_seconds` |
| `src/code_agent/mcp.py:219` | `_read_response` | `raise McpError(f"MCP request timed out after {self.config.timeout_seconds:g}s.")` |
| `src/code_agent/mcp.py:221` | `_read_response` | `payload = self._responses.get(timeout=remaining)` |
| `src/code_agent/mcp.py:224` | `_read_response` | `f"MCP request timed out after {self.config.timeout_seconds:g}s."` |
| `src/code_agent/mcp_validation.py:57` | `validate_configuration` | `if config.timeout_seconds <= 0:` |
| `src/code_agent/mcp_validation.py:58` | `validate_configuration` | `findings.append(ValidationFinding(Severity.WARNING, "invalid_timeout", f"invalid timeout ({config.timeout_seconds}), should be > 0"))` |
| `src/code_agent/memory_benchmark.py:181` | `benchmark_disabled` | `stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=60,` |
| `src/code_agent/memory_eval.py:58` | `_real_model_environment` | `include_reflect: bool, timeout_seconds: float, trials: int) -> dict[str, str]:` |
| `src/code_agent/memory_eval.py:74` | `_real_model_environment` | `or timeout_seconds > 120):` |
| `src/code_agent/memory_eval.py:78` | `_real_model_environment` | `cwd=repository, capture_output=True, check=False, timeout=5)` |
| `src/code_agent/memory_eval.py:84` | `_real_model_environment` | `], cwd=repository, capture_output=True, check=True, timeout=5)` |
| `src/code_agent/memory_eval.py:99` | `run_evaluation` | `manifest: Path, runner: list[str], output: Path, *, timeout_seconds: float = 600.0,` |
| `src/code_agent/memory_eval.py:103` | `run_evaluation` | `if not runner or not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or not 1 <= trials <= 100:` |
| `src/code_agent/memory_eval.py:104` | `run_evaluation` | `raise ValueError("A runner command and positive timeout are required.")` |
| `src/code_agent/memory_eval.py:110` | `run_evaluation` | `source, cases, include_reflect=include_reflect, timeout_seconds=timeout_seconds, trials=trials,` |
| `src/code_agent/memory_eval.py:159` | `run_evaluation` | `timeout=timeout_seconds, check=False,` |
| `src/code_agent/memory_eval.py:163` | `run_evaluation` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/memory_eval.py:165` | `run_evaluation` | `error = "timeout"` |
| `src/code_agent/memory_eval_runner.py:32` | `<module>` | `"Fix the timeout retry bug in this project using the previously rejected approach lesson.",` |
| `src/code_agent/memory_operator.py:100` | `memory_health` | `"provider_unavailable" if status in {MemoryStatus.UNAVAILABLE, MemoryStatus.TIMEOUT} else` |
| `src/code_agent/memory_operator.py:148` | `run_live_smoke` | `service: ExperienceMemoryService, bank_id: str, *, timeout_seconds: float = 60.0,` |
| `src/code_agent/memory_operator.py:154` | `run_live_smoke` | `if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:` |
| `src/code_agent/memory_operator.py:162` | `run_live_smoke` | `deadline = time.monotonic() + timeout_seconds` |
| `src/code_agent/memory_operator.py:167` | `bounded_service` | `raise TimeoutError` |
| `src/code_agent/memory_operator.py:169` | `bounded_service` | `"timeout_seconds": min(service.config.timeout_seconds, remaining),` |
| `src/code_agent/memory_operator.py:216` | `run_live_smoke` | `return {"stage": stage, "passed": False, "state": "timeout", **identity}` |
| `src/code_agent/memory_operator.py:225` | `run_live_smoke` | `timeout_seconds=min(3.0, bounded.config.timeout_seconds),` |
| `src/code_agent/memory_operator.py:259` | `run_live_smoke` | `except TimeoutError:` |
| `src/code_agent/memory_operator.py:260` | `run_live_smoke` | `return {"stage": stage, "passed": False, "status": "timeout", **identity, **metrics}` |
| `src/code_agent/memory_operator.py:268` | `memory_live_smoke` | `timeout_seconds: float = typer.Option(60.0, min=1.0, max=300.0),` |
| `src/code_agent/memory_operator.py:275` | `memory_live_smoke` | `result = run_live_smoke(service, bank_id, timeout_seconds=timeout_seconds, include_reflect=include_reflect)` |
| `src/code_agent/memory_operator.py:286` | `memory_evaluate` | `timeout_seconds: float = typer.Option(600.0, min=1.0),` |
| `src/code_agent/memory_operator.py:295` | `memory_evaluate` | `manifest, shlex.split(runner), output, timeout_seconds=timeout_seconds, trials=trials,` |
| `src/code_agent/model_registry.py:33` | `ModelRuntimeDefaults` | `timeout_seconds: float &#124; None = None` |
| `src/code_agent/models.py:30` | `<module>` | `"timeout",` |
| `src/code_agent/models.py:76` | `ModelProviderConfig` | `timeout_seconds: float = 60.0` |
| `src/code_agent/models.py:136` | `OpenAICompatibleChatClient` | `timeout_seconds: float = 60.0` |
| `src/code_agent/models.py:156` | `__post_init__` | `timeout=self.timeout_seconds,` |
| `src/code_agent/models.py:158` | `__post_init__` | `# attempts and make a 60 second logical timeout last several minutes.` |
| `src/code_agent/models.py:163` | `complete` | `return self.complete_with_timeout(messages, self.timeout_seconds)` |
| `src/code_agent/models.py:165` | `complete_with_timeout` | `def complete_with_timeout(self, messages: list[ChatMessage], timeout_seconds: float) -> str:` |
| `src/code_agent/models.py:174` | `_make_request` | `"timeout": remaining_seconds,` |
| `src/code_agent/models.py:186` | `complete_with_timeout` | `return self._call_with_retry(_make_request, timeout_seconds=timeout_seconds)` |
| `src/code_agent/models.py:193` | `stream_complete` | `return self.stream_complete_with_timeout(messages, on_token, self.timeout_seconds)` |
| `src/code_agent/models.py:195` | `stream_complete_with_timeout` | `def stream_complete_with_timeout(` |
| `src/code_agent/models.py:199` | `stream_complete_with_timeout` | `timeout_seconds: float,` |
| `src/code_agent/models.py:211` | `_make_request` | `"timeout": remaining_seconds,` |
| `src/code_agent/models.py:222` | `_make_request` | `raise TimeoutError("Model stream exceeded its turn deadline.")` |
| `src/code_agent/models.py:246` | `stream_complete_with_timeout` | `return self._call_with_retry(_make_request, timeout_seconds=timeout_seconds)` |
| `src/code_agent/models.py:267` | `_call_with_retry` | `timeout_seconds: float,` |
| `src/code_agent/models.py:269` | `_call_with_retry` | `if timeout_seconds <= 0:` |
| `src/code_agent/models.py:270` | `_call_with_retry` | `raise TimeoutError("Model turn deadline expired before the request started.")` |
| `src/code_agent/models.py:271` | `_call_with_retry` | `deadline = time.monotonic() + timeout_seconds` |
| `src/code_agent/models.py:279` | `_call_with_retry` | `raise TimeoutError(` |
| `src/code_agent/models.py:280` | `_call_with_retry` | `f"Model turn exceeded its {timeout_seconds:g}s absolute deadline."` |
| `src/code_agent/models.py:284` | `_call_with_retry` | `raise TimeoutError("Model response arrived after its turn deadline.")` |
| `src/code_agent/models.py:313` | `_call_with_retry` | `raise TimeoutError(` |
| `src/code_agent/models.py:314` | `_call_with_retry` | `f"Model turn exceeded its {timeout_seconds:g}s absolute deadline."` |
| `src/code_agent/models.py:374` | `complete_with_timeout` | `def complete_with_timeout(self, messages: list[ChatMessage], timeout_seconds: float) -> str:` |
| `src/code_agent/models.py:375` | `complete_with_timeout` | `return self._try_clients_with_deadline(messages, timeout_seconds, stream_callback=None)` |
| `src/code_agent/models.py:384` | `stream_complete_with_timeout` | `def stream_complete_with_timeout(` |
| `src/code_agent/models.py:388` | `stream_complete_with_timeout` | `timeout_seconds: float,` |
| `src/code_agent/models.py:390` | `stream_complete_with_timeout` | `return self._try_clients_with_deadline(messages, timeout_seconds, stream_callback=on_token)` |
| `src/code_agent/models.py:395` | `_try_clients_with_deadline` | `timeout_seconds: float,` |
| `src/code_agent/models.py:399` | `_try_clients_with_deadline` | `deadline = time.monotonic() + timeout_seconds` |
| `src/code_agent/models.py:404` | `call` | `raise TimeoutError(` |
| `src/code_agent/models.py:405` | `call` | `f"Model turn exceeded its {timeout_seconds:g}s absolute deadline."` |
| `src/code_agent/models.py:408` | `call` | `method = getattr(client, "stream_complete_with_timeout", None)` |
| `src/code_agent/models.py:412` | `call` | `method = getattr(client, "complete_with_timeout", None)` |
| `src/code_agent/models.py:507` | `create_openai_compatible_client` | `timeout_seconds=provider.timeout_seconds,` |
| `src/code_agent/models.py:575` | `classify_model_error` | `if isinstance(exc, (openai.APITimeoutError, TimeoutError)) or "timed out" in lowered or "timeout" in lowered:` |
| `src/code_agent/models.py:576` | `classify_model_error` | `return ClassifiedModelError("timeout", retryable=True, fallbackable=True, message=message)` |
| `src/code_agent/orchestration.py:27` | `AgentProfile` | `timeout_seconds: float = 120.0` |
| `src/code_agent/orchestration.py:39` | `register` | `if profile.token_budget < 1 or profile.execution_budget < 1 or profile.timeout_seconds <= 0:` |
| `src/code_agent/orchestration.py:71` | `SubagentRequest` | `timeout_seconds: float &#124; None = None` |
| `src/code_agent/orchestration.py:115` | `spawn` | `timeout_seconds=min(request.timeout_seconds or profile.timeout_seconds, profile.timeout_seconds),` |
| `src/code_agent/platform_runtime.py:144` | `_parse_mcp_config` | `timeout_seconds=float(raw.get("timeout_seconds", 30)),` |
| `src/code_agent/plugins.py:97` | `load` | `_command_handler(manifest.root, command, float(raw.get("timeout_seconds", 30))),` |
| `src/code_agent/plugins.py:112` | `load` | `timeout_seconds=float(raw.get("timeout_seconds", 120)),` |
| `src/code_agent/plugins.py:144` | `_command_handler` | `def _command_handler(root: Path, command: tuple[str, ...], timeout_seconds: float):` |
| `src/code_agent/plugins.py:158` | `invoke` | `timeout=timeout_seconds,` |
| `src/code_agent/process_worker.py:210` | `_monitor_child` | `if self.spec.timeout_seconds and time.monotonic() - self.started_monotonic >= self.spec.timeout_seconds:` |
| `src/code_agent/process_worker.py:212` | `_monitor_child` | `self.failure_reason = "timeout"` |
| `src/code_agent/process_worker.py:213` | `_monitor_child` | `self._emit("timeout", timeout_seconds=self.spec.timeout_seconds, severity="error")` |
| `src/code_agent/process_worker.py:218` | `_monitor_child` | `reader.join(timeout=1.0)` |
| `src/code_agent/process_worker.py:229` | `_monitor_child` | `return_code = process.wait(timeout=2.0)` |
| `src/code_agent/process_worker.py:230` | `_monitor_child` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/process_worker.py:232` | `_monitor_child` | `return_code = process.wait(timeout=2.0)` |
| `src/code_agent/process_worker.py:382` | `_terminate_child` | `process.wait(timeout=grace_seconds)` |
| `src/code_agent/process_worker.py:384` | `_terminate_child` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/process_worker.py:409` | `_signal_container_process` | `timeout=5,` |
| `src/code_agent/process_worker.py:418` | `_container_port_ready` | `"import socket,sys; s=socket.socket(); s.settimeout(.25); "` |
| `src/code_agent/process_worker.py:434` | `_container_port_ready` | `timeout=3,` |
| `src/code_agent/process_worker.py:456` | `_container_resources` | `timeout=5,` |
| `src/code_agent/processes.py:74` | `ProcessSupervisor` | `"""Tracks local child processes so stops and timeouts can clean up trees."""` |
| `src/code_agent/processes.py:145` | `run_shell` | `timeout_seconds: int,` |
| `src/code_agent/processes.py:169` | `run_shell` | `deadline = time.monotonic() + timeout_seconds` |
| `src/code_agent/processes.py:213` | `run_shell` | `stdout, stderr = process.communicate(timeout=min(poll_seconds, remaining))` |
| `src/code_agent/processes.py:221` | `run_shell` | `except subprocess.TimeoutExpired as exc:` |
| `src/code_agent/processes.py:245` | `terminate_process_tree` | `process.wait(timeout=grace_seconds)` |
| `src/code_agent/processes.py:247` | `terminate_process_tree` | `except (OSError, SystemError, subprocess.TimeoutExpired):` |
| `src/code_agent/processes.py:254` | `terminate_process_tree` | `timeout=max(2.0, grace_seconds),` |
| `src/code_agent/processes.py:258` | `terminate_process_tree` | `process.wait(timeout=min(2.0, grace_seconds))` |
| `src/code_agent/processes.py:260` | `terminate_process_tree` | `except (OSError, subprocess.TimeoutExpired):` |
| `src/code_agent/processes.py:266` | `terminate_process_tree` | `timeout=10,` |
| `src/code_agent/processes.py:270` | `terminate_process_tree` | `except (OSError, subprocess.TimeoutExpired):` |
| `src/code_agent/processes.py:280` | `terminate_process_tree` | `process.wait(timeout=grace_seconds)` |
| `src/code_agent/processes.py:281` | `terminate_process_tree` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/processes.py:299` | `wait` | `def wait(self, timeout: float &#124; None = None) -> None:` |
| `src/code_agent/processes.py:300` | `wait` | `deadline = time.monotonic() + (timeout or 0)` |
| `src/code_agent/processes.py:301` | `wait` | `while timeout is None or time.monotonic() < deadline:` |
| `src/code_agent/processes.py:307` | `wait` | `raise subprocess.TimeoutExpired(str(self.pid), timeout)` |
| `src/code_agent/processes.py:424` | `capture_process_streams` | `reader.join(timeout=2.0)` |
| `src/code_agent/processes.py:463` | `capture_process_streams` | `else "timeout"` |
| `src/code_agent/prompts.py:125` | `system_prompt` | `{{ "type": "start_process", "command": "npm run dev", "name": "web", "working_directory": null, "interactive": false, "pty": false, "timeout_seconds": 0, "readiness_port": 3000, "auto_restart": true, "max_restarts": 3, "restart_backoff_seconds": 1.0, "max_restart_backoff_seconds": 30.0, "memory_limit_mb": 1024, "cpu_time_limit_seconds": null, "log_max_bytes": 5000000, "log_backups": 3 }}` |
| `src/code_agent/release_smoke.py:14` | `SmokeCommand` | `timeout_seconds: int = 300` |
| `src/code_agent/release_smoke.py:95` | `run_release_smoke` | `timeout=item.timeout_seconds,` |
| `src/code_agent/release_smoke.py:98` | `run_release_smoke` | `except subprocess.TimeoutExpired as exc:` |
| `src/code_agent/release_smoke.py:107` | `run_release_smoke` | `for part in [stderr or "", f"Timed out after {item.timeout_seconds}s."]` |
| `src/code_agent/repo_index.py:934` | `_connect` | `conn = sqlite3.connect(self.db_path, timeout=10)` |
| `src/code_agent/repo_index.py:936` | `_connect` | `conn.execute("pragma busy_timeout = 10000")` |
| `src/code_agent/repo_index.py:1087` | `stop` | `def stop(self, timeout: float = 2.0) -> None:` |
| `src/code_agent/repo_index.py:1091` | `stop` | `self._thread.join(timeout=timeout)` |
| `src/code_agent/runtime_migration.py:469` | `wait` | `def wait(self, execution_id: str, timeout: float &#124; None = None) -> ExecutionProjection:` |
| `src/code_agent/runtime_migration.py:471` | `wait` | `thread.join(timeout)` |
| `src/code_agent/runtime_migration.py:473` | `wait` | `raise TimeoutError("Execution plane worker is still running.")` |
| `src/code_agent/safety.py:124` | `ShellPolicy` | `timeout_seconds: int = 60` |
| `src/code_agent/safety.py:198` | `safe_exception` | `if isinstance(exc, (TimeoutError,)):` |
| `src/code_agent/safety.py:220` | `classify_shell_command` | `timeout_seconds=0,` |
| `src/code_agent/safety.py:230` | `classify_shell_command` | `timeout_seconds=0,` |
| `src/code_agent/safety.py:240` | `classify_shell_command` | `timeout_seconds=0,` |
| `src/code_agent/safety.py:250` | `classify_shell_command` | `timeout_seconds=0,` |
| `src/code_agent/safety.py:260` | `classify_shell_command` | `timeout_seconds=180,` |
| `src/code_agent/safety.py:269` | `classify_shell_command` | `timeout_seconds=120,` |
| `src/code_agent/safety.py:280` | `classify_shell_command` | `timeout_seconds=0,` |
| `src/code_agent/safety.py:289` | `classify_shell_command` | `timeout_seconds=60,` |
| `src/code_agent/safety.py:297` | `classify_shell_command` | `timeout_seconds=30,` |
| `src/code_agent/safety.py:305` | `classify_shell_command` | `timeout_seconds=0,` |
| `src/code_agent/sandbox.py:204` | `format_sandbox_limits` | `"private HOME/TMP/cache dirs, timeouts, and audit logs apply",` |
| `src/code_agent/sandbox_security.py:68` | `SandboxResourceLimits` | `timeout_seconds: int &#124; None = None` |
| `src/code_agent/sandbox_security.py:150` | `from_workspace` | `timeout_seconds=_optional_int(resources.get("timeout_seconds")),` |
| `src/code_agent/sandbox_security.py:208` | `effective_timeout` | `def effective_timeout(self, shell_policy: ShellPolicy) -> int:` |
| `src/code_agent/sandbox_security.py:209` | `effective_timeout` | `if self.resources.timeout_seconds is not None:` |
| `src/code_agent/sandbox_security.py:210` | `effective_timeout` | `return min(shell_policy.timeout_seconds, self.resources.timeout_seconds)` |
| `src/code_agent/sandbox_security.py:211` | `effective_timeout` | `return shell_policy.timeout_seconds` |
| `src/code_agent/sandbox_security.py:488` | `run_shell` | `timeout_seconds: int,` |
| `src/code_agent/sandbox_security.py:526` | `run_shell` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:536` | `run_shell` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:545` | `run_shell` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:575` | `run_shell` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:654` | `_run_reusable_container` | `timeout_seconds: int,` |
| `src/code_agent/sandbox_security.py:683` | `_run_reusable_container` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:719` | `_run_container` | `timeout_seconds: int,` |
| `src/code_agent/sandbox_security.py:887` | `_run_container` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:940` | `_wait_for_container_process` | `timeout_seconds: int,` |
| `src/code_agent/sandbox_security.py:953` | `_wait_for_container_process` | `deadline=started + timeout_seconds,` |
| `src/code_agent/sandbox_security.py:960` | `_wait_for_container_process` | `deadline = time.monotonic() + timeout_seconds` |
| `src/code_agent/sandbox_security.py:990` | `_wait_for_container_process` | `stdout, stderr = process.communicate(timeout=min(0.2, remaining))` |
| `src/code_agent/sandbox_security.py:999` | `_wait_for_container_process` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/sandbox_security.py:1008` | `cleanup_container` | `timeout_seconds: int = 10,` |
| `src/code_agent/sandbox_security.py:1020` | `cleanup_container` | `timeout=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:1029` | `cleanup_container` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/sandbox_security.py:1030` | `cleanup_container` | `return False, f"Forced cleanup timed out after {timeout_seconds}s."` |
| `src/code_agent/sandbox_security.py:1126` | `sandbox_health` | `"directories, env scrubbing, policy checks, timeouts, process-tree cleanup, "` |
| `src/code_agent/sandbox_security.py:1210` | `container_daemon_available` | `def container_daemon_available(runtime_path: str, *, timeout_seconds: int = 5) -> tuple[bool, str]:` |
| `src/code_agent/sandbox_security.py:1216` | `container_daemon_available` | `timeout=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:1220` | `container_daemon_available` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/sandbox_security.py:1221` | `container_daemon_available` | `return False, f"{runtime_path} info timed out after {timeout_seconds}s."` |
| `src/code_agent/sandbox_security.py:1234` | `inspect_container_runtime_security` | `timeout_seconds: int = 5,` |
| `src/code_agent/sandbox_security.py:1246` | `inspect_container_runtime_security` | `timeout=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:1250` | `inspect_container_runtime_security` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/sandbox_security.py:1251` | `inspect_container_runtime_security` | `return None, f"{runtime} security inspection timed out after {timeout_seconds}s."` |
| `src/code_agent/sandbox_security.py:1309` | `inspect_container_image` | `timeout_seconds: int = 10,` |
| `src/code_agent/sandbox_security.py:1323` | `inspect_container_image` | `timeout=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:1327` | `inspect_container_image` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/sandbox_security.py:1328` | `inspect_container_image` | `return False, (), f"Image inspect for {image} timed out after {timeout_seconds}s."` |
| `src/code_agent/sandbox_security.py:1404` | `validate_container_image_scan` | `timeout_seconds: int = 300,` |
| `src/code_agent/sandbox_security.py:1431` | `validate_container_image_scan` | `timeout=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:1435` | `validate_container_image_scan` | `except subprocess.TimeoutExpired:` |
| `src/code_agent/sandbox_security.py:1436` | `validate_container_image_scan` | `return False, f"Trivy image scan timed out after {timeout_seconds}s."` |
| `src/code_agent/sandbox_security.py:1515` | `windows_virtualization_diagnostic` | `def windows_virtualization_diagnostic(*, timeout_seconds: int = 5) -> str:` |
| `src/code_agent/sandbox_security.py:1521` | `windows_virtualization_diagnostic` | `timeout=timeout_seconds,` |
| `src/code_agent/sandbox_security.py:1524` | `windows_virtualization_diagnostic` | `except (OSError, subprocess.TimeoutExpired):` |
| `src/code_agent/schema.py:185` | `StartProcessAction` | `timeout_seconds: int = Field(default=0, ge=0, le=604800)` |
| `src/code_agent/schema.py:189` | `StartProcessAction` | `restart_backoff_seconds: float = Field(default=1.0, ge=0.1, le=300.0)` |
| `src/code_agent/schema.py:271` | `RepoMapAction` | `max_files: int = Field(default=80, ge=10, le=300)` |
| `src/code_agent/schema.py:319` | `LspRenameAction` | `new_name: str = Field(min_length=1, max_length=300)` |
| `src/code_agent/storage.py:250` | `_connect` | `conn = sqlite3.connect(self.db_path, timeout=30)` |
| `src/code_agent/storage.py:253` | `_connect` | `conn.execute("pragma busy_timeout = 30000")` |
| `src/code_agent/tools.py:938` | `_run_shell` | `f"Timeout: {self.sandbox_policy.effective_timeout(policy)}s\n"` |
| `src/code_agent/tools.py:953` | `_run_shell` | `timeout_seconds=self.sandbox_policy.effective_timeout(policy),` |
| `src/code_agent/tools.py:960` | `_run_shell` | `timeout_output = str(process_result["output"])` |
| `src/code_agent/tools.py:967` | `_run_shell` | `ordered_output = timeout_output or "\n".join(` |
| `src/code_agent/tools.py:981` | `_run_shell` | `"timeout_seconds": self.sandbox_policy.effective_timeout(policy),` |
| `src/code_agent/tools.py:1001` | `_run_shell` | `"timeout_seconds": self.sandbox_policy.effective_timeout(policy),` |
| `src/code_agent/tools.py:1027` | `_run_shell` | `detail = f"Shell command timed out after {self.sandbox_policy.effective_timeout(policy)}s."` |
| `src/code_agent/tools.py:1099` | `_start_process` | `timeout_seconds=action.timeout_seconds,` |
| `src/code_agent/tools.py:1235` | `_search` | `timeout=60,` |
| `src/code_agent/tools.py:1273` | `_inspect_git_diff` | `inside_work_tree = self._git(["rev-parse", "--is-inside-work-tree"], timeout=15)` |
| `src/code_agent/tools.py:1278` | `_inspect_git_diff` | `("Status", self._git(["status", "--short"], timeout=30)),` |
| `src/code_agent/tools.py:1279` | `_inspect_git_diff` | `("Unstaged changes", self._git(["diff", "--name-status"], timeout=30)),` |
| `src/code_agent/tools.py:1280` | `_inspect_git_diff` | `("Staged changes", self._git(["diff", "--cached", "--name-status"], timeout=30)),` |
| `src/code_agent/tools.py:1290` | `_inspect_git_diff` | `("Unstaged diff", self._git(["diff", "--no-ext-diff"], timeout=60)),` |
| `src/code_agent/tools.py:1291` | `_inspect_git_diff` | `("Staged diff", self._git(["diff", "--cached", "--no-ext-diff"], timeout=60)),` |
| `src/code_agent/tools.py:1594` | `_fetch_url` | `with urllib.request.urlopen(request, timeout=20) as response:` |
| `src/code_agent/tools.py:1657` | `_run_shell_process` | `timeout_seconds: int,` |
| `src/code_agent/tools.py:1662` | `_run_shell_process` | `timeout_seconds=timeout_seconds,` |
| `src/code_agent/tools.py:1909` | `_git_apply` | `timeout=60,` |
| `src/code_agent/tools.py:1918` | `_git` | `def _git(self, args: list[str], timeout: int) -> ToolResult:` |
| `src/code_agent/tools.py:1924` | `_git` | `timeout=timeout,` |
| `src/code_agent/transactions.py:364` | `plan_patch` | `timeout=60,` |
| `src/code_agent/transactions.py:1414` | `three_way_merge` | `timeout=30,` |
