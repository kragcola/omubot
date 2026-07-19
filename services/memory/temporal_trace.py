"""Temporal memory trace assembler (Option A sidecar).

Builds a current-first supersedes trajectory for historical-intent /
premise-conflict turns. Ordinary retrieval remains active-only; this module
never feeds RRF/pack and never includes graph facts.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from services.memory.card_eligibility import (
    DEFAULT_CARD_ELIGIBILITY_POLICY,
    CardEligibilityPolicy,
    is_card_eligible_for_recall,
)

_L = logger.bind(channel="memory")

# Authorization closed lists — only current_message may fire these.
_HISTORICAL_MARKERS: tuple[str, ...] = (
    "以前",
    "之前",
    "曾经",
    "原来",
    "当时",
    "过去",
    "从前",
    "往年",
)

_PREMISE_MARKERS: tuple[str, ...] = (
    "还记得",
    "明明",
    "我记得",
    "仍然",
    "还住",
    "不是还",
)

# "不是…吗" style rhetorical premise (allow short gap).
_PREMISE_NOT_Q_RE = re.compile(r"不是.{0,12}吗")

_STOPWORDS: frozenset[str] = frozenset({
    "我", "你", "他", "她", "它", "们", "的", "了", "吗", "呢", "啊", "吧",
    "在", "是", "有", "和", "与", "或", "也", "都", "就", "还", "很", "太",
    "什么", "哪里", "哪", "怎么", "如何", "为什么", "多少", "谁", "这", "那",
    "一个", "一下", "过", "着", "被", "把", "让", "给", "到", "对",
    "用户", "现在", "目前", "今天", "以前", "之前", "曾经", "原来", "当时",
    "过去", "记得", "明明", "仍然", "不是", "还是",
})

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[A-Za-z0-9_]{2,}")

# Evidence / observation hard bounds per knowledge point (v1).
_MAX_OBS_PER_CARD = 2
_MAX_REFS_PER_KP = 12
_MAX_CHARS_HARD = 600
_MAX_DEPTH_HARD = 4
_MAX_HEADS_HARD = 24
_MAX_KPS_HARD = 2


@dataclass
class TemporalTraceConfig:
    enabled: bool = True
    max_heads: int = 24
    max_kps: int = 2
    max_depth: int = 4
    max_chars: int = 600


@dataclass
class TraceNode:
    card_id: str
    content: str
    status: str
    confidence: float
    source_message_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None  # earlier only; from successor captured_at/created_at


@dataclass
class KnowledgePointTrace:
    current: TraceNode | dict[str, Any]
    earlier: list[TraceNode | dict[str, Any]]
    trajectory: str
    evidence_refs: list[str]
    reason: str  # historical_intent | premise_conflict
    confidence: float


@dataclass
class TemporalTrace:
    text: str
    reason: str
    knowledge_points: list[KnowledgePointTrace] = field(default_factory=list)
    omitted_reason: str | None = None


class TemporalTraceAssembler:
    """Assemble a deterministic temporal-trace DTO from CardStore supersedes chains."""

    def __init__(
        self,
        store: Any,
        config: TemporalTraceConfig | None = None,
        *,
        group_memory_config: Any = None,
        card_eligibility: CardEligibilityPolicy | None = None,
        now_provider: Any = None,
    ) -> None:
        self.store = store
        self.config = config or TemporalTraceConfig()
        self._group_memory_config = group_memory_config
        self._card_eligibility = (
            card_eligibility
            if card_eligibility is not None
            else DEFAULT_CARD_ELIGIBILITY_POLICY
        )
        self._now_provider = now_provider

    def set_group_memory_config(self, config: Any) -> None:
        """Hot-reload path: keep pool resolver in sync with ordinary retrieval."""
        self._group_memory_config = config

    def set_card_eligibility(self, policy: CardEligibilityPolicy) -> None:
        """Hot-reload eligibility policy (active-head filter only)."""
        self._card_eligibility = policy

    async def assemble(
        self,
        current_message: str = "",
        rewritten_query: str = "",
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        active_memory_hits: Any = None,
        **kwargs: Any,
    ) -> TemporalTrace | None:
        del kwargs
        cfg = self.config
        if not cfg.enabled:
            return None
        message = current_message or ""
        reason = _detect_trigger_reason(message)
        if reason is None:
            return None

        try:
            scope, scope_ids = self._resolve_scope_ids(
                session_id=session_id,
                user_id=user_id,
                group_id=group_id,
            )
            if not scope_ids:
                return None

            # Matching tokens may include rewritten_query; authorization does not.
            query_tokens = _extract_tokens(message, rewritten_query or "")
            if not query_tokens:
                query_tokens = _extract_tokens(message, "")

            head_ids = await self._collect_head_ids(
                scope=scope,
                scope_ids=scope_ids,
                active_memory_hits=active_memory_hits,
            )
            if not head_ids:
                return None

            candidates: list[tuple[float, str, list[Any], str]] = []
            max_depth = max(1, min(int(cfg.max_depth), _MAX_DEPTH_HARD))
            for head_id in head_ids:
                chain = await self.store.walk_supersedes_chain(head_id, max_depth=max_depth)
                if not chain or len(chain) < 2:
                    continue
                # Cross-category integrity: store should already fail closed, but
                # double-check so Dream fact→status corrections never emit KPs.
                head = chain[0]
                if any(
                    c.category != head.category
                    or c.scope != head.scope
                    or c.scope_id != head.scope_id
                    for c in chain
                ):
                    continue
                score = _score_chain(chain, query_tokens, message, rewritten_query or "")
                if score <= 0:
                    continue
                if reason == "premise_conflict" and not _has_earlier_only_evidence(
                    chain, message,
                ):
                    continue
                candidates.append((score, head.card_id, chain, reason))

            if not candidates:
                return None

            # Stable rank: score desc, card_id asc.
            candidates.sort(key=lambda row: (-row[0], row[1]))

            # Ambiguity R9:
            # - equal top scores → fail closed (None)
            # - multiple weak matches without a decisive winner → single highest pure chain
            # - strong matches may still emit up to max_kps pure chains
            if len(candidates) >= 2 and candidates[0][0] == candidates[1][0]:
                return None

            max_kps = max(0, min(int(cfg.max_kps), _MAX_KPS_HARD))
            selected = (
                candidates[:1]
                if len(candidates) >= 2 and candidates[0][0] < 6.0
                else candidates[:max_kps]
            )
            if not selected:
                return None

            kps: list[KnowledgePointTrace] = []
            for _score, _hid, chain, kp_reason in selected:
                kp = await self._build_kp(chain, reason=kp_reason)
                if kp is not None:
                    kps.append(kp)
            if not kps:
                return None

            # max_chars below schema minimum (100) fail closed — never partial guidance.
            text = _render_text(kps, max_chars=int(cfg.max_chars))
            if not text.strip():
                return None
            return TemporalTrace(text=text, reason=reason, knowledge_points=kps)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _L.debug("temporal_trace assemble failed | error={}", type(exc).__name__)
            return None

    def _resolve_scope_ids(
        self,
        *,
        session_id: str,
        user_id: str,
        group_id: str | None,
    ) -> tuple[str, list[str]]:
        """Mirror RetrievalGate scope resolution (pool-aware for group).

        - group: one or more pool IDs (incl. ``__global__``); never private user
        - private/user: only ``user/<user_id>``
        """
        if group_id is not None and str(group_id).strip() != "":
            gid = str(group_id)
            gmc = self._group_memory_config
            if gmc is not None and hasattr(gmc, "resolve_group_pools"):
                pools = list(gmc.resolve_group_pools(gid) or [])
                scope_ids = [str(p) for p in pools if str(p).strip()]
                if scope_ids:
                    return "group", scope_ids
            return "group", [gid]
        sid = session_id or ""
        if sid.startswith("group_"):
            gid = sid[len("group_"):] or str(group_id or "")
            if not gid:
                return "group", []
            gmc = self._group_memory_config
            if gmc is not None and hasattr(gmc, "resolve_group_pools"):
                pools = list(gmc.resolve_group_pools(gid) or [])
                scope_ids = [str(p) for p in pools if str(p).strip()]
                if scope_ids:
                    return "group", scope_ids
            return "group", [gid]
        uid = str(user_id or "").strip()
        if not uid:
            return "user", []
        return "user", [uid]

    def _head_eligible(self, card: Any) -> bool:
        """Active heads must pass Card category time eligibility.

        Superseded parents loaded later via ``walk_supersedes_chain`` are
        **not** filtered here — trajectory history stays available.
        """
        if str(getattr(card, "status", "active") or "active") != "active":
            return False
        now = None
        if self._now_provider is not None:
            try:
                now = self._now_provider()
            except Exception:
                now = None
        return is_card_eligible_for_recall(
            card,
            policy=self._card_eligibility,
            now=now,
        )

    async def _collect_head_ids(
        self,
        *,
        scope: str,
        scope_ids: list[str],
        active_memory_hits: Any,
    ) -> list[str]:
        cfg = self.config
        max_heads = max(0, min(int(cfg.max_heads), _MAX_HEADS_HARD))
        if max_heads == 0 or not scope_ids:
            return []
        ordered: list[str] = []
        seen: set[str] = set()
        allowed_scope_ids = {str(s) for s in scope_ids}

        for raw in _iter_hit_ids(active_memory_hits):
            if raw in seen:
                continue
            card = await self.store.get_card(raw)
            if card is None:
                continue
            if not self._head_eligible(card):
                continue
            if card.scope != scope or str(card.scope_id) not in allowed_scope_ids:
                continue
            seen.add(card.card_id)
            ordered.append(card.card_id)
            if len(ordered) >= max_heads:
                return ordered

        # On trigger, also scan active chain heads across all resolved scope_ids
        # under one global max_heads budget.
        remaining = max_heads - len(ordered)
        if remaining <= 0:
            return ordered[:max_heads]
        # Fetch the full hard head scan cap per scope before eligibility
        # filtering. A malformed/expired row can sort ahead of a usable head;
        # asking only ``remaining`` rows would let it consume the budget.
        per_scope_limit = _MAX_HEADS_HARD
        for sid in scope_ids:
            if len(ordered) >= max_heads:
                break
            try:
                heads = await self.store.list_active_chain_heads(
                    scope, sid, limit=per_scope_limit,
                )
            except Exception:
                heads = []
            for card in heads or []:
                cid = getattr(card, "card_id", None) or getattr(card, "id", None)
                if not cid:
                    continue
                cid_s = str(cid)
                if cid_s in seen:
                    continue
                if getattr(card, "scope", scope) != scope:
                    continue
                if str(getattr(card, "scope_id", sid)) not in allowed_scope_ids:
                    continue
                if not self._head_eligible(card):
                    continue
                seen.add(cid_s)
                ordered.append(cid_s)
                if len(ordered) >= max_heads:
                    break
        return ordered[:max_heads]

    async def _build_kp(
        self,
        chain: list[Any],
        *,
        reason: str,
    ) -> KnowledgePointTrace | None:
        head = chain[0]
        parents = list(chain[1:])
        if not parents:
            return None

        current = TraceNode(
            card_id=str(head.card_id),
            content=str(head.content or ""),
            status=str(head.status or "active"),
            confidence=float(getattr(head, "confidence", 0.0) or 0.0),
            source_message_id=_source_msg(head),
            valid_from=getattr(head, "captured_at", None) or getattr(head, "created_at", None),
            valid_to=None,
        )

        earlier_nodes: list[TraceNode | dict[str, Any]] = []
        # valid_to of parent = successor (closer to head) captured_at/created_at
        for idx, parent in enumerate(parents):
            successor = chain[idx]  # chain[0]=head for first parent
            valid_to = (
                getattr(successor, "captured_at", None)
                or getattr(successor, "created_at", None)
            )
            earlier_nodes.append(
                TraceNode(
                    card_id=str(parent.card_id),
                    content=str(parent.content or ""),
                    status=str(parent.status or "superseded"),
                    confidence=float(getattr(parent, "confidence", 0.0) or 0.0),
                    source_message_id=_source_msg(parent),
                    valid_from=(
                        getattr(parent, "captured_at", None)
                        or getattr(parent, "created_at", None)
                    ),
                    valid_to=str(valid_to) if valid_to else None,
                )
            )

        # Trajectory: oldest-in-window → current (short content labels).
        ordered_for_traj = [*list(reversed(parents)), head]
        labels = [_short_label(c.content) for c in ordered_for_traj]
        trajectory = " → ".join(labels)

        evidence_refs = await self._collect_evidence_refs(chain)
        confidences = [
            float(getattr(c, "confidence", 0.0) or 0.0) for c in chain
        ]
        confidence = min(confidences) if confidences else float(
            getattr(head, "confidence", 0.0) or 0.0
        )

        return KnowledgePointTrace(
            current=current,
            earlier=earlier_nodes,
            trajectory=trajectory,
            evidence_refs=evidence_refs,
            reason=reason,
            confidence=confidence,
        )

    async def _collect_evidence_refs(self, chain: list[Any]) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()

        def _add(ref: str) -> None:
            r = (ref or "").strip()
            if not r or r in seen:
                return
            if len(refs) >= _MAX_REFS_PER_KP:
                return
            seen.add(r)
            refs.append(r)

        for card in chain:
            if len(refs) >= _MAX_REFS_PER_KP:
                break
            cid = str(getattr(card, "card_id", "") or "").strip()
            if cid:
                _add(f"card:{cid}")
            mid = _source_msg(card)
            if mid:
                _add(f"message:{mid}")
            list_obs = getattr(self.store, "list_observations", None)
            if not callable(list_obs):
                continue
            try:
                # Prefer bounded public API; fall back for older stores.
                try:
                    obs_list = await list_obs(card.card_id, limit=_MAX_OBS_PER_CARD)  # type: ignore[misc]
                except TypeError:
                    obs_list = await list_obs(card.card_id)  # type: ignore[misc]
                    obs_list = list(obs_list or [])[:_MAX_OBS_PER_CARD]
            except Exception:
                obs_list = []
            for obs in obs_list or []:
                if len(refs) >= _MAX_REFS_PER_KP:
                    break
                oid = getattr(obs, "observation_id", None)
                if oid:
                    _add(f"obs:{oid}")
        return refs


def _detect_trigger_reason(message: str) -> str | None:
    """Return reason from current_message only; None if no trigger."""
    text = message or ""
    if not text.strip():
        return None
    has_premise = any(m in text for m in _PREMISE_MARKERS) or bool(
        _PREMISE_NOT_Q_RE.search(text)
    )
    has_historical = any(m in text for m in _HISTORICAL_MARKERS)
    # Premise markers take priority when both present (e.g. rhetorical 不是…吗).
    if has_premise:
        return "premise_conflict"
    if has_historical:
        return "historical_intent"
    return None


def _iter_hit_ids(active_memory_hits: Any) -> list[str]:
    if not active_memory_hits:
        return []
    out: list[str] = []
    for item in active_memory_hits:
        if item is None:
            continue
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
            continue
        if isinstance(item, dict):
            for key in ("card_id", "id", "source_id"):
                val = item.get(key)
                if val:
                    out.append(str(val))
                    break
            continue
        for attr in ("card_id", "id", "source_id"):
            val = getattr(item, attr, None)
            if val:
                out.append(str(val))
                break
    return out


def _source_msg(card: Any) -> str | None:
    mid = getattr(card, "source_msg_id", None)
    if mid is None:
        mid = getattr(card, "source_message_id", None)
    if mid is None:
        return None
    s = str(mid).strip()
    return s or None


def _short_label(content: str, limit: int = 24) -> str:
    text = " ".join((content or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _extract_tokens(message: str, rewritten: str) -> set[str]:
    pool = f"{message or ''} {rewritten or ''}"
    tokens: set[str] = set()
    for run in _CJK_RE.findall(pool):
        if len(run) == 1:
            if run not in _STOPWORDS:
                tokens.add(run)
            continue
        # Unigrams for meaningful chars + bigrams.
        for ch in run:
            if ch not in _STOPWORDS and "\u4e00" <= ch <= "\u9fff":
                tokens.add(ch)
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if bg not in _STOPWORDS:
                tokens.add(bg)
        if run not in _STOPWORDS and len(run) >= 2:
            tokens.add(run)
    for word in _LATIN_RE.findall(pool):
        tokens.add(word.lower())
    return {t for t in tokens if t and t not in _STOPWORDS}


def _content_tokens(content: str) -> set[str]:
    return _extract_tokens(content or "", "")


def _score_chain(
    chain: list[Any],
    query_tokens: set[str],
    message: str,
    rewritten: str,
) -> float:
    """General lexical overlap only — no hardcoded topic-shape free passes."""
    if not query_tokens:
        query_tokens = _extract_tokens(message, rewritten)
    if not query_tokens:
        return 0.0
    content_blob_tokens: set[str] = set()
    for card in chain:
        content_blob_tokens |= _content_tokens(str(getattr(card, "content", "") or ""))
    overlap = query_tokens & content_blob_tokens
    if not overlap:
        return 0.0
    # Distinctive multi-char overlaps weigh more.
    score = 0.0
    for tok in overlap:
        score += 2.0 if len(tok) >= 2 else 1.0
    return score


def _has_earlier_only_evidence(
    chain: list[Any],
    message: str,
) -> bool:
    """Premise conflict needs a distinctive earlier-only token in current_message.

    rewritten_query must never authorize or complete premise evidence.
    """
    if len(chain) < 2:
        return False
    head_tokens = _content_tokens(str(getattr(chain[0], "content", "") or ""))
    earlier_tokens: set[str] = set()
    for card in chain[1:]:
        earlier_tokens |= _content_tokens(str(getattr(card, "content", "") or ""))
    # Distinctive earlier-only tokens: multi-char preferred (places/entities).
    earlier_only = {
        t for t in earlier_tokens - head_tokens
        if len(t) >= 2 and t not in _STOPWORDS
    }
    msg = message or ""
    if not msg or not earlier_only:
        return False
    # Prefer longer tokens first so place names beat fragments.
    return any(
        tok in msg
        for tok in sorted(earlier_only, key=lambda t: (-len(t), t))
    )


def _node_content(node: TraceNode | dict[str, Any] | Any) -> str:
    if isinstance(node, TraceNode):
        return node.content
    if isinstance(node, dict):
        return str(node.get("content") or node.get("text") or "")
    return str(getattr(node, "content", None) or getattr(node, "text", None) or node or "")


def _ellipsis_field(text: str, limit: int) -> str:
    """Field-level ellipsis only — never mid-ref / mid-line hard cut without …."""
    text = " ".join((text or "").split())
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"
    return text[: limit - 1] + "…"


_GUIDANCE_LINE = "说明: 日常回答以当前为准；以下仅在历史/前提冲突时提供时间轨迹。"
# Schema / runtime contract: never emit a partial trace under this budget.
_MIN_CHARS_CONTRACT = 100


def _render_kp_lines(kp: KnowledgePointTrace) -> list[str]:
    current_content = _node_content(kp.current)
    earlier_lines: list[str] = []
    for item in kp.earlier or []:
        c = _node_content(item)
        if c:
            earlier_lines.append(c)

    lines = [f"当前: {current_content}"]
    for e in earlier_lines:
        lines.append(f"更早: {e}")
    if kp.trajectory:
        lines.append(f"轨迹: {kp.trajectory}")
    if kp.evidence_refs:
        # Whole refs only — never partial typed ids.
        lines.append(f"依据: {' '.join(str(r) for r in kp.evidence_refs if r)}")
    lines.append(_GUIDANCE_LINE)
    return lines


def _fit_kp_lines(lines: list[str], budget: int) -> list[str] | None:
    """Fit one knowledge point into budget without mid-line slicing.

    Prefer dropping later lines (earlier/traj/refs) before shrinking fields.
    Current line is kept first; field-level ellipsis only when necessary.
    Always keeps a complete 说明 line when a fit is returned.
    """
    if budget < _MIN_CHARS_CONTRACT:
        return None
    joined = "\n".join(lines)
    if len(joined) <= budget:
        return lines

    # Drop optional trailing lines (依据, then 轨迹) while keeping 当前/更早/说明.
    working = list(lines)
    droppable_prefixes = ("依据:", "轨迹:")
    for prefix in droppable_prefixes:
        if len("\n".join(working)) <= budget:
            break
        working = [ln for ln in working if not ln.startswith(prefix)]

    if len("\n".join(working)) <= budget:
        return working

    # Field-level ellipsis on 更早 / 当前 content (keep labels intact).
    def _shrink_line(line: str, target_total: int, fixed_other: int) -> str:
        for prefix in ("更早: ", "当前: ", "轨迹: "):
            if line.startswith(prefix):
                avail = max(0, target_total - fixed_other - len(prefix))
                body = line[len(prefix) :]
                return prefix + _ellipsis_field(body, avail)
        return line

    # Iteratively shrink longest content-bearing lines.
    for _ in range(8):
        joined = "\n".join(working)
        if len(joined) <= budget:
            return working
        # Shrink the longest 更早 / 当前 / 轨迹 line.
        candidates = [
            i for i, ln in enumerate(working)
            if ln.startswith(("当前: ", "更早: ", "轨迹: "))
        ]
        if not candidates:
            break
        idx = max(candidates, key=lambda i: len(working[i]))
        fixed = len("\n".join(working)) - len(working[idx])
        working[idx] = _shrink_line(working[idx], budget, fixed)
        if (
            working[idx].endswith("…")
            and len(working[idx]) <= 4
            and not working[idx].startswith("当前:")
        ):
            # Line collapsed — drop non-current content lines.
            working.pop(idx)

    joined = "\n".join(working)
    if len(joined) <= budget:
        return working

    # Last resort: keep 当前 + complete 说明 only (field ellipsis on current body).
    current = next((ln for ln in lines if ln.startswith("当前:")), "当前:")
    guidance = next(
        (ln for ln in lines if ln.startswith("说明:")),
        _GUIDANCE_LINE,
    )
    # Never mid-cut guidance. If complete 说明 cannot fit with 当前, fail closed.
    if len(guidance) + 1 + len("当前: …") > budget:
        return None
    avail = budget - len(guidance) - 1
    if current.startswith("当前: "):
        body = current[len("当前: ") :]
        body_budget = max(0, avail - len("当前: "))
        current = "当前: " + _ellipsis_field(body, body_budget)
    if len(current) > avail:
        current = _ellipsis_field(current, avail)
    result = [current, guidance]
    if len("\n".join(result)) <= budget and any(
        ln.startswith("当前:") for ln in result
    ):
        return result
    return None


def _render_text(kps: list[KnowledgePointTrace], *, max_chars: int) -> str:
    """Deterministic body-only render; plugin owns the 记忆时间轨迹 label.

    Contract: max_chars must be in [100, 600]. Below 100 fail closed (empty —
    never partial 说明 / mid-cut guidance). Hard-capped at 600. Prefers whole
    knowledge points / whole lines; never mid-line or mid-ref hard cuts.
    Successful output starts with 当前: and includes a complete 说明 line.
    """
    try:
        requested = int(max_chars)
    except (TypeError, ValueError):
        return ""
    # Defense in depth: programmatic TemporalTraceConfig may bypass pydantic.
    if requested < _MIN_CHARS_CONTRACT:
        return ""
    hard = min(requested, _MAX_CHARS_HARD)
    sections: list[list[str]] = []
    for kp in kps:
        sections.append(_render_kp_lines(kp))

    if not sections:
        return ""

    # Prefer whole KPs in order (current-first already inside each KP).
    kept: list[str] = []
    used = 0
    for i, lines in enumerate(sections):
        sep = 2 if kept else 0  # "\n\n" between KPs
        budget = hard - used - sep
        if budget < _MIN_CHARS_CONTRACT and not kept:
            # Cannot fit a contract-compliant first KP.
            return ""
        if budget <= 0:
            break
        fitted = _fit_kp_lines(lines, budget)
        if fitted is None:
            if i == 0:
                return ""
            break
        block = "\n".join(fitted)
        if kept:
            used += 2 + len(block)
        else:
            used = len(block)
        kept.append(block)
        if used >= hard:
            break

    text = "\n\n".join(kept)
    # Never embed the plugin-owned label.
    text = text.replace("记忆时间轨迹", "时间轨迹")
    # Safety: if somehow still over, drop trailing KPs rather than mid-slice.
    while len(text) > hard and "\n\n" in text:
        text = text.rsplit("\n\n", 1)[0]
    if len(text) > hard:
        # Absolute last resort: only emit if 当前 + complete 说明 still fit.
        current = next(
            (ln for ln in text.splitlines() if ln.startswith("当前:")),
            "当前: …",
        )
        guidance = _GUIDANCE_LINE
        if len(current) + 1 + len(guidance) <= hard:
            text = f"{current}\n{guidance}"
        else:
            return ""
    if not text.strip():
        return ""
    # Contract: current-first + complete 说明 (never mid-cut guidance).
    if not text.lstrip().startswith("当前:"):
        return ""
    if not any(ln == _GUIDANCE_LINE for ln in text.splitlines()):
        return ""
    return text
