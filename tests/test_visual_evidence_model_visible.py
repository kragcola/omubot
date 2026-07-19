"""Behavioral contracts: model-visible visual evidence has no diagnostics."""

from __future__ import annotations

from services.llm.client import _ground_visual_content, _inject_visual_evidence_system_block
from services.media.character_recognizer import CharacterRecognition
from services.media.vision import _STICKER_DESCRIBE_PROMPT, classify_image_intent
from services.media.visual_evidence import (
    VisualEvidence,
    format_visual_evidence_system_block,
    model_visible_visual_summary,
    render_visual_evidence,
    to_image_ref_sidechannel,
)

_DIAGNOSTIC_MARKERS = (
    "置信阈值",
    "低置信候选",
    "置信度",
    "threshold",
    "confidence",
    "distance",
    "0.231",
    "0.247",
    "0.225",
    "0.178",
)


def _sample_evidence() -> VisualEvidence:
    return VisualEvidence(
        image_sha256="deadbeef" * 8,
        image_sha256_short="deadbeef",
        recognitions=(
            CharacterRecognition(
                matched=True,
                character_id="hatsune_miku",
                character_name="初音未来",
                work="Project SEKAI",
                context_label="Project SEKAI / Virtual Singer",
                difference=0.10,
                threshold=0.178,
                detection_count=4,
            ),
            CharacterRecognition(
                matched=False,
                candidate_character_id="kasane_teto",
                candidate_character_name="重音テト",
                difference=0.231,
                threshold=0.178,
                detection_count=4,
            ),
            CharacterRecognition(
                matched=False,
                candidate_character_id="tsurumaki_maki",
                candidate_character_name="弦巻マキ",
                difference=0.247,
                threshold=0.178,
                detection_count=4,
            ),
            CharacterRecognition(
                matched=False,
                candidate_character_id="one",
                candidate_character_name="ONE",
                difference=0.225,
                threshold=0.178,
                detection_count=4,
            ),
        ),
        vision_description="开心地跳起来",
    )


def test_render_visual_evidence_has_no_confidence_or_distance_diagnostics() -> None:
    text = render_visual_evidence(_sample_evidence()) or ""
    assert text, "expected non-empty model-visible evidence"
    for marker in _DIAGNOSTIC_MARKERS:
        assert marker not in text, f"diagnostic leaked into model-visible text: {marker!r} in {text!r}"
    assert "初音未来" in text
    assert "未能" in text or "不确定" in text or "其余" in text


def test_vision_describe_prompt_is_structured_not_chatty_usage_prose() -> None:
    prompt = _STICKER_DESCRIBE_PROMPT
    assert "适合在什么聊天场景" not in prompt
    assert "像真人随手发的表情包说明" not in prompt
    assert "用法" not in prompt or "用途标签" in prompt
    assert any(token in prompt for token in ("对象", "动作", "情绪", "OCR", "结构化"))


def test_classify_image_intent_routes_non_description_without_default_prose() -> None:
    assert classify_image_intent("") == "react"
    assert classify_image_intent("哈哈哈") == "react"
    assert classify_image_intent("图里是什么") == "describe"
    assert classify_image_intent("这是谁") == "identify"
    assert classify_image_intent("帮我看看上面写了什么") == "ocr"


def test_model_visible_summary_intent_gates_vlm_prose() -> None:
    ev = _sample_evidence()
    react = model_visible_visual_summary(ev, intent="react") or ""
    describe = model_visible_visual_summary(ev, intent="describe") or ""
    identify = model_visible_visual_summary(ev, intent="identify") or ""
    assert "初音未来" in react
    assert "开心地跳起来" not in react  # react must not replay VLM prose
    assert "开心地跳起来" in describe or "初音未来" in describe
    assert "初音未来" in identify
    for s in (react, describe, identify):
        for marker in _DIAGNOSTIC_MARKERS:
            assert marker not in s


def test_system_block_composition_respects_intent() -> None:
    side = to_image_ref_sidechannel(_sample_evidence(), intent="react")
    assert side["provenance"] == "visual_system"
    assert len(side["image_sha256"]) == 64
    react_block = format_visual_evidence_system_block([side], user_text="哈哈哈") or ""
    desc_block = format_visual_evidence_system_block([side], user_text="详细描述一下这张图") or ""
    assert "初音未来" in react_block
    assert "开心地跳起来" not in react_block
    assert "视觉侧信道" in react_block
    # describe may expose observation
    assert "开心地" in desc_block or "初音未来" in desc_block
    for marker in ("置信阈值", "低置信候选", "0.231", "threshold="):
        assert marker not in react_block
        assert marker not in desc_block


def test_inject_system_block_from_messages() -> None:
    side = to_image_ref_sidechannel(_sample_evidence(), intent="identify")
    # Side-channel attaches onto image_ref dicts (type required for collect).
    image_ref = {
        "type": "image_ref",
        "path": "/tmp/x.png",
        "media_type": "image/png",
        **side,
    }
    messages = [{"role": "user", "content": [{"type": "text", "text": "这是谁"}, image_ref]}]
    blocks = _inject_visual_evidence_system_block([], messages, user_text="这是谁")
    text = "\n".join(b["text"] for b in blocks)
    assert "初音未来" in text
    assert "0.231" not in text
    assert "置信阈值" not in text


def test_ground_visual_content_has_no_low_confidence_jargon() -> None:
    grounded = _ground_visual_content("这是谁«图片1: 初音未来»", "current")
    text = grounded if isinstance(grounded, str) else str(grounded)
    assert "低置信候选" not in text
    assert "置信" not in text
    assert "不确定" in text or "证据不足" in text
