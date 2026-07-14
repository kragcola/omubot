"""Command dispatcher — matches /slash commands and routes them to plugin handlers.

Plugins declare commands through ``register_commands()``. The Router dispatches
them after access/presence/mute gates and before research/state/plugin hooks,
timeline writes, rendering, or LLM processing. The atomic snapshot retains each
command owner and refreshes with PluginBus state transactions.
"""

from __future__ import annotations

import importlib
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from functools import cache
from typing import Any, cast

import interegular
from loguru import logger

from kernel.types import Command, RichCommandContext
from services.admin_access import is_effective_admin

_sre_runtime = importlib.import_module("_sre")
sre_constants = importlib.import_module("re._constants")
sre_parser = importlib.import_module("re._parser")
_EXTRA_CASES = cast(
    dict[int, tuple[int, ...]],
    vars(importlib.import_module("re._casefix"))["_EXTRA_CASES"],
)

_log = logger.bind(channel="command")
_TRAILING_COMMAND_PUNCT = "。，！？、；：.,!?;:"
_TRAILING_COMMAND_RE = re.compile(rf"[{re.escape(_TRAILING_COMMAND_PUNCT)}]+$")


class CommandDispatcher:
    """Collects commands from PluginBus and dispatches incoming messages."""

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._commands: dict[str, tuple[Command, Any | None]] = {}
        self._patterns: tuple[tuple[re.Pattern[str], Command, Any | None], ...] = ()
        self._refresh_failures = 0
        self._active_refresh_error = ""
        self._last_refresh_error = ""
        bind_registry = getattr(bus, "bind_command_registry", None)
        if callable(bind_registry):
            bind_registry(self)
        else:
            self.refresh()

    def refresh(self) -> None:
        """Prepare and atomically commit a command snapshot."""
        self.commit_refresh(self.prepare_refresh())

    def prepare_refresh(
        self,
    ) -> tuple[
        dict[str, tuple[Command, Any | None]],
        tuple[tuple[re.Pattern[str], Command, Any | None], ...],
    ]:
        """Build and validate a candidate without mutating live state."""
        try:
            return self._build_snapshot()
        except Exception as exc:
            self._refresh_failures += 1
            self._active_refresh_error = str(exc)
            self._last_refresh_error = str(exc)
            _log.error("command registry refresh failed | error={}", exc)
            raise

    def commit_refresh(
        self,
        prepared: tuple[
            dict[str, tuple[Command, Any | None]],
            tuple[tuple[re.Pattern[str], Command, Any | None], ...],
        ],
    ) -> None:
        """Commit a fully validated snapshot; this operation does not perform I/O."""
        commands, patterns = prepared
        self._commands = commands
        self._patterns = patterns
        self._active_refresh_error = ""
        _log.info("commands loaded | count={}", len(commands))

    def snapshot_state(
        self,
    ) -> tuple[
        dict[str, tuple[Command, Any | None]],
        tuple[tuple[re.Pattern[str], Command, Any | None], ...],
    ]:
        """Capture the current active snapshot for transaction rollback."""
        return dict(self._commands), tuple(self._patterns)

    def restore_state(
        self,
        snapshot: tuple[
            dict[str, tuple[Command, Any | None]],
            tuple[tuple[re.Pattern[str], Command, Any | None], ...],
        ],
    ) -> None:
        """Restore an already validated active snapshot without rebuilding it."""
        commands, patterns = snapshot
        self._commands = dict(commands)
        self._patterns = tuple(patterns)
        self._active_refresh_error = ""

    def _build_snapshot(
        self,
    ) -> tuple[
        dict[str, tuple[Command, Any | None]],
        tuple[tuple[re.Pattern[str], Command, Any | None], ...],
    ]:
        """Build and validate a candidate snapshot without mutating live state."""
        commands: dict[str, tuple[Command, Any | None]] = {}
        patterns: list[tuple[re.Pattern[str], Command, Any | None]] = []

        collect_bindings = getattr(self._bus, "collect_command_bindings", None)
        if callable(collect_bindings):
            bindings = list(cast(Iterable[tuple[Any, Command]], collect_bindings()))
        else:
            bindings = [(None, cmd) for cmd in self._bus.collect_commands()]

        for owner, cmd in bindings:
            name = _normalize_command_token(cmd.name).lower()
            _bind_command_token(commands, name, cmd, owner)
            _validate_subcommand_tree(cmd, root=name)
            for alias in cmd.aliases:
                alias_key = _normalize_command_token(alias).lower()
                _bind_command_token(commands, alias_key, cmd, owner)
            if cmd.pattern:
                try:
                    compiled = re.compile(cmd.pattern, re.IGNORECASE)
                except re.error as exc:
                    raise ValueError(
                        "invalid command pattern: "
                        f"owner={_owner_name(owner)!r} command={cmd.name!r} "
                        f"pattern={cmd.pattern!r}: {exc}"
                    ) from exc
                patterns.append((compiled, cmd, owner))

        _validate_pattern_routes(commands, patterns)

        return commands, tuple(patterns)

    def health_snapshot(self) -> dict[str, Any]:
        """Return command-registry diagnostics for runtime health aggregation."""
        return {
            "status": "error" if self._active_refresh_error else "ok",
            "command_count": len(self._commands),
            "pattern_count": len(self._patterns),
            "failed_refreshes": self._refresh_failures,
            "active_error": self._active_refresh_error,
            "last_error": self._last_refresh_error,
        }

    @property
    def commands(self) -> dict[str, Command]:
        return {name: entry[0] for name, entry in self._commands.items()}

    def is_known(self, text: str) -> bool:
        """Return whether slash text resolves to an enabled command owner."""
        stripped_text = (text or "").strip()
        if not stripped_text.startswith("/"):
            return False
        stripped = stripped_text[1:].strip()
        raw_name, _ = _split_once(stripped)
        name = _normalize_command_token(raw_name).lower()
        entry = self._commands.get(name) if name else None
        if entry is None:
            for pattern, pattern_cmd, owner in self._patterns:
                if pattern.match(stripped) is not None:
                    entry = (pattern_cmd, owner)
                    break
        return entry is not None and _owner_enabled(entry[1])

    async def dispatch(
        self,
        bot: Any,
        event: Any,
        text: str,
        *,
        is_private: bool,
        user_id: str,
        group_id: str | None,
        plugin_ctx: Any,
    ) -> bool:
        """Parse and execute a command from message text.

        Returns True for every slash command consumed by the command layer:
        known commands, disabled-owner commands, and unknown slash roots.
        Returns False only for non-slash text, which may continue to normal chat.
        """
        stripped_text = text.strip()
        if not stripped_text.startswith("/"):
            return False

        stripped = stripped_text[1:].strip()
        raw_name, args = _split_once(stripped)
        name = _normalize_command_token(raw_name).lower()

        entry = self._commands.get(name) if name else None
        if entry is None:
            for pattern, pattern_cmd, owner in self._patterns:
                match = pattern.match(stripped)
                if match is None:
                    continue
                entry = (pattern_cmd, owner)
                args = stripped[match.end():].lstrip()
                break

        if entry is None or not _owner_enabled(entry[1]):
            token = f"/{name}" if name else "/"
            await _send(bot, event, f"未知指令：{token}")
            return True

        cmd, owner = entry

        return await self._dispatch_cmd(
            cmd,
            args,
            bot,
            event,
            is_private,
            user_id,
            group_id,
            plugin_ctx,
            owner=owner,
        )

    async def _dispatch_cmd(
        self,
        cmd: Command,
        args: str,
        bot: Any,
        event: Any,
        is_private: bool,
        user_id: str,
        group_id: str | None,
        plugin_ctx: Any,
        root_cmd: Command | None = None,
        owner: Any | None = None,
    ) -> bool:
        """Recursively dispatch a command, trying sub-commands first.

        Guard checks (admin_only, private_only, require_args) run before the
        handler.  Unknown sub-commands produce a helpful error listing available
        sub-commands.
        """
        if root_cmd is None:
            root_cmd = cmd

        if not _owner_enabled(owner):
            await _send(bot, event, f"未知指令：/{root_cmd.name}")
            return True

        args = args.strip()

        # ---- sub-command matching ----
        if cmd.sub_commands and args:
            raw_subcommand, sub_args = _split_once(args)
            first_word = _normalize_command_token(raw_subcommand).lower()
            sub = _find_sub(cmd.sub_commands, first_word)
            if sub is not None:
                return await self._dispatch_cmd(
                    sub, sub_args, bot, event, is_private,
                    user_id, group_id, plugin_ctx, root_cmd=root_cmd, owner=owner,
                )

            # Unknown sub-command
            if not cmd.passthrough_unknown:
                visible = [s for s in cmd.sub_commands if not s.hidden]
                names = ", ".join(f"/{root_cmd.name} {s.name}" for s in visible)
                await _send(bot, event, f"未知子命令。可用：{names}")
                return True
            # else: fall through to call the parent handler with original args

        # ---- guard checks (inherit admin/private from root) ----
        if (cmd.admin_only or root_cmd.admin_only) and not is_effective_admin(
            user_id,
            plugin_ctx,
        ):
            await _send(bot, event, "无权限")
            return True
        if (cmd.private_only or root_cmd.private_only) and not is_private:
            await _send(bot, event, "请在私聊中使用此指令")
            return True
        if cmd.require_args and not args:
            await _send(bot, event, f"用法：{cmd.usage}")
            return True

        # ---- execute handler ----
        ctx = RichCommandContext(
            bot=bot,
            event=event,
            args=args,
            is_private=is_private,
            user_id=user_id,
            group_id=group_id,
            command=cmd,
            root_command=root_cmd,
            plugin_ctx=plugin_ctx,
        )

        try:
            await cmd.handler(ctx)
        except Exception:
            _log.warning("command_dispatch_failed | name={}", cmd.name, exc_info=True)
            await _send(bot, event, "指令执行失败，请稍后再试")

        return True


