from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

CATEGORY_ICONS: dict[str, str] = {
    "Conversation": "💬",
    "Workspace": "📁",
    "Models": "🤖",
    "Keys": "🔑",
    "History": "🕒",
    "Permissions": "🛡",
    "Sandbox": "🧪",
    "Configuration": "⚙",
    "Debug": "🐞",
    "Advanced": "⚙",
}

CATEGORY_ORDER: list[str] = [
    "Conversation",
    "Workspace",
    "Models",
    "Keys",
    "History",
    "Permissions",
    "Sandbox",
    "Configuration",
    "Debug",
    "Advanced",
]


@dataclass(frozen=True)
class CommandMeta:
    name: str
    aliases: tuple[str, ...] = ()
    category: str = "Conversation"
    description: str = ""
    hidden: bool = False
    keywords: tuple[str, ...] = ()
    subcommands: tuple[str, ...] = ()
    usage: str = ""
    examples: tuple[str, ...] = ()
    rank: int = 100
    source: str = "core"

    @property
    def display_name(self) -> str:
        return self.name


class CommandRegistry:
    """Thread-safe authoritative command registry with pre-cached search indexes."""

    def __init__(self, commands: Iterable[CommandMeta] | None = None) -> None:
        self._lock = threading.RLock()
        self._commands: dict[str, CommandMeta] = {}
        self._recent_history: list[str] = []
        
        # Cached search indexes
        self._exact_map: dict[str, CommandMeta] = {}
        self._alias_map: dict[str, CommandMeta] = {}
        self._keyword_index: dict[str, set[CommandMeta]] = {}
        self._prefix_index: dict[str, set[CommandMeta]] = {}

        if commands:
            for cmd in commands:
                self._register_internal(cmd)
            self._rebuild_indexes()

    def register(self, meta: CommandMeta) -> None:
        """Register or update a command dynamically."""
        with self._lock:
            self._register_internal(meta)
            self._rebuild_indexes()

    def unregister(self, name_or_alias: str) -> bool:
        """Remove a dynamically registered command by name."""
        with self._lock:
            canonical = self.lookup(name_or_alias)
            if canonical and canonical.name in self._commands:
                del self._commands[canonical.name]
                self._rebuild_indexes()
                return True
            return False

    def _register_internal(self, meta: CommandMeta) -> None:
        self._commands[meta.name] = meta

    def _rebuild_indexes(self) -> None:
        """Build immutable indexes whenever commands change."""
        exact_map: dict[str, CommandMeta] = {}
        alias_map: dict[str, CommandMeta] = {}
        keyword_index: dict[str, set[CommandMeta]] = {}
        prefix_index: dict[str, set[CommandMeta]] = {}

        for cmd in self._commands.values():
            exact_map[cmd.name.lower()] = cmd
            for alias in cmd.aliases:
                alias_map[alias.lower()] = cmd

            # Index keywords
            all_terms = set(cmd.keywords) | {cmd.name.lstrip("/"), cmd.category.lower()}
            for term in all_terms:
                term_lower = term.lower()
                if term_lower not in keyword_index:
                    keyword_index[term_lower] = set()
                keyword_index[term_lower].add(cmd)

            # Index prefixes for command name and aliases
            names_to_prefix = [cmd.name.lower()] + [a.lower() for a in cmd.aliases]
            for n in names_to_prefix:
                for i in range(1, len(n) + 1):
                    prefix = n[:i]
                    if prefix not in prefix_index:
                        prefix_index[prefix] = set()
                    prefix_index[prefix].add(cmd)

        self._exact_map = exact_map
        self._alias_map = alias_map
        self._keyword_index = keyword_index
        self._prefix_index = prefix_index

    def lookup(self, name_or_alias: str) -> CommandMeta | None:
        """Fast lock-free lookup by name or alias."""
        key = name_or_alias.strip().lower()
        if not key.startswith("/"):
            key = "/" + key
        return self._exact_map.get(key) or self._alias_map.get(key)

    def iter_visible(self) -> list[CommandMeta]:
        """Return non-hidden commands sorted by rank then name."""
        with self._lock:
            return sorted(
                [cmd for cmd in self._commands.values() if not cmd.hidden],
                key=lambda c: (c.rank, c.name),
            )

    def iter_hidden(self) -> list[CommandMeta]:
        """Return hidden commands sorted by rank then name."""
        with self._lock:
            return sorted(
                [cmd for cmd in self._commands.values() if cmd.hidden],
                key=lambda c: (c.rank, c.name),
            )

    def all_commands(self) -> list[CommandMeta]:
        """Return all registered commands sorted by rank then name."""
        with self._lock:
            return sorted(self._commands.values(), key=lambda c: (c.rank, c.name))

    def by_category(self, *, include_hidden: bool = False) -> dict[str, list[CommandMeta]]:
        """Group commands by category."""
        with self._lock:
            grouped: dict[str, list[CommandMeta]] = {}
            for cat in CATEGORY_ORDER:
                grouped[cat] = []
            
            for cmd in self._commands.values():
                if cmd.hidden and not include_hidden:
                    continue
                cat = cmd.category if cmd.category in grouped else "Advanced"
                if cat not in grouped:
                    grouped[cat] = []
                grouped[cat].append(cmd)

            for cat in list(grouped.keys()):
                if not grouped[cat]:
                    del grouped[cat]
                else:
                    grouped[cat].sort(key=lambda c: (c.rank, c.name))
            return grouped

    def record_usage(self, command_name: str) -> None:
        """Record session command usage."""
        cmd = self.lookup(command_name)
        if not cmd:
            return
        with self._lock:
            if cmd.name in self._recent_history:
                self._recent_history.remove(cmd.name)
            self._recent_history.insert(0, cmd.name)
            self._recent_history = self._recent_history[:10]

    def recent(self, limit: int = 5) -> list[CommandMeta]:
        """Return recent commands for this session."""
        with self._lock:
            res: list[CommandMeta] = []
            for name in self._recent_history:
                cmd = self._exact_map.get(name)
                if cmd and cmd not in res:
                    res.append(cmd)
                if len(res) >= limit:
                    break
            return res


