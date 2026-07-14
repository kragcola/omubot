"""State owner for Admin learning extract-all runs."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from kernel.background_tasks import (
    BackgroundTaskSupervisor,
    ShutdownPolicy,
    TaskKind,
    TaskSpec,
)

ExtractRunnerFactory = Callable[[], Awaitable[Any]]
_TZ_SHANGHAI = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class ExtractRunParams:
    limit: int = 80
    max_batches: int = 1
    batch_size: int = 50
    timeout_seconds: float = 120.0


class LearningExtractCoordinator:
    """Own run registration, execution, snapshots, and concurrency control."""

    def __init__(
        self,
        *,
        nouns: tuple[str, ...] = ("slang", "style", "consolidator"),
        run_limit: int = 20,
        task_supervisor: BackgroundTaskSupervisor | None = None,
    ) -> None:
        self.lock = asyncio.Lock()
        self.runs: dict[str, dict[str, Any]] = {}
        self._nouns = nouns
        self._run_limit = run_limit
        self._task_supervisor = task_supervisor
        self._active_run_id: str | None = None
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._stopping = False
        self._stopped = False
        self._stop_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._stopping or self._stopped:
            raise RuntimeError("learning extract coordinator is stopped")

    async def stop(self) -> None:
        if self._stopped:
            return
        if self._stop_task is None:
            self._stopping = True
            self._stop_task = asyncio.create_task(
                self._stop_all(),
                name="learning-extract:stop",
            )
        await asyncio.shield(self._stop_task)

    async def run(
        self,
        *,
        group_id: str,
        params: ExtractRunParams,
        runners: Mapping[str, ExtractRunnerFactory],
        wait: bool = True,
    ) -> dict[str, Any]:
        return await self._run(
            group_id=group_id,
            params=params,
            runners=runners,
            nouns=self._nouns,
            wait=wait,
        )

    async def run_noun(
        self,
        *,
        noun: str,
        group_id: str,
        params: ExtractRunParams,
        runner: ExtractRunnerFactory,
        wait: bool = True,
    ) -> dict[str, Any]:
        if noun not in self._nouns:
            raise ValueError(f"unknown learning extract noun: {noun}")
        return await self._run(
            group_id=group_id,
            params=params,
            runners={noun: runner},
            nouns=(noun,),
            wait=wait,
        )

    async def _run(
        self,
        *,
        group_id: str,
        params: ExtractRunParams,
        runners: Mapping[str, ExtractRunnerFactory],
        nouns: tuple[str, ...],
        wait: bool,
    ) -> dict[str, Any]:
        if self._stopping or self._stopped:
            return {
                **self._not_found(""),
                "error": "stopped",
                "status": "stopped",
            }
        if self._active_run_id or self.lock.locked():
            payload = self.status(self._active_run_id or "")
            payload.update({"ok": False, "error": "already_running"})
            return payload

        self._prune_runs()
        run = self._create_run(group_id=group_id, params=params, nouns=nouns)
        run_id = str(run["run_id"])
        self._active_run_id = run_id

        async def execute_background() -> None:
            await self._execute(
                run_id,
                runners=runners,
                nouns=nouns,
                timeout_seconds=params.timeout_seconds,
            )

        if self._task_supervisor is not None:
            task = self._task_supervisor.spawn(
                TaskSpec(
                    name="learning.extract",
                    owner="admin.learning_extract",
                    kind=TaskKind.HEAVY,
                    shutdown=ShutdownPolicy.CANCEL,
                ),
                execute_background,
            )
        else:
            task = asyncio.create_task(
                execute_background(),
                name=f"learning-extract:{run_id}",
            )
        self._tasks[run_id] = task
        task.add_done_callback(lambda done, key=run_id: self._task_done(key, done))
        if not wait:
            return self._snapshot(run)
        await task
        return self.status(run_id)

    def status(self, run_id: str) -> dict[str, Any]:
        run = self.runs.get(str(run_id or ""))
        if run is None:
            return self._not_found(run_id)
        return self._snapshot(run)

    async def _execute(
        self,
        run_id: str,
        *,
        runners: Mapping[str, ExtractRunnerFactory],
        nouns: tuple[str, ...],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        run = self.runs.get(run_id)
        if run is None:
            return self._not_found(run_id)

        try:
            async with self.lock:
                self._update_run(run, status="running")
                results = await asyncio.gather(
                    *(
                        self._run_noun(
                            run,
                            noun,
                            runners.get(noun),
                            timeout_seconds=timeout_seconds,
                        )
                        for noun in nouns
                    ),
                    return_exceptions=False,
                )
                run["results"] = dict(results)
                self._update_run(
                    run,
                    status=self._final_status(run["results"]),
                    finished=True,
                )
        except asyncio.CancelledError:
            self._update_run(
                run,
                status="failed",
                error="cancelled",
                finished=True,
            )
            raise
        except Exception as exc:
            self._update_run(run, status="failed", error=str(exc), finished=True)
        finally:
            if self._active_run_id == run_id:
                self._active_run_id = None

        return self._snapshot(run)

    async def _run_noun(
        self,
        run: dict[str, Any],
        noun: str,
        runner: ExtractRunnerFactory | None,
        *,
        timeout_seconds: float,
    ) -> tuple[str, dict[str, Any]]:
        self._update_noun(run, noun, status="running")
        result = await self._run_with_timeout(
            noun,
            runner,
            timeout_seconds=timeout_seconds,
        )
        self._update_noun(
            run,
            noun,
            status=self._noun_status(result),
            result=result,
        )
        return noun, result

    @staticmethod
    async def _run_with_timeout(
        noun: str,
        runner: ExtractRunnerFactory | None,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if runner is None:
            return {
                "ok": False,
                "error": f"{noun} extractor not available",
                "noun": noun,
                "skipped": True,
            }
        try:
            result = await asyncio.wait_for(runner(), timeout=timeout_seconds)
            return LearningExtractCoordinator._normalize_result(noun, result)
        except TimeoutError:
            return {"ok": False, "error": "timeout", "noun": noun}
        except asyncio.CancelledError:
            return {"ok": False, "error": "cancelled", "noun": noun}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "noun": noun}

    def _create_run(
        self,
        *,
        group_id: str,
        params: ExtractRunParams,
        nouns: tuple[str, ...],
    ) -> dict[str, Any]:
        run_id = "learn_ext_" + secrets.token_hex(6)
        now = self._now_iso()
        run: dict[str, Any] = {
            "ok": True,
            "run_id": run_id,
            "status": "queued",
            "error": "",
            "started_at": now,
            "updated_at": now,
            "finished_at": "",
            "group_id": group_id,
            "params": {
                "limit": params.limit,
                "max_batches": params.max_batches,
                "batch_size": params.batch_size,
                "timeout_seconds": params.timeout_seconds,
            },
            "nouns": {
                noun: {
                    "status": "pending",
                    "result": None,
                    "error": "",
                    "updated_at": now,
                }
                for noun in nouns
            },
            "results": {},
        }
        self.runs[run_id] = run
        self._prune_runs()
        return run

    @staticmethod
    def _update_run(
        run: dict[str, Any],
        *,
        status: str,
        error: str = "",
        finished: bool = False,
    ) -> None:
        now = LearningExtractCoordinator._now_iso()
        run["status"] = status
        run["updated_at"] = now
        run["error"] = error
        if finished:
            run["finished_at"] = now

    @staticmethod
    def _update_noun(
        run: dict[str, Any],
        noun: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        now = LearningExtractCoordinator._now_iso()
        nouns = run.get("nouns")
        if not isinstance(nouns, dict):
            nouns = {}
            run["nouns"] = nouns
        noun_state = nouns.setdefault(noun, {})
        noun_state["status"] = status
        noun_state["updated_at"] = now
        if result is not None:
            noun_state["result"] = result
            noun_state["error"] = str(result.get("error") or "")
        run["updated_at"] = now

    @staticmethod
    def _not_found(run_id: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "not_found",
            "run_id": run_id,
            "status": "not_found",
            "started_at": "",
            "updated_at": "",
            "finished_at": "",
            "group_id": "",
            "params": {},
            "nouns": {},
            "results": {},
        }

    @staticmethod
    def _snapshot(run: dict[str, Any]) -> dict[str, Any]:
        raw_nouns = run.get("nouns")
        nouns = raw_nouns if isinstance(raw_nouns, dict) else {}
        raw_results = run.get("results")
        results = raw_results if isinstance(raw_results, dict) else {}
        return {
            "ok": bool(run.get("ok", True)),
            "run_id": str(run.get("run_id") or ""),
            "status": str(run.get("status") or ""),
            "error": str(run.get("error") or ""),
            "started_at": str(run.get("started_at") or ""),
            "updated_at": str(run.get("updated_at") or ""),
            "finished_at": str(run.get("finished_at") or ""),
            "group_id": str(run.get("group_id") or ""),
            "params": dict(run.get("params") or {}),
            "nouns": {
                str(noun): {
                    "status": str(state.get("status") or ""),
                    "result": LearningExtractCoordinator._copy_result(state.get("result")),
                    "error": str(state.get("error") or ""),
                    "updated_at": str(state.get("updated_at") or ""),
                }
                for noun, state in nouns.items()
                if isinstance(state, dict)
            },
            "results": {str(noun): LearningExtractCoordinator._copy_result(result) for noun, result in results.items()},
        }

    @staticmethod
    def _copy_result(result: Any) -> dict[str, Any] | None:
        if result is None:
            return None
        if isinstance(result, dict):
            return dict(result)
        return {"ok": True, "result": result}

    @staticmethod
    def _normalize_result(noun: str, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"ok": True, "noun": noun, "result": result}
        payload = dict(result)
        payload.setdefault("ok", True)
        payload.setdefault("noun", noun)
        return payload

    @staticmethod
    def _noun_status(result: dict[str, Any]) -> str:
        if result.get("skipped"):
            return "skipped"
        if result.get("error") == "timeout":
            return "timeout"
        if result.get("error") == "cancelled":
            return "cancelled"
        if result.get("ok") is False:
            return "failed"
        return "completed"

    @staticmethod
    def _final_status(results: Mapping[str, dict[str, Any]]) -> str:
        statuses = [LearningExtractCoordinator._noun_status(result) for result in results.values()]
        failed = {"failed", "timeout", "cancelled"}
        if statuses and all(status in failed for status in statuses):
            return "failed"
        if any(status in failed for status in statuses):
            return "partial_failed"
        return "completed"

    def _prune_runs(self) -> None:
        removable = [run_id for run_id in self.runs if run_id != self._active_run_id]
        while len(self.runs) > self._run_limit and removable:
            self.runs.pop(removable.pop(0), None)

    def _task_done(
        self,
        run_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        self._tasks.pop(run_id, None)
        if not task.cancelled():
            task.exception()

    async def _stop_all(self) -> None:
        try:
            tasks = tuple(task for task in self._tasks.values() if not task.done())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._tasks.clear()
            self._active_run_id = None
            self._stopped = True
            self._stopping = False

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(_TZ_SHANGHAI).isoformat(timespec="seconds")


async def run_coordinated_extract(
    ctx: Any,
    *,
    noun: str,
    params: ExtractRunParams,
    runner: ExtractRunnerFactory,
    group_id: str = "",
) -> dict[str, Any]:
    """Run one extractor through the application coordinator when available."""
    coordinator = getattr(ctx, "learning_extract_coordinator", None)
    if not isinstance(coordinator, LearningExtractCoordinator):
        return LearningExtractCoordinator._normalize_result(noun, await runner())

    payload = await coordinator.run_noun(
        noun=noun,
        group_id=group_id,
        params=params,
        runner=runner,
    )
    raw_result = payload.get("results", {}).get(noun)
    if not isinstance(raw_result, dict):
        return {
            "ok": False,
            "error": str(payload.get("error") or payload.get("status") or "extract failed"),
            "noun": noun,
            "coordinator_run_id": str(payload.get("run_id") or ""),
        }
    result = dict(raw_result)
    result["coordinator_run_id"] = str(payload.get("run_id") or "")
    return result
