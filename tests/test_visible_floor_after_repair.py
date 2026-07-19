"""Final visible floor after repair/rewrite + punctuation segment rejection."""

from __future__ import annotations

from services.llm.client import (
    LLMClient,
    _inject_visual_evidence_system_block,
    _is_blank_or_punctuation_only_reply,
    _messages_have_visual_sidechannel,
    _strip_visual_diagnostic_phrases,
)
from services.llm.segmentation import _is_punctuation_only_segment, natural_split


class _NeverMergeRng:
    def random(self) -> float:
        return 0.0


def test_finalize_rejects_ellipsis_and_punctuation_only() -> None:
    client = object.__new__(LLMClient)
    for bad in ("...", "…", "……", "!!!", "？？", ".,.", "   ", "☆", "~"):
        out, state = LLMClient._finalize_visible_reply(
            client,
            reply=bad,
            session_id="s1",
            force_reply=False,
            has_visible_tool_output=False,
            is_group=True,
        )
        assert out == "", f"expected suppress for {bad!r}, got {out!r} state={state}"
        assert state == "suppressed"


def test_finalize_keeps_emoji_reactions() -> None:
    client = object.__new__(LLMClient)
    for good in ("😂", "👍", "🎉", "😅哈哈"):
        assert _is_blank_or_punctuation_only_reply(good) is False or "哈" in good
        if good in {"😂", "👍", "🎉"}:
            assert _is_punctuation_only_segment(good) is False
            out, state = LLMClient._finalize_visible_reply(
                client,
                reply=good,
                session_id="s1",
                force_reply=False,
                has_visible_tool_output=False,
                is_group=True,
            )
            assert out == good
            assert state == "reply"


def test_finalize_accepts_natural_language() -> None:
    client = object.__new__(LLMClient)
    out, state = LLMClient._finalize_visible_reply(
        client,
        reply="被你发现啦，下次注意。",
        session_id="s1",
        force_reply=False,
        has_visible_tool_output=False,
        is_group=True,
    )
    assert "被你发现" in out
    assert state == "reply"


def test_natural_split_never_emits_leading_ellipsis_only_segment() -> None:
    rng = _NeverMergeRng()
    for text in ("…然后呢", "……然后呢", "...然后呢", "！！冲啊"):
        segments = natural_split(text, rng=rng)
        for seg in segments:
            body = "".join(ch for ch in seg if not ch.isspace())
            assert body, f"empty segment from {text!r}: {segments!r}"
            if all(not (ch.isalnum() or "\u4e00" <= ch <= "\u9fff") for ch in body):
                # emoji-only is ok; pure punct is not
                if any(ord(ch) > 0x2000 for ch in body):
                    continue
                raise AssertionError(f"punctuation-only segment {seg!r} from {text!r}: {segments!r}")


def test_post_transform_floor_rejects_ellipsis_after_repair_candidate() -> None:
    """Simulate persona-repair / humanization rewrite output re-entering finalize."""
    client = object.__new__(LLMClient)
    # After rewrite, model-like post-transform yielded ellipsis / punct only.
    for repaired in ("...", "…", "……", "!!!", "☆", "   "):
        out, state = LLMClient._finalize_visible_reply(
            client,
            reply=repaired,
            session_id="s1",
            force_reply=False,
            has_visible_tool_output=False,
            is_group=True,
        )
        assert out == "", f"post-transform must not pass {repaired!r}"
        assert state == "suppressed"
        # And must not reach segmentation as a sendable segment
        assert natural_split(repaired, rng=_NeverMergeRng()) == []


def test_tool_exhausted_path_same_floor() -> None:
    """Tool-exhausted path uses the same finalize helper (contract)."""
    client = object.__new__(LLMClient)
    # Same helper is called on tool-exhausted path in production chat().
    out, state = LLMClient._finalize_visible_reply(
        client,
        reply="...",
        session_id="s1",
        force_reply=True,  # addressed / force may fallback
        has_visible_tool_output=False,
        is_group=False,
    )
    # force_reply private: falls back to non-empty ack, never ellipsis
    assert out != "..."
    assert state in {"fallback", "reply"}
    if state == "fallback":
        assert out  # non-empty floor


def test_visual_diagnostic_strip_only_known_forms() -> None:
    dirty = "我觉得是初音未来，置信阈值0.18，distance=0.12，低置信候选：重音テト"
    cleaned = _strip_visual_diagnostic_phrases(dirty)
    assert "置信阈值" not in cleaned
    assert "distance=" not in cleaned
    assert "低置信候选" not in cleaned
    assert "初音未来" in cleaned
    # non-visual confidence discussion preserved when no markers
    ordinary = "我有信心你会喜欢"
    assert _strip_visual_diagnostic_phrases(ordinary) == ordinary


def test_inject_visual_evidence_system_block_intent_gated() -> None:
    refs_msg = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "哈哈哈"},
                {
                    "type": "image_ref",
                    "path": "/tmp/x.png",
                    "media_type": "image/png",
                    "image_sha256": "a" * 64,
                    "image_sha256_short": "aaaaaaaa",
                    "visual_intent": "react",
                    "visual_observation": "开心地跳起来，双手比耶",
                    "visual_identity": ["初音未来"],
                    "visual_ocr": "",
                    "visual_summary": "初音未来",
                    "provenance": "visual_system",
                },
            ],
        }
    ]
    assert _messages_have_visual_sidechannel(refs_msg) is True
    # react: identity only, not full VLM prose
    blocks = _inject_visual_evidence_system_block([], refs_msg, user_text="哈哈哈")
    joined = "\n".join(b.get("text", "") for b in blocks)
    assert "初音未来" in joined
    assert "双手比耶" not in joined
    assert "置信" not in joined
    # describe: can include observation summary
    blocks2 = _inject_visual_evidence_system_block([], refs_msg, user_text="图里是什么")
    joined2 = "\n".join(b.get("text", "") for b in blocks2)
    assert "初音未来" in joined2 or "开心地" in joined2