def build_default_registry() -> CommandRegistry:
    """Create and populate the default CommandRegistry with core Agent47 commands."""
    commands = [
        # Conversation
        CommandMeta(
            name="/help",
            category="Conversation",
            description="Show basic commands and quick start guide",
            usage="/help",
            examples=("/help",),
            keywords=("help", "info", "usage", "guide", "commands"),
            rank=10,
        ),
        CommandMeta(
            name="/status",
            category="Conversation",
            description="Show session overview (workspace, model, mode, auth, keys)",
            usage="/status",
            examples=("/status",),
            keywords=("status", "state", "info", "workspace", "auth"),
            rank=20,
        ),
        CommandMeta(
            name="/model",
            aliases=("/models",),
            category="Models",
            description="Change or inspect the active AI model",
            subcommands=("select", "list"),
            usage="/model\n/model <name>\n/model select",
            examples=("/model", "/model gemini-2.5-flash", "/model select"),
            keywords=("model", "models", "provider", "llm", "gemini", "openai", "anthropic"),
            rank=30,
        ),
        CommandMeta(
            name="/keys",
            category="Keys",
            description="Inspect API key configuration status & fallback chain",
            subcommands=("add", "remove", "list"),
            usage="/keys",
            examples=("/keys",),
            keywords=("keys", "api", "provider", "credentials", "auth", "keyring"),
            rank=40,
        ),
        CommandMeta(
            name="/clear",
            category="Conversation",
            description="Clear the terminal screen",
            usage="/clear",
            examples=("/clear",),
            keywords=("clear", "cls", "clean"),
            rank=50,
        ),
        CommandMeta(
            name="/exit",
            aliases=("/quit", "/q", "/stop"),
            category="Conversation",
            description="Exit the interactive Agent47 session",
            usage="/exit",
            examples=("/exit", "/quit"),
            keywords=("exit", "quit", "stop", "close", "bye"),
            rank=60,
        ),
        # Workspace
        CommandMeta(
            name="/files",
            category="Workspace",
            description="Show current workspace root path",
            usage="/files",
            examples=("/files",),
            keywords=("files", "workspace", "path", "dir", "directory"),
            rank=70,
        ),
        CommandMeta(
            name="/cwd",
            category="Workspace",
            description="Change or inspect current working directory",
            usage="/cwd <path>",
            examples=("/cwd ~/projects/my-repo",),
            keywords=("cwd", "cd", "directory", "folder", "path", "workspace"),
            rank=80,
        ),
        CommandMeta(
            name="/write",
            category="Workspace",
            description="Enable write mode (allow file modifications and shell execution)",
            usage="/write",
            examples=("/write",),
            keywords=("write", "mutation", "execute", "apply", "enable"),
            rank=90,
        ),
        CommandMeta(
            name="/dry-run",
            category="Workspace",
            description="Enable dry-run mode (read-only, prevent mutations)",
            usage="/dry-run",
            examples=("/dry-run",),
            keywords=("dry-run", "readonly", "safe", "inspect", "preview"),
            rank=100,
        ),
        CommandMeta(
            name="/diff-view",
            aliases=("/diff",),
            category="Workspace",
            description="View the last generated patch in the interactive diff viewer",
            usage="/diff-view",
            examples=("/diff-view", "/diff"),
            keywords=("diff", "view", "patch", "changes", "viewer"),
            rank=105,
        ),
        CommandMeta(
            name="/diff-mode",
            category="Workspace",
            description="Set preferred interactive diff viewer mode (unified or side-by-side)",
            subcommands=("unified", "side-by-side"),
            usage="/diff-mode\n/diff-mode unified\n/diff-mode side-by-side",
            examples=("/diff-mode", "/diff-mode side-by-side", "/diff-mode unified"),
            keywords=("diff-mode", "mode", "side-by-side", "unified", "view"),
            rank=106,
        ),
        # History
        CommandMeta(
            name="/history",
            category="History",
            description="Show recent saved agent runs",
            usage="/history",
            examples=("/history",),
            keywords=("history", "runs", "past", "log", "previous"),
            rank=110,
        ),
        CommandMeta(
            name="/history-show",
            category="History",
            description="Show detailed steps for a specific saved run",
            usage="/history-show <run_id>",
            examples=("/history-show 42",),
            keywords=("history-show", "run", "detail", "inspect", "steps"),
            rank=120,
        ),
        CommandMeta(
            name="/resume",
            category="History",
            description="Resume a saved run with optional new instruction",
            usage="/resume <run_id> [extra instruction]",
            examples=("/resume 42", "/resume 42 fix the remaining failing test"),
            keywords=("resume", "continue", "rerun", "retry"),
            rank=130,
        ),
        CommandMeta(
            name="/restore",
            aliases=("/revert",),
            category="History",
            description="Restore file changes made during a prior run",
            usage="/restore <run_id|last>",
            examples=("/restore last", "/restore 42"),
            keywords=("restore", "revert", "undo", "rollback"),
            rank=140,
        ),
        # Models & Profiles
        CommandMeta(
            name="/profile",
            category="Models",
            description="Set model profile (default, planner, coder, reviewer, fast)",
            usage="/profile <name>",
            examples=("/profile planner", "/profile default"),
            keywords=("profile", "mode", "role", "planner", "coder", "fast"),
            rank=150,
        ),
        # Permissions (Hidden by default)
        CommandMeta(
            name="/approve-all",
            category="Permissions",
            description="Auto-approve all actions after the first confirmation",
            hidden=True,
            usage="/approve-all",
            examples=("/approve-all",),
            keywords=("approve-all", "permissions", "auto", "grant"),
            rank=200,
        ),
        CommandMeta(
            name="/auto-read",
            category="Permissions",
            description="Auto-approve read-only tools, require approval for writes",
            hidden=True,
            usage="/auto-read",
            examples=("/auto-read",),
            keywords=("auto-read", "permissions", "readonly", "auto"),
            rank=210,
        ),
        CommandMeta(
            name="/per-action",
            category="Permissions",
            description="Require manual confirmation for every action",
            hidden=True,
            usage="/per-action",
            examples=("/per-action",),
            keywords=("per-action", "permissions", "strict", "confirm"),
            rank=220,
        ),
        # Sandbox (Hidden by default)
        CommandMeta(
            name="/sandbox",
            category="Sandbox",
            description="Create/manage isolated workspace sandbox",
            hidden=True,
            subcommands=("diff", "apply", "off"),
            usage="/sandbox\n/sandbox diff\n/sandbox apply\n/sandbox off",
            examples=("/sandbox", "/sandbox diff", "/sandbox apply", "/sandbox off"),
            keywords=("sandbox", "isolate", "diff", "apply", "container"),
            rank=230,
        ),
        # Configuration (Hidden by default)
        CommandMeta(
            name="/stream",
            category="Configuration",
            description="Turn live response streaming on or off",
            hidden=True,
            subcommands=("off", "on"),
            usage="/stream [off]",
            examples=("/stream off", "/stream on"),
            keywords=("stream", "streaming", "live", "output"),
            rank=240,
        ),
        CommandMeta(
            name="/settings",
            category="Configuration",
            description="View current session configuration settings",
            hidden=True,
            usage="/settings",
            examples=("/settings",),
            keywords=("settings", "config", "options", "preferences"),
            rank=250,
        ),
        CommandMeta(
            name="/steer",
            category="Configuration",
            description="Set guidance to steer agent behavior in future turns",
            hidden=True,
            subcommands=("clear", "off", "reset"),
            usage="/steer <guidance>\n/steer clear",
            examples=("/steer focus on writing unit tests", "/steer clear"),
            keywords=("steer", "steering", "prompt", "guidance", "persona"),
            rank=260,
        ),
        CommandMeta(
            name="/max-steps",
            category="Configuration",
            description="Set maximum agent tool steps per turn",
            hidden=True,
            usage="/max-steps <n>",
            examples=("/max-steps 15",),
            keywords=("max-steps", "steps", "limit"),
            rank=270,
        ),
        CommandMeta(
            name="/max-failures",
            category="Configuration",
            description="Set maximum consecutive tool failure count",
            hidden=True,
            usage="/max-failures <n>",
            examples=("/max-failures 5",),
            keywords=("max-failures", "failures", "retry", "limit"),
            rank=280,
        ),
        # Debug (Hidden by default)
        CommandMeta(
            name="/debug",
            category="Debug",
            description="Show stack trace of the last error",
            hidden=True,
            usage="/debug",
            examples=("/debug",),
            keywords=("debug", "trace", "stacktrace", "error", "exception"),
            rank=300,
        ),
        CommandMeta(
            name="/report",
            category="Debug",
            description="Show info on saved run reports",
            hidden=True,
            usage="/report",
            examples=("/report",),
            keywords=("report", "summary", "evaluation"),
            rank=320,
        ),
        # Advanced reference
        CommandMeta(
            name="/advanced",
            category="Advanced",
            description="Show all advanced and power-user commands",
            usage="/advanced",
            examples=("/advanced",),
            keywords=("advanced", "power", "all", "more", "hidden"),
            rank=330,
        ),
    ]
    return CommandRegistry(commands)


