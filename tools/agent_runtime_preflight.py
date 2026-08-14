#!/usr/bin/env python3
"""Read-only preflight for a proposed Agent Runtime v2 activation.

The command parses only the ``agent_runtime`` TOML section, checks the five
explicit SQLite sources and backup digests, and validates the digest-pinned
rollout manifest. It never opens writable Runtime stores, starts a worker, or
prints credentials, source paths, targets, or evidence references.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.agent_runtime.activation import ProductionActivationProfileV1  # noqa: E402
from services.agent_runtime.rollout_attestation import (  # noqa: E402
    ProfileBoundRolloutAttestorV1,
)
from services.agent_runtime.rollout_readiness import (  # noqa: E402
    ACTIVATION_GATE_NAMES,
    ROLLBACK_GATE_NAMES,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Agent Runtime v2 source and manifest preflight. "
            "It never starts a worker or changes local state."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="TOML file containing the proposed agent_runtime section",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root used to resolve storage-relative paths",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent for the secret-safe report (default: 2)",
    )
    return parser


def _settings(value: Mapping[str, Any]) -> SimpleNamespace:
    """Adapt the TOML tree to the profile's attribute-oriented input contract."""

    def convert(item: Any) -> Any:
        if isinstance(item, Mapping):
            return SimpleNamespace(**{str(key): convert(child) for key, child in item.items()})
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return tuple(convert(child) for child in item)
        return item

    return convert(value)


def _source_summary(raw: object) -> dict[str, dict[str, str]]:
    reports = raw.get("sources", {}) if isinstance(raw, Mapping) else {}
    result: dict[str, dict[str, str]] = {}
    for name in ("runtime", "memory", "worldbook", "operator", "invocation"):
        report = reports.get(name, {}) if isinstance(reports, Mapping) else {}
        status = report.get("status") if isinstance(report, Mapping) else None
        reason = report.get("reason") if isinstance(report, Mapping) else None
        result[name] = {
            "status": status if isinstance(status, str) else "not_ready",
            "reason": reason if isinstance(reason, str) else "preflight_unavailable",
        }
    return result


async def _gate_summary(
    *,
    attestor: ProfileBoundRolloutAttestorV1,
    names: tuple[str, ...],
    activation: bool,
) -> dict[str, Any]:
    try:
        raw = (
            await attestor.activation_attestation()
            if activation
            else await attestor.rollback_attestation()
        )
    except Exception:
        return {
            "status": "not_ready",
            "blocking_gates": list(names),
            "gates": {
                name: {
                    "status": "not_assessed",
                    "reason": "attestation_invalid",
                    "evidence_at": None,
                }
                for name in names
            },
        }

    gates: dict[str, dict[str, Any]] = {}
    for name in names:
        gate = raw.get(name, {}) if isinstance(raw, Mapping) else {}
        gates[name] = {
            "status": gate.get("status") if isinstance(gate.get("status"), str) else "not_assessed",
            "reason": gate.get("reason") if isinstance(gate.get("reason"), str) else "attestation_invalid",
            "evidence_at": gate.get("evidence_at") if isinstance(gate.get("evidence_at"), str) else None,
        }
    blocked = [name for name, gate in gates.items() if gate["status"] != "ready"]
    return {
        "status": "ready" if not blocked else "not_ready",
        "blocking_gates": blocked,
        "gates": gates,
    }


async def run_preflight(*, config_path: Path, repo_root: Path) -> dict[str, Any]:
    """Return a secret-safe readiness report without mutating activation state."""

    try:
        raw_config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {"status": "not_ready", "reason": "config_unavailable"}
    raw_settings = raw_config.get("agent_runtime")
    if not isinstance(raw_settings, Mapping) or not bool(raw_settings.get("enabled", False)):
        return {"status": "not_ready", "reason": "agent_runtime_disabled"}
    try:
        profile = ProductionActivationProfileV1.from_settings(
            _settings(raw_settings),
            repo_root=repo_root,
        )
    except (TypeError, ValueError):
        return {"status": "not_ready", "reason": "profile_invalid"}
    if profile is None:
        return {"status": "not_ready", "reason": "agent_runtime_disabled"}

    source_report = await profile.preflight()
    try:
        attestor = ProfileBoundRolloutAttestorV1(profile)
    except (TypeError, ValueError):
        return {
            "status": "not_ready",
            "sources": _source_summary(source_report),
            "activation": _gate_summary_unavailable(ACTIVATION_GATE_NAMES),
            "rollback": _gate_summary_unavailable(ROLLBACK_GATE_NAMES),
        }
    activation = await _gate_summary(
        attestor=attestor,
        names=ACTIVATION_GATE_NAMES,
        activation=True,
    )
    rollback = await _gate_summary(
        attestor=attestor,
        names=ROLLBACK_GATE_NAMES,
        activation=False,
    )
    sources = _source_summary(source_report)
    ready = (
        source_report.get("status") == "ready"
        and activation["status"] == "ready"
        and rollback["status"] == "ready"
    )
    return {
        "status": "ready" if ready else "not_ready",
        "sources": sources,
        "activation": activation,
        "rollback": rollback,
    }


def _gate_summary_unavailable(names: tuple[str, ...]) -> dict[str, Any]:
    return {
        "status": "not_ready",
        "blocking_gates": list(names),
        "gates": {
            name: {
                "status": "not_assessed",
                "reason": "attestation_invalid",
                "evidence_at": None,
            }
            for name in names
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(
        run_preflight(
            config_path=args.config.resolve(),
            repo_root=args.repo_root.resolve(),
        )
    )
    print(json.dumps(report, ensure_ascii=True, indent=args.indent, sort_keys=True))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
