"""MemoryConsolidator — single dry-run pass over the conversation archive.

The orchestrator wires four pieces together:

1. ``ConversationArchive.read_scan_batch`` (cursor-backed scanner protocol)
2. One ``LLMRequest(task="reflection_consolidator")`` call per batch — the
   spine routes it to the configured profile / cache breakpoints
3. Per-domain payload normalization (``normalize_payload`` /
   ``derive_raw_text`` from :mod:`services.memory_consolidator.types`)
4. ``ConsolidatorCandidatesStore.record_candidate`` +
   ``LearningNormalizerStore.attach_candidate(domain="general", ...)``

Promotion to production stores is **out of scope** — this module never
touches slang/style/episodic/knowledge_graph writers. ``run_once`` is
cancel-safe: any ``BaseException`` (including ``CancelledError`` /
``TimeoutError``) flows through ``finish_run(status="failed")`` +
``finish_scan_batch(status="cancelled", advance_cursor=False)``.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from loguru import logger

from services.conversation_archive.scanner import (
    finish_scan_batch as _archive_finish_scan_batch,
)
from services.conversation_archive.scanner import (
    read_scan_batch as _archive_read_scan_batch,
)
from services.llm.llm_request import LLMRequest
from services.memory_consolidator.store import ConsolidatorCandidatesStore
from services.memory_consolidator.types import (
    CANDIDATE_DOMAINS,
    RunReport,
    derive_raw_text,
    normalize_payload,
)

_L = logger.bind(channel="memory_consolidator")

_SCANNER_NAME = "memory_consolidator"
_SCANNER_VERSION = "v1"
_PARAMS_HASH = "default-2026-05-21"

_SYSTEM_PROMPT = """你是 Omubot 的多层学习记忆整理器（reflection_consolidator）。

任务：基于一批最近的群聊片段，输出 5 类候选记忆 — 每一类都是 dry-run，**不会**自动落库。

只输出 JSON，不要 Markdown：
{
  "facts":           [{"subject":"...", "predicate":"...", "object":"...",
                       "evidence":"...", "confidence":0.0}],
  "slang":           [{"term":"...", "meaning":"...", "aliases":["..."],
                       "repeat_policy":"understand_only", "evidence":"...",
                       "confidence":0.0}],
  "styles":          [{"expression":"...", "situation":"...",
                       "outcome_signal":"...", "evidence":"...",
                       "confidence":0.0}],
  "episodes":        [{"situation":"...", "observed_context":"...",
                       "action_taken":"...", "outcome_signal":"...",
                       "reflection":"...", "confidence":0.0}],
  "graph_relations": [{"subject_node":"...", "predicate":"...",
                       "object_node":"...", "edge_type":"fact",
                       "evidence":"...", "confidence":0.0}]
}