class CommandCompleter(Completer):
    """prompt_toolkit Completer for live slash command discovery & completion."""

    def __init__(self, registry: CommandRegistry) -> None:
        self.registry = registry

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor
        
        # Only trigger if input starts with a slash
        if not text.startswith("/"):
            return

        # Handle subcommand completion (e.g., "/sandbox ")
        if " " in text:
            cmd_part, _, sub_part = text.partition(" ")
            cmd_meta = self.registry.lookup(cmd_part)
            if cmd_meta and cmd_meta.subcommands:
                sub_query = sub_part.lower()
                for sub in cmd_meta.subcommands:
                    if sub.lower().startswith(sub_query):
                        yield Completion(
                            text=sub,
                            start_position=-len(sub_part),
                            display=f"  {sub}",
                            display_meta=f"Option for {cmd_meta.name}",
                        )
            return

        query = text.lower()
        start_pos = -len(text)

        # Empty search: user just typed '/'
        if text == "/":
            recent_cmds = self.registry.recent(limit=3)
            yielded: set[str] = set()

            if recent_cmds:
                for cmd in recent_cmds:
                    icon = CATEGORY_ICONS.get(cmd.category, "•")
                    yield Completion(
                        text=cmd.name,
                        start_position=start_pos,
                        display=f"{icon}  {cmd.name:<16} (Recent)",
                        display_meta=cmd.description,
                    )
                    yielded.add(cmd.name)

            # Essential top commands
            essentials = self.registry.iter_visible()[:8]
            for cmd in essentials:
                if cmd.name in yielded:
                    continue
                icon = CATEGORY_ICONS.get(cmd.category, "•")
                yield Completion(
                    text=cmd.name,
                    start_position=start_pos,
                    display=f"{icon}  {cmd.name:<16}",
                    display_meta=cmd.description,
                )
                yielded.add(cmd.name)

            # More... entry
            yield Completion(
                text="/advanced",
                start_position=start_pos,
                display="⚙  /advanced         Show all commands (More...)",
                display_meta="View every available command by category",
            )
            return

        # Live filtering search using tiered matching
        from .fuzzy import search_commands
        matches = search_commands(query, self.registry, limit=12)

        for cmd in matches:
            icon = CATEGORY_ICONS.get(cmd.category, "•")
            yield Completion(
                text=cmd.name,
                start_position=start_pos,
                display=f"{icon}  {cmd.name:<16}",
                display_meta=cmd.description,
            )
