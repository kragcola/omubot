"""Pure assessment of complete visible replies against one frozen Persona turn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .scheduling import TurnStateSnapshot

PersonaOutputReason = Literal[
    "self_ai_identity", "self_model_identity", "persona_setting_disclosure",
    "prompt_disclosure", "empty_visible_reply",
    "self_canonical_identity",
]

# These are explicit first-person predicates, not an "I am X" word blacklist.
# A complete identity noun must end at punctuation/the clause end: an AI fan or
# engineer is not claiming to be an AI. Names from runtime config are not used.
_CLAUSE_START = r"(?:^|[，,；;：:])\s*(?:(?:其实|实际上|事实上|本质上)\s*)?"
_IDENTITY_END = r"(?:呀|啊|呢|哦)?(?=\s*(?:$|[，,。！？!?；;：:、（）().]))"
_AI_ZH = (
    r"(?:AI(?:助手|助理|机器人|语言模型|模型)?|人工智能(?:助手|助理|语言模型|模型)?"
    r"|(?:大型?)?语言模型|机器人)"
)
_AI_EN = r"(?:AI(?: assistant)?|artificial intelligence|(?:large )?language model|robot)"
_MODEL = r"(?:ChatGPT|GPT|Claude|DeepSeek|OpenAI|Anthropic)(?:[- ][0-9][A-Za-z0-9.-]{0,31})?"
_FIRST_PERSON_EN = r"I(?: am|['’]m)"
_REASON_PATTERNS: tuple[tuple[PersonaOutputReason, re.Pattern[str]], ...] = (
    ("self_ai_identity", re.compile(
        _CLAUSE_START + r"(?:(?:我(?:就)?是|作为)\s*(?:一个?|一名)?\s*" + _AI_ZH
        + r"|(?:" + _FIRST_PERSON_EN + r"|As)\s+(?:an?\s+)?" + _AI_EN + r")"
        + _IDENTITY_END, re.IGNORECASE,
    )),
    ("self_model_identity", re.compile(
        _CLAUSE_START + r"(?:(?:我(?:就)?是|我叫)\s*|" + _FIRST_PERSON_EN + r"\s+)" + _MODEL
        + _IDENTITY_END, re.IGNORECASE,
    )),
    ("persona_setting_disclosure", re.compile(
        _CLAUSE_START + r"(?:我的(?:人设|设定|角色设定)(?:是|为|[:：])"
        + r"|(?:根据|按照)我的(?:人设|设定|角色设定)"
        + r"|my\s+(?:persona|character\s+settings?)\s+is\b)", re.IGNORECASE,
    )),
    ("prompt_disclosure", re.compile(
        _CLAUSE_START + r"(?:我的(?:系统|开发者)?(?:提示词|prompt)(?:内容)?(?:是|为|要求|规定|写着|[:：])"
        + r"|(?:根据|按照)(?:我的(?:系统|开发者)?|系统|开发者)(?:提示词|prompt)"
        + r"|(?:系统|开发者)(?:提示词|prompt)(?:要求|规定|告诉)我"
        + r"|my\s+(?:system\s+|developer\s+)?prompt\s+(?:is|says|requires)\b)", re.IGNORECASE,
    )),
)
_PROTECTED = re.compile(
    r"```[^\n]*\n[\s\S]*?(?:```|\Z)|`[^`\r\n]+`|"
    r"(?:https?://|ftp://|www\.)[^\s<>，。！？“”‘’]+|"
    r"“[^”]*”|‘(?:[^’]|(?<=\w)’(?=\w))*’|「[^」]*」|『[^』]*』|《[^》]*》|"
    r'''"[^"\r\n]+"|(?<!\w)'(?:[^'\r\n]|(?<=\w)'(?=\w))+'(?!\w)|'''
    r"^[ \t]*>[^\n]*(?:\n|$)", re.IGNORECASE | re.MULTILINE,
)


def _protected_mask(text: str) -> bytearray:
    mask = bytearray(len(text))
    for match in _PROTECTED.finditer(text):
        mask[match.start():match.end()] = b"\x01" * (match.end() - match.start())
    return mask


def _sentence_ranges(text: str, mask: bytearray) -> list[tuple[int, int]]:
    """Keep original slices, including proper-name punctuation and whitespace."""
    ranges: list[tuple[int, int]] = []
    start = 0
    for index, character in enumerate(text):
        if mask[index]:
            continue
        period_end = character == "." and (
            index + 1 == len(text) or not text[index + 1].isascii() or text[index + 1].isspace()
        )
        if character in "。！？!?\n" or period_end:
            ranges.append((start, index + 1))
            start = index + 1
    if start < len(text):
        ranges.append((start, len(text)))
    return ranges


@dataclass(frozen=True, slots=True)
class PersonaOutputAssessment:
    """Safe diagnostic reasons and visible candidate bound to the frozen Persona.

    A rejected result has no visible text. A filtered result is the unchanged
    concatenation of retained sentences, never fragments of a hard declaration.
    """

    status: Literal["accepted", "filtered", "rejected"]
    visible_text: str | None
    reason_codes: tuple[PersonaOutputReason, ...]
    persona_version: str


def assess_persona_output(text: str, snapshot: TurnStateSnapshot) -> PersonaOutputAssessment:
    """Assess a complete bounded candidate before its first visible emission.

    Quoted discussion, code and URLs cannot trigger identity rules. The caller
    still owns output limits, turn validity and the sole Actions send boundary.
    This function never sends, repairs, loads config or guesses a canonical name
    from a display nickname. Stream callers must assemble a complete sentence,
    not assess independent deltas that can split an identity predicate.
    """
    mask = _protected_mask(text)
    canonical_identity = None
    if snapshot.persona_mode == "source" and snapshot.persona_canonical_name:
        canonical_identity = re.compile(
            _CLAUSE_START
            + r"(?:我(?:就)?是|我叫|我的名字(?:是|叫)|" + _FIRST_PERSON_EN + r"\s+)\s*"
            + re.escape(snapshot.persona_canonical_name) + _IDENTITY_END,
            re.IGNORECASE,
        )
    kept: list[str] = []
    reasons: list[PersonaOutputReason] = []
    for start, end in _sentence_ranges(text, mask):
        # Replacing protected text with spaces keeps clause boundaries without
        # treating quoted self-reference as this speaker's own declaration.
        visible_predicate = "".join(
            " " if mask[index] else text[index] for index in range(start, end)
        )
        sentence_reasons: list[PersonaOutputReason] = [
            reason for reason, pattern in _REASON_PATTERNS if pattern.search(visible_predicate)
        ]
        if canonical_identity is not None and canonical_identity.search(visible_predicate):
            sentence_reasons.append("self_canonical_identity")
        if sentence_reasons:
            for reason in sentence_reasons:
                if reason not in reasons:
                    reasons.append(reason)
        else:
            kept.append(text[start:end])
    visible_text = "".join(kept)
    if not visible_text.strip() or reasons and not any(char.isalnum() for char in visible_text):
        reasons.append("empty_visible_reply")
        return PersonaOutputAssessment("rejected", None, tuple(reasons), snapshot.persona_version)
    return PersonaOutputAssessment(
        "filtered" if reasons else "accepted", visible_text, tuple(reasons), snapshot.persona_version,
    )
