from __future__ import annotations

import pytest

from services.homophone.interpreter import build_homophone_hint, interpret_homophones


def test_interpret_homophones_recognizes_full_attitude_phrase() -> None:
    result = interpret_homophones("窝讨厌泥")

    assert result.original_text == "窝讨厌泥"
    assert result.interpreted_text == "我讨厌你"
    assert result.confidence >= 0.95
    assert any(
        evidence.source_text == "窝讨厌泥"
        and evidence.interpreted_text == "我讨厌你"
        and evidence.start == 0
        and evidence.end == len("窝讨厌泥")
        for evidence in result.evidence
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("窝不讨厌泥", "我不讨厌你"),
        ("  窝讨厌泥！", "  我讨厌你！"),
    ],
)
def test_interpret_homophones_preserves_unmatched_text(source: str, expected: str) -> None:
    result = interpret_homophones(source)

    assert result.original_text == source
    assert result.interpreted_text == expected
    assert result.confidence >= 0.95


def test_interpret_homophones_is_deterministic_and_idempotent() -> None:
    first = interpret_homophones("窝讨厌泥")
    repeated = interpret_homophones("窝讨厌泥")

    assert first == repeated
    assert first.interpreted_text == "我讨厌你"

    interpreted_again = interpret_homophones(first.interpreted_text)
    assert interpreted_again.original_text == "我讨厌你"
    assert interpreted_again.interpreted_text == "我讨厌你"
    assert interpreted_again.confidence == 0.0
    assert interpreted_again.evidence == ()


@pytest.mark.parametrize(
    "text",
    [
        "泥土很好",
        "蜂窝煤",
        "被窝里有泥",
        "我讨厌泥鳅",
        "这窝泥不好挖",
        "窝",
        "泥",
        "今天天气很好",
        "倪好",
        "妮妮",
    ],
)
def test_interpret_homophones_leaves_literal_or_ordinary_text_unchanged(text: str) -> None:
    result = interpret_homophones(text)

    assert result.original_text == text
    assert result.interpreted_text == text
    assert result.confidence == 0.0
    assert result.evidence == ()
    assert build_homophone_hint(text) == ""


@pytest.mark.parametrize(
    "text",
    [
        "酱紫色的颜料",
        "蓝瘦香菇汤很好喝",
        "蓝瘦香菇火锅很好吃",
        "蓝瘦香菇炒饭",
        "酱紫棕色的颜料",
        "酱紫褐色的布",
        "布吉岛屿很多",
    ],
)
def test_interpret_homophones_does_not_match_phrase_prefixes_inside_ordinary_words(
    text: str,
) -> None:
    result = interpret_homophones(text)

    assert result.original_text == text
    assert result.interpreted_text == text
    assert result.confidence == 0.0
    assert result.evidence == ()
    assert build_homophone_hint(text) == ""


@pytest.mark.parametrize(
    ("source", "expected", "matched_surface"),
    [
        ("我真的蓝瘦香菇", "我真的难受想哭", "蓝瘦香菇"),
        ("不要酱紫！", "不要这样子！", "酱紫"),
        ("布吉岛怎么办", "不知道怎么办", "布吉岛"),
    ],
)
def test_interpret_homophones_keeps_clear_phrase_boundaries_recognizable(
    source: str,
    expected: str,
    matched_surface: str,
) -> None:
    result = interpret_homophones(source)

    assert result.original_text == source
    assert result.interpreted_text == expected
    assert result.confidence >= 0.95
    assert {evidence.source_text for evidence in result.evidence} == {matched_surface}
    assert build_homophone_hint(source)


@pytest.mark.parametrize(
    ("source", "expected", "matched_surface"),
    [
        ("不要酱紫做", "不要这样子做", "酱紫"),
        ("你酱紫说不对", "你这样子说不对", "酱紫"),
        ("布吉岛去哪", "不知道去哪", "布吉岛"),
        ("蓝瘦香菇死我了", "难受想哭死我了", "蓝瘦香菇"),
    ],
)
def test_interpret_homophones_recalls_clear_phrases_before_action_suffixes(
    source: str,
    expected: str,
    matched_surface: str,
) -> None:
    result = interpret_homophones(source)

    assert result.original_text == source
    assert result.interpreted_text == expected
    assert result.confidence >= 0.95
    assert {evidence.source_text for evidence in result.evidence} == {matched_surface}
    assert build_homophone_hint(source)


@pytest.mark.parametrize(
    "text",
    [
        "“窝讨厌泥”是什么意思",
        "把“我讨厌你”写成谐音",
        "```text\n窝讨厌泥\n```",
        "看看 https://example.com/窝讨厌泥",
    ],
)
def test_interpret_homophones_does_not_inject_into_carrier_or_metalanguage(text: str) -> None:
    result = interpret_homophones(text)

    assert result.original_text == text
    assert result.interpreted_text == text
    assert result.confidence == 0.0
    assert result.evidence == ()
    assert build_homophone_hint(text) == ""


@pytest.mark.parametrize(
    ("source", "expected", "evidence_sources"),
    [
        (
            "这张图见 https://example.com，窝讨厌泥",
            "这张图见 https://example.com，我讨厌你",
            {"窝讨厌泥"},
        ),
        (
            "“酱紫”是什么意思，但窝讨厌泥",
            "“酱紫”是什么意思，但我讨厌你",
            {"窝讨厌泥"},
        ),
        (
            "```text\n窝讨厌泥\n```\n蟹蟹",
            "```text\n窝讨厌泥\n```\n谢谢",
            {"蟹蟹"},
        ),
    ],
)
def test_interpret_homophones_protects_carrier_spans_without_blocking_plain_text(
    source: str,
    expected: str,
    evidence_sources: set[str],
) -> None:
    result = interpret_homophones(source)

    assert result.original_text == source
    assert result.interpreted_text == expected
    assert {evidence.source_text for evidence in result.evidence} == evidence_sources
    assert build_homophone_hint(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("蟹蟹", "谢谢"),
        ("布吉岛", "不知道"),
        ("有木有", "有没有"),
        ("肿么", "怎么"),
    ],
)
def test_interpret_homophones_supports_common_whole_phrase_mappings(
    source: str,
    expected: str,
) -> None:
    result = interpret_homophones(source)

    assert result.original_text == source
    assert result.interpreted_text == expected
    assert result.confidence >= 0.95
    assert result.evidence


def test_build_homophone_hint_is_explicitly_non_authoritative() -> None:
    hint = build_homophone_hint("窝讨厌泥")

    assert "窝讨厌泥" in hint
    assert "我讨厌你" in hint
    assert "仅作理解" in hint
    assert "不确定时忽略" in hint
    assert "已改写用户输入" not in hint
    assert "用户的真实意思是" not in hint
