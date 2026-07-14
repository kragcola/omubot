"""Deterministic fixture runner for TopicBlockTracker behavior contracts."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from services.group.topic_block import TopicBlockTracker


def _pool_of(tracker: TopicBlockTracker, group_id: str, block_id: str) -> str:
    in_active = block_id in tracker._blocks.get(group_id, {})
    in_reservoir = block_id in tracker._reservoir.get(group_id, {})
    if in_active and not in_reservoir:
        return "active"
    if in_reservoir and not in_active:
        return "reservoir"
    return "both" if in_active else "missing"


def _activity_baseline(tracker: TopicBlockTracker, group_id: str, now: float) -> dict[str, float]:
    baseline = {
        block_id: block.activity
        for block_id, block in tracker._reservoir.get(group_id, {}).items()
    }
    for block_id, block in tracker._blocks.get(group_id, {}).items():
        dt = max(0.0, now - block.last_access)
        baseline[block_id] = block.activity * math.pow(
            tracker._decay_a,
            tracker._decay_lambda * dt,
        )
    return baseline


def _invariant_violations(tracker: TopicBlockTracker, group_id: str) -> list[str]:
    violations: list[str] = []
    active = tracker._blocks.get(group_id, {})
    reservoir = tracker._reservoir.get(group_id, {})
    duplicate_ids = set(active).intersection(reservoir)
    if duplicate_ids:
        violations.append("block_in_both_pools")
    if len(active) > tracker._max_blocks:
        violations.append("active_capacity_exceeded")
    message_index = tracker._msg_to_block.get(group_id, {})
    for message_id, block_id in message_index.items():
        block = active.get(block_id) or reservoir.get(block_id)
        if block is None:
            violations.append(f"index_missing_block:{message_id}")
        elif message_id not in block.message_ids:
            violations.append(f"index_missing_message:{message_id}")
    message_owners: dict[int, str] = {}
    for block_id, block in {**reservoir, **active}.items():
        for message_id in block.message_ids:
            previous_owner = message_owners.setdefault(message_id, block_id)
            if previous_owner != block_id:
                violations.append(f"message_in_multiple_blocks:{message_id}")
            if message_index.get(message_id) != block_id:
                violations.append(f"block_message_missing_index:{message_id}")
    return violations


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    tracker = TopicBlockTracker()
    tracker.configure(**case.get("config", {}))
    group_id = "fixture"
    event_results: list[dict[str, Any]] = []
    case_violations: list[str] = []
    last_now = 0.0

    for event in case.get("events", []):
        last_now = float(event["time_offset_s"])
        activity_before = _activity_baseline(tracker, group_id, last_now)
        reply_to = event.get("reply_to") or {}
        block = tracker.observe(
            group_id,
            message_id=int(event["message_id"]),
            speaker=str(event["actor"]),
            text=str(event["synthetic_text"]),
            reply_to_sender_id=str(reply_to.get("actor", "")),
            reply_to_message_id=reply_to.get("message_id"),
            reply_to_self=bool(event.get("reply_to_self", False)),
            at_targets=tuple(str(item) for item in event.get("mentions", [])),
            at_self=bool(event.get("at_self", False)),
            now=last_now,
        )
        pool = _pool_of(tracker, group_id, block.block_id)
        activity_delta = round(block.activity - activity_before.get(block.block_id, 0.0), 9)
        indexed_reachable = (
            tracker._msg_to_block.get(group_id, {}).get(int(event["message_id"])) == block.block_id
            and tracker.pick_block_by_id(group_id, block.block_id) is block
        )
        expected = event["expect"]
        matches = {
            "block": block.block_id == expected["block"],
            "pool": pool == expected["pool"],
            "activity_delta": abs(activity_delta - float(expected["activity_delta"])) < 1e-6,
            "indexed_reachable": indexed_reachable is bool(expected["indexed_reachable"]),
        }
        violations = _invariant_violations(tracker, group_id)
        case_violations.extend(f"seq={event['seq']}:{item}" for item in violations)
        event_results.append(
            {
                "seq": int(event["seq"]),
                "block": block.block_id,
                "pool": pool,
                "activity_delta": activity_delta,
                "indexed_reachable": indexed_reachable,
                "matches": matches,
                "passed": all(matches.values()) and not violations,
            }
        )

    expected_final = case.get("expect_final", {})
    actual_final: dict[str, Any] = {
        "active": sorted(tracker._blocks.get(group_id, {})),
        "reservoir": sorted(tracker._reservoir.get(group_id, {})),
    }
    if "anchor" in expected_final:
        anchor = tracker.pick_anchor_block(group_id, now=last_now)
        actual_final["anchor"] = anchor.block_id if anchor is not None else None
    final_matches = all(actual_final.get(key) == value for key, value in expected_final.items())
    return {
        "case_id": str(case["case_id"]),
        "events": event_results,
        "final": actual_final,
        "invariant_violations": case_violations,
        "passed": all(item["passed"] for item in event_results) and final_matches and not case_violations,
    }


def run_replay_fixture(path: Path) -> dict[str, Any]:
    """Run one replay fixture and return a machine-readable report."""
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported topic-block replay schema")
    if payload.get("provenance") != "synthetic":
        raise ValueError("topic-block replay fixtures must be synthetic")
    cases = [_run_case(case) for case in payload.get("cases", [])]
    return {
        "schema_version": int(payload["schema_version"]),
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "case_count": len(cases),
        "event_count": sum(len(case["events"]) for case in cases),
        "cases": cases,
        "passed": bool(cases) and all(case["passed"] for case in cases),
    }