def _normalize_command_token(token: str) -> str:
    """Trim trailing accidental punctuation from a command token only."""
    cleaned = (token or "").strip()
    if not cleaned:
        return ""
    return _TRAILING_COMMAND_RE.sub("", cleaned)


def _split_once(text: str) -> tuple[str, str]:
    """Split a command head from arguments using any Unicode whitespace."""
    parts = (text or "").split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0], parts[1] if len(parts) > 1 else ""


def _owner_enabled(owner: Any | None) -> bool:
    return owner is None or bool(getattr(owner, "enabled", False))


def _owner_name(owner: Any | None) -> str:
    if owner is None:
        return "<unowned>"
    return str(getattr(owner, "name", "") or type(owner).__name__)


def _bind_command_token(
    commands: dict[str, tuple[Command, Any | None]],
    token: str,
    command: Command,
    owner: Any | None,
) -> None:
    previous = commands.get(token)
    if previous is not None:
        previous_command, previous_owner = previous
        raise ValueError(
            "command token collision: "
            f"token={token!r} "
            f"owner={_owner_name(previous_owner)!r} command={previous_command.name!r} "
            f"conflicts_with_owner={_owner_name(owner)!r} command={command.name!r}"
        )
    commands[token] = (command, owner)


def _validate_subcommand_tree(command: Command, *, root: str) -> None:
    bindings: dict[str, Command] = {}
    for subcommand in command.sub_commands:
        tokens = [subcommand.name, *subcommand.aliases]
        for raw_token in tokens:
            token = _normalize_command_token(raw_token).lower()
            previous = bindings.get(token)
            if previous is not None:
                raise ValueError(
                    "subcommand token collision: "
                    f"root={root!r} token={token!r} "
                    f"command={previous.name!r} "
                    f"conflicts_with_command={subcommand.name!r}"
                )
            bindings[token] = subcommand
        child_root = f"{root} {_normalize_command_token(subcommand.name).lower()}"
        _validate_subcommand_tree(subcommand, root=child_root)


