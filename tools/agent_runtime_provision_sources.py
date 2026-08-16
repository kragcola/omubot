#!/usr/bin/env python3
"""Provision the explicit, fail-closed Agent Runtime v2 source set.

This command deliberately creates *new* contract stores.  Existing Omubot
databases are read only for the Worldbook witness and are never adopted as a
Runtime, Memory, Worldbook, operator, or invocation source.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_NAMES = ("runtime", "memory", "worldbook", "operator", "invocation")
_SOURCE_SCHEMAS = {
    "runtime": 2,
    "memory": 1,
    "worldbook": 1,
    "operator": 1,
    "invocation": 2,
}
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
_OPERATOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,95}$")
_WORKER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,95}$")
_DEFAULT_SCOPES = ("memory:read", "time:read")
_DEFAULT_TARGETS = ("network:web-search",)
_OPERATOR_SCOPES = (
    "memory:candidate:decide",
    "runtime:read",
    "runtime:tool:approve",
    "runtime:tool:reconcile",
)
_OPERATOR_GRANTS = (
    ("memory:candidate:decide", "memory:candidates"),
    ("runtime:read", "runtime:ledger"),
    ("runtime:tool:approve", "tool_call:canary"),
    ("runtime:tool:reconcile", "tool_call:canary"),
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_bytes(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any, *, mode: int | None = None) -> None:
    _write_bytes(path, _canonical_json(value) + b"\n", mode=mode)


def _safe_id(value: object, *, pattern: re.Pattern[str], field: str) -> str:
    clean = str(value or "").strip()
    if pattern.fullmatch(clean) is None:
        raise ValueError(f"{field} is invalid")
    return clean


def _copy_sqlite(source: Path, destination: Path) -> None:
    """Copy a closed SQLite source through the SQLite backup API."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    source_connection = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()


