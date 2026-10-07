"""Request-local candidates for a small, manually reviewed slang vocabulary.

The original message remains the only source of truth.  This module only
returns bounded evidence for a caller that explicitly chooses to use it; it
does not rewrite text, persist an interpretation, or resolve anything outside
the supplied string.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

MAX_INPUT_CHARS = 8000
MAX_CANDIDATES = 32
MAX_APPROVED_SURFACES = 128
MAX_APPROVED_SURFACE_CHARS = 128
MAX_MATCHES_PER_RULE = MAX_CANDIDATES


@dataclass(frozen=True, slots=True)
class Candidate:
    """One bounded, non-authoritative interpretation candidate."""

    rule_id: str
    source_text: str
    interpreted_text: str
    start: int
    end: int
    confidence: float


@dataclass(frozen=True, slots=True)
class Interpretation:
    """Original request text plus request-local interpretation candidates."""

    original_text: str
    candidates: tuple[Candidate, ...] = ()

    @property
    def changed(self) -> bool:
        """Whether at least one candidate was found; the text is never changed."""

        return bool(self.candidates)


@dataclass(frozen=True, slots=True)
class _PhraseRule:
    rule_id: str
    source_text: str
    interpreted_text: str
    confidence: float = 0.98
    allowed_cjk_suffix_prefixes: tuple[str, ...] | None = None


_PHRASE_RULES = tuple(
    sorted(
        (
            _PhraseRule(
                "phrase_lanshou_xianggu",
                "蓝瘦香菇",
                "难受想哭",
                allowed_cjk_suffix_prefixes=(
                    "啊",
                    "呀",
                    "了",
                    "呢",
                    "哦",
                    "吧",
                    "嘛",
                    "呜",
                    "哈",
                    "死",
                    "哭",
                ),
            ),
            _PhraseRule(
                "phrase_bujidao",
                "布吉岛",
                "不知道",
                allowed_cjk_suffix_prefixes=(
                    "啊",
                    "呀",
                    "了",
                    "呢",
                    "哦",
                    "吧",
                    "嘛",
                    "怎么",
                    "去哪",
                    "去哪里",
                    "为什么",
                    "为何",
                    "该",
                    "要",
                    "能",
                    "会",
                    "可以",
                    "你",
                    "我",
                    "他",
                    "这",
                    "那",
                    "还",
                    "才",
                    "不",
                ),
            ),
            _PhraseRule("phrase_youmuyou", "有木有", "有没有"),
            _PhraseRule(
                "phrase_jiangzi",
                "酱紫",
                "这样子",
                allowed_cjk_suffix_prefixes=(
                    "啊",
                    "呀",
                    "了",
                    "呢",
                    "哦",
                    "吧",
                    "嘛",
                    "滴",
                    "哒",
                    "做",
                    "说",
                    "想",
                    "看",
                    "对",
                    "搞",
                    "弄",
                    "问",
                    "写",
                    "讲",
                    "叫",
                    "让",
                    "把",
                    "给",
                    "跟",
                    "玩",
                    "吃",
                    "喝",
                    "去",
                    "来",
                    "走",
                    "用",
                    "处理",
                    "回复",
                ),
            ),
            _PhraseRule("phrase_xiexie", "蟹蟹", "谢谢"),
            _PhraseRule("phrase_zenme", "肿么", "怎么"),
            _PhraseRule("phrase_keyi", "阔以", "可以"),
        ),
        key=lambda rule: len(rule.source_text),
        reverse=True,
    )
)


_ATTITUDE_PATTERN = re.compile(
    r"(?:^|[\s，,。.!！?？;；]|但是|可是|然后|所以|因为|其实|但|可|而)"
    r"(?P<phrase>窝\s*"
    r"(?P<predicate>不讨厌|不喜欢|讨厌|喜欢|想念|相信|需要|支持|想|爱)"
    r"\s*泥)(?![\u3400-\u9fff])"
)
_ATTITUDE_RULE_NAMES = {
    "不讨厌": "bu_taoyan",
    "不喜欢": "bu_xihuan",
    "讨厌": "taoyan",
    "喜欢": "xihuan",
    "想念": "xiangnian",
    "相信": "xiangxin",
    "需要": "xuyao",
    "支持": "zhichi",
    "想": "xiang",
    "爱": "ai",
}
_META_LANGUAGE_PATTERN = re.compile(
    r"是什么意思|什么含义|写成谐音|谐音怎么|翻译成|怎么读"
)
_URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s，,。！!？?；;“”‘’「」『』]+", re.IGNORECASE
)
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


def interpret(
    text: str,
    *,
    approved_surfaces: Iterable[str] = (),
) -> Interpretation:
    """Return conservative candidates without rewriting or persisting ``text``.

    ``approved_surfaces`` is request-local group context.  A matching surface
    suppresses only its generic candidate; this function does not look it up or
    share it with another request or group.
    """

    if type(text) is not str:
        raise TypeError("text must be a string")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"text must be at most {MAX_INPUT_CHARS} characters")
    approved_keys = _approved_surface_keys(approved_surfaces)
    if not text:
        return Interpretation(original_text=text)

    normalized = unicodedata.normalize("NFKC", text)
    # A changed length would make normalized offsets unsafe to apply to the
    # original text.  Fail closed instead of guessing at a mapping.
    if len(normalized) != len(text):
        return Interpretation(original_text=text)

    protected_spans = _protected_spans(normalized)
    candidates = _collect_candidates(normalized, text, protected_spans)
    return Interpretation(
        original_text=text,
        candidates=_select_candidates(candidates, approved_keys),
    )


def _approved_surface_keys(approved_surfaces: Iterable[str]) -> frozenset[str]:
    if isinstance(approved_surfaces, str):
        raise TypeError("approved_surfaces must be an iterable of surfaces")

    keys: set[str] = set()
    for index, surface in enumerate(approved_surfaces):
        if index >= MAX_APPROVED_SURFACES:
            raise ValueError(f"approved_surfaces must contain at most {MAX_APPROVED_SURFACES} items")
        if type(surface) is not str:
            raise TypeError("approved surfaces must be strings")
        if len(surface) > MAX_APPROVED_SURFACE_CHARS:
            raise ValueError(
                f"approved surfaces must be at most {MAX_APPROVED_SURFACE_CHARS} characters"
            )
        key = _surface_key(surface)
        if key:
            keys.add(key)
    return frozenset(keys)


def _collect_candidates(
    normalized: str,
    original: str,
    protected_spans: tuple[tuple[int, int], ...],
) -> list[Candidate]:
    candidates: list[Candidate] = []

    attitude_matches = 0
    for match in _ATTITUDE_PATTERN.finditer(normalized):
        if attitude_matches >= MAX_MATCHES_PER_RULE:
            break
        start, end = match.span("phrase")
        if _overlaps_protected(start, end, protected_spans):
            continue
        predicate = match.group("predicate")
        candidates.append(
            Candidate(
                rule_id=f"attitude_wo_{_ATTITUDE_RULE_NAMES[predicate]}_ni",
                source_text=original[start:end],
                interpreted_text=f"我{predicate}你",
                start=start,
                end=end,
                confidence=0.99,
            )
        )
        attitude_matches += 1

    for rule in _PHRASE_RULES:
        start = 0
        matches = 0
        while matches < MAX_MATCHES_PER_RULE:
            start = normalized.find(rule.source_text, start)
            if start < 0:
                break
            end = start + len(rule.source_text)
            if not _phrase_is_allowed(normalized, start, end, rule, protected_spans):
                start = end
                continue
            candidates.append(
                Candidate(
                    rule_id=rule.rule_id,
                    source_text=original[start:end],
                    interpreted_text=rule.interpreted_text,
                    start=start,
                    end=end,
                    confidence=rule.confidence,
                )
            )
            matches += 1
            start = end
    return candidates


def _phrase_is_allowed(
    text: str,
    start: int,
    end: int,
    rule: _PhraseRule,
    protected_spans: tuple[tuple[int, int], ...],
) -> bool:
    if _overlaps_protected(start, end, protected_spans):
        return False
    if (
        end < len(text)
        and _is_cjk(text[end])
        and rule.allowed_cjk_suffix_prefixes is not None
        and not text[end:].startswith(rule.allowed_cjk_suffix_prefixes)
    ):
        return False
    return True


def _select_candidates(
    candidates: list[Candidate],
    approved_keys: frozenset[str],
) -> tuple[Candidate, ...]:
    candidates.sort(key=lambda item: (item.start, -(item.end - item.start), item.rule_id))
    selected: list[Candidate] = []
    for candidate in candidates:
        if _surface_key(candidate.source_text) in approved_keys:
            continue
        if selected and candidate.start < selected[-1].end:
            continue
        selected.append(candidate)
        if len(selected) >= MAX_CANDIDATES:
            break
    return tuple(selected)


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
    merged: list[tuple[int, int]] = [spans[0]]
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
    return any(
        start < protected_end and end > protected_start
        for protected_start, protected_end in protected_spans
    )


def _surface_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff"
