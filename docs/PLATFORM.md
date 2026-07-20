# Dynamic platform

Agent47 now has a runtime extension layer rather than a second static tool switch. It provides:

- namespaced, versioned tools with aliases, schemas, dependencies, health checks, reload and removal;
- prioritized lifecycle hooks with failure isolation;
- MCP stdio servers with capability negotiation, tools, resources, prompts, subscriptions, caching, reconnect and bounded reads;
- hierarchical repository/workspace instructions and task-selected skills;
- plugin manifests with dependency and permission declarations;
- custom agent profiles, isolated child contexts and dependency-aware parallel orchestration.

Run `code-agent platform inspect` to list built-in skills and agent profiles. Workspace executable
extensions are deliberately disabled by default. Set `AGENT_TRUST_WORKSPACE_EXTENSIONS=true` for an
agent run, or pass `--trust-workspace-extensions` to the inspection command, only after reviewing
`.agents/plugins/*/plugin.json` and `.agents/mcp.json`.

## Skills

Workspace skills live at `.agents/skills/<name>/SKILL.md`. The frontmatter contains only `name` and
`description`; optional execution metadata belongs in `skill.json`. Agent47 ships security auditing,
test generation, code review, documentation, dependency analysis, refactoring, performance, and
DevOps skills. Skills are selected from task wording and contribute workflow, verification and
acceptance guidance without overriding the core security prompt.

## MCP configuration

`.agents/mcp.json` contains a `servers` object. Each server accepts `command`, `enabled`,
`timeout_seconds`, `reconnect_attempts`, `auth_env_keys`, and `allowed_tools`. Only named authentication
environment variables plus a minimal process environment reach the server. Discovered MCP tools are
registered under `mcp-<server>.*` and still require dynamic-tool approval.

## Plugins

A plugin is rooted at `.agents/plugins/<name>/plugin.json` and declares API version `1`, semantic
version, dependencies, permissions, command tools, optional skills, and optional agent profiles.
Plugin commands must resolve inside the plugin directory, use `shell=False`, receive a scrubbed
environment, and have a timeout. Unloading removes its tools, skills, and hooks.

## Multi-agent API

`AgentCatalog` contains planner, researcher, coder, reviewer, tester, security-auditor,
documentation-writer, performance-optimizer, refactoring-expert, and dependency-analyzer profiles.
`SubagentManager` deep-copies JSON context, intersects requested capabilities with the profile,
caps token/execution/time budgets, contains failures, and supports cancellation. The
`MultiAgentOrchestrator` validates a task DAG and runs independent ready nodes concurrently.

The orchestration layer accepts an injected runner. This keeps provider construction and sandbox
policy outside the scheduler and makes the same isolation contract usable by the CLI, tests, or a
future service host.
