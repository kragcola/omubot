"""Pure helpers for conflict-aware memory write decisions (hot-path v1).

No DB / network I/O — unit-tested in isolation by the write-policy suite.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Conservative: prefer false-negatives over false-positive reinforce/supersede.
DUPLICATE_SIMILARITY_THRESHOLD: float = 0.72

# Strong, unambiguous update / correction cues.
_STRONG_UPDATE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"改成",
        r"改为",
        r"换成",
        r"搬到",
        r"搬家",
        r"不再",
        r"目前",
        r"从今",
        r"纠正",
        r"不是.+而是",
        r"原来.+现在",
    )
)

# Weaker temporal cues that only count when paired with substantive update verbs.
# Prevents bare casual phrases like "我现在在忙" / "我已经吃过了" / "以后再说".
_CONTEXTUAL_UPDATE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"现在.{0,12}(住|搬|改|换|是|在.{0,4}(上海|北京|杭州|广州|深圳|成都|重庆|南京|武汉|西安|苏州|天津))",
        r"已经.{0,8}(搬|改|换|不|住|是)",
        r"以后.{0,8}(住|搬|改|换|不|会)",
    )
)

# Function-word / framing tokens that must not alone count as lexical support.
_LEXICAL_STOP: frozenset[str] = frozenset(
    {
        "用户",
        "我",
        "你",
        "他",
        "她",
        "现在",
        "目前",
        "已经",
        "以后",
        "从今",
        "住在",
        "住",
        "了",
        "的",
        "是",
        "在",
        "和",
        "与",
        "也",
        "都",
        "就",
        "还",
        "不",
        "再",
        "把",
        "被",
        "让",
        "给",
        "到",
        "会",
        "说",
        "过",
        "一下",
        "一下下",
    }
)

# Strip whitespace and common punctuation (incl. CJK full-width).
_PUNCT_RE = re.compile(
    r"[\s"
    r"!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~"
    r"。，、；：？！…—·「」『』【】《》〈〉（）￥"
    r"]+",
)


def normalize_content(text: str) -> str:
    """Collapse whitespace/punctuation and lower-case for duplicate checks."""
    if not text:
        return ""
    collapsed = _PUNCT_RE.sub("", str(text)).casefold()
    return collapsed


# Alias expected by some callers / tests.
normalize = normalize_content


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    if len(text) < n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def content_similarity(a: str, b: str) -> float:
    """Normalized similarity in [0, 1].

    Exact/punctuation-normalized equals score 1.0. Partial containment
    (e.g. 「用户喜欢猫」 vs 「用户喜欢猫和狗」) must NOT force 1.0 — use
    char-bigram Jaccard only so longer multi-preference statements stay
    below the duplicate threshold.
    """
    na = normalize_content(a)
    nb = normalize_content(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Conservative: never return 1.0 for non-exact matches.
    return _jaccard(_char_ngrams(na), _char_ngrams(nb))


def is_high_confidence_duplicate(
    a: str,
    b: str,
    *,
    threshold: float | None = None,
) -> bool:
    """True when *a* and *b* are near-identical under the write policy."""
    cut = float(DUPLICATE_SIMILARITY_THRESHOLD if threshold is None else threshold)
    return content_similarity(a, b) >= cut


# Aliases for flexible import surfaces asserted by tests.
high_confidence_duplicate = is_high_confidence_duplicate
is_duplicate = is_high_confidence_duplicate


def has_explicit_update_signal(text: str) -> bool:
    """Detect Chinese update/correction cues that authorize supersede.

    Bare casual phrases containing 现在/已经/以后 alone must NOT match;
    true residence/preference update phrases still match.
    """
    if not text:
        return False
    raw = str(text)
    if any(pat.search(raw) for pat in _STRONG_UPDATE_PATTERNS):
        return True
    return any(pat.search(raw) for pat in _CONTEXTUAL_UPDATE_PATTERNS)


# Aliases
explicit_update_signal = has_explicit_update_signal
has_update_signal = has_explicit_update_signal


def _contentful_tokens(text: str) -> set[str]:
    """Extract contentful n-grams (len 2–4) excluding framing stop words."""
    norm = normalize_content(text)
    if not norm:
        return set()
    tokens: set[str] = set()
    for n in (2, 3, 4):
        if len(norm) < n:
            continue
        for i in range(len(norm) - n + 1):
            tok = norm[i : i + n]
            if tok in _LEXICAL_STOP:
                continue
            # Drop pure function-character n-grams.
            if all(c in "的了是在我你他她就还也都不" for c in tok):
                continue
            tokens.add(tok)
    return tokens


def allows_supersede(user_msg: str, new_content: str) -> bool:
    """Authorize supersede only from *user_msg* cue + lexical support.

    LLM-proposed *new_content* must never self-authorize, even when it
    contains update cues. Requires:
    1) explicit update signal in user_msg only
    2) shared contentful tokens between user_msg and new_content
       (e.g. city name 上海 for a residence move)
    """
    if not user_msg or not new_content:
        return False
    if not has_explicit_update_signal(user_msg):
        return False
    user_tokens = _contentful_tokens(user_msg)
    content_tokens = _contentful_tokens(new_content)
    if not user_tokens or not content_tokens:
        return False
    return bool(user_tokens & content_tokens)


# Aliases preferred by some call sites / tests.
may_supersede = allows_supersede
authorize_supersede = allows_supersede


def find_duplicate_target(
    content: str,
    candidates: Iterable[object],
    *,
    category: str | None = None,
    threshold: float | None = None,
) -> object | None:
    """Return the first candidate with matching content (and optional category).

    Candidates are expected to expose ``.content`` and optionally ``.category``.
    """
    best: object | None = None
    best_score = -1.0
    cut = float(DUPLICATE_SIMILARITY_THRESHOLD if threshold is None else threshold)
    for card in candidates:
        card_content = getattr(card, "content", None)
        if not isinstance(card_content, str):
            continue
        if category is not None:
            card_cat = getattr(card, "category", None)
            if card_cat is not None and card_cat != category:
                continue
        score = content_similarity(content, card_content)
        if score >= cut and score > best_score:
            best = card
            best_score = score
    return best