def _strip_regex_anchors(source: str) -> str:
    """Remove zero-width anchors outside character classes for FSM analysis."""
    output: list[str] = []
    escaped = False
    in_character_class = False
    for char in source:
        if escaped:
            output.extend(("\\", char))
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
        elif char == "]" and in_character_class:
            in_character_class = False
        if char in {"^", "$"} and not in_character_class:
            continue
        output.append(char)
    if escaped:
        output.append("\\")
    return "".join(output)


def _unsupported_pattern_construct(source: str) -> str | None:
    """Return constructs whose Python ``re`` semantics cannot be modeled safely."""
    group_constructs = {
        "(?=": "positive lookahead",
        "(?!": "negative lookahead",
        "(?<=": "positive lookbehind",
        "(?<!": "negative lookbehind",
        "(?P=": "named backreference",
        '(?(': "conditional group",
    }
    index = 0
    in_character_class = False
    while index < len(source):
        char = source[index]
        if char == "\\":
            escaped = source[index + 1] if index + 1 < len(source) else ""
            if in_character_class and escaped in "WDS":
                return f"negated Unicode shorthand \\{escaped} inside character class"
            if not in_character_class and escaped in "bB":
                return f"Unicode word boundary \\{escaped}"
            if (
                not in_character_class
                and escaped in "123456789"
            ):
                return "numeric backreference"
            index += 2
            continue
        if char == "[":
            in_character_class = True
        elif char == "]" and in_character_class:
            in_character_class = False
        elif not in_character_class:
            for marker, description in group_constructs.items():
                if source.startswith(marker, index):
                    return description
        index += 1
    return None


