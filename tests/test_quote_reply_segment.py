"""Tests for quote reply anchor handling."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from kernel.config import ResolvedHumanization
from services.llm.client import _apply_quote_reply_anchor, _extract_quote_anchor, _quote_reply_enabled


def test_extract_quote_anchor_converts_valid_msg_id_to_cq_reply() -> None:
    msg_id, text = _extract_quote_anchor(' <quote msg_id="12345"/> 好的我接这句')

    assert msg_id == "12345"
    assert text == "好的我接这句"
    assert _apply_quote_reply_anchor(text, msg_id) == "[CQ:reply,id=12345]好的我接这句"


def test_extract_quote_anchor_strips_invalid_msg_id() -> None:
    msg_id, text = _extract_quote_anchor('<quote msg_id="abc"/> 好的')

    assert msg_id is None
    assert text == "好的"


def test_economy_shape_strips_quote_anchor() -> None:
    msg_id, text = _extract_quote_anchor('<quote msg_id="12345"/> 好的')
    if not _quote_reply_enabled(ResolvedHumanization()):
        msg_id = None

    assert _apply_quote_reply_anchor(text, msg_id) == "好的"


def test_multiple_quote_anchors_take_first_and_strip_all_tags() -> None:
    msg_id, text = _extract_quote_anchor('<quote msg_id="111"/>前半句 <quote msg_id="222"/>后半句')

    assert msg_id == "111"
    assert text == "前半句 后半句"


def test_explicit_quote_flag_is_single_source() -> None:
    enabled = cast(
        ResolvedHumanization,
        SimpleNamespace(qq_interactions_quote_reply_enabled=True),
    )
    disabled = cast(
        ResolvedHumanization,
        SimpleNamespace(
            qq_interactions_quote_reply_enabled=False,
            streaming_segment_enabled=True,
            pause_then_extend_enabled=True,
        ),
    )
    legacy_shape_only = cast(
        ResolvedHumanization,
        SimpleNamespace(
            streaming_segment_enabled=True,
            pause_then_extend_enabled=True,
        ),
    )

    assert _quote_reply_enabled(enabled) is True
    assert _quote_reply_enabled(disabled) is False
    assert _quote_reply_enabled(legacy_shape_only) is False
