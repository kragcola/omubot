#!/usr/bin/env python3
"""Install a reviewed Agent Runtime v2 ingress-only configuration safely.

The command accepts only a source-verified profile whose rollout gates are all
``not_assessed``.  It therefore enables durable authoritative ingress while
keeping worker startup fail-closed until live evidence is collected.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import sys
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.config import BotConfig  # noqa: E402
from services.agent_runtime.activation import ProductionActivationProfileV1  # noqa: E402
from services.agent_runtime.rollout_attestation import (  # noqa: E402
    ProfileBoundRolloutAttestorV1,
)

_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = path.read_bytes()
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object is required: {path}")
    return value, payload


def _settings(value: Mapping[str, Any]) -> SimpleNamespace:
    def convert(item: Any) -> Any:
        if isinstance(item, Mapping):
            return SimpleNamespace(**{str(key): convert(child) for key, child in item.items()})
        if isinstance(item, list):
            return tuple(convert(child) for child in item)
        return item

    return convert(value)


def _safe_under(path: Path, root: Path, *, directory: str) -> Path:
    resolved = path.resolve()
    allowed = (root / directory).resolve()
    if not resolved.is_relative_to(allowed):
        raise ValueError(f"path must be under {directory}")
    return resolved


def _write_exclusive(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)


def _write_atomic(path: Path, payload: bytes, *, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


async def _validate_ingress_only_fragment(
    *,
    repo_root: Path,
    fragment: Mapping[str, Any],
) -> ProductionActivationProfileV1:
    raw_runtime = fragment.get("agent_runtime")
    if not isinstance(raw_runtime, Mapping) or set(fragment) != {"agent_runtime"}:
        raise ValueError("activation fragment is invalid")
    profile = ProductionActivationProfileV1.from_settings(
        _settings(raw_runtime),
        repo_root=repo_root,
    )
    if profile is None:
        raise ValueError("ingress-only profile must be enabled")
    source_report = await profile.preflight()
    if source_report.get("status") != "ready":
        raise ValueError("ingress-only profile source preflight failed")
    attestor = ProfileBoundRolloutAttestorV1(profile)
    activation = await attestor.activation_attestation()
    rollback = await attestor.rollback_attestation()
    if any(gate["status"] != "not_assessed" for gate in activation.values()) or any(
        gate["status"] != "not_assessed" for gate in rollback.values()
    ):
        raise ValueError("ingress-only profile must not be worker-ready")
    return profile


async def install_ingress_only_profile(
    *,
    repo_root: str | Path,
    config_path: str | Path,
    fragment_path: str | Path,
    run_id: str,
) -> dict[str, str]:
    """Back up and atomically install a source-verified, worker-closed profile."""

    root = Path(repo_root).resolve()
    clean_run_id = str(run_id or "").strip()
    if _RUN_ID_RE.fullmatch(clean_run_id) is None:
        raise ValueError("run_id is invalid")
    raw_config = Path(config_path)
    raw_fragment = Path(fragment_path)
    if raw_config.is_symlink() or raw_fragment.is_symlink():
        raise ValueError("config and fragment paths must not be symlinks")
    config = _safe_under(raw_config, root, directory="config")
    fragment_file = _safe_under(raw_fragment, root, directory="storage")
    if not config.is_file():
        raise ValueError("config path must be a regular file")
    if not fragment_file.is_file():
        raise ValueError("fragment path must be a regular file")

    existing, original_payload = _load_json(config)
    fragment, _fragment_payload = _load_json(fragment_file)
    if "agent_runtime" in existing:
        raise FileExistsError("agent_runtime already exists in config")
    await _validate_ingress_only_fragment(repo_root=root, fragment=fragment)

    updated = deepcopy(existing)
    updated["agent_runtime"] = deepcopy(fragment["agent_runtime"])
    BotConfig.model_validate(updated)
    output_payload = json.dumps(
        updated,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    backup = config.parent / "backups" / f"config.before-agent-runtime-v2-{clean_run_id}.json"
    if backup.exists():
        raise FileExistsError(backup)
    mode = config.stat().st_mode & 0o777
    _write_exclusive(backup, original_payload, mode=mode)
    try:
        _write_atomic(config, output_payload, mode=mode)
        reloaded, _payload = _load_json(config)
        await _validate_ingress_only_fragment(
            repo_root=root,
            fragment={"agent_runtime": reloaded.get("agent_runtime")},
        )
    except BaseException:
        _write_atomic(config, original_payload, mode=mode)
        raise
    return {
        "status": "installed_ingress_only",
        "backup_path": str(backup),
        "backup_sha256": _sha256(original_payload),
        "config_sha256": _sha256(output_payload),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fragment", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the configuration write; omitted mode is a no-op",
    )
    return parser


async def _main(args: argparse.Namespace) -> int:
    if not args.apply:
        print(json.dumps({"status": "dry_run_requires_apply"}, sort_keys=True))
        return 0
    result = await install_ingress_only_profile(
        repo_root=args.repo_root,
        config_path=args.config,
        fragment_path=args.fragment,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
