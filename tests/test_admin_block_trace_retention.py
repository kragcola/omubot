"""Admin block-trace pruning must use the bounded owner retention path."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.block_trace import create_block_trace_router


class _RecordingBlockTraceStore:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, bool]] = []

    async def prune(self, *, keep_days: int = 7) -> int:
        del keep_days
        raise AssertionError("legacy unbounded prune path must not be called")

    async def apply_retention(
        self,
        *,
        keep_days: int,
        batch_size: int,
        dry_run: bool,
    ) -> dict[str, object]:
        self.calls.append((keep_days, batch_size, dry_run))
        return {
            "candidate_count": batch_size,
            "deleted_count": 0 if dry_run else batch_size,
            "details": {"prompt_block_traces": batch_size},
        }


def test_admin_prune_delegates_to_bounded_owner_retention() -> None:
    store = _RecordingBlockTraceStore()
    app = FastAPI()
    app.include_router(
        create_block_trace_router(
            ctx=SimpleNamespace(block_trace_store=store),
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/block-trace/prune",
            params={"keep_days": 14, "batch_size": 3, "dry_run": False},
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "applied",
        "dry_run": False,
        "candidate_count": 3,
        "deleted": 3,
        "details": {"prompt_block_traces": 3},
    }
    assert store.calls == [(14, 3, False)]


def test_admin_prune_supports_bounded_dry_run() -> None:
    store = _RecordingBlockTraceStore()
    app = FastAPI()
    app.include_router(
        create_block_trace_router(
            ctx=SimpleNamespace(block_trace_store=store),
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/block-trace/prune",
            params={"keep_days": 30, "batch_size": 2, "dry_run": True},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "dry_run"
    assert response.json()["deleted"] == 0
    assert store.calls == [(30, 2, True)]
