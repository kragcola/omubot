"""Bounded prompt projection shared by chat and Schedule."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from services.worldbook.config import WorldbookConfig
from services.worldbook.domain import (
    LifeState,
    ProjectionBlock,
    ProjectionTrace,
    SocialEvidenceRef,
    SourceMeta,
    StoryLedgerView,
    Storylet,
    validate_life_state_meta,
)
from services.worldbook.trigger import TriggerHit, budget_atomic_blocks

ProjectionMode = Literal["chat", "schedule"]


def _decay_has_elapsed(decay_at: str | None) -> bool:
    if not decay_at:
        # Missing TTL fails closed (treat as expired / not projectable).
        return True
    try:
        parsed = datetime.fromisoformat(str(decay_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)


def _life_item_projectable(item: Any) -> tuple[bool, str]:
    """Return (ok, budget_decision) for a life-state item."""
    meta = item.meta
    try:
        validate_life_state_meta(meta, require_ttl=True)
    except ValueError as exc:
        msg = str(exc).lower()
        if "decay_at" in msg or "ttl" in msg:
            return False, "rejected:missing_ttl"
        if "updated_at" in msg or "scope" in msg or "source" in msg or "privacy" in msg:
            return False, "rejected:missing_metadata"
        if "untrusted" in msg:
            return False, "rejected:untrusted_source"
        return False, "rejected:invalid_metadata"
    if _decay_has_elapsed(meta.decay_at):
        return False, "rejected:expired"
    return True, "accepted"


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    blocks: tuple[ProjectionBlock, ...]
    traces: tuple[ProjectionTrace, ...]
    mode: ProjectionMode

    @property
    def text(self) -> str:
        parts = [b.text for b in self.blocks if b.text.strip()]
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "blocks": [b.to_dict() for b in self.blocks],
            "traces": [t.to_dict() for t in self.traces],
        }


def _trace_for_block(
    block: ProjectionBlock,
    *,
    budget_decision: str,
) -> ProjectionTrace:
    return ProjectionTrace(
        source=block.meta.source,
        scope=block.meta.scope,
        hit_reason=block.meta.hit_reason or budget_decision,
        evidence_refs=block.meta.evidence_refs,
        budget_decision=budget_decision,
        priority=block.meta.priority,
        label=block.label,
        char_count=block.char_count,
        metadata={"block_id": block.block_id, "atomic": block.atomic},
    )


class PromptProjection:
    """Assemble atomic projection blocks with deterministic budgeting."""

    def __init__(self, config: WorldbookConfig) -> None:
        self._config = config

    def project(
        self,
        *,
        mode: ProjectionMode,
        canon_hits: Sequence[TriggerHit] = (),
        life_state: LifeState | None = None,
        ledger: StoryLedgerView | None = None,
        social: Sequence[SocialEvidenceRef] = (),
        storylets: Sequence[Storylet] = (),
        group_id: str | None = None,
        user_id: str | None = None,
    ) -> ProjectionResult:
        if not self._config.enabled:
            return ProjectionResult(blocks=(), traces=(), mode=mode)
        if mode == "chat" and not self._config.chat_projection_enabled:
            return ProjectionResult(blocks=(), traces=(), mode=mode)
        if mode == "schedule" and not self._config.schedule_projection_enabled:
            return ProjectionResult(blocks=(), traces=(), mode=mode)

        blocks: list[ProjectionBlock] = []
        traces: list[ProjectionTrace] = []
        total_remaining = int(self._config.total_budget_chars)

        # 1) Canon (atomic)
        accepted, decisions = budget_atomic_blocks(
            list(canon_hits),
            budget_chars=min(self._config.canon_budget_chars, total_remaining),
        )
        decision_map = dict(decisions)
        for hit in accepted:
            decision = decision_map.get(hit.entry.entry_id, "accepted")
            block = ProjectionBlock(
                block_id=f"canon:{hit.entry.entry_id}",
                label=hit.entry.title or hit.entry.entry_id,
                text=hit.entry.text,
                atomic=True,
                meta=SourceMeta(
                    source="native_canon",
                    scope="world",
                    confidence="high",
                    privacy="system",
                    hit_reason=hit.hit_reason,
                    priority=hit.entry.priority,
                    evidence_refs=(f"canon:{hit.entry.entry_id}",),
                    budget_decision=decision,
                ),
            )
            if block.char_count > total_remaining:
                traces.append(
                    _trace_for_block(block, budget_decision="rejected:total_budget")
                )
                continue
            blocks.append(block)
            traces.append(_trace_for_block(block, budget_decision=decision))
            total_remaining -= block.char_count
        for entry_id, decision in decisions:
            if decision.startswith("rejected"):
                traces.append(
                    ProjectionTrace(
                        source="native_canon",
                        scope="world",
                        hit_reason="budget",
                        evidence_refs=(f"canon:{entry_id}",),
                        budget_decision=decision,
                        label=entry_id,
                    )
                )

        # 2) Life state — fail closed on missing TTL / metadata / untrusted label
        if life_state is not None and life_state.items and total_remaining > 0:
            life_budget = min(self._config.life_budget_chars, total_remaining)
            lines: list[str] = []
            life_refs: list[str] = []
            for key in sorted(life_state.items.keys()):
                item = life_state.items[key]
                ok, decision = _life_item_projectable(item)
                if not ok:
                    traces.append(
                        ProjectionTrace(
                            source="life_state",
                            scope=item.meta.scope or "unknown",
                            hit_reason=f"life:{item.key}",
                            evidence_refs=item.meta.evidence_refs,
                            budget_decision=decision,
                            label=item.key,
                        )
                    )
                    continue
                line = f"{item.key}: {item.value}"
                if sum(len(x) + 1 for x in lines) + len(line) > life_budget:
                    traces.append(
                        ProjectionTrace(
                            source="life_state",
                            scope=item.meta.scope,
                            hit_reason=f"life:{item.key}",
                            evidence_refs=item.meta.evidence_refs,
                            budget_decision="rejected:budget",
                            label=item.key,
                        )
                    )
                    continue
                lines.append(line)
                life_refs.extend(item.meta.evidence_refs)
            if lines:
                text = "【生活状态】\n" + "\n".join(lines)
                block = ProjectionBlock(
                    block_id="life_state",
                    label="生活状态",
                    text=text,
                    atomic=True,
                    meta=SourceMeta(
                        source="life_state",
                        scope="self",
                        confidence="medium",
                        privacy="private",
                        hit_reason="life_state",
                        priority=80,
                        evidence_refs=tuple(life_refs),
                        budget_decision="accepted",
                    ),
                )
                if block.char_count <= total_remaining:
                    blocks.append(block)
                    traces.append(
                        _trace_for_block(block, budget_decision="accepted")
                    )
                    total_remaining -= block.char_count

        # 3) Story ledger (main + sides + ambient), no social
        if ledger is not None and total_remaining > 0:
            arc_budget = min(self._config.arc_budget_chars, total_remaining)
            arc_blocks = self._ledger_blocks(ledger, budget=arc_budget)
            for block in arc_blocks:
                if block.char_count <= total_remaining:
                    blocks.append(block)
                    traces.append(
                        _trace_for_block(block, budget_decision="accepted")
                    )
                    total_remaining -= block.char_count
                else:
                    traces.append(
                        _trace_for_block(block, budget_decision="rejected:budget")
                    )

        # 4) Social evidence — chat only, current scope, fail-closed
        if mode == "chat" and self._config.social_evidence_enabled:
            social_blocks = self._social_blocks(
                social,
                group_id=group_id,
                user_id=user_id,
                budget=min(self._config.social_budget_chars, total_remaining),
            )
            for block, decision in social_blocks:
                if decision == "accepted" and block.char_count <= total_remaining:
                    blocks.append(block)
                    traces.append(_trace_for_block(block, budget_decision=decision))
                    total_remaining -= block.char_count
                else:
                    traces.append(_trace_for_block(block, budget_decision=decision))
        elif mode == "schedule" and social:
            # Explicit negative: schedule must never receive social evidence.
            for ref in social:
                traces.append(
                    ProjectionTrace(
                        source="social_evidence",
                        scope=f"group:{ref.group_id}",
                        hit_reason="schedule_forbidden",
                        evidence_refs=(ref.evidence_ref(),),
                        budget_decision="rejected:schedule_no_social",
                        label=ref.experience_id,
                    )
                )

        # 5) Selected storylet narrative snippets
        if storylets and total_remaining > 0:
            story_budget = min(self._config.storylet_budget_chars, total_remaining)
            used = 0
            for storylet in storylets:
                text = f"【事件候选·{storylet.title}】{storylet.text}".strip()
                if used + len(text) > story_budget:
                    traces.append(
                        ProjectionTrace(
                            source="storylet",
                            scope=storylet.scope,
                            hit_reason=f"storylet:{storylet.storylet_id}",
                            evidence_refs=(f"storylet:{storylet.storylet_id}",),
                            budget_decision="rejected:budget",
                            label=storylet.title,
                            char_count=len(text),
                        )
                    )
                    continue
                block = ProjectionBlock(
                    block_id=f"storylet:{storylet.storylet_id}",
                    label=storylet.title,
                    text=text,
                    atomic=True,
                    meta=SourceMeta(
                        source="storylet",
                        scope=storylet.scope,
                        confidence="medium",
                        privacy="system",
                        hit_reason=f"storylet:{storylet.storylet_id}",
                        priority=storylet.priority,
                        evidence_refs=(f"storylet:{storylet.storylet_id}",),
                        budget_decision="accepted",
                    ),
                )
                if block.char_count <= total_remaining:
                    blocks.append(block)
                    traces.append(
                        _trace_for_block(block, budget_decision="accepted")
                    )
                    total_remaining -= block.char_count
                    used += block.char_count

        return ProjectionResult(
            blocks=tuple(blocks),
            traces=tuple(traces),
            mode=mode,
        )

    def _ledger_blocks(
        self,
        ledger: StoryLedgerView,
        *,
        budget: int,
    ) -> list[ProjectionBlock]:
        lines: list[str] = ["【故事账本】"]
        remaining = budget

        def add_arc(role: str, arc: Any) -> None:
            nonlocal remaining
            if not isinstance(arc, dict):
                return
            arc_id = str(arc.get("arc_id") or "")
            title = str(arc.get("title") or arc_id)
            stage = str(arc.get("stage") or "")
            goals = arc.get("goals") if isinstance(arc.get("goals"), list) else []
            threads = (
                arc.get("open_threads")
                if isinstance(arc.get("open_threads"), list)
                else []
            )
            piece = f"- ({role}) {title} [{stage}]"
            if goals:
                piece += " 目标:" + "；".join(str(g) for g in goals[:2])
            if threads:
                piece += " 线索:" + "；".join(str(t) for t in threads[:2])
            if len(piece) + 1 > remaining:
                return
            lines.append(piece)
            remaining -= len(piece) + 1

        if ledger.main is not None:
            add_arc("main", dict(ledger.main))
        for side in ledger.sides:
            add_arc("side", dict(side))
        for amb in ledger.ambient:
            add_arc("ambient", dict(amb))
        if len(lines) <= 1:
            return []
        text = "\n".join(lines)
        return [
            ProjectionBlock(
                block_id="story_ledger",
                label="故事账本",
                text=text,
                atomic=True,
                meta=SourceMeta(
                    source="story_ledger",
                    scope="fiction",
                    confidence="high",
                    privacy="system",
                    hit_reason="ledger_stack",
                    priority=90,
                    evidence_refs=tuple(
                        f"arc:{aid}" for aid in ledger.all_arc_ids()
                    ),
                    budget_decision="accepted",
                ),
            )
        ]

    def _social_blocks(
        self,
        social: Sequence[SocialEvidenceRef],
        *,
        group_id: str | None,
        user_id: str | None,
        budget: int,
    ) -> list[tuple[ProjectionBlock, str]]:
        """Project only current group AND current user evidence; fail closed."""
        out: list[tuple[ProjectionBlock, str]] = []
        if not group_id or not user_id:
            decision = "rejected:private_or_missing_scope"
            if group_id and not user_id:
                decision = "rejected:missing_user"
            for ref in social:
                block = ProjectionBlock(
                    block_id=f"social:{ref.experience_id}",
                    label="共同经历",
                    text=ref.summary,
                    atomic=True,
                    meta=SourceMeta(
                        source="social_evidence",
                        scope=f"group:{ref.group_id}",
                        confidence=ref.confidence,
                        privacy=ref.privacy,
                        hit_reason="scope_forbidden",
                        priority=70,
                        evidence_refs=(ref.evidence_ref(),),
                        budget_decision=decision,
                    ),
                )
                out.append((block, decision))
            return out

        used = 0
        lines: list[str] = []
        refs: list[str] = []
        confidences: list[str] = []
        privacies: list[str] = []
        for ref in social:
            if str(ref.group_id) != str(group_id):
                block = ProjectionBlock(
                    block_id=f"social:{ref.experience_id}",
                    label="共同经历",
                    text=ref.summary,
                    atomic=True,
                    meta=SourceMeta(
                        source="social_evidence",
                        scope=f"group:{ref.group_id}",
                        confidence=ref.confidence,
                        privacy=ref.privacy,
                        hit_reason="cross_group",
                        priority=70,
                        evidence_refs=(ref.evidence_ref(),),
                        budget_decision="rejected:cross_group",
                    ),
                )
                out.append((block, "rejected:cross_group"))
                continue
            if str(ref.user_id) != str(user_id):
                block = ProjectionBlock(
                    block_id=f"social:{ref.experience_id}",
                    label="共同经历",
                    text=ref.summary,
                    atomic=True,
                    meta=SourceMeta(
                        source="social_evidence",
                        scope=f"group:{ref.group_id}",
                        confidence=ref.confidence,
                        privacy=ref.privacy,
                        hit_reason="cross_user",
                        priority=70,
                        evidence_refs=(ref.evidence_ref(),),
                        budget_decision="rejected:cross_user",
                    ),
                )
                out.append((block, "rejected:cross_user"))
                continue
            if ref.privacy not in {"group", "public"}:
                block = ProjectionBlock(
                    block_id=f"social:{ref.experience_id}",
                    label="共同经历",
                    text=ref.summary,
                    atomic=True,
                    meta=SourceMeta(
                        source="social_evidence",
                        scope=f"group:{ref.group_id}",
                        confidence=ref.confidence,
                        privacy=ref.privacy,
                        hit_reason="privacy_forbidden",
                        priority=70,
                        evidence_refs=(ref.evidence_ref(),),
                        budget_decision="rejected:privacy",
                    ),
                )
                out.append((block, "rejected:privacy"))
                continue
            if not str(ref.evidence_message_id or "").strip():
                block = ProjectionBlock(
                    block_id=f"social:{ref.experience_id}",
                    label="共同经历",
                    text=ref.summary,
                    atomic=True,
                    meta=SourceMeta(
                        source="social_evidence",
                        scope=f"group:{ref.group_id}",
                        confidence="low",
                        privacy=ref.privacy,
                        hit_reason="missing_evidence",
                        priority=70,
                        evidence_refs=(),
                        budget_decision="rejected:missing_evidence",
                    ),
                )
                out.append((block, "rejected:missing_evidence"))
                continue
            line = f"- {ref.summary}".strip()
            if used + len(line) + 1 > budget:
                block = ProjectionBlock(
                    block_id=f"social:{ref.experience_id}",
                    label="共同经历",
                    text=ref.summary,
                    atomic=True,
                    meta=SourceMeta(
                        source="social_evidence",
                        scope=f"group:{ref.group_id}",
                        confidence=ref.confidence,
                        privacy=ref.privacy,
                        hit_reason="social_evidence",
                        priority=70,
                        evidence_refs=(ref.evidence_ref(),),
                        budget_decision="rejected:budget",
                    ),
                )
                out.append((block, "rejected:budget"))
                continue
            lines.append(line)
            refs.append(ref.evidence_ref())
            confidences.append(str(ref.confidence))
            privacies.append(str(ref.privacy))
            used += len(line) + 1
        if lines:
            # Preserve source confidence/privacy: never invent higher trust.
            conf_rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
            worst_conf = min(
                confidences,
                key=lambda c: conf_rank.get(c, 0),
            )
            # If any accepted row is public-only, keep public; else group.
            privacy_out: str = "public" if all(p == "public" for p in privacies) else "group"
            text = "【当前群共同经历】\n" + "\n".join(lines)
            block = ProjectionBlock(
                block_id="social_evidence",
                label="共同经历",
                text=text,
                atomic=True,
                meta=SourceMeta(
                    source="social_evidence",
                    scope=f"group:{group_id}",
                    confidence=worst_conf,  # type: ignore[arg-type]
                    privacy=privacy_out,  # type: ignore[arg-type]
                    hit_reason="current_scope",
                    priority=70,
                    evidence_refs=tuple(refs),
                    budget_decision="accepted",
                ),
            )
            out.append((block, "accepted"))
        return out
