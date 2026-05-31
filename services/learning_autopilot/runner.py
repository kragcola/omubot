"""Autopilot runner — orchestrates all noun reviewers based on settings."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .base import AggressivenessConfig, ReviewBatchResult, ReviewerBase

logger = logging.getLogger(__name__)

TZ_SHANGHAI = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


class AutopilotRunner:
    """Central coordinator that drives all registered reviewers."""

    def __init__(self, *, llm_client: Any = None, storage_dir: Path | None = None) -> None:
        self._llm_client = llm_client
        self._storage_dir = storage_dir or Path("storage")
        self._reviewers: dict[str, ReviewerBase] = {}
        self._running: set[str] = set()
        self._lock = asyncio.Lock()

    def register(self, reviewer: ReviewerBase) -> None:
        self._reviewers[reviewer.domain] = reviewer

    @property
    def domains(self) -> list[str]:
        return list(self._reviewers.keys())

    def get_reviewer(self, domain: str) -> ReviewerBase | None:
        return self._reviewers.get(domain)

    async def status_all(self, config: AggressivenessConfig) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for domain, reviewer in self._reviewers.items():
            try:
                state = await reviewer.get_state()
                pending = await reviewer.count_pending(config)
                result[domain] = {
                    "active": state.active,
                    "processed": state.processed,
                    "approved": state.approved,
                    "rejected": state.rejected,
                    "kept": state.kept,
                    "remaining": pending,
                    "last_done_at": state.last_done_at,
                }
            except Exception as exc:
                result[domain] = {"error": str(exc)}
        return result

    async def run_domain(
        self,
        domain: str,
        *,
        batch_size: int = 10,
        config: AggressivenessConfig | None = None,
    ) -> ReviewBatchResult:
        reviewer = self._reviewers.get(domain)
        if not reviewer:
            return ReviewBatchResult(ok=False, error=f"Unknown domain: {domain}")

        async with self._lock:
            if domain in self._running:
                return ReviewBatchResult(ok=False, error=f"{domain} already running")
            self._running.add(domain)

        cfg = config or AggressivenessConfig()
        try:
            result = await reviewer.run_one_batch(
                batch_size=batch_size,
                config=cfg,
                llm_client=self._llm_client,
            )
            logger.info(
                "autopilot[%s] batch done: +%d approved, +%d rejected, %d remaining",
                domain, result.approved_in_batch, result.rejected_in_batch, result.remaining,
            )
            return result
        except Exception as exc:
            logger.exception("autopilot[%s] batch failed", domain)
            return ReviewBatchResult(ok=False, error=str(exc))
        finally:
            async with self._lock:
                self._running.discard(domain)

    async def run_all(
        self,
        *,
        batch_size: int = 10,
        config: AggressivenessConfig | None = None,
    ) -> dict[str, ReviewBatchResult]:
        cfg = config or AggressivenessConfig()
        tasks = {
            domain: self.run_domain(domain, batch_size=batch_size, config=cfg)
            for domain in self._reviewers
        }
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
        results: dict[str, ReviewBatchResult] = {}
        for domain, result in zip(tasks.keys(), gathered, strict=True):
            if isinstance(result, Exception):
                results[domain] = ReviewBatchResult(ok=False, error=str(result))
            else:
                results[domain] = result
        return results

    def is_running(self, domain: str | None = None) -> bool:
        if domain:
            return domain in self._running
        return len(self._running) > 0
