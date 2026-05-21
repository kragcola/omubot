#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEMANTIC_THRESHOLDS: tuple[int, ...] = (2, 4, 8, 12, 24, 60, 100)


@dataclass(slots=True)
class CheckResult:
    failures: int = 0
    warnings: int = 0

    def ok(self, message: str) -> None:
        print(f"[ok]   {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"[warn] {message}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"[fail] {message}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke check slang semantic review acceptance.")
    parser.add_argument("--base-url", default=os.environ.get("OMUBOT_ADMIN_BASE_URL", "http://localhost:8081"))
    parser.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", "admin"))
    parser.add_argument("--service", default="bot", help="docker compose service name for log checks")
    parser.add_argument("--since-minutes", type=int, default=15, help="look back window for docker logs")
    parser.add_argument("--timeout-seconds", type=float, default=60.0, help="Admin API request timeout")
    parser.add_argument("--page-size", type=int, default=100, help="API page size for terms/pending scans")
    parser.add_argument("--max-pages", type=int, default=10, help="Max pages to scan when collecting terms")
    parser.add_argument("--group-id", default="", help="Optional group id to narrow term/pending checks")
    parser.add_argument("--skip-logs", action="store_true", help="Skip docker log checks")
    parser.add_argument("--strict-logs", action="store_true", help="Fail when expected log lines are missing")
    parser.add_argument(
        "--no-seed-demo-pending",
        action="store_false",
        dest="seed_demo_pending",
        help="Do not auto-seed a temporary pending candidate when no eligible one exists.",
    )
    parser.add_argument(
        "--require-semantic-reviewed",
        type=int,
        default=1,
        help="Minimum semantic_reviewed count expected after the forced review run",
    )
    parser.set_defaults(seed_demo_pending=True)
    return parser.parse_args()


def _client(base_url: str, token: str, timeout_s: float) -> httpx.Client:
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=max(5.0, timeout_s), follow_redirects=True)
    resp = client.post("/api/admin/login", json={"token": token})
    if resp.status_code >= 400:
        raise RuntimeError(f"admin login failed: HTTP {resp.status_code} {resp.text[:200]}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"admin login returned invalid JSON: {exc}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"admin login rejected: {payload}")
    return client


