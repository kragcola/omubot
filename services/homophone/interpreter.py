"""Pure, conservative homophone interpretation contract."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class _PhraseRule:
    rule_id: str
    source_text: str
    interpreted_text: str
    confidence: float = 0.98
    allowed_cjk_suffix_prefixes: tuple[str, ...] | None = None


_PHRASE_RULES = tuple(sorted((
    _PhraseRule(
        "phrase_lanshou_xianggu",
        "蓝瘦香菇",
        "难受想哭",
        allowed_cjk_suffix_prefixes=("啊", "呀", "了", "呢", "哦", "吧", "嘛", "呜", "哈", "死", "哭"),
    ),
    _PhraseRule(
        "phrase_bujidao",
        "布吉岛",
        "不知道",
        allowed_cjk_suffix_prefixes=(
            "啊", "呀", "了", "呢", "哦", "吧", "嘛",
            "怎么", "去哪", "去哪里", "为什么", "为何",
            "该", "要", "能", "会", "可以", "你", "我", "他", "这", "那", "还", "才", "不",
        ),
    ),
    _PhraseRule("phrase_youmuyou", "有木有", "有没有"),
    _PhraseRule(
        "phrase_jiangzi",
        "酱紫",
        "这样子",
        allowed_cjk_suffix_prefixes=(
            "啊", "呀", "了", "呢", "哦", "吧", "嘛", "滴", "哒",
            "做", "说", "想", "看", "对", "搞", "弄", "问", "写", "讲", "叫", "让",
            "把", "给", "跟", "玩", "吃", "喝", "去", "来", "走", "用", "处理", "回复",
        ),
    ),
    _PhraseRule("phrase_xiexie", "蟹蟹", "谢谢"),
    _PhraseRule("phrase_zenme", "肿么", "怎么"),
    _PhraseRule("phrase_keyi", "阔以", "可以"),
), key=lambda rule: len(rule.source_text), reverse=True))

_ATTITUDE_PATTERN = re.compile(
    r"(?:^|[\s，,。.!！?？;；]|但是|可是|然后|所以|因为|其实|但|可|而)"
    r"(?P<phrase>窝\s*"
    r"(?P<predicate>不讨厌|不喜欢|讨厌|喜欢|想念|相信|需要|支持|想|爱)"
    r"\s*泥)(?![\u3400-\u9fff])"
)
_META_LANGUAGE_PATTERN = re.compile(r"是什么意思|什么含义|写成谐音|谐音怎么|翻译成|怎么读")
_URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s，,。！!？?；;“”‘’「」『』]+", re.IGNORECASE)
_FENCED_CODE_PATTERN = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*(?:`|$)")
_CQ_PATTERN = re.compile(r"\[CQ:[^\]]*(?:\]|$)", re.IGNORECASE)
_QUOTED_PATTERNS = (
    re.compile(r'"[^"\n]*(?:"|$)'),
    re.compile(r"'[^'\n]*(?:'|$)"),
    re.compile(r"“[^”\n]*(?:”|$)"),
    re.compile(r"‘[^’\n]*(?:’|$)"),
    re.compile(r"「[^」\n]*(?:」|$)"),
    re.compile(r"『[^』\n]*(?:』|$)"),
    re.compile(r"《[^》\n]*(?:》|$)"),
)
_CLAUSE_PATTERN = re.compile(r"[^，,。.!！?？;；\n]+")


@dataclass(frozen=True)
class HomophoneEvidence:
    rule_id: str
    source_text: str
    interpreted_text: str
    start: int
    end: int
    confidence: float


@dataclass(frozen=True)
class HomophoneInterpretation:
    original_text: str
    interpreted_text: str
    confidence: float
    evidence: tuple[HomophoneEvidence, ...] = ()

    @property
    def changed(self) -> bool:
        return self.interpreted_text != self.original_text


def interpret_homophones(text: str) -> HomophoneInterpretation:
    """Return a request-time candidate without mutating ``text``."""

    original = str(text or "")
    if not original:
        return _unchanged(original)

    normalized = unicodedata.normalize("NFKC", original)
    # Compatibility normalization can alter string length for some symbols.
    # Refuse those inputs so evidence offsets always refer to the original.
    if len(normalized) != len(original):
        return _unchanged(original)

    protected_spans = _protected_spans(normalized)
    evidence = _collect_evidence(normalized, original, protected_spans)
    if not evidence:
        return _unchanged(original)

    interpreted = original
    for item in reversed(evidence):
        interpreted = interpreted[:item.start] + item.interpreted_text + interpreted[item.end:]
    return HomophoneInterpretation(
        original_text=original,
        interpreted_text=interpreted,
        confidence=min(item.confidence for item in evidence),
        evidence=tuple(evidence),
    )


def build_homophone_hint(text: str) -> str:
    """Build a prompt hint for a high-confidence interpretation, if any."""

    return format_homophone_hint(interpret_homophones(text))


def format_homophone_hint(result: HomophoneInterpretation) -> str:
    """Format an already computed interpretation as a non-authoritative hint."""

    if not result.changed:
        return ""
    lines = ["以下是请求期的谐音理解辅助（不会改写用户原文）："]
    lines.extend(
        f"- 原文片段“{item.source_text}”可能表达“{item.interpreted_text}”"
        for item in result.evidence
    )
    lines.append(
        "仅作理解参考；上下文冲突或不确定时忽略。"
        "不要把候选当作已确认事实，也不要主动纠正或刻意复述谐音。"
    )
    return "\n".join(lines)


def _unchanged(text: str) -> HomophoneInterpretation:
    return HomophoneInterpretation(
        original_text=text,
        interpreted_text=text,
        confidence=0.0,
    )


def _collect_evidence(
    text: str,
    original: str,
    protected_spans: tuple[tuple[int, int], ...],
) -> list[HomophoneEvidence]:
    candidates: list[HomophoneEvidence] = []
    for match in _ATTITUDE_PATTERN.finditer(text):
        phrase_start, phrase_end = match.span("phrase")
        if _overlaps_protected(phrase_start, phrase_end, protected_spans):
            continue
        predicate = match.group("predicate")
        candidates.append(HomophoneEvidence(
            rule_id=f"attitude_wo_{predicate}_ni",
            source_text=original[phrase_start:phrase_end],
            interpreted_text=f"我{predicate}你",
            start=phrase_start,
            end=phrase_end,
            confidence=0.99,
        ))
    for rule in _PHRASE_RULES:
        start = 0
        while True:
            start = text.find(rule.source_text, start)
            if start < 0:
                break
            end = start + len(rule.source_text)
            if (
                _overlaps_protected(start, end, protected_spans)
                or (
                    end < len(text)
                    and _is_cjk(text[end])
                    and rule.allowed_cjk_suffix_prefixes is not None
                    and not text[end:].startswith(rule.allowed_cjk_suffix_prefixes)
                )
            ):
                start = end
                continue
            candidates.append(HomophoneEvidence(
                rule_id=rule.rule_id,
                source_text=original[start:end],
                interpreted_text=rule.interpreted_text,
                start=start,
                end=end,
                confidence=rule.confidence,
            ))
            start = end

    candidates.sort(key=lambda item: (item.start, -(item.end - item.start), item.rule_id))
    selected: list[HomophoneEvidence] = []
    for candidate in candidates:
        if selected and candidate.start < selected[-1].end:
            continue
        selected.append(candidate)
    return selected


def _protected_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for pattern in (
        _URL_PATTERN,
        _FENCED_CODE_PATTERN,
        _INLINE_CODE_PATTERN,
        _CQ_PATTERN,
        *_QUOTED_PATTERNS,
    ):
        spans.extend((match.start(), match.end()) for match in pattern.finditer(text))
    for clause in _CLAUSE_PATTERN.finditer(text):
        if _META_LANGUAGE_PATTERN.search(clause.group(0)):
            spans.append((clause.start(), clause.end()))
    if not spans:
        return ()
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _overlaps_protected(
    start: int,
    end: int,
    protected_spans: tuple[tuple[int, int], ...],
) -> bool:
    return any(start < protected_end and end > protected_start for protected_start, protected_end in protected_spans)


def _is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff"
