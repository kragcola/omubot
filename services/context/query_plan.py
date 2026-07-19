"""Query-Aware Retrieval Planner v1 (qa_rg_v1).

Pure synchronous deterministic planner. Classifies closed retrieval *needs*
from query text and emits post-RRF type occupancy caps + pack ContextBudget
profiles. Never mutates Thinker retrieve_mode, never touches storage/LLM/
network/async, never changes graph hops or card-retrieval ownership.

v1 only affects:
  - type_caps applied after RRF fusion
  - pack ContextBudget bucket soft caps
  - top_k clamp into [1, max_hits]
  - secret-free reason codes / metrics metadata
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from services.context.packing import ContextBudget

PLAN_VERSION: Final[str] = "qa_rg_v1"

QueryNeed = Literal[
    "ordinary_fact",
    "preference",
    "temporal_current",
    "temporal_earlier",
    "premise_check",
    "relation_multihop",
    "broad_recall",
    "doc_grounding",
]

CLOSED_NEEDS: Final[tuple[QueryNeed, ...]] = (
    "ordinary_fact",
    "preference",
    "temporal_current",
    "temporal_earlier",
    "premise_check",
    "relation_multihop",
    "broad_recall",
    "doc_grounding",
)

_NEED_PRIORITY: Final[tuple[QueryNeed, ...]] = (
    "premise_check",
    "temporal_earlier",
    "temporal_current",
    "preference",
    "relation_multihop",
    "doc_grounding",
    "broad_recall",
    "ordinary_fact",
)

# Closed vocabularies for the metrics sanitizer.
_ALLOWED_TYPE_CAP_KEYS: Final[frozenset[str]] = frozenset(
    {"memory_card", "doc_chunk", "graph_fact"}
)
_ALLOWED_MODES: Final[frozenset[str]] = frozenset({"skip", "doc", "fact", "hybrid"})
_ALLOWED_PROFILE_IDS: Final[frozenset[str]] = frozenset(
    {
        "ordinary_identity",
        "preference_memory_v1",
        "temporal_current_memory_v1",
        "temporal_earlier_memory_v1",
        "premise_check_memory_v1",
        "relation_graph_v1",
        "doc_grounding_v1",
        "broad_recall_clamp_v1",
    }
)
_ALLOWED_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "disabled",
        "identity",
        "ordinary",
        "need:preference",
        "need:temporal_current",
        "need:temporal_earlier",
        "need:premise_check",
        "need:relation_multihop",
        "need:doc_grounding",
        "need:broad_recall",
        "profile:preference_memory_v1",
        "profile:temporal_current_memory_v1",
        "profile:temporal_earlier_memory_v1",
        "profile:premise_check_memory_v1",
        "profile:relation_graph_v1",
        "profile:doc_grounding_v1",
        "profile:broad_recall_clamp_v1",
        "doc_cap:1",
        "graph_favor",
        "clamp_all",
    }
)
# Dynamic doc_cap:N for N in 0..32
_DOC_CAP_RE: Final[re.Pattern[str]] = re.compile(r"^doc_cap:(0|[1-9]|[12]\d|3[0-2])$")

# --- Markers: Chinese are phrase-substring; English use word-boundary tokens ---

_PREFERENCE_ZH: Final[tuple[str, ...]] = ("喜欢", "偏好", "爱吃", "讨厌", "不喜欢", "口味")
_PREFERENCE_EN: Final[tuple[str, ...]] = ("prefer", "favorite", "favourite")

_TEMPORAL_EARLIER_ZH: Final[tuple[str, ...]] = (
    "以前",
    "之前",
    "曾经",
    "原来",
    "当时",
    "过去",
    "从前",
    "往年",
)
_TEMPORAL_EARLIER_EN: Final[tuple[str, ...]] = (
    "earlier",
    "before",
    "used to",
    "previously",
)

_TEMPORAL_CURRENT_ZH: Final[tuple[str, ...]] = (
    "现在",
    "如今",
    "此刻",
)
# "当前" / "目前" / "今天" are intentionally excluded: they are common
# document/status phrasing and too weak to move a pack budget by themselves.
_TEMPORAL_CURRENT_EN: Final[tuple[str, ...]] = ("now", "currently", "today")

_PREMISE_MARKERS: Final[tuple[str, ...]] = (
    "还记得",
    "明明",
    "我记得",
    "不是还",
)
_PREMISE_NOT_Q_RE = re.compile(r"不是.{0,12}吗")

# Relation: drop weak standalone 认识/朋友/相关; keep 关系/多跳/co-occurrence phrases.
_RELATION_ZH: Final[tuple[str, ...]] = (
    "关系",
    "同事",
    "之间",
    "和谁",
    "谁和",
    "关联",
    "多跳",
)
_RELATION_EN: Final[tuple[str, ...]] = (
    "relation",
    "relationship",
    "connected",
    "knows",
)

_DOC_GROUNDING_ZH: Final[tuple[str, ...]] = (
    "手册",
    "文档",
    "说明书",
    "文档里",
    "按文档",
    "部署手册",
    "文档资料",
    "手册里",
)
# Drop bare 依据; keep strong doc phrases.
_DOC_GROUNDING_EN: Final[tuple[str, ...]] = (
    "according to",
    "documentation",
    "manual",
    "readme",
    "wiki",
)

_BROAD_RECALL_ZH: Final[tuple[str, ...]] = (
    "都有哪些",
    "有哪些",
    "所有记忆",
    "关于我的记忆",
    "汇总",
    "回忆一下",
    "记得我",
)
# Drop bare 全部/列出.
_BROAD_RECALL_EN: Final[tuple[str, ...]] = (
    "everything about",
    "all my",
    "what do you know",
)

# Profile budget ratios (memory, doc, graph) — sum to 100.
_PROFILE_RATIOS: Final[dict[str, tuple[int, int, int]]] = {
    "preference": (60, 15, 25),
    "temporal_current": (60, 10, 30),
    "temporal_earlier": (60, 10, 30),
    "premise_check": (65, 10, 25),
    "relation_multihop": (25, 10, 65),
    "doc_grounding": (15, 70, 15),
}

_PROFILE_IDS: Final[dict[str, str]] = {
    "preference": "preference_memory_v1",
    "temporal_current": "temporal_current_memory_v1",
    "temporal_earlier": "temporal_earlier_memory_v1",
    "premise_check": "premise_check_memory_v1",
    "relation_multihop": "relation_graph_v1",
    "doc_grounding": "doc_grounding_v1",
    "broad_recall": "broad_recall_clamp_v1",
}


@dataclass(frozen=True, slots=True)
class QueryAwarePlan:
    """Deterministic retrieval plan for one pre-prompt turn."""

    version: str
    needs: tuple[QueryNeed, ...]
    profile_id: str
    retrieve_mode: str
    top_k: int
    type_caps: Mapping[str, int]
    budget: ContextBudget
    reason_codes: tuple[str, ...]
    enabled: bool
    identity: bool

    def to_metrics(self) -> dict[str, object]:
        """Secret-free metrics payload (no raw query / card content)."""
        return sanitize_plan_meta(
            {
                "version": self.version,
                "needs": list(self.needs),
                "profile_id": self.profile_id,
                "mode": self.retrieve_mode,
                "top_k": self.top_k,
                "type_caps": dict(self.type_caps),
                "reason_codes": list(self.reason_codes),
                "enabled": self.enabled,
                "identity": self.identity,
            }
        )


def sanitize_plan_meta(plan_meta: Mapping[str, object] | None) -> dict[str, object]:
    """Drop unknown keys; deep-copy allowed nested values into closed vocabularies.

    Safe to call from ContextService when recording metrics. Never persists
    arbitrary strings as needs / profile IDs / reason codes.
    """
    if not plan_meta:
        return {}
    out: dict[str, object] = {}

    out["version"] = PLAN_VERSION

    needs_raw = plan_meta.get("needs")
    needs_out: list[str] = []
    if isinstance(needs_raw, (list, tuple)):
        closed = set(CLOSED_NEEDS)
        for item in needs_raw:
            if isinstance(item, str) and item in closed and item not in needs_out:
                needs_out.append(item)
            if len(needs_out) >= 2:
                break
    if not needs_out:
        needs_out = ["ordinary_fact"]
    out["needs"] = list(needs_out)

    profile = plan_meta.get("profile_id")
    if isinstance(profile, str) and profile in _ALLOWED_PROFILE_IDS:
        out["profile_id"] = profile
    else:
        out["profile_id"] = "ordinary_identity"

    mode = plan_meta.get("mode")
    if isinstance(mode, str) and mode in _ALLOWED_MODES:
        out["mode"] = mode
    else:
        out["mode"] = "hybrid"

    top_k = plan_meta.get("top_k")
    if isinstance(top_k, bool):
        tk = 1
    else:
        try:
            tk = int(top_k)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            tk = 1
    out["top_k"] = max(1, min(tk, 64))

    caps_raw = plan_meta.get("type_caps")
    caps_out: dict[str, int] = {}
    if isinstance(caps_raw, Mapping):
        for key, val in caps_raw.items():
            if key not in _ALLOWED_TYPE_CAP_KEYS:
                continue
            if isinstance(val, bool):
                continue
            try:
                caps_out[str(key)] = max(0, min(int(val), 64))
            except (TypeError, ValueError):
                continue
    out["type_caps"] = dict(caps_out)

    reasons_raw = plan_meta.get("reason_codes")
    reasons_out: list[str] = []
    if isinstance(reasons_raw, (list, tuple)):
        for code in reasons_raw:
            if not isinstance(code, str):
                continue
            if (code in _ALLOWED_REASON_CODES or _DOC_CAP_RE.match(code)) and code not in reasons_out:
                reasons_out.append(code)
            if len(reasons_out) >= 8:
                break
    out["reason_codes"] = list(reasons_out)

    enabled = plan_meta.get("enabled")
    identity = plan_meta.get("identity")
    out["enabled"] = enabled if isinstance(enabled, bool) else True
    out["identity"] = identity if isinstance(identity, bool) else False
    return out


def plan_query_aware_retrieval(
    *,
    query: str,
    current_message: str = "",
    retrieve_mode: str = "hybrid",
    max_hits: int = 5,
    max_doc_hits: int = 3,
    budget: ContextBudget | None = None,
    enabled: bool = True,
) -> QueryAwarePlan:
    """Build a pure deterministic query-aware plan.

    Parameters
    ----------
    query:
        Resolved retrieval query (rewritten_query or conversation_text).
    current_message:
        Raw current turn text; used only for need classification (not
        TemporalTrace authorization — that stays on the plugin path).
    retrieve_mode:
        Thinker mode; returned unchanged (never widened/narrowed).
    max_hits / max_doc_hits:
        Hard caps from plugin config.
    budget:
        Configured ContextBudget; profile keeps total_tokens and buffer_tokens.
    enabled:
        Kill-switch; False forces ordinary identity profile.
    """
    base = budget or ContextBudget()
    mode = retrieve_mode or "hybrid"
    max_hits = max(1, int(max_hits))
    max_doc_hits = max(1, int(max_doc_hits))
    top_k = max_hits

    if not enabled:
        return _identity_plan(
            mode=mode,
            top_k=top_k,
            max_doc_hits=max_doc_hits,
            budget=base,
            enabled=False,
            reason_codes=("disabled", "identity"),
        )

    needs = _classify_needs(query=query or "", current_message=current_message or "")
    if not needs or needs == ("ordinary_fact",):
        return _identity_plan(
            mode=mode,
            top_k=top_k,
            max_doc_hits=max_doc_hits,
            budget=base,
            enabled=True,
            reason_codes=("ordinary", "identity"),
        )

    primary = needs[0]
    profile_id, type_caps, profile_budget, reason_codes = _profile_for(
        primary=primary,
        max_hits=max_hits,
        max_doc_hits=max_doc_hits,
        budget=base,
    )
    return QueryAwarePlan(
        version=PLAN_VERSION,
        needs=needs,
        profile_id=profile_id,
        retrieve_mode=mode,
        top_k=top_k,
        type_caps=type_caps,
        budget=profile_budget,
        reason_codes=reason_codes,
        enabled=True,
        identity=False,
    )


def _identity_plan(
    *,
    mode: str,
    top_k: int,
    max_doc_hits: int,
    budget: ContextBudget,
    enabled: bool,
    reason_codes: tuple[str, ...],
) -> QueryAwarePlan:
    return QueryAwarePlan(
        version=PLAN_VERSION,
        needs=("ordinary_fact",),
        profile_id="ordinary_identity",
        retrieve_mode=mode,
        top_k=top_k,
        type_caps={"doc_chunk": max_doc_hits},
        budget=budget,
        reason_codes=reason_codes,
        enabled=enabled,
        identity=True,
    )


def _classify_needs(*, query: str, current_message: str) -> tuple[QueryNeed, ...]:
    """Conservative closed-set classifier; max two needs; unknown → ordinary_fact.

    Intentionally does **not** treat natural-language abstain cues
    (别猜 / 不知道) as pre-skip signals — those remain ordinary retrieval.
    """
    text = f"{query}\n{current_message}".strip().lower()
    if not text:
        return ("ordinary_fact",)

    detected: list[QueryNeed] = []

    if _has_premise(text):
        detected.append("premise_check")
    if _match_markers(text, _TEMPORAL_EARLIER_ZH, _TEMPORAL_EARLIER_EN):
        detected.append("temporal_earlier")
    if _match_markers(text, _TEMPORAL_CURRENT_ZH, _TEMPORAL_CURRENT_EN):
        detected.append("temporal_current")
    if _match_markers(text, _PREFERENCE_ZH, _PREFERENCE_EN):
        detected.append("preference")
    if _match_markers(text, _RELATION_ZH, _RELATION_EN):
        detected.append("relation_multihop")
    if _match_markers(text, _DOC_GROUNDING_ZH, _DOC_GROUNDING_EN):
        detected.append("doc_grounding")
    if _match_markers(text, _BROAD_RECALL_ZH, _BROAD_RECALL_EN):
        detected.append("broad_recall")

    if not detected:
        return ("ordinary_fact",)

    ordered: list[QueryNeed] = []
    for need in _NEED_PRIORITY:
        if need in detected and need not in ordered:
            ordered.append(need)
        if len(ordered) >= 2:
            break
    return tuple(ordered) if ordered else ("ordinary_fact",)


def _has_premise(text: str) -> bool:
    if any(m in text for m in _PREMISE_MARKERS):
        return True
    return _PREMISE_NOT_Q_RE.search(text) is not None


def _match_markers(
    text: str,
    zh_markers: Sequence[str],
    en_markers: Sequence[str],
) -> bool:
    if any(m in text for m in zh_markers):
        return True
    return any(_en_token_in(text, m) for m in en_markers)


def _en_token_in(text: str, marker: str) -> bool:
    """Word-boundary match for English tokens/phrases (ASCII alnum boundaries)."""
    m = marker.lower().strip()
    if not m:
        return False
    # Phrase: allow flexible internal whitespace; edges require non-alnum or string ends.
    parts = [re.escape(p) for p in m.split() if p]
    if not parts:
        return False
    body = r"\s+".join(parts)
    pattern = rf"(?<![a-z0-9]){body}(?![a-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _profile_for(
    *,
    primary: QueryNeed,
    max_hits: int,
    max_doc_hits: int,
    budget: ContextBudget,
) -> tuple[str, dict[str, int], ContextBudget, tuple[str, ...]]:
    """Named integer profiles: type_caps + budget reallocation within content capacity."""
    total = budget.total_tokens
    buffer_tokens = budget.buffer_tokens
    capacity = max(0, total - buffer_tokens)

    if primary == "broad_recall":
        mem, doc, graph = _normalize_proportions(
            budget.memory_tokens,
            budget.doc_tokens,
            budget.graph_tokens,
            capacity,
        )
        caps = {
            "memory_card": max_hits,
            "doc_chunk": max_doc_hits,
            "graph_fact": max_hits,
        }
        profile_budget = ContextBudget(
            total_tokens=total,
            memory_tokens=mem,
            doc_tokens=doc,
            graph_tokens=graph,
            buffer_tokens=buffer_tokens,
        )
        return (
            "broad_recall_clamp_v1",
            caps,
            profile_budget,
            ("need:broad_recall", "profile:broad_recall_clamp_v1", "clamp_all"),
        )

    if primary not in _PROFILE_RATIOS:
        return (
            "ordinary_identity",
            {"doc_chunk": max_doc_hits},
            budget,
            ("ordinary", "identity"),
        )

    ratios = _PROFILE_RATIOS[primary]
    mem, doc, graph = _allocate_by_ratio(ratios, capacity)
    profile_id = _PROFILE_IDS[primary]
    caps = _type_caps_for(primary, max_hits=max_hits, max_doc_hits=max_doc_hits)
    reasons = _reason_codes_for(primary, profile_id=profile_id, max_doc_hits=max_doc_hits)
    profile_budget = ContextBudget(
        total_tokens=total,
        memory_tokens=mem,
        doc_tokens=doc,
        graph_tokens=graph,
        buffer_tokens=buffer_tokens,
    )
    return profile_id, caps, profile_budget, reasons


def _type_caps_for(
    primary: QueryNeed,
    *,
    max_hits: int,
    max_doc_hits: int,
) -> dict[str, int]:
    if primary in {
        "preference",
        "temporal_current",
        "temporal_earlier",
        "premise_check",
    }:
        return {
            "memory_card": max_hits,
            "doc_chunk": min(1, max_doc_hits),
            "graph_fact": min(2, max_hits),
        }
    if primary == "relation_multihop":
        return {
            "memory_card": min(3, max_hits),
            "doc_chunk": min(1, max_doc_hits),
            "graph_fact": max_hits,
        }
    if primary == "doc_grounding":
        return {
            "memory_card": min(2, max_hits),
            "doc_chunk": max_doc_hits,
            "graph_fact": min(2, max_hits),
        }
    return {"doc_chunk": max_doc_hits}


def _reason_codes_for(
    primary: QueryNeed,
    *,
    profile_id: str,
    max_doc_hits: int,
) -> tuple[str, ...]:
    base = (f"need:{primary}", f"profile:{profile_id}")
    if primary in {
        "preference",
        "temporal_current",
        "temporal_earlier",
        "premise_check",
    }:
        return (*base, "doc_cap:1")
    if primary == "relation_multihop":
        return (*base, "graph_favor")
    if primary == "doc_grounding":
        return (*base, f"doc_cap:{max_doc_hits}")
    return base


def _allocate_by_ratio(
    ratios: tuple[int, int, int],
    capacity: int,
) -> tuple[int, int, int]:
    """Deterministic largest-remainder allocation of capacity by integer ratios."""
    if capacity <= 0:
        return 0, 0, 0
    r_sum = sum(ratios)
    if r_sum <= 0:
        return _normalize_proportions(1, 1, 1, capacity)
    raw = [capacity * r / r_sum for r in ratios]
    floors = [int(x) for x in raw]
    rem = capacity - sum(floors)
    # Stable tie-break: higher fractional part first, then lower index.
    order = sorted(
        range(3),
        key=lambda i: (-(raw[i] - floors[i]), i),
    )
    for i in order:
        if rem <= 0:
            break
        floors[i] += 1
        rem -= 1
    return floors[0], floors[1], floors[2]


def _normalize_proportions(
    memory_tokens: int,
    doc_tokens: int,
    graph_tokens: int,
    capacity: int,
) -> tuple[int, int, int]:
    """Scale configured bucket weights into content_capacity (equal weights if all zero)."""
    if capacity <= 0:
        return 0, 0, 0
    mem = max(0, int(memory_tokens))
    doc = max(0, int(doc_tokens))
    graph = max(0, int(graph_tokens))
    if mem + doc + graph <= 0:
        return _allocate_by_ratio((1, 1, 1), capacity)
    return _allocate_by_ratio((mem, doc, graph), capacity)