def _get_json(client: httpx.Client, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = client.get(path, params=params)
    if resp.status_code >= 400:
        raise RuntimeError(f"GET {path} failed: HTTP {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"GET {path} returned non-object JSON")
    return payload


def _post_json(
    client: httpx.Client,
    path: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resp = client.post(path, json=body or {})
    if resp.status_code >= 400:
        raise RuntimeError(f"POST {path} failed: HTTP {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"POST {path} returned non-object JSON")
    return payload


def _largest_threshold(count: int, last_count: int) -> int | None:
    threshold = 0
    for candidate in SEMANTIC_THRESHOLDS:
        if count >= candidate:
            threshold = candidate
    if threshold <= 0 or threshold <= last_count:
        return None
    return threshold


def _iter_terms(
    client: httpx.Client,
    *,
    params: dict[str, Any],
    page_size: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        payload = _get_json(
            client,
            "/api/admin/slang/terms",
            params={**params, "page": page, "page_size": page_size},
        )
        batch = payload.get("terms") or []
        if not isinstance(batch, list):
            raise RuntimeError("terms payload missing list")
        for item in batch:
            if isinstance(item, dict):
                items.append(item)
        if len(batch) < page_size:
            break
    return items


def _iter_pending(client: httpx.Client, *, group_id: str, page_size: int) -> list[dict[str, Any]]:
    payload = _get_json(
        client,
        "/api/admin/slang/pending",
        params={"group_id": group_id, "page_size": page_size},
    )
    batch = payload.get("pending") or []
    if not isinstance(batch, list):
        raise RuntimeError("pending payload missing list")
    return [item for item in batch if isinstance(item, dict)]


def _semantic_meta_flags(meta: dict[str, Any] | None) -> list[str]:
    if not isinstance(meta, dict):
        return []
    keys = [
        "semantic_review",
        "semantic_inference_complete",
        "semantic_no_info",
        "semantic_failed",
        "semantic_candidate_confirmed",
        "semantic_auto_approved",
        "semantic_rejected",
        "semantic_context_meaning",
        "semantic_literal_meaning",
        "semantic_is_similar",
        "semantic_compare_reason",
        "last_semantic_inference_count",
    ]
    return [key for key in keys if key in meta]


def _docker_logs(service: str, since_minutes: int) -> str:
    if shutil.which("docker") is None:
        raise RuntimeError("docker is not available")

    commands = [
        ["docker", "compose", "logs", service, "--since", f"{since_minutes}m", "--no-color"],
        ["docker-compose", "logs", service, "--since", f"{since_minutes}m", "--no-color"],
    ]
    last_error = ""
    for cmd in commands:
        if shutil.which(cmd[0]) is None:
            continue
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            return proc.stdout
        last_error = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
    raise RuntimeError(last_error or "failed to collect docker logs")


def _find_latest_daily_review_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    daily_runs = [run for run in runs if isinstance(run, dict) and run.get("meta", {}).get("kind") == "daily_ai_review"]
    if not daily_runs:
        return None
    daily_runs.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return daily_runs[0]


def _collect_semantic_terms(terms: list[dict[str, Any]], *, group_id: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for term in terms:
        if group_id and str(term.get("group_id") or "") != group_id:
            continue
        meta = term.get("meta") if isinstance(term.get("meta"), dict) else {}
        flags = _semantic_meta_flags(meta)
        if flags:
            matches.append({"term": term, "flags": flags})
    return matches


def _collect_semantic_pending(pending: list[dict[str, Any]], *, group_id: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in pending:
        if group_id and str(item.get("group_id") or "") != group_id:
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        flags = _semantic_meta_flags(meta)
        if flags:
            matches.append({"pending": item, "flags": flags})
    return matches


def _collect_due_pending(pending: list[dict[str, Any]], *, group_id: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in pending:
        if group_id and str(item.get("group_id") or "") != group_id:
            continue
        count = int(item.get("count") or 0)
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        last_count = int(meta.get("last_semantic_inference_count") or 0)
        threshold = _largest_threshold(count, last_count)
        if threshold is not None:
            matches.append({"pending": item, "threshold": threshold})
    return matches


def _resolve_review_group(
    *,
    requested_group_id: str,
    settings: dict[str, Any],
    due_pending: list[dict[str, Any]],
    available_groups: list[str],
) -> str:
    if requested_group_id:
        return requested_group_id
    if due_pending:
        return str(due_pending[0]["pending"].get("group_id") or "100")
    if available_groups:
        return available_groups[0]
    allowlist = [str(item) for item in settings.get("group_allowlist") or []]
    if allowlist:
        return allowlist[0]
    return "100"


def _make_demo_message_seed(group_id: str) -> dict[str, str]:
    token = secrets.token_hex(4)
    message_id = 900_000_000 + (int(token, 16) % 100_000_000)
    return {
        "group_id": group_id,
        "message_text": f"烟雾复核消息{token} 这是临时群聊测试消息",
        "message_id": str(message_id),
    }


def _count_live_user_messages(client: httpx.Client, group_id: str) -> int:
    payload = _get_json(client, f"/api/admin/groups/{group_id}/messages", params={"limit": 20})
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        return 0
    count = 0
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "") == "user" and str(item.get("content_text") or "").strip():
            count += 1
    return count


def _seed_demo_message(client: httpx.Client, seed: dict[str, str]) -> None:
    payload = _post_json(
        client,
        "/api/admin/slang/debug/message/seed",
        body={
            "group_id": seed["group_id"],
            "role": "user",
            "speaker": "SmokeUser(999000)",
            "content_text": seed["message_text"],
            "message_id": seed["message_id"],
        },
    )
    if not payload.get("ok"):
        raise RuntimeError(f"seed debug message rejected: {payload}")


def _cleanup_demo_message(client: httpx.Client, seed: dict[str, str]) -> None:
    payload = _post_json(
        client,
        "/api/admin/slang/debug/message/delete",
        body={
            "group_id": seed["group_id"],
            "content_text": seed["message_text"],
            "message_id": seed.get("message_id", ""),
        },
    )
    if not payload.get("ok"):
        raise RuntimeError(f"delete debug message rejected: {payload}")


def _seed_demo_pending_api(client: httpx.Client, group_id: str) -> dict[str, str]:
    token = secrets.token_hex(4)
    term = f"烟雾复核{token}"
    pending_id = f"pending_smoke_{token}"
    aliases = [f"{term}别名"]
    message_text = f"{term} 这是临时群聊测试消息"
    message_id = 900_000_000 + (int(token, 16) % 100_000_000)
    payload = _post_json(
        client,
        "/api/admin/slang/debug/pending/seed",
        body={
            "pending_id": pending_id,
            "group_id": group_id,
            "term": term,
            "meaning": "自动化烟雾测试样本",
            "aliases": aliases,
            "count": SEMANTIC_THRESHOLDS[-1],
            "confidence": 0.99,
            "evidence": f"{term} 这是临时烟雾测试样本",
            "reason": "semantic_smoke_seed",
            "repeat_policy": "understand_only",
            "meta": {
                "smoke_seed": True,
                "smoke_seed_term": term,
            },
        },
    )
    if not payload.get("ok"):
        raise RuntimeError(f"seed debug pending rejected: {payload}")
    return {
        "term": term,
        "term_key": payload["term_key"],
        "pending_id": payload["pending_id"],
        "group_id": group_id,
        "count": str(payload.get("count") or SEMANTIC_THRESHOLDS[-1]),
        "message_text": message_text,
        "message_id": str(message_id),
    }


def _cleanup_demo_seed_api(client: httpx.Client, seed: dict[str, str]) -> None:
    payload = _post_json(
        client,
        "/api/admin/slang/debug/pending/delete",
        body={
            "pending_id": seed["pending_id"],
            "group_id": seed["group_id"],
            "term": seed["term"],
        },
    )
    if not payload.get("ok"):
        raise RuntimeError(f"delete debug pending rejected: {payload}")


def _cleanup_all_demo_seeds_api(client: httpx.Client, page_size: int) -> int:
    groups_payload = _get_json(client, "/api/admin/slang/groups")
    groups = [str(item) for item in groups_payload.get("groups") or [] if str(item).strip()]
    seeds: list[dict[str, Any]] = []
    for group_id in groups:
        try:
            pending = _iter_pending(client, group_id=group_id, page_size=page_size)
        except Exception:
            continue
        seeds.extend(
            item
            for item in pending
            if str(item.get("pending_id") or "").startswith("pending_smoke_")
        )
    for item in seeds:
        _cleanup_demo_seed_api(
            client,
            {
                "pending_id": str(item.get("pending_id") or ""),
                "group_id": str(item.get("group_id") or ""),
                "term": str(item.get("term") or ""),
            },
        )
    return len(seeds)


def _run_forced_review(client: httpx.Client, group_id: str) -> dict[str, Any]:
    resp = client.post(
        "/api/admin/slang/review/run",
        json={"group_id": group_id, "force": True, "review_candidates": True},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"POST /api/admin/slang/review/run failed: HTTP {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("forced review returned non-object JSON")
    if not payload.get("ok"):
        raise RuntimeError(f"forced review rejected: {payload}")
    return payload


def main() -> int:
    args = _parse_args()
    result = CheckResult()

    try:
        client = _client(args.base_url, args.token, args.timeout_seconds)
        seed: dict[str, str] | None = None
        message_seed: dict[str, str] | None = None
        message_seeded = False
        try:
            result.ok(f"admin API authenticated at {args.base_url}")

            settings = _get_json(client, "/api/admin/slang/settings").get("settings") or {}
            if not isinstance(settings, dict):
                raise RuntimeError("settings payload missing object")
            review_enabled = bool(settings.get("daily_ai_review_enabled"))
            review_times = settings.get("daily_ai_review_times") or []
            if review_enabled:
                result.ok(f"daily AI review enabled, times={review_times}")
            else:
                result.fail("daily AI review is disabled")

            allowlist = [str(item) for item in settings.get("group_allowlist") or []]
            if args.group_id and allowlist and args.group_id not in allowlist:
                result.fail(f"group {args.group_id} is not in the slang allowlist")

            cleaned_demo_seeds = _cleanup_all_demo_seeds_api(client, args.page_size)
            if cleaned_demo_seeds:
                result.ok(f"cleaned leftover demo seeds | count={cleaned_demo_seeds}")

            groups_payload = _get_json(client, "/api/admin/slang/groups")
            available_groups = [str(item) for item in groups_payload.get("groups") or [] if str(item).strip()]
            target_groups = [args.group_id] if args.group_id else available_groups
            if not target_groups:
                target_groups = [str(item) for item in allowlist] or ["100"]
            pending: list[dict[str, Any]] = []
            for group_id in target_groups:
                try:
                    pending.extend(_iter_pending(client, group_id=group_id, page_size=args.page_size))
                except Exception as exc:
                    result.warn(f"skipping group {group_id} during pending scan: {exc}")
            due_pending = _collect_due_pending(pending, group_id=args.group_id)
            review_group_id = _resolve_review_group(
                requested_group_id=args.group_id,
                settings=settings,
                due_pending=due_pending,
                available_groups=available_groups,
            )
            if due_pending:
                first = due_pending[0]
                pending_item = first["pending"]
                result.ok(
                    "eligible pending found "
                    f"{pending_item.get('pending_id')} term={pending_item.get('term')} "
                    f"count={pending_item.get('count')} threshold={first['threshold']}"
                )
            elif args.seed_demo_pending:
                seed = _seed_demo_pending_api(client, review_group_id)
                result.ok(
                    "seeded demo pending "
                    f"{seed['pending_id']} term={seed['term']} group={seed['group_id']} count={seed['count']}"
                )
            else:
                result.warn("no eligible pending candidates found at the current semantic threshold")

            live_user_messages = _count_live_user_messages(client, review_group_id)
            if args.seed_demo_pending and live_user_messages == 0:
                message_seed = _make_demo_message_seed(review_group_id)
                _seed_demo_message(client, message_seed)
                message_seeded = True
                result.ok(
                    "seeded demo live message "
                    f"group={message_seed['group_id']} message_id={message_seed['message_id']}"
                )

            review_result = _run_forced_review(client, review_group_id)
            run_id = str(review_result.get("run_id") or "")
            result.ok(f"forced daily review started | run={run_id} group={review_group_id}")

            runs_payload = _get_json(client, "/api/admin/slang/extract/runs", params={"limit": 20})
            runs = runs_payload.get("runs") or []
            if not isinstance(runs, list):
                raise RuntimeError("runs payload missing list")
            latest_run = next(
                (item for item in runs if isinstance(item, dict) and str(item.get("run_id") or "") == run_id),
                None,
            )
            if latest_run is None:
                latest_run = _find_latest_daily_review_run([item for item in runs if isinstance(item, dict)])
            if latest_run is None:
                result.fail("no daily_ai_review run found in recent runs")
                semantic_reviewed = 0
                semantic_approved = 0
                semantic_rejected = 0
                semantic_kept = 0
                semantic_no_info = 0
                semantic_failed = 0
            else:
                meta = latest_run.get("meta") if isinstance(latest_run.get("meta"), dict) else {}
                semantic_reviewed = int(meta.get("semantic_reviewed") or 0)
                semantic_approved = int(meta.get("semantic_approved") or 0)
                semantic_rejected = int(meta.get("semantic_rejected") or 0)
                semantic_kept = int(meta.get("semantic_kept") or 0)
                semantic_no_info = int(meta.get("semantic_no_info") or 0)
                semantic_failed = int(meta.get("semantic_failed") or 0)
                result.ok(
                    "daily review run "
                    f"{latest_run.get('run_id')} status={latest_run.get('status')} "
                    f"semantic_reviewed={semantic_reviewed} semantic_approved={semantic_approved} "
                    f"semantic_rejected={semantic_rejected} semantic_kept={semantic_kept} "
                    f"semantic_no_info={semantic_no_info} semantic_failed={semantic_failed}"
                )
                if str(latest_run.get("status") or "") != "success":
                    result.fail(f"latest daily review run is not success: {latest_run.get('status')}")

            required_semantic_reviewed = max(args.require_semantic_reviewed, 1)
            if latest_run is not None and semantic_reviewed < required_semantic_reviewed:
                result.fail(
                    f"semantic_reviewed={semantic_reviewed} < required {required_semantic_reviewed}"
                )

            all_terms = _iter_terms(
                client,
                params={"group_id": review_group_id} if review_group_id else {},
                page_size=args.page_size,
                max_pages=args.max_pages,
            )
            pending_after = _iter_pending(client, group_id=review_group_id, page_size=args.page_size)
            semantic_terms = _collect_semantic_terms(all_terms, group_id=review_group_id)
            semantic_pending = _collect_semantic_pending(pending_after, group_id=review_group_id)
            if semantic_terms:
                sample = semantic_terms[0]
                term = sample["term"]
                result.ok(
                    "semantic meta found on term "
                    f"{term.get('term_id')} term={term.get('term')} status={term.get('status')} "
                    f"flags={','.join(sample['flags'])}"
                )
            elif semantic_pending:
                sample = semantic_pending[0]
                item = sample["pending"]
                result.ok(
                    "semantic meta found on pending "
                    f"{item.get('pending_id')} term={item.get('term')} count={item.get('count')} "
                    f"flags={','.join(sample['flags'])}"
                )
            else:
                result.warn("no semantic_* meta found in scanned term or pending pages")

            if not args.skip_logs:
                try:
                    logs = _docker_logs(args.service, args.since_minutes)
                    start_hit = (
                        "daily slang AI review start" in logs
                        or "slang review start | task=slang_review" in logs
                    )
                    finish_hit = "slang review finished | task=slang_review" in logs
                    if start_hit:
                        result.ok("docker logs include slang review start | task=slang_review")
                    elif args.strict_logs:
                        result.fail("docker logs do not include slang review start | task=slang_review")
                    else:
                        result.warn("docker logs do not include slang review start | task=slang_review")
                    if finish_hit:
                        result.ok("docker logs include slang review finished | task=slang_review")
                    elif args.strict_logs:
                        result.fail("docker logs do not include slang review finished | task=slang_review")
                    else:
                        result.warn("docker logs do not include slang review finished | task=slang_review")
                    semantic_hits = re.findall(r"semantic_reviewed=(\d+)", logs)
                    if semantic_hits:
                        value = int(semantic_hits[-1])
                        result.ok(f"docker logs report semantic_reviewed={value}")
                    elif args.strict_logs:
                        result.fail("docker logs do not include semantic_reviewed count")
                    else:
                        result.warn("docker logs do not include semantic_reviewed count")
                except Exception as exc:
                    if args.strict_logs:
                        result.fail(f"docker log check failed: {exc}")
                    else:
                        result.warn(f"docker log check failed: {exc}")

        finally:
            if message_seeded and message_seed is not None:
                try:
                    _cleanup_demo_message(client, message_seed)
                    result.ok(f"cleaned up demo live message {message_seed['message_id']}")
                except Exception as exc:
                    result.fail(f"failed to clean up demo live message: {exc}")
            if seed is not None:
                try:
                    _cleanup_demo_seed_api(client, seed)
                    result.ok(f"cleaned up demo pending {seed['pending_id']}")
                except Exception as exc:
                    result.fail(f"failed to clean up demo pending: {exc}")
            client.close()

    except Exception as exc:
        result.fail(str(exc))

    print()
    print(f"[smoke] Summary: {result.failures} fail, {result.warnings} warn")
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
