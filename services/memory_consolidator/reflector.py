"""ReflectionGenerator — D.3 reflection pipeline.

One pass:

1. Collect :class:`NegativeSignal` rows from configured sources
2. For each signal, dedup against ``consolidator_reflection_log``
   (UNIQUE source_table+source_id) — skipped signals never touch the
   LLM, so a noisy admin clicking "reject" 5×/min won't burn tokens
3. Fire one ``reflection_consolidator`` LLM call per fresh signal,
   forcing JSON schema matching :class:`EpisodePayload`
4. ``record_reflection_candidate`` writes candidate + log row in the
   same SQLite transaction (UNIQUE constraint = exactly-once safety net
   for races between dedup-check and INSERT)

The whole run is wrapped in a :class:`ConsolidatorCandidatesStore`
``ScanRun`` row so admin can audit "this batch of reflections came from
which signals". LLM / parse / write failures of one signal never
abort the run — they're logged and the run proceeds.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from services.llm.llm_request import LLMRequest
from services.memory_consolidator.feedback_sources import (
    NegativeSignal,
    collect_negative_signals,
)
from services.memory_consolidator.store import ConsolidatorCandidatesStore

_L = logger.bind(channel="memory_consolidator")

_REFLECTION_SYSTEM_PROMPT = """你是 Omubot 的反思整理器（reflection_consolidator D.3）。

任务：基于一条「Bot 被纠正」的负反馈信号，输出一条 episode 反思候选。

只输出 JSON，不要 Markdown：
{
  "situation":         "...",
  "observed_context":  "...",
  "action_taken":      "...",
  "outcome_signal":    "...",
  "reflection":        "...",
  "confidence":        0.0
}

字段含义：
- situation：当时是什么场景（例：用户问技术细节）
- observed_context：上下文（例：早晨、用户语气急）
- action_taken：Bot 做了什么（例：直接给结论没解释）
- outcome_signal：结果反馈（例：用户给了 negative）
- reflection：下次该怎么做（例：先简短确认再展开）

