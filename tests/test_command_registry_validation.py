"""RED contracts for command registry validation and atomic refresh behavior."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin, Command
from services.command import CommandDispatcher


class _CommandOwner(AmadeusPlugin):
    """Mutable owner exercising the public plugin command contract."""

    def __init__(self, owner: str, commands: list[Command]) -> None:
        super().__init__()
        self.name = owner
        self.enabled = True
        self.commands = commands
        self.fail_registration = False

    def register_commands(self) -> list[Command]:
        if self.fail_registration:
            raise RuntimeError("temporary registry failure")
        return list(self.commands)


def _command(
    name: str,
    *,
    aliases: list[str] | None = None,
    pattern: str = "",
    sub_commands: list[Command] | None = None,
) -> Command:
    return Command(
        name=name,
        aliases=list(aliases or []),
        pattern=pattern,
        sub_commands=list(sub_commands or []),
        handler=AsyncMock(),
    )


def _dispatcher(*owners: _CommandOwner) -> CommandDispatcher:
    bus = PluginBus()
    for owner in owners:
        bus.register(owner)
    return CommandDispatcher(bus)


def test_refresh_registration_failure_preserves_snapshot_and_reports_owner() -> None:
    owner = _CommandOwner("flaky_owner", [_command("stable")])
    dispatcher = _dispatcher(owner)
    before = dispatcher.commands
    healthy = dispatcher.health_snapshot()

    assert healthy["status"] == "ok"
    assert dispatcher.is_known("/stable") is True

    owner.fail_registration = True
    with pytest.raises(RuntimeError, match="temporary registry failure"):
        dispatcher.refresh()

    after = dispatcher.commands
    assert after.keys() == before.keys()
    assert all(after[token] is command for token, command in before.items())
    assert dispatcher.is_known("/stable") is True

    health = dispatcher.health_snapshot()
    assert health["status"] == "error"
    assert owner.name in health["last_error"]


@pytest.mark.parametrize(
    ("first", "second", "token"),
    [
        pytest.param(
            _command("Status"),
            _command("status。"),
            "status",
            id="name-name-case-cjk-punctuation",
        ),
        pytest.param(
            _command("Deploy"),
            _command("other", aliases=["deploy！"]),
            "deploy",
            id="name-alias-case-cjk-punctuation",
        ),
        pytest.param(
            _command("first", aliases=["Review?"]),
            _command("second", aliases=["review。"]),
            "review",
            id="alias-alias-case-mixed-punctuation",
        ),
    ],
)
def test_dispatcher_rejects_canonical_subcommand_token_collisions(
    first: Command,
    second: Command,
    token: str,
) -> None:
    root = _command("Ops", sub_commands=[first, second])
    owner = _CommandOwner("subcommand_owner", [root])

    with pytest.raises(ValueError) as exc_info:
        _dispatcher(owner)

    message = str(exc_info.value).lower()
    assert "root" in message
    assert "ops" in message
    assert "token" in message
    assert token in message


def test_dispatcher_rejects_distinct_regexes_with_a_shared_match() -> None:
    broad = _CommandOwner(
        "broad_pattern_owner",
        [_command("broad", pattern=r"^foo(?:\s|$)")],
    )
    exact = _CommandOwner(
        "exact_pattern_owner",
        [_command("exact", pattern=r"^foo$")],
    )

    with pytest.raises(ValueError):
        _dispatcher(broad, exact)


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param(r"^foo!$", id="trailing-punctuation-shadowed"),
        pytest.param(r"^foo\s+bar", id="literal-root-with-arguments-shadowed"),
    ],
)
def test_dispatcher_rejects_pattern_entrypoints_shadowed_by_literal(
    pattern: str,
) -> None:
    literal = _CommandOwner("literal_owner", [_command("foo")])
    patterned = _CommandOwner(
        "pattern_owner",
        [_command("patterned", pattern=pattern)],
    )

    with pytest.raises(ValueError):
        _dispatcher(literal, patterned)


def test_dispatcher_rejects_character_class_pattern_matching_literal_root() -> None:
    literal = _CommandOwner("literal_owner", [_command("m")])
    patterned = _CommandOwner(
        "pattern_owner",
        [_command("letter", pattern=r"^[a-z]$")],
    )

    with pytest.raises(ValueError):
        _dispatcher(literal, patterned)


def test_dispatcher_rejects_unicode_word_pattern_matching_cjk_literal_root() -> None:
    literal = _CommandOwner("literal_owner", [_command("吃")])
    patterned = _CommandOwner(
        "pattern_owner",
        [_command("word", pattern=r"^\w$")],
    )

    with pytest.raises(ValueError):
        _dispatcher(literal, patterned)


def test_dispatcher_rejects_patterns_with_shared_cjk_unicode_word_match() -> None:
    unicode_word = _CommandOwner(
        "unicode_word_owner",
        [_command("🔧", pattern=r"^\w$")],
    )
    exact_cjk = _CommandOwner(
        "exact_cjk_owner",
        [_command("🔥", pattern=r"^吃$")],
    )

    with pytest.raises(ValueError):
        _dispatcher(unicode_word, exact_cjk)


@pytest.mark.parametrize(
    ("left_pattern", "right_pattern", "witness"),
    [
        pytest.param(r"^[a-z]$", "^İ$", "İ", id="latin-dotted-capital-i"),
        pytest.param(r"^\x49$", "^İ$", "İ", id="escaped-latin-capital-i"),
        pytest.param("^Σ$", "^ς$", "ς", id="greek-final-sigma"),
        pytest.param(r"^[a-z]$", "^ſ$", "ſ", id="latin-long-s"),
    ],
)
def test_dispatcher_rejects_python_unicode_ignorecase_pattern_collisions(
    left_pattern: str,
    right_pattern: str,
    witness: str,
) -> None:
    assert re.compile(left_pattern, re.IGNORECASE).match(witness) is not None
    assert re.compile(right_pattern, re.IGNORECASE).match(witness) is not None
    left = _CommandOwner(
        "left_unicode_case_owner",
        [_command("🔧", pattern=left_pattern)],
    )
    right = _CommandOwner(
        "right_unicode_case_owner",
        [_command("🔥", pattern=right_pattern)],
    )

    with pytest.raises(ValueError):
        _dispatcher(left, right)


@pytest.mark.parametrize(
    ("unicode_pattern", "exact_pattern"),
    [
        pytest.param(r"^\d$", "^٣$", id="unicode-decimal"),
        pytest.param(r"^\s$", "^\u00a0$", id="unicode-whitespace"),
        pytest.param(r"^\W$", "^。$", id="unicode-non-word"),
    ],
)
def test_dispatcher_rejects_other_unicode_shorthand_pattern_collisions(
    unicode_pattern: str,
    exact_pattern: str,
) -> None:
    shorthand = _CommandOwner(
        "unicode_shorthand_owner",
        [_command("🔧", pattern=unicode_pattern)],
    )
    exact = _CommandOwner(
        "exact_unicode_owner",
        [_command("🔥", pattern=exact_pattern)],
    )

    with pytest.raises(ValueError):
        _dispatcher(shorthand, exact)


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param(r"^(?=foo)foo$", id="positive-lookahead"),
        pytest.param(r"^(?!bar)foo$", id="negative-lookahead"),
        pytest.param(r"^(?<=/)foo$", id="positive-lookbehind"),
        pytest.param(r"^(?<!/)foo$", id="negative-lookbehind"),
        pytest.param(r"^(foo)\1$", id="numeric-backreference"),
        pytest.param(
            r"^(?P<word>foo)(?P=word)$",
            id="named-backreference",
        ),
        pytest.param(r"^(foo)?(?(1)bar|baz)$", id="conditional-group"),
    ],
)
def test_dispatcher_fails_closed_for_unsupported_advanced_pattern(
    pattern: str,
) -> None:
    owner = _CommandOwner(
        "advanced_pattern_owner",
        [_command("advanced", pattern=pattern)],
    )

    with pytest.raises(ValueError):
        _dispatcher(owner)