def _escape_automaton_character_class_codepoint(codepoint: int) -> str:
    char = chr(codepoint)
    named_controls = {
        9: r"\t",
        10: r"\n",
        11: r"\v",
        12: r"\f",
        13: r"\r",
    }
    if codepoint in named_controls:
        return named_controls[codepoint]
    if codepoint < 32 or codepoint == 127:
        return f"\\x{codepoint:02x}"
    if char in {"\\", "]", "-", "^"}:
        return f"\\{char}"
    return char


def _character_class_content(codepoints: Iterable[int]) -> str:
    ordered = sorted(set(codepoints))
    ranges: list[tuple[int, int]] = []
    for codepoint in ordered:
        if not ranges or codepoint != ranges[-1][1] + 1:
            ranges.append((codepoint, codepoint))
        else:
            ranges[-1] = (ranges[-1][0], codepoint)

    parts: list[str] = []
    for start, end in ranges:
        parts.append(_escape_automaton_character_class_codepoint(start))
        if end != start:
            parts.extend(("-", _escape_automaton_character_class_codepoint(end)))
    return "".join(parts)


@cache
def _unicode_character_class(shorthand: str) -> str:
    predicates = {
        "w": lambda char: char == "_" or char.isalnum(),
        "d": str.isdecimal,
        "s": str.isspace,
    }
    predicate = predicates[shorthand]
    return _character_class_content(
        codepoint
        for codepoint in range(sys.maxunicode + 1)
        if predicate(chr(codepoint))
    )


@cache
def _unicode_casefold_tables() -> tuple[dict[int, frozenset[int]], dict[int, frozenset[int]]]:
    lower_buckets: defaultdict[int, set[int]] = defaultdict(set)
    for codepoint in range(sys.maxunicode + 1):
        if _sre_runtime.unicode_iscased(codepoint):
            lower_buckets[_sre_runtime.unicode_tolower(codepoint)].add(codepoint)

    extra_graph: defaultdict[int, set[int]] = defaultdict(set)
    for codepoint, extras in _EXTRA_CASES.items():
        key = _sre_runtime.unicode_tolower(codepoint)
        for extra in extras:
            extra_key = _sre_runtime.unicode_tolower(extra)
            extra_graph[key].add(extra_key)
            extra_graph[extra_key].add(key)
    return (
        {key: frozenset(values) for key, values in lower_buckets.items()},
        {key: frozenset(values) for key, values in extra_graph.items()},
    )


@cache
def _python_ignorecase_codepoints(codepoint: int) -> frozenset[int]:
    if not _sre_runtime.unicode_iscased(codepoint):
        return frozenset({codepoint})
    lower_buckets, extra_graph = _unicode_casefold_tables()
    initial = _sre_runtime.unicode_tolower(codepoint)
    keys = {initial}
    pending = [initial]
    while pending:
        for related in extra_graph.get(pending.pop(), ()):
            if related not in keys:
                keys.add(related)
                pending.append(related)
    equivalents = {codepoint}
    for key in keys:
        equivalents.add(key)
        equivalents.update(lower_buckets.get(key, ()))
    return frozenset(equivalents)