约束：
- confidence ∈ [0,1]，反思类候选默认偏保守，0.3~0.6 之间
- reflection 字段必须给出可执行的"下次怎么做"，不能空着或泛泛而谈
- situation / reflection 不能为空字符串
"""


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


def _safe_confidence(raw: Any, *, default: float = 0.5) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, value))


@dataclass(slots=True)
class ReflectionRunReport:
    """Summary returned by :meth:`ReflectionGenerator.run_once`."""

    run_id: str
    signals_total: int
    signals_skipped_dedup: int
    candidates: int
    failures: int
    status: str
    error_text: str = ""


class ReflectionGenerator:
    """One reflection pass: signals → LLM → episode candidates."""

    def __init__(
        self,
        *,
        store: ConsolidatorCandidatesStore,
        llm_client: Any,
        style_store: Any = None,
        slang_store: Any = None,
        style_store_getter: Callable[[], Any] | None = None,
        slang_store_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._store = store
        self._llm_client = llm_client
        self._style_store = style_store
        self._slang_store = slang_store
        self._style_store_getter = style_store_getter
        self._slang_store_getter = slang_store_getter

    def _resolve_style_store(self) -> Any:
        if self._style_store_getter is not None:
            try:
                return self._style_store_getter()
            except Exception:
                return None
        return self._style_store

    def _resolve_slang_store(self) -> Any:
        if self._slang_store_getter is not None:
            try:
                return self._slang_store_getter()
            except Exception:
                return None
        return self._slang_store

    async def run_once(
        self,
        *,
        group_id: str = "",
        triggered_by: str = "admin",
        scope: str = "group",
        max_signals: int = 10,
    ) -> ReflectionRunReport:
        """Pull negative signals → reflect on each new one → record."""
        if scope not in {"group", "user", "global"}:
            raise ValueError(f"invalid scope: {scope!r}")
        run_id = await self._store.start_run(
            triggered_by=triggered_by or "admin",
            group_id=str(group_id or ""),
            scope=scope,  # type: ignore[arg-type]
            meta={
                "kind": "reflection",
                "max_signals": int(max_signals),
            },
        )
        signals: list[NegativeSignal] = []
        skipped_dedup = 0
        candidates_total = 0
        failures = 0
        status = "done"
        error_text = ""
        completed = False
        try:
            signals = await collect_negative_signals(
                style_store=self._resolve_style_store(),
                slang_store=self._resolve_slang_store(),
                group_id=str(group_id or ""),
                limit_per_source=max(5, int(max_signals)),
            )
            signals = signals[: max(1, int(max_signals))]
            for signal in signals:
                if not signal.source_id or not signal.source_table:
                    failures += 1
                    continue
                existing = await self._store.get_reflection_log(
                    source_table=signal.source_table,
                    source_id=signal.source_id,
                )
                if existing is not None:
                    skipped_dedup += 1
                    continue
                try:
                    payload = await self._reflect_one(signal=signal)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failures += 1
                    _L.warning(
                        "reflection LLM/parse failed | run={} signal={}/{} error={}",
                        run_id,
                        signal.source_table,
                        signal.source_id,
                        exc,
                    )
                    continue
                if payload is None:
                    failures += 1
                    continue
                try:
                    await self._store.record_reflection_candidate(
                        run_id=run_id,
                        scope=scope,  # type: ignore[arg-type]
                        group_id=str(signal.group_id or group_id or ""),
                        source_message_pks=[],
                        payload=payload,
                        confidence=_safe_confidence(payload.get("confidence")),
                        source_table=signal.source_table,
                        source_id=signal.source_id,
                        meta={
                            "signal_summary": signal.summary,
                            "signal_meta": signal.meta,
                        },
                    )
                    candidates_total += 1
                except Exception as exc:
                    failures += 1
                    _L.warning(
                        "reflection record failed | run={} signal={}/{} error={}",
                        run_id,
                        signal.source_table,
                        signal.source_id,
                        exc,
                    )
            await self._store.finish_run(
                run_id,
                status="done",
                scanned_count=len(signals),
                candidates_count=candidates_total,
            )
            completed = True
            return ReflectionRunReport(
                run_id=run_id,
                signals_total=len(signals),
                signals_skipped_dedup=skipped_dedup,
                candidates=candidates_total,
                failures=failures,
                status=status,
            )
        except BaseException as exc:
            error_text = (
                f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            )
            raise
        finally:
            if not completed:
                try:
                    await asyncio.shield(
                        self._store.finish_run(
                            run_id,
                            status="failed",
                            scanned_count=len(signals),
                            candidates_count=candidates_total,
                            error_text=error_text or "cancelled",
                        )
                    )
                except Exception as exc:
                    _L.warning(
                        "reflection finish_run cleanup failed | run={} error={}",
                        run_id,
                        exc,
                    )

    async def _reflect_one(
        self,
        *,
        signal: NegativeSignal,
    ) -> dict[str, Any] | None:
        """Fire one ``reflection_consolidator`` call; return the projected payload.

        Returns ``None`` when the model output is unparseable or missing
        required fields. The payload returned here still contains the
        raw ``confidence`` key — caller projects it through
        :func:`normalize_payload` (via ``record_reflection_candidate``)
        which drops unknown keys.
        """
        body = self._format_signal(signal)
        request = LLMRequest(
            task="reflection_consolidator",
            user_id="",
            group_id=str(signal.group_id) or None,
            stable_blocks=[_REFLECTION_SYSTEM_PROMPT],
            user_messages=[{"role": "user", "content": body}],
            max_tokens=900,
            requires_capabilities=("chat",),
            auto_record_usage=True,
        )
        result = await self._llm_client._call(request)
        text = str(
            (result or {}).get("text")
            or (result or {}).get("output_text")
            or ""
        )
        parsed = _extract_json_object(text)
        if not parsed:
            return None
        situation = str(parsed.get("situation", "") or "").strip()
        reflection = str(parsed.get("reflection", "") or "").strip()
        if not situation or not reflection:
            return None
        return {
            "situation": situation,
            "observed_context": str(parsed.get("observed_context", "") or ""),
            "action_taken": str(parsed.get("action_taken", "") or ""),
            "outcome_signal": str(parsed.get("outcome_signal", "") or ""),
            "reflection": reflection,
            "confidence": parsed.get("confidence"),
        }

    @staticmethod
    def _format_signal(signal: NegativeSignal) -> str:
        meta_pairs = [
            f"{k}={v}" for k, v in (signal.meta or {}).items() if str(v).strip()
        ]
        meta_line = " ".join(meta_pairs) if meta_pairs else "（无附加元数据）"
        return (
            f"信号来源：{signal.source_table}/{signal.source_id}\n"
            f"群：{signal.group_id or '（无）'}\n"
            f"概要：{signal.summary}\n"
            f"细节：\n{signal.detail}\n"
            f"元数据：{meta_line}"
        )
