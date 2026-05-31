from __future__ import annotations

from services.upstream_filter import should_drop_message


def test_upstream_filter_drops_peer_bot_message() -> None:
    result = should_drop_message(
        42,
        "状态回执",
        "100",
        enabled=True,
        known_other_bots={"100": ["42"]},
        command_patterns=["#napcat"],
    )

    assert result.should_drop is True
    assert result.reason == "peer_bot_message"


def test_upstream_filter_drops_line_start_command() -> None:
    result = should_drop_message(
        7,
        "#napcat info",
        "100",
        enabled=True,
        known_other_bots={},
        command_patterns=["#napcat", "/napcat"],
    )

    assert result.should_drop is True
    assert result.reason == "upstream_command"


def test_upstream_filter_keeps_inline_reference() -> None:
    result = should_drop_message(
        7,
        "刚才用 #napcat 查了一下",
        "100",
        enabled=True,
        known_other_bots={},
        command_patterns=["#napcat"],
    )

    assert result.should_drop is False


def test_upstream_filter_disabled_short_circuits() -> None:
    result = should_drop_message(
        42,
        "#napcat info",
        "100",
        enabled=False,
        known_other_bots={"100": ["42"]},
        command_patterns=["#napcat"],
    )

    assert result.should_drop is False