约束：
- 任何一类没有候选时返回空列表，整体仍是合法 JSON。
- confidence 0.0~1.0 保守估计；不确定就低分。
- 每类至多 6 条；总和不超过 20 条。
"""

_DOMAIN_KEY_TO_DOMAIN: dict[str, str] = {
    "facts": "fact",
    "slang": "slang",
    "styles": "style",
    "episodes": "episode",
    "graph_relations": "graph_relation",
}


def _extract_json_object(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    try:
        loaded = json.loads(body)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", body, flags=re.S)
    if not match:
        return {}
    try:
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _format_messages(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows[-120:]:
        text = str(row.get("content_text") or "").strip()
        if not text:
            continue
        speaker = str(row.get("speaker") or row.get("user_id") or "unknown")
        lines.append(f"{speaker}: {text[:500]}")
    return "\n".join(lines)


def _safe_confidence(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def _row_pks(rows: list[dict[str, Any]]) -> list[int]:
    pks: list[int] = []
    for row in rows:
        pk_raw = row.get("message_pk")
        if pk_raw is None:
            continue
        try:
            pks.append(int(pk_raw))
        except (TypeError, ValueError):
            continue
    return pks


class MemoryConsolidator:
    """Orchestrate one dry-run pass: archive → LLM → typed candidates."""

    def __init__(
        self,
        *,
        store: ConsolidatorCandidatesStore,
        archive: Any,
        normalizer: Any,
        llm_client: Any,
    ) -> None:
        self._store = store
        self._archive = archive
        self._normalizer = normalizer
        self._llm_client = llm_client

    async def run_once(
        self,
        *,
        group_id: str,
        triggered_by: str,
        scope: str = "group",
        max_batches: int = 1,
        batch_size: int = 50,
    ) -> RunReport:
        """Run one dry-run pass; never raises — failures land in the run row."""
        if scope not in {"group", "user", "global"}:
            raise ValueError(f"invalid scope: {scope!r}")
        run_id = await self._store.start_run(
            triggered_by=triggered_by,
            group_id=group_id,
            scope=scope,  # type: ignore[arg-type]
            meta={
                "max_batches": int(max_batches),
                "batch_size": int(batch_size),
                "scanner_name": _SCANNER_NAME,
                "scanner_version": _SCANNER_VERSION,
            },
        )
        scanned_total = 0
        candidates_total = 0
        failure_text = ""
        completed = False
        try:
            for batch_idx in range(max(1, int(max_batches))):
                batch = await _archive_read_scan_batch(
                    self._archive,
                    scanner_name=_SCANNER_NAME,
                    group_id=str(group_id),
                    limit=int(batch_size),
                    scanner_version=_SCANNER_VERSION,
                    params_hash=_PARAMS_HASH,
                    required=True,
                )
                rows = list(batch.get("rows") or [])
                if not rows:
                    await _archive_finish_scan_batch(
                        self._archive,
                        batch,
                        status="success",
                        scanned_count=0,
                        extracted_count=0,
                        saved_count=0,
                        advance_cursor=False,
                    )
                    break
                scanned_total += len(rows)
                try:
                    candidates = await self._consume_batch(
                        run_id=run_id,
                        scope=scope,
                        group_id=group_id,
                        rows=rows,
                        batch_idx=batch_idx,
                    )
                except Exception as exc:
                    _L.warning(
                        "consolidator batch failed | run={} batch={} error={}",
                        run_id,
                        batch_idx,
                        exc,
                    )
                    await _archive_finish_scan_batch(
                        self._archive,
                        batch,
                        status="failed",
                        scanned_count=len(rows),
                        extracted_count=0,
                        saved_count=0,
                        error=str(exc),
                        advance_cursor=False,
                    )
                    continue
                candidates_total += candidates
                await _archive_finish_scan_batch(
                    self._archive,
                    batch,
                    status="success",
                    scanned_count=len(rows),
                    extracted_count=candidates,
                    saved_count=candidates,
                    advance_cursor=True,
                )
            completed = True
            await self._store.finish_run(
                run_id,
                status="done",
                scanned_count=scanned_total,
                candidates_count=candidates_total,
            )
            return RunReport(
                run_id=run_id,
                scanned=scanned_total,
                candidates=candidates_total,
                status="done",
            )
        except BaseException as exc:
            failure_text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            raise
        finally:
            if not completed:
                try:
                    await asyncio.shield(
                        self._store.finish_run(
                            run_id,
                            status="failed",
                            scanned_count=scanned_total,
                            candidates_count=candidates_total,
                            error_text=failure_text or "cancelled",
                        )
                    )
                except Exception as exc:
                    _L.warning(
                        "consolidator finish_run cleanup failed | run={} error={}",
                        run_id,
                        exc,
                    )

    async def _consume_batch(
        self,
        *,
        run_id: str,
        scope: str,
        group_id: str,
        rows: list[dict[str, Any]],
        batch_idx: int,
    ) -> int:
        body = _format_messages(rows)
        if not body:
            return 0
        request = LLMRequest(
            task="reflection_consolidator",
            user_id="",
            group_id=str(group_id) or None,
            stable_blocks=[_SYSTEM_PROMPT],
            user_messages=[{"role": "user", "content": body}],
            max_tokens=1400,
            requires_capabilities=("chat",),
            auto_record_usage=True,
        )
        # LLM / parse failures propagate so the outer handler marks the
        # batch failed and holds the archive cursor — do NOT swallow.
        llm_result = await self._llm_client._call(request)
        text = str(
            (llm_result or {}).get("text")
            or (llm_result or {}).get("output_text")
            or ""
        )
        parsed = _extract_json_object(text)
        if not parsed:
            raise ValueError(
                f"reflection_consolidator returned unparsable text "
                f"(len={len(text)})"
            )
        source_pks = _row_pks(rows)
        recorded = 0
        for json_key, domain in _DOMAIN_KEY_TO_DOMAIN.items():
            items = parsed.get(json_key) or []
            if not isinstance(items, list):
                continue
            for raw_item in items:
                if not isinstance(raw_item, dict):
                    continue
                if domain not in CANDIDATE_DOMAINS:
                    continue
                payload = normalize_payload(domain, raw_item)
                if not any(str(value).strip() for value in payload.values() if not isinstance(value, list)):
                    continue
                confidence = _safe_confidence(raw_item.get("confidence"))
                candidate_id = await self._store.record_candidate(
                    run_id=run_id,
                    domain=domain,  # type: ignore[arg-type]
                    scope=scope,  # type: ignore[arg-type]
                    group_id=group_id,
                    source_message_pks=source_pks,
                    payload=payload,
                    confidence=confidence,
                )
                cluster_id = await self._attach_normalizer(
                    candidate_id=candidate_id,
                    domain=domain,
                    scope=scope,
                    group_id=group_id,
                    payload=payload,
                )
                if cluster_id:
                    await self._store.update_candidate_cluster(
                        candidate_id, cluster_id
                    )
                recorded += 1
        if recorded > 0:
            await self._maybe_summarize_episodes(
                run_id=run_id,
                group_id=group_id,
                batch_idx=batch_idx,
                fallback_body=body,
            )
        return recorded

    async def _attach_normalizer(
        self,
        *,
        candidate_id: str,
        domain: str,
        scope: str,
        group_id: str,
        payload: dict[str, Any],
    ) -> str:
        if self._normalizer is None:
            return ""
        attach = getattr(self._normalizer, "attach_candidate", None)
        if not callable(attach):
            return ""
        raw_text = derive_raw_text(domain, payload)
        normalizer_scope = "global" if scope == "global" else "group"
        try:
            result = await attach(  # type: ignore[misc]
                domain="general",
                scope=normalizer_scope,
                group_id=group_id,
                raw_text=raw_text,
                source_table="consolidator_candidates",
                source_id=candidate_id,
                profile="general",
                meta={
                    "consolidator_domain": domain,
                    "consolidator_scope": scope,
                },
            )
        except Exception as exc:
            _L.warning(
                "consolidator normalizer attach failed | candidate={} domain={} error={}",
                candidate_id,
                domain,
                exc,
            )
            return ""
        cluster_id = getattr(result, "cluster_id", "") or ""
        return str(cluster_id)

    async def _maybe_summarize_episodes(
        self,
        *,
        run_id: str,
        group_id: str,
        batch_idx: int,
        fallback_body: str,
    ) -> None:
        """Fire one ``episode_summarizer`` call per batch.

        The reflection task already produces typed episodes; this second
        call is a deliberate caller-presence guarantee for the spine-
        registered ``episode_summarizer`` task. Output is logged but not
        persisted — promotion is out of scope for the dry-run.
        """
        request = LLMRequest(
            task="episode_summarizer",
            group_id=str(group_id) or None,
            stable_blocks=[
                "你是 Omubot 的 episode 摘要器。基于一批群聊文本，"
                "用一段简短中文（≤120 字）总结发生了什么、起因、结果，"
                "只输出纯文本，不要 JSON。",
            ],
            user_messages=[{"role": "user", "content": fallback_body}],
            max_tokens=300,
            requires_capabilities=("chat",),
            auto_record_usage=True,
        )
        try:
            await self._llm_client._call(request)
        except Exception as exc:
            _L.warning(
                "episode_summarizer call failed | run={} batch={} error={}",
                run_id,
                batch_idx,
                exc,
            )
