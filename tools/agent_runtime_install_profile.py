#!/usr/bin/env python3
"""Install a reviewed Agent Runtime v2 ingress-only configuration safely.

The command accepts only a source-verified profile whose rollout gates are all
``not_assessed``.  It therefore enables durable authoritative ingress while
keeping worker startup fail-closed until live evidence is collected.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Mapping
from contextlib import contextmanager, suppress
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
_WEB_SEARCH_SCOPE = "network:search"
_WEB_SEARCH_TARGET = "network:web-search"


class _WebSearchScopeRepairPlan:
    __slots__ = (
        "config",
        "manifest",
        "manifest_generation_name",
        "original_config_payload",
        "original_manifest_payload",
        "updated_config_payload",
        "updated_manifest_payload",
    )

    def __init__(
        self,
        *,
        config: Path,
        manifest: Path,
        original_config_payload: bytes,
        original_manifest_payload: bytes,
        updated_config_payload: bytes,
        updated_manifest_payload: bytes,
        manifest_generation_name: str,
    ) -> None:
        self.config = config
        self.manifest = manifest
        self.original_config_payload = original_config_payload
        self.original_manifest_payload = original_manifest_payload
        self.updated_config_payload = updated_config_payload
        self.updated_manifest_payload = updated_manifest_payload
        self.manifest_generation_name = manifest_generation_name


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


def _safe_child_name(name: str) -> None:
    if not name or Path(name).name != name:
        raise ValueError("child path name is invalid")


def _no_follow_flag() -> int:
    flag = getattr(os, "O_NOFOLLOW", None)
    if flag is None:
        raise RuntimeError("secure no-follow file operations are unavailable")
    return int(flag)


def _directory_flags() -> int:
    directory = getattr(os, "O_DIRECTORY", None)
    if directory is None:
        raise RuntimeError("secure directory file operations are unavailable")
    return os.O_RDONLY | int(directory) | _no_follow_flag()


@contextmanager
def _safe_directory_fd(path: Path, *, message: str) -> Any:
    try:
        descriptor = os.open(path, _directory_flags())
    except OSError:
        raise ValueError(message) from None
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError(message)
        yield descriptor
    finally:
        os.close(descriptor)


def _open_child_directory(parent_fd: int, name: str) -> int:
    _safe_child_name(name)
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError:
        raise ValueError("child directory is unsafe") from None
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("child directory is unsafe")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _safe_child_directory(parent_fd: int, name: str) -> Any:
    try:
        descriptor = _open_child_directory(parent_fd, name)
    except FileNotFoundError:
        created = False
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        else:
            created = True
        descriptor = _open_child_directory(parent_fd, name)
        if created:
            try:
                _sync_directory(parent_fd)
            except BaseException:
                os.close(descriptor)
                raise
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _entry_exists_at(parent_fd: int, name: str) -> bool:
    _safe_child_name(name)
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _directory_path_matches(path: Path, directory_fd: int) -> bool:
    try:
        expected = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    actual = os.fstat(directory_fd)
    return (
        stat.S_ISDIR(expected.st_mode)
        and expected.st_dev == actual.st_dev
        and expected.st_ino == actual.st_ino
    )


def _child_directory_matches(parent_fd: int, name: str, child_fd: int) -> bool:
    _safe_child_name(name)
    try:
        expected = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    actual = os.fstat(child_fd)
    return (
        stat.S_ISDIR(expected.st_mode)
        and expected.st_dev == actual.st_dev
        and expected.st_ino == actual.st_ino
    )


def _read_bytes_at(parent_fd: int, name: str, *, message: str) -> bytes:
    _safe_child_name(name)
    try:
        descriptor = os.open(name, os.O_RDONLY | _no_follow_flag(), dir_fd=parent_fd)
    except OSError:
        raise ValueError(message) from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(message)
        handle = os.fdopen(descriptor, "rb")
        descriptor = -1
        with handle:
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_payload_descriptor(descriptor: int, payload: bytes, *, mode: int) -> None:
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("output path is unsafe")
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _sync_directory(parent_fd: int) -> None:
    try:
        os.fsync(parent_fd)
    except OSError:
        raise RuntimeError("output directory sync failed") from None


def _write_exclusive_at(parent_fd: int, name: str, payload: bytes, *, mode: int) -> None:
    _safe_child_name(name)
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow_flag(),
            mode,
            dir_fd=parent_fd,
        )
    except FileExistsError:
        raise
    except OSError:
        raise ValueError("output path is unsafe") from None
    _write_payload_descriptor(descriptor, payload, mode=mode)
    _sync_directory(parent_fd)


def _write_atomic_at(parent_fd: int, name: str, payload: bytes, *, mode: int) -> None:
    _safe_child_name(name)
    temporary_name = f".{name}.{secrets.token_hex(8)}.tmp"
    try:
        _write_exclusive_at(parent_fd, temporary_name, payload, mode=mode)
        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        _sync_directory(parent_fd)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=parent_fd)


def _write_atomic(path: Path, payload: bytes, *, mode: int) -> None:
    with _safe_directory_fd(path.parent, message="output directory is unsafe") as parent_fd:
        _write_atomic_at(parent_fd, path.name, payload, mode=mode)


@contextmanager
def _profile_repair_lock(config: Path) -> Any:
    """Coordinate only profile-repair writers without deleting lock evidence."""

    with _safe_directory_fd(
        config.parent,
        message="profile repair directory is unsafe",
    ) as config_dir_fd:
        try:
            descriptor = os.open(
                ".agent-runtime-profile-repair.lock",
                os.O_RDWR | os.O_CREAT | _no_follow_flag(),
                0o600,
                dir_fd=config_dir_fd,
            )
        except OSError:
            raise ValueError("profile repair lock is unsafe") from None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("profile repair lock is unsafe")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("another profile repair is already running") from None
            yield config_dir_fd
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


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
    backup_name = f"config.before-agent-runtime-v2-{clean_run_id}.json"
    backup = config.parent / "backups" / backup_name
    mode = config.stat().st_mode & 0o777
    with (
        _safe_directory_fd(config.parent, message="config directory is unsafe") as config_dir_fd,
        _safe_child_directory(config_dir_fd, "backups") as backup_dir_fd,
    ):
        if _entry_exists_at(backup_dir_fd, backup_name):
            raise FileExistsError(backup)
        _write_exclusive_at(backup_dir_fd, backup_name, original_payload, mode=mode)
        if not _child_directory_matches(config_dir_fd, "backups", backup_dir_fd):
            raise RuntimeError("config backup directory changed during installation")
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


async def repair_unassessed_web_search_scope(
    *,
    repo_root: str | Path,
    config_path: str | Path,
    run_id: str,
    apply: bool = True,
) -> dict[str, str]:
    """Re-pin an all-unassessed ingress profile after the web-search scope fix.

    This is deliberately narrower than a general profile editor. It only fixes
    the original provisioner's missing ``network:search`` scope for an existing
    ``network:web-search`` canary, and rejects any profile with assessed gates.
    """

    root = Path(repo_root).resolve()
    clean_run_id = str(run_id or "").strip()
    if _RUN_ID_RE.fullmatch(clean_run_id) is None:
        raise ValueError("run_id is invalid")
    raw_config = Path(config_path)
    if raw_config.is_symlink():
        raise ValueError("config path must not be a symlink")
    config = _safe_under(raw_config, root, directory="config")
    if not config.is_file():
        raise ValueError("config path must be a regular file")

    plan = await _prepare_web_search_scope_repair(
        root=root,
        config=config,
        run_id=clean_run_id,
    )
    if plan is None:
        return {"status": "web_search_scope_already_present"}
    if not apply:
        return {
            "status": "web_search_scope_repair_ready",
            "config_sha256": _sha256(plan.updated_config_payload),
            "manifest_sha256": _sha256(plan.updated_manifest_payload),
        }

    # All cancellation-prone validation completed before this critical section.
    # A newly written manifest is an unreferenced immutable orphan until the
    # final config-pointer replacement succeeds.
    config_backup_name = (
        f"config.before-agent-runtime-web-search-scope-{clean_run_id}.json"
    )
    config_backup = config.parent / "backups" / config_backup_name
    new_manifest = (
        plan.manifest.parent
        / "attestation-generations"
        / plan.manifest_generation_name
    )
    with (
        _profile_repair_lock(config) as config_dir_fd,
        _safe_directory_fd(
            plan.manifest.parent,
            message="attestation manifest directory is unsafe",
        ) as manifest_parent_fd,
    ):
        if (
            not _directory_path_matches(config.parent, config_dir_fd)
            or not _directory_path_matches(plan.manifest.parent, manifest_parent_fd)
            or _read_bytes_at(
                config_dir_fd,
                config.name,
                message="config path is unsafe",
            )
            != plan.original_config_payload
            or _read_bytes_at(
                manifest_parent_fd,
                plan.manifest.name,
                message="attestation manifest is unsafe",
            )
            != plan.original_manifest_payload
        ):
            raise RuntimeError("ingress profile changed during repair")
        config_stat = os.stat(
            config.name,
            dir_fd=config_dir_fd,
            follow_symlinks=False,
        )
        manifest_stat = os.stat(
            plan.manifest.name,
            dir_fd=manifest_parent_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(config_stat.st_mode) or not stat.S_ISREG(manifest_stat.st_mode):
            raise RuntimeError("ingress profile changed during repair")
        with (
            _safe_child_directory(config_dir_fd, "backups") as config_backup_dir_fd,
            _safe_child_directory(
                manifest_parent_fd,
                "attestation-generations",
            ) as manifest_generation_dir_fd,
        ):
            if (
                _entry_exists_at(config_backup_dir_fd, config_backup_name)
                or _entry_exists_at(
                    manifest_generation_dir_fd,
                    plan.manifest_generation_name,
                )
            ):
                raise FileExistsError("web-search scope repair output already exists")
            config_mode = config_stat.st_mode & 0o777
            manifest_mode = manifest_stat.st_mode & 0o777
            _write_exclusive_at(
                config_backup_dir_fd,
                config_backup_name,
                plan.original_config_payload,
                mode=config_mode,
            )
            _write_exclusive_at(
                manifest_generation_dir_fd,
                plan.manifest_generation_name,
                plan.updated_manifest_payload,
                mode=manifest_mode,
            )
            if (
                not _directory_path_matches(config.parent, config_dir_fd)
                or not _directory_path_matches(
                    plan.manifest.parent,
                    manifest_parent_fd,
                )
                or not _child_directory_matches(
                    config_dir_fd,
                    "backups",
                    config_backup_dir_fd,
                )
                or not _child_directory_matches(
                    manifest_parent_fd,
                    "attestation-generations",
                    manifest_generation_dir_fd,
                )
                or _read_bytes_at(
                    config_dir_fd,
                    config.name,
                    message="config path is unsafe",
                )
                != plan.original_config_payload
                or _read_bytes_at(
                    manifest_parent_fd,
                    plan.manifest.name,
                    message="attestation manifest is unsafe",
                )
                != plan.original_manifest_payload
            ):
                raise RuntimeError("repair output directory changed")
            _write_atomic_at(
                config_dir_fd,
                config.name,
                plan.updated_config_payload,
                mode=config_mode,
            )

    return {
        "status": "repinned_ingress_only_web_search_scope",
        "config_backup_path": str(config_backup),
        "manifest_path": str(new_manifest),
        "previous_manifest_path": str(plan.manifest),
        "config_sha256": _sha256(plan.updated_config_payload),
        "manifest_sha256": _sha256(plan.updated_manifest_payload),
    }


async def _prepare_web_search_scope_repair(
    *,
    root: Path,
    config: Path,
    run_id: str,
) -> _WebSearchScopeRepairPlan | None:
    """Validate a repair entirely in memory before any apply-mode write."""

    existing, original_config_payload = _load_json(config)
    raw_runtime = existing.get("agent_runtime")
    if not isinstance(raw_runtime, Mapping):
        raise ValueError("agent_runtime configuration is required")
    current_profile = await _validate_ingress_only_fragment(
        repo_root=root,
        fragment={"agent_runtime": raw_runtime},
    )
    if current_profile.allowed_target_refs != (_WEB_SEARCH_TARGET,):
        raise ValueError("web-search must be the only canary target")
    if _WEB_SEARCH_SCOPE in current_profile.principal_scopes:
        return None

    raw_attestation = raw_runtime.get("attestation")
    if not isinstance(raw_attestation, Mapping):
        raise ValueError("attestation configuration is required")
    manifest_value = raw_attestation.get("manifest_path")
    raw_manifest = root / Path(str(manifest_value or ""))
    if raw_manifest.is_symlink():
        raise ValueError("attestation manifest must not be a symlink")
    manifest = _safe_under(raw_manifest, root, directory="storage")
    if not manifest.is_file():
        raise ValueError("attestation manifest must be a regular file")
    original_manifest_payload = manifest.read_bytes()
    if (
        current_profile.attestation is None
        or _sha256(original_manifest_payload)
        != current_profile.attestation.manifest_sha256
    ):
        raise ValueError("attestation manifest changed during repair")
    try:
        manifest_data = json.loads(original_manifest_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("attestation manifest is invalid") from None
    if not isinstance(manifest_data, dict):
        raise ValueError("attestation manifest is invalid")

    manifest_generation_name = f"rollout-attestation.web-search-scope-{run_id}.json"
    manifest_relative = (
        manifest.parent / "attestation-generations" / manifest_generation_name
    ).relative_to(root)
    updated = deepcopy(existing)
    updated_runtime = updated["agent_runtime"]
    assert isinstance(updated_runtime, dict)
    updated_runtime["principal_scopes"] = sorted(
        {*current_profile.principal_scopes, _WEB_SEARCH_SCOPE}
    )
    updated_attestation = updated_runtime.get("attestation")
    if not isinstance(updated_attestation, dict):
        raise ValueError("attestation configuration is required")
    updated_attestation["manifest_path"] = str(manifest_relative)
    updated_attestation["manifest_sha256"] = "sha256:" + "0" * 64
    updated_profile = ProductionActivationProfileV1.from_settings(
        _settings(updated_runtime),
        repo_root=root,
    )
    if updated_profile is None:
        raise ValueError("ingress-only profile must be enabled")
    manifest_data["profile_fingerprint"] = updated_profile.attestation_profile_fingerprint()
    updated_manifest_payload = json.dumps(
        manifest_data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    updated_attestation["manifest_sha256"] = _sha256(updated_manifest_payload)
    final_profile = ProductionActivationProfileV1.from_settings(
        _settings(updated_runtime),
        repo_root=root,
    )
    if final_profile is None or (
        final_profile.attestation_profile_fingerprint()
        != manifest_data["profile_fingerprint"]
    ):
        raise ValueError("repaired ingress-only profile is invalid")
    BotConfig.model_validate(updated)
    updated_config_payload = json.dumps(
        updated,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    return _WebSearchScopeRepairPlan(
        config=config,
        manifest=manifest,
        original_config_payload=original_config_payload,
        original_manifest_payload=original_manifest_payload,
        updated_config_payload=updated_config_payload,
        updated_manifest_payload=updated_manifest_payload,
        manifest_generation_name=manifest_generation_name,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fragment", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--repair-web-search-scope",
        action="store_true",
        help="re-pin only an all-unassessed ingress profile missing network:search",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the configuration write; omitted mode is a no-op",
    )
    return parser


async def _main(args: argparse.Namespace) -> int:
    if args.repair_web_search_scope:
        if args.fragment is not None:
            raise ValueError("web-search scope repair does not accept a fragment")
        result = await repair_unassessed_web_search_scope(
            repo_root=args.repo_root,
            config_path=args.config,
            run_id=args.run_id,
            apply=args.apply,
        )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    if not args.apply:
        print(json.dumps({"status": "dry_run_requires_apply"}, sort_keys=True))
        return 0
    if args.fragment is None:
        raise ValueError("fragment is required for ingress-only installation")
    result = await install_ingress_only_profile(
        repo_root=args.repo_root,
        config_path=args.config,
        fragment_path=args.fragment,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.repair_web_search_scope and args.fragment is not None:
        parser.error("--repair-web-search-scope does not accept --fragment")
    if not args.repair_web_search_scope and args.fragment is None:
        parser.error("--fragment is required unless --repair-web-search-scope is set")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
