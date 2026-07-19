"""Style extraction fail-closed for visual/system/bot-derived provenance."""

from __future__ import annotations

import inspect

from services.style.extractor import is_style_human_evidence_eligible
from services.style.manual_extract import run_style_manual_extract


def test_visual_marker_rows_are_not_human_eligible() -> None:
    assert not is_style_human_evidence_eligible(
        {"role": "user", "content_text": "«图片1: 初音未来开心地跳起来，适合在聊天中卖萌»"}
    )
    assert not is_style_human_evidence_eligible(
        {"role": "user", "content_text": "[图片: 检测到4个角色/头像；可信识别：初音未来]"}
    )
    assert not is_style_human_evidence_eligible(
        {"role": "user", "content_text": "«当前视觉指代约束: 证据不足时说不太确定»"}
    )
    assert not is_style_human_evidence_eligible(
        {"role": "user", "content_text": "画面中约有4个角色/头像；可确认：初音未来"}
    )
    assert not is_style_human_evidence_eligible(
        {"role": "user", "content_text": "检测到3个角色/头像", "provenance": "visual_system"}
    )
    assert not is_style_human_evidence_eligible(
        {"role": "user", "content_text": "检测到3个角色/头像"}
    )


def test_bot_and_system_rows_are_not_human_eligible() -> None:
    assert not is_style_human_evidence_eligible(
        {"role": "assistant", "content_text": "被你发现啦"}
    )
    assert not is_style_human_evidence_eligible(
        {"role": "system", "content_text": "用户发了张图"}
    )
    assert not is_style_human_evidence_eligible(
        {"role": "user", "content_text": "普通聊天", "source_type": "system"}
    )


def test_plain_user_chat_is_human_eligible() -> None:
    assert is_style_human_evidence_eligible(
        {"role": "user", "content_text": "今天好热啊，下班了吗"}
    )
    # Ordinary human sentence containing generic 检测到 remains eligible
    assert is_style_human_evidence_eligible(
        {"role": "user", "content_text": "我检测到你心情不好"}
    )


def test_manual_extract_invokes_eligibility_helper() -> None:
    src = inspect.getsource(run_style_manual_extract)
    assert "is_style_human_evidence_eligible" in src
