"""Strip visible self-declaration / persona drift phrases from replies."""

from __future__ import annotations

import re

from services.llm.persona_patterns import DECLARATION_PATTERNS
from services.llm.sentinel_registry import (
    RULE_ORDER_PERSONA_DRIFT,
    GuardrailContext,
    GuardrailHit,
    GuardrailResult,
    register_rule,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])|(?<=\n)")
_LEADING_DECLARATION_RE = re.compile(r"^(?:作为\s*(?:WxS|W×S|wxs).{0,10}(?:成员|一员))[，,、:：]?", re.IGNORECASE)
_AI_PREFIX_RE = re.compile(r"^(?:我是|作为)(?:一个?)?(?:AI|人工智能|语言模型|机器人)", re.IGNORECASE)
_MODEL_PREFIX_RE = re.compile(r"^(?:我是|我叫)\s*(?:Claude|GPT|Anthropic|OpenAI)", re.IGNORECASE)
_WXS_MEMBER_TAIL_RE = re.compile(r"^(?:WxS|W×S|wxs).{0,10}(?:成员|一员)[。！？!?]?$", re.IGNORECASE)

# "Hard" identity declarations — genuine persona drift that must fail closed when
# stripping leaves nothing usable: AI/model self-reference, persona/setting leak,
# explicit name claim, WxS membership. These are detected anywhere in a sentence
# (not just at the start) so mixed clauses like "我是{name}，我是一个AI" are caught.
# Deliberately EXCLUDES the broad "我是X" heuristic (DECLARATION_PATTERNS[0]): on
# its own it fires on ordinary speech ("我是个吃货"/"我是来帮忙的"), so a sentence
# that matches *only* that pattern must not trigger a fail-closed fallback.
_HARD_AI_RE = re.compile(r"(?:我是|作为)(?:一个?)?(?:AI|人工智能|语言模型|机器人)", re.IGNORECASE)
_HARD_MODEL_RE = re.compile(r"(?:我是|我叫)\s*(?:Claude|GPT|Anthropic|OpenAI)", re.IGNORECASE)
_HARD_SETTING_RE = re.compile(r"我的(?:设定|人设|角色|身份)(?:是|为)")
_HARD_NAME_KW_RE = re.compile(r"我(?:的)?(?:名字|本名)(?:是|叫)")


def _persona_drift_enabled(config: object | None) -> bool:
    drift = getattr(config, "persona_drift", config)
    return bool(getattr(drift, "enabled", False))


def _matches_declaration(sentence: str, *, bot_name: str) -> bool:
    if sentence.startswith("我是说"):
        return False
    if any(pattern.search(sentence) for pattern in DECLARATION_PATTERNS):
        return True
    if bot_name:
        normalized = re.escape(bot_name)
        if re.search(rf"我(?:是|叫){normalized}", sentence):
            return True
    return False


def _is_hard_declaration(sentence: str, *, bot_name: str) -> bool:
    """A genuine identity declaration (vs the broad ``我是X`` heuristic).

    Only hard declarations may force a fail-closed fallback when stripping
    collapses the whole reply — otherwise normal speech that merely matches the
    broad pattern would be suppressed.
    """
    if sentence.startswith("我是说"):
        return False
    if _HARD_AI_RE.search(sentence) or _HARD_MODEL_RE.search(sentence):
        return True
    if _HARD_SETTING_RE.search(sentence) or _HARD_NAME_KW_RE.search(sentence):
        return True
    if _LEADING_DECLARATION_RE.search(sentence) or _WXS_MEMBER_TAIL_RE.search(sentence):
        return True
    return bool(bot_name and re.search(rf"我(?:是|叫){re.escape(bot_name)}", sentence))


def _rewrite_sentence(sentence: str, *, bot_name: str) -> tuple[str, bool]:
    original = sentence.strip()
    if not original:
        return "", False
    rewritten = original
    changed = False
    if _LEADING_DECLARATION_RE.search(rewritten):
        rewritten = _LEADING_DECLARATION_RE.sub("", rewritten, count=1).strip()
        changed = True
    if _AI_PREFIX_RE.search(rewritten):
        rewritten = _AI_PREFIX_RE.sub("", rewritten, count=1).strip(" ，,。！？!?")
        changed = True
    if _MODEL_PREFIX_RE.search(rewritten):
        rewritten = _MODEL_PREFIX_RE.sub("", rewritten, count=1).strip(" ，,。！？!?")
        changed = True
    if bot_name:
        name_pattern = re.compile(
            rf"^我(?:是|叫){re.escape(bot_name)}[呀哦啦呢]?"
            rf"(?=$|[，,、:：。！？!?~～…])[，,、:：。！？!?~～…]*",
            re.IGNORECASE,
        )
        if name_pattern.search(rewritten):
            rewritten = name_pattern.sub("", rewritten, count=1).strip()
            changed = True
    if _WXS_MEMBER_TAIL_RE.search(rewritten):
        rewritten = ""
        changed = True
    if changed and rewritten:
        return rewritten, True
    return original, False


def strip_declarations(text: str, *, bot_name: str = "") -> tuple[str, list[str]]:
    parts = _SENTENCE_SPLIT_RE.split(text)
    kept: list[str] = []
    matched: list[str] = []
    hard_matched = False
    for part in parts:
        sentence = part.strip()
        if not sentence:
            continue
        if not _matches_declaration(sentence, bot_name=bot_name):
            kept.append(sentence)
            continue
        if _is_hard_declaration(sentence, bot_name=bot_name):
            hard_matched = True
        rewritten, changed = _rewrite_sentence(sentence, bot_name=bot_name)
        matched.append(sentence)
        if changed and rewritten:
            if _is_hard_declaration(rewritten, bot_name=bot_name):
                # The rewrite stripped the wrong part — e.g. removed the name
                # from "我是{name}，我是一个AI" but kept the AI declaration.
                # Drop the residual and fail closed instead of leaking it.
                hard_matched = True
                continue
            kept.append(rewritten)
            continue
    cleaned = "".join(kept).strip()
    if matched and not cleaned:
        # Stripping collapsed the whole reply. Fail closed (return empty so the
        # rule reports passed=False and the caller substitutes a fallback) ONLY
        # when a hard identity declaration drove the match — e.g. a lone
        # "我是一个AI" or "我是{name}". For soft matches (the broad "我是X"
        # heuristic firing on ordinary speech with no real declaration), keep the
        # original text to avoid suppressing normal replies.
        if hard_matched:
            return "", matched
        return text, matched
    return cleaned or text, matched


def persona_drift_rule(text: str, ctx: GuardrailContext) -> GuardrailResult:
    if not _persona_drift_enabled(ctx.config):
        return GuardrailResult(passed=True, text=text)
    cleaned, matched = strip_declarations(text, bot_name=ctx.bot_name)
    if not matched:
        return GuardrailResult(passed=True, text=text)
    hit = GuardrailHit(
        name="persona_drift",
        severity="medium",
        action="rewrite",
        metadata={"matched_sentences": matched[:4]},
    )
    return GuardrailResult(
        passed=bool(cleaned.strip()),
        text=cleaned,
        hits=(hit,),
        metadata={"persona_drift_matches": matched},
    )


register_rule(persona_drift_rule, order=RULE_ORDER_PERSONA_DRIFT)