def _interegular_ignorecase_codepoints(codepoint: int) -> frozenset[int]:
    char = chr(codepoint)
    variants = {char, char.lower(), char.upper()}
    return frozenset(ord(value) for value in variants if len(value) == 1)


def _casefold_class_extras(source: str) -> frozenset[int]:
    try:
        parsed = sre_parser.parse(source, 0)
    except re.error:
        return frozenset()
    if len(parsed.data) != 1:
        return frozenset()
    opcode, argument = parsed.data[0]
    if opcode is sre_constants.LITERAL:
        items = [(sre_constants.LITERAL, argument)]
    elif opcode is sre_constants.IN:
        items = argument
    else:
        return frozenset()

    explicit: set[int] = set()
    for item_opcode, item_argument in items:
        if item_opcode is sre_constants.LITERAL:
            explicit.add(int(item_argument))
        elif item_opcode is sre_constants.RANGE:
            start, end = item_argument
            explicit.update(range(int(start), int(end) + 1))

    extras: set[int] = set()
    for codepoint in explicit:
        extras.update(
            _python_ignorecase_codepoints(codepoint)
            - _interegular_ignorecase_codepoints(codepoint)
        )
    return frozenset(extras)


def _character_class_end(source: str, start: int) -> int | None:
    escaped = False
    for index in range(start + 1, len(source)):
        char = source[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "]" and index > start + 1:
            return index
    return None


def _expand_unicode_casefold_gaps(source: str) -> str:
    """Over-approximate Python ``re.IGNORECASE`` where interegular is narrower."""
    output: list[str] = []
    index = 0
    while index < len(source):
        if source.startswith("(?P<", index):
            end = source.find(">", index + 4)
            if end == -1:
                output.append(source[index:])
                break
            output.append(source[index:end + 1])
            index = end + 1
            continue
        if source.startswith("(?", index):
            flag_end = index + 2
            while flag_end < len(source) and source[flag_end] in "aiLmsux-":
                flag_end += 1
            if flag_end > index + 2 and flag_end < len(source) and source[flag_end] in {":", ")"}:
                output.append(source[index:flag_end + 1])
                index = flag_end + 1
                continue
        char = source[index]
        if char == "[":
            end = _character_class_end(source, index)
            if end is None:
                output.append(source[index:])
                break
            class_source = source[index:end + 1]
            extras = _casefold_class_extras(class_source)
            if extras:
                class_source = class_source[:-1] + _character_class_content(extras) + "]"
            output.append(class_source)
            index = end + 1
            continue
        if char == "\\" and index + 1 < len(source):
            escape_widths = {"x": 2, "u": 4, "U": 8}
            marker = source[index + 1]
            width = escape_widths.get(marker)
            if width is not None:
                end = index + 2 + width
                raw_codepoint = source[index + 2:end]
                try:
                    codepoint = int(raw_codepoint, 16)
                    chr(codepoint)
                except (ValueError, OverflowError):
                    pass
                else:
                    extras = (
                        _python_ignorecase_codepoints(codepoint)
                        - _interegular_ignorecase_codepoints(codepoint)
                    )
                    if extras:
                        equivalents = _python_ignorecase_codepoints(codepoint)
                        output.append(f"[{_character_class_content(equivalents)}]")
                        index = end
                        continue
            output.extend((char, source[index + 1]))
            index += 2
            continue
        if char not in ".^$*+?{}[]\\|()":
            extras = (
                _python_ignorecase_codepoints(ord(char))
                - _interegular_ignorecase_codepoints(ord(char))
            )
            if extras:
                equivalents = _python_ignorecase_codepoints(ord(char))
                output.append(f"[{_character_class_content(equivalents)}]")
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _expand_unicode_shorthands(source: str) -> str:
    """Align interegular character classes with Python's Unicode ``re`` mode."""
    output: list[str] = []
    index = 0
    in_character_class = False
    while index < len(source):
        char = source[index]
        if char == "\\" and index + 1 < len(source):
            shorthand = source[index + 1]
            if shorthand in "wWdDsS":
                content = _unicode_character_class(shorthand.lower())
                if in_character_class:
                    output.append(content)
                else:
                    negated = "^" if shorthand.isupper() else ""
                    output.append(f"[{negated}{content}]")
                index += 2
                continue
            output.extend((char, shorthand))
            index += 2
            continue
        if char == "[":
            in_character_class = True
        elif char == "]" and in_character_class:
            in_character_class = False
        output.append(char)
        index += 1
    return "".join(output)


def _pattern_automaton(pattern: re.Pattern[str]) -> Any:
    unsupported = _unsupported_pattern_construct(pattern.pattern)
    if unsupported is not None:
        raise ValueError(
            "command pattern cannot be analyzed safely: "
            f"pattern={pattern.pattern}: unsupported {unsupported}"
        )
    source = _strip_regex_anchors(pattern.pattern)
    source = _expand_unicode_casefold_gaps(source)
    source = _expand_unicode_shorthands(source)
    expression = f"(?i:(?:{source}).*)"
    try:
        return interegular.parse_pattern(expression).to_fsm()
    except Exception as exc:
        raise ValueError(
            "command pattern cannot be analyzed safely: "
            f"pattern={pattern.pattern}: {exc}"
        ) from exc


def _literal_route_automaton(token: str) -> Any:
    escaped_token = re.escape(token)
    escaped_punctuation = re.escape(_TRAILING_COMMAND_PUNCT)
    expression = (
        f"(?i:{escaped_token}[{escaped_punctuation}]*"
        r"(?:\s.*|))"
    )
    return interegular.parse_pattern(expression).to_fsm()


def _automaton_witness(first: Any, second: Any) -> str | None:
    intersection = first & second
    if intersection.empty():
        return None
    return "".join(next(intersection.strings()))


def _validate_pattern_routes(
    commands: dict[str, tuple[Command, Any | None]],
    patterns: list[tuple[re.Pattern[str], Command, Any | None]],
) -> None:
    automata: list[Any] = []
    for pattern, pattern_command, pattern_owner in patterns:
        pattern_automaton = _pattern_automaton(pattern)
        automata.append(pattern_automaton)
        for token, literal in commands.items():
            literal_command, literal_owner = literal
            if pattern_command is literal_command and pattern_owner is literal_owner:
                continue
            witness = _automaton_witness(
                pattern_automaton,
                _literal_route_automaton(token),
            )
            if witness is None:
                continue
            raise ValueError(
                "command pattern/token collision: "
                f"pattern={pattern.pattern} witness={witness!r} "
                f"owner={_owner_name(pattern_owner)!r} "
                f"command={pattern_command.name!r} token={token!r} "
                f"conflicts_with_owner={_owner_name(literal_owner)!r} "
                f"command={literal_command.name!r}"
            )

    for index, (pattern, command, owner) in enumerate(patterns):
        for other_index in range(index):
            other_pattern, other_command, other_owner = patterns[other_index]
            witness = _automaton_witness(automata[index], automata[other_index])
            if witness is None:
                continue
            raise ValueError(
                "command pattern collision: "
                f"pattern={other_pattern.pattern} "
                f"owner={_owner_name(other_owner)!r} command={other_command.name!r} "
                f"conflicts_with_pattern={pattern.pattern} "
                f"conflicts_with_owner={_owner_name(owner)!r} command={command.name!r} "
                f"witness={witness!r}"
            )


def _find_sub(sub_commands: list[Command], word: str) -> Command | None:
    """Find a sub-command by name or alias. Returns None if not found."""
    for sub in sub_commands:
        name = _normalize_command_token(sub.name).lower()
        aliases = {_normalize_command_token(alias).lower() for alias in sub.aliases}
        if name == word or word in aliases:
            return sub
    return None


async def _send(bot: Any, event: Any, text: str) -> None:
    """Send a plain-text reply."""
    from nonebot.adapters.onebot.v11 import Message
    await bot.send(event, Message(text))