def _sqlite_facts(path: Path, *, expected_schema: int) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if user_version != expected_schema or quick_check != "ok":
            raise RuntimeError(
                f"SQLite source verification failed: {path.name} "
                f"schema={user_version} quick_check={quick_check}"
            )
    finally:
        connection.close()
    return {
        "schema_version": user_version,
        "quick_check": quick_check,
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _event_namespace(event_id: object) -> str:
    value = str(event_id or "").strip()
    return value.split(".", 1)[0] if "." in value else value


def _worldbook_witness(repo_root: Path) -> dict[str, Any]:
    """Read legacy Worldbook JSON without importing it into the new store."""

    root = repo_root / "storage" / "worldbook"
    if not root.is_dir():
        return {
            "status": "not_assessed",
            "reason": "legacy_worldbook_unavailable",
            "legacy_namespaces": {"commit": "", "life_state": ""},
        }

    errors: list[str] = []
    proposal_ids: set[str] = set()
    decision_ids: set[str] = set()
    decisions_by_proposal: dict[str, str] = {}
    commit_ids: set[str] = set()
    commits_by_proposal: dict[str, str] = {}
    commit_events: set[str] = set()
    digest = hashlib.sha256()

    def read_json(path: Path) -> Any:
        payload = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(payload)
        return json.loads(payload.decode("utf-8"))

    def load_directory(name: str, ids: set[str], *, key: str) -> None:
        directory = root / name
        if not directory.is_dir():
            errors.append(f"missing_{name}")
            return
        paths = sorted(directory.glob("*.json"))
        if not paths:
            errors.append(f"empty_{name}")
        for path in paths:
            try:
                payload = read_json(path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                errors.append(f"invalid_{name}")
                continue
            if not isinstance(payload, dict) or not str(payload.get(key) or "").strip():
                errors.append(f"missing_{name}_id")
                continue
            ids.add(str(payload[key]).strip())

    load_directory("proposals", proposal_ids, key="proposal_id")

    decisions = root / "decisions"
    if not decisions.is_dir():
        errors.append("missing_decisions")
    else:
        paths = sorted(decisions.glob("*.json"))
        if not paths:
            errors.append("empty_decisions")
        for path in paths:
            try:
                payload = read_json(path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                errors.append("invalid_decisions")
                continue
            if not isinstance(payload, dict):
                errors.append("missing_decisions_id")
                continue
            proposal_id = str(payload.get("proposal_id") or "").strip()
            decision_id = str(payload.get("decision_id") or "").strip()
            if not proposal_id or not decision_id:
                errors.append("missing_decisions_id")
                continue
            if proposal_id in decisions_by_proposal:
                errors.append("duplicate_decision_proposal")
                continue
            decision_ids.add(proposal_id)
            decisions_by_proposal[proposal_id] = decision_id

    commits = root / "commits"
    if not commits.is_dir():
        errors.append("missing_commits")
    else:
        paths = sorted(commits.glob("*.json"))
        if not paths:
            errors.append("empty_commits")
        for path in paths:
            try:
                payload = read_json(path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                errors.append("invalid_commits")
                continue
            if not isinstance(payload, dict):
                errors.append("missing_commits_id")
                continue
            proposal_id = str(payload.get("proposal_id") or "").strip()
            decision_id = str(payload.get("decision_id") or "").strip()
            event_id = str(payload.get("event_id") or "").strip()
            if not proposal_id or not decision_id or not event_id:
                errors.append("missing_commits_id")
                continue
            if proposal_id in commits_by_proposal:
                errors.append("duplicate_commit_proposal")
                continue
            commit_ids.add(proposal_id)
            commits_by_proposal[proposal_id] = decision_id
            commit_events.add(event_id)

    life_state_path = root / "life_state.json"
    life_state_events: set[str] = set()
    if not life_state_path.is_file():
        errors.append("missing_life_state")
    else:
        try:
            life_state = read_json(life_state_path)
            raw_events = life_state.get("applied_event_ids", [])
            if not isinstance(raw_events, list) or any(
                not isinstance(event, str) or not event.strip() for event in raw_events
            ):
                raise ValueError
            life_state_events = {event.strip() for event in raw_events}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
            errors.append("invalid_life_state")

    if proposal_ids != decision_ids or proposal_ids != commit_ids:
        errors.append("proposal_decision_commit_mismatch")
    elif any(
        decisions_by_proposal[proposal_id] != commits_by_proposal[proposal_id]
        for proposal_id in proposal_ids
    ):
        errors.append("proposal_decision_mismatch")
    commit_namespaces = {_event_namespace(event) for event in commit_events if event}
    life_namespaces = {_event_namespace(event) for event in life_state_events if event}
    if commit_namespaces & life_namespaces:
        errors.append("worldbook_namespace_overlap")

    status = "ready" if not errors else "not_ready"
    return {
        "status": status,
        "reason": "legacy_readonly_chain_verified" if not errors else errors[0],
        "proposal_count": len(proposal_ids),
        "decision_count": len(decision_ids),
        "commit_count": len(commit_ids),
        "life_state_event_count": len(life_state_events),
        "legacy_namespaces": {
            "commit": ",".join(sorted(commit_namespaces)),
            "life_state": ",".join(sorted(life_namespaces)),
        },
        "source_sha256": "sha256:" + digest.hexdigest(),
        "errors": errors,
    }


def _not_assessed_gates() -> tuple[dict[str, Any], dict[str, Any]]:
    from services.agent_runtime.rollout_readiness import (
        ACTIVATION_GATE_NAMES,
        ROLLBACK_GATE_NAMES,
    )

    def gates(names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "status": "not_assessed",
                "reason": "operator_evidence_required",
                "evidence_at": None,
                "evidence_ref": None,
            }
            for name in names
        }

    return gates(ACTIVATION_GATE_NAMES), gates(ROLLBACK_GATE_NAMES)


async def _initialize_sources(
    paths: dict[str, Path], *, credential: str, operator_id: str
) -> None:
    from services.agent_runtime.invocation_store import TrustedInvocationStoreV1
    from services.agent_runtime.ledger import AgentRuntimeLedger
    from services.agent_runtime.operator_auth import OperatorAuthorizationStoreV1
    from services.memory.governance_store import MemoryGovernanceStore
    from services.worldbook.governance_store import WorldbookGovernanceStore

    stores: list[Any] = [
        AgentRuntimeLedger(paths["runtime"]),
        MemoryGovernanceStore(paths["memory"]),
        WorldbookGovernanceStore(paths["worldbook"]),
        OperatorAuthorizationStoreV1(paths["operator"]),
        TrustedInvocationStoreV1(paths["invocation"]),
    ]
    for store in stores:
        await store.init()
        if isinstance(store, OperatorAuthorizationStoreV1):
            await store.provision(
                operator_id=operator_id,
                credential=credential,
                granted_scopes=_OPERATOR_SCOPES,
                resource_grants=_OPERATOR_GRANTS,
            )
        await store.close()


def _profile_settings(
    *,
    source_config: dict[str, dict[str, Any]],
    worker_id: str,
    scopes: tuple[str, ...],
    targets: tuple[str, ...],
    manifest_path: str,
    manifest_sha256: str,
) -> SimpleNamespace:
    values: dict[str, Any] = {
        "enabled": True,
        "worker_id": worker_id,
        "max_workers": 1,
        "principal_scopes": scopes,
        "allowed_target_refs": targets,
        "attestation": {
            "manifest_path": manifest_path,
            "manifest_sha256": manifest_sha256,
        },
    }
    values.update(source_config)

    def convert(value: Any) -> Any:
        if isinstance(value, dict):
            return SimpleNamespace(**{key: convert(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(convert(item) for item in value)
        return value

    return convert(values)


async def provision_sources(
    *,
    repo_root: str | Path,
    run_id: str,
    worker_id: str,
    operator_id: str,
    credential: str,
    principal_scopes: tuple[str, ...] = _DEFAULT_SCOPES,
    allowed_target_refs: tuple[str, ...] = _DEFAULT_TARGETS,
) -> dict[str, Any]:
    """Create and verify one immutable initial source set.

    The final directory is committed with one rename.  A pre-existing final
    directory is always an error, which makes reruns explicit and idempotent.
    """

    root = Path(repo_root).resolve()
    clean_run_id = _safe_id(run_id, pattern=_RUN_ID_RE, field="run_id")
    clean_worker_id = _safe_id(worker_id, pattern=_WORKER_ID_RE, field="worker_id")
    clean_operator_id = _safe_id(operator_id, pattern=_OPERATOR_ID_RE, field="operator_id")
    if len(credential) < 24 or not credential.isascii() or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in credential
    ):
        raise ValueError("credential is invalid")
    scopes = tuple(
        sorted({str(value).strip() for value in principal_scopes if str(value).strip()})
    )
    targets = tuple(
        sorted({str(value).strip() for value in allowed_target_refs if str(value).strip()})
    )
    if not scopes or not targets or any("*" in value for value in (*scopes, *targets)):
        raise ValueError("principal scopes and target refs must be explicit and non-wildcard")

    storage = root / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    final_root = storage / "agent-runtime-v2"
    if final_root.exists() or final_root.is_symlink():
        raise FileExistsError(final_root)
    staging = Path(
        tempfile.mkdtemp(prefix=f".agent-runtime-v2-{clean_run_id}-", dir=storage)
    )

    try:
        source_paths = {
            name: staging / f"{name}.db"
            for name in _SOURCE_NAMES
        }
        await _initialize_sources(
            source_paths,
            credential=credential,
            operator_id=clean_operator_id,
        )

        backup_dir = staging / "backups" / clean_run_id
        restore_dir = staging / "rehearsals" / "restore"
        source_config: dict[str, dict[str, Any]] = {}
        source_reports: dict[str, Any] = {}
        for name in _SOURCE_NAMES:
            source = source_paths[name]
            backup = backup_dir / f"{name}.db"
            restored = restore_dir / f"{name}.db"
            _copy_sqlite(source, backup)
            _copy_sqlite(backup, restored)
            report = _sqlite_facts(source, expected_schema=_SOURCE_SCHEMAS[name])
            backup_digest = _sha256_file(backup)
            restored_report = _sqlite_facts(restored, expected_schema=_SOURCE_SCHEMAS[name])
            if restored_report["quick_check"] != "ok":
                raise RuntimeError(f"restore rehearsal failed for {name}")
            source_reports[name] = {
                **report,
                "backup_sha256": backup_digest,
                "restore": restored_report,
            }
            source_config[name] = {
                "db_path": f"storage/agent-runtime-v2/{name}.db",
                "expected_schema_version": _SOURCE_SCHEMAS[name],
                "backup_path": f"storage/agent-runtime-v2/backups/{clean_run_id}/{name}.db",
                "backup_sha256": backup_digest,
                "restore_evidence_ref": f"restore:agent-runtime-v2:{clean_run_id}:{name}",
                "rollback_evidence_ref": f"rollback:agent-runtime-v2:{clean_run_id}:{name}",
            }

        witness = _worldbook_witness(root)
        _write_json(staging / "witnesses" / "worldbook-readonly.json", witness)
        rollback_rehearsal = {
            "contract": "agent_runtime_v2_rollback_rehearsal.v1",
            "status": "not_assessed",
            "reason": "requires_reviewed_bot_only_config_disable",
            "run_id": clean_run_id,
            "worker_id": clean_worker_id,
            "preserve_unknown_and_dispatching": True,
            "napcat_operation": "none",
        }
        _write_json(staging / "rehearsals" / "rollback.json", rollback_rehearsal)

        manifest_path = "storage/agent-runtime-v2/rollout-attestation.json"
        settings = _profile_settings(
            source_config=source_config,
            worker_id=clean_worker_id,
            scopes=scopes,
            targets=targets,
            manifest_path=manifest_path,
            manifest_sha256="sha256:" + "0" * 64,
        )
        from services.agent_runtime.activation import ProductionActivationProfileV1

        profile = ProductionActivationProfileV1.from_settings(settings, repo_root=root)
        if profile is None:
            raise RuntimeError("provisioned activation profile unexpectedly disabled")
        activation, rollback = _not_assessed_gates()
        manifest = {
            "contract_version": "agent_runtime_rollout_attestation.v1",
            "schema_version": 1,
            "profile_fingerprint": profile.attestation_profile_fingerprint(),
            "activation": activation,
            "rollback": rollback,
        }
        _write_json(staging / "rollout-attestation.json", manifest)
        manifest_digest = _sha256_file(staging / "rollout-attestation.json")
        config = {
            "agent_runtime": {
                "enabled": True,
                "worker_id": clean_worker_id,
                "max_workers": 1,
                "principal_scopes": list(scopes),
                "allowed_target_refs": list(targets),
                **source_config,
                "attestation": {
                    "manifest_path": manifest_path,
                    "manifest_sha256": manifest_digest,
                },
            }
        }
        _write_json(staging / "activation-config.json", config)
        _write_bytes(
            staging / "operator-credential.env",
            f"AGENT_RUNTIME_OPERATOR_ID={clean_operator_id}\n"
            f"AGENT_RUNTIME_OPERATOR_CREDENTIAL={credential}\n".encode("ascii"),
            mode=0o600,
        )
        _write_json(
            staging / "source-inventory.json",
            {
                "contract": "agent_runtime_v2_source_inventory.v1",
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "run_id": clean_run_id,
                "sources": source_reports,
                "worldbook_witness": witness,
                "manifest_sha256": manifest_digest,
            },
        )

        os.replace(staging, final_root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        "root": str(final_root.relative_to(root)),
        "activation_config_path": str((final_root / "activation-config.json").relative_to(root)),
        "manifest_path": str((final_root / "rollout-attestation.json").relative_to(root)),
        "operator_secret_path": str(
            (final_root / "operator-credential.env").relative_to(root)
        ),
        "manifest_sha256": manifest_digest,
        "sources": source_reports,
        "worldbook_witness": witness,
        "status": "provisioned_not_assessed",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--worker-id", default="agent-runtime-prod-001")
    parser.add_argument("--operator-id", default="prod-operator")
    parser.add_argument(
        "--credential",
        help="operator credential; omitted credentials are generated and stored locally",
    )
    return parser


async def _main(args: argparse.Namespace) -> int:
    credential = args.credential or secrets.token_urlsafe(48)
    result = await provision_sources(
        repo_root=args.repo_root,
        run_id=args.run_id,
        worker_id=args.worker_id,
        operator_id=args.operator_id,
        credential=credential,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
