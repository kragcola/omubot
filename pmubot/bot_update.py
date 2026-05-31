from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


class BotUpdateError(RuntimeError):
    pass


def _repo_root() -> Path:
    repo_root = Path(os.getenv("PMUBOT_REPO_ROOT", "/workspace")).resolve()
    if not repo_root.exists():
        raise BotUpdateError(f"pmubot repo root not found: {repo_root}")
    return repo_root


def _docker_env() -> dict[str, str]:
    env = os.environ.copy()
    env["DOCKER_HOST"] = os.getenv("DOCKER_UPDATE_HOST", "tcp://socket-proxy-update:2375").strip()
    env.setdefault("COMPOSE_PROJECT_NAME", os.getenv("COMPOSE_PROJECT_NAME", "omubot"))
    return env


def _format_failure(cmd: list[str], result: subprocess.CompletedProcess[str]) -> str:
    stdout = (result.stdout or "").strip().splitlines()[-20:]
    stderr = (result.stderr or "").strip().splitlines()[-20:]
    parts = [f"command failed: {' '.join(cmd)}", f"exit={result.returncode}"]
    if stdout:
        parts.append("stdout:\n" + "\n".join(stdout))
    if stderr:
        parts.append("stderr:\n" + "\n".join(stderr))
    return "\n".join(parts)


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise BotUpdateError(
            f"command timed out after {timeout_seconds}s: {' '.join(cmd)}"
        ) from exc
    if result.returncode != 0:
        raise BotUpdateError(_format_failure(cmd, result))
    return result


def _inspect_bot_state(repo_root: Path, env: dict[str, str]) -> dict[str, str]:
    result = _run(
        ["docker", "inspect", "qq-bot", "--format", "{{.Config.Image}} {{.Image}} {{.State.StartedAt}}"],
        cwd=repo_root,
        env=env,
        timeout_seconds=30,
    )
    parts = result.stdout.strip().split()
    if len(parts) < 3:
        raise BotUpdateError(f"unexpected docker inspect payload: {result.stdout!r}")
    return {
        "image_name": parts[0],
        "image_id": parts[1],
        "started_at": parts[2],
    }


def _git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip()


def _git_dirty(repo_root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return True
    return bool(result.stdout.strip())


def _rollback_to_previous_image(
    *,
    repo_root: Path,
    env: dict[str, str],
    previous_image_name: str,
    previous_image_id: str,
) -> dict[str, str]:
    try:
        _run(
            ["docker", "image", "tag", previous_image_id, previous_image_name],
            cwd=repo_root,
            env=env,
            timeout_seconds=30,
        )
        _run(
            ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "bot"],
            cwd=repo_root,
            env=env,
            timeout_seconds=1800,
        )
    except BotUpdateError as exc:
        return {"status": "failed", "message": str(exc)}
    return {"status": "ok", "message": "previous bot image restored"}


def run_bot_update() -> dict[str, object]:
    repo_root = _repo_root()
    env = _docker_env()
    before = _inspect_bot_state(repo_root, env)
    result: dict[str, object] = {
        "repo_root": str(repo_root),
        "docker_host": env["DOCKER_HOST"],
        "git_commit": _git_commit(repo_root),
        "git_dirty": _git_dirty(repo_root),
        "before": before,
        "backup_command": "bash scripts/backup-databases.sh",
        "compose_command": "docker compose up -d --build --no-deps bot",
    }

    backup = _run(
        ["bash", "scripts/backup-databases.sh"],
        cwd=repo_root,
        env=env,
        timeout_seconds=900,
    )
    result["backup_summary"] = (backup.stdout or "").strip().splitlines()[-1] if backup.stdout else ""

    compose_error: BotUpdateError | None = None
    update_attempts = 0
    for update_attempts in range(1, 3):
        try:
            _run(
                ["docker", "compose", "up", "-d", "--build", "--no-deps", "bot"],
                cwd=repo_root,
                env=env,
                timeout_seconds=3600,
            )
        except BotUpdateError as exc:
            compose_error = exc
            if update_attempts < 2:
                time.sleep(3)
                continue
            break
        else:
            compose_error = None
            break

    if compose_error is not None:
        rollback = _rollback_to_previous_image(
            repo_root=repo_root,
            env=env,
            previous_image_name=before["image_name"],
            previous_image_id=before["image_id"],
        )
        raise BotUpdateError(
            f"bot update failed after backup: {compose_error}\n"
            f"rollback_status={rollback['status']} rollback_message={rollback['message']}"
        ) from compose_error

    result["update_attempts"] = update_attempts
    result["after"] = _inspect_bot_state(repo_root, env)
    return result
