from __future__ import annotations

import hmac
import logging
import os
import re
import subprocess
from pathlib import Path

import audit
import httpx
from bot_update import BotUpdateError, run_bot_update
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from napcat_client import NapCatClient, NapCatError

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
LOGGER = logging.getLogger("pmubot")
CORE_CONTAINER_NAMES = {
    "ccip-sidecar",
    "napcat",
    "pmubot",
    "qq-bot",
    "socket-proxy",
    "socket-proxy-update",
    "socket-proxy-write",
    "watchtower",
}
RUNTIME_CONTAINER_RE = re.compile(r"^(?:napcat|qq-bot)(?:-.+)?$")
SEMVER_TAG_RE = re.compile(r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")

app = FastAPI(title="pmubot", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def docker_base_url(*, write: bool = False) -> str:
    env_name = "DOCKER_WRITE_HOST" if write else "DOCKER_HOST"
    default = "tcp://socket-proxy-write:2375" if write else "tcp://socket-proxy:2375"
    raw = os.getenv(env_name, default).strip()
    if raw.startswith("tcp://"):
        return "http://" + raw[len("tcp://") :]
    if raw.startswith(("http://", "https://")):
        return raw
    raise RuntimeError(f"Unsupported {env_name} for pmubot: {raw}")


def compose_project_name() -> str:
    return os.getenv("COMPOSE_PROJECT_NAME", "omubot").strip() or "omubot"


def normalize_container(item: dict[str, object]) -> dict[str, object]:
    names = item.get("Names") or [""]
    name = str(names[0]).lstrip("/")
    image = str(item.get("Image") or "")
    status = str(item.get("Status") or item.get("State") or "unknown")
    tag = image.rsplit(":", 1)[1] if ":" in image else "latest"
    labels = item.get("Labels") if isinstance(item.get("Labels"), dict) else {}
    return {
        "name": name,
        "state": str(item.get("State") or "unknown"),
        "status": status,
        "uptime": status,
        "image": image,
        "image_tag": tag,
        "compose_project": str(labels.get("com.docker.compose.project") or ""),
        "compose_service": str(labels.get("com.docker.compose.service") or ""),
    }


def is_managed_container(item: dict[str, object]) -> bool:
    project = str(item.get("compose_project") or "")
    name = str(item.get("name") or "")
    if project == compose_project_name():
        return True
    if name in CORE_CONTAINER_NAMES:
        return True
    return bool(RUNTIME_CONTAINER_RE.match(name))


def is_restartable_container(name: str) -> bool:
    return bool(RUNTIME_CONTAINER_RE.match(name))


def ccip_sidecar_base_url() -> str:
    return os.getenv("CCIP_SIDECAR_BASE_URL", "http://host.docker.internal:8620").strip()


def repo_root() -> Path:
    root = Path(os.getenv("PMUBOT_REPO_ROOT", "/workspace")).resolve()
    if not root.exists():
        raise RuntimeError(f"pmubot repo root not found: {root}")
    return root


def compose_env() -> dict[str, str]:
    env = os.environ.copy()
    env["DOCKER_HOST"] = os.getenv("DOCKER_UPDATE_HOST", "tcp://socket-proxy-update:2375").strip()
    env.setdefault("COMPOSE_PROJECT_NAME", os.getenv("COMPOSE_PROJECT_NAME", "omubot"))
    return env


def run_compose_command(
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
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out after {timeout_seconds}s: {' '.join(cmd)}") from exc
    if result.returncode != 0:
        stderr_tail = "\n".join((result.stderr or "").strip().splitlines()[-20:])
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{stderr_tail}".strip())
    return result


def ensure_ccip_sidecar_running() -> dict[str, str]:
    env = compose_env()
    root = repo_root()
    inspect = subprocess.run(
        ["docker", "inspect", "ccip-sidecar", "--format", "{{.State.Status}}"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    status = (inspect.stdout or "").strip()
    if inspect.returncode == 0 and status == "running":
        return {"status": "running"}
    if inspect.returncode == 0:
        run_compose_command(
            ["docker", "start", "ccip-sidecar"],
            cwd=root,
            env=env,
            timeout_seconds=120,
        )
        return {"status": "started"}
    run_compose_command(
        ["docker", "compose", "up", "-d", "--build", "--no-deps", "ccip-sidecar"],
        cwd=root,
        env=env,
        timeout_seconds=3600,
    )
    return {"status": "created"}


def pmubot_token() -> str:
    return os.getenv("PMUBOT_TOKEN", "").strip()


def get_napcat_client() -> NapCatClient:
    return NapCatClient(
        base_url=os.getenv("NAPCAT_WEBUI_BASE_URL", "http://host.docker.internal:6099"),
        token_file=os.getenv("NAPCAT_WEBUI_TOKEN_FILE", "/run/secrets/napcat-webui.json"),
    )


async def docker_get(path: str, params: dict[str, str] | None = None) -> object:
    async with httpx.AsyncClient(base_url=docker_base_url(), timeout=5.0, trust_env=False) as client:
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.json()


async def docker_get_bytes(path: str, params: dict[str, str] | None = None) -> bytes:
    """Read-proxy GET returning raw bytes (for the logs endpoint, which streams a
    multiplexed byte payload rather than JSON)."""
    async with httpx.AsyncClient(base_url=docker_base_url(), timeout=8.0, trust_env=False) as client:
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.content


def demux_docker_log_stream(raw: bytes) -> str:
    """Docker's /logs (TTY off) multiplexes stdout/stderr with an 8-byte header
    per frame: [stream_type, 0, 0, 0, len_be32]. Strip the headers and return
    decoded text. If the payload has no valid frames (TTY on), decode as-is."""
    out: list[bytes] = []
    i = 0
    n = len(raw)
    saw_frame = False
    while i + 8 <= n:
        stream_type = raw[i]
        if stream_type not in (0, 1, 2):
            break  # not a frame header — bail to raw decode
        length = int.from_bytes(raw[i + 4 : i + 8], "big")
        start = i + 8
        end = start + length
        if end > n:
            break
        out.append(raw[start:end])
        i = end
        saw_frame = True
    if not saw_frame:
        return raw.decode("utf-8", errors="replace")
    return b"".join(out).decode("utf-8", errors="replace")


def docker_restart_timeout_seconds() -> float:
    raw = os.getenv("PMUBOT_RESTART_TIMEOUT_SECONDS", "30").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 30.0


async def docker_post(path: str, *, timeout: float) -> None:
    async with httpx.AsyncClient(
        base_url=docker_base_url(write=True),
        timeout=timeout,
        trust_env=False,
    ) as client:
        response = await client.post(path)
        response.raise_for_status()


async def require_write_access(
    authorization: str | None = Header(default=None),
    confirm: int = Query(default=0),
) -> None:
    expected = pmubot_token()
    if not expected:
        raise HTTPException(status_code=503, detail="PMUBOT_TOKEN is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    provided = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid PMUBOT_TOKEN")
    if confirm != 1:
        raise HTTPException(status_code=400, detail="confirm=1 required for write operations")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def fetch_ccip_sidecar_health() -> dict[str, object] | None:
    async with httpx.AsyncClient(base_url=ccip_sidecar_base_url(), timeout=3.0, trust_env=False) as client:
        try:
            response = await client.get("/health")
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None


async def fetch_managed_containers() -> list[dict[str, object]]:
    try:
        payload = await docker_get("/containers/json", params={"all": "1"})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Docker proxy request failed: {exc}") from exc

    if not isinstance(payload, list):
        raise HTTPException(status_code=502, detail="Docker proxy returned unexpected payload")

    containers = [normalize_container(item) for item in payload if isinstance(item, dict)]
    selected = [item for item in containers if is_managed_container(item)]
    ccip = next((item for item in selected if item["name"] == "ccip-sidecar"), None)
    if ccip is None or ccip["state"] != "running":
        try:
            recovery = await run_in_threadpool(ensure_ccip_sidecar_running)
        except RuntimeError as exc:
            LOGGER.warning("pmubot sidecar recovery failed: %s", exc)
        else:
            LOGGER.info("pmubot sidecar recovery | action=%s", recovery.get("status"))
            payload = await docker_get("/containers/json", params={"all": "1"})
            containers = [normalize_container(item) for item in payload if isinstance(item, dict)]
            selected = [item for item in containers if is_managed_container(item)]
            ccip = next((item for item in selected if item["name"] == "ccip-sidecar"), None)

    if ccip is not None and ccip["state"] == "running":
        health_payload = await fetch_ccip_sidecar_health()
        if health_payload is not None:
            ccip["health"] = str(health_payload.get("status") or "unknown")
            ccip["character_count"] = str(health_payload.get("character_count") or "0")
            ccip["registry_version"] = str(health_payload.get("registry_version") or "")
            ccip["api_version"] = str(health_payload.get("api_version") or "")

    return sorted(selected, key=lambda item: str(item["name"]))


@app.get("/api/containers")
async def list_containers() -> list[dict[str, object]]:
    return await fetch_managed_containers()


def _assert_managed_name(name: str) -> None:
    """Read endpoints accept any container pmubot manages (core set or runtime
    napcat*/qq-bot* instances), not arbitrary names."""
    if name in CORE_CONTAINER_NAMES or RUNTIME_CONTAINER_RE.match(name):
        return
    raise HTTPException(status_code=404, detail=f"Container {name!r} is not managed by pmubot")


@app.get("/api/containers/{name}/logs")
async def container_logs(name: str, tail: int = Query(default=200, ge=1, le=1000)) -> dict[str, object]:
    _assert_managed_name(name)
    try:
        raw = await docker_get_bytes(
            f"/containers/{name}/logs",
            params={"stdout": "1", "stderr": "1", "tail": str(tail), "timestamps": "0"},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Docker logs request failed: {exc}") from exc
    return {"container": name, "tail": tail, "content": demux_docker_log_stream(raw)}


@app.get("/api/containers/{name}/stats")
async def container_stats(name: str) -> dict[str, object]:
    _assert_managed_name(name)
    try:
        payload = await docker_get(f"/containers/{name}/stats", params={"stream": "false"})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Docker stats request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Docker stats returned unexpected payload")
    return {"container": name, **summarize_stats(payload)}


def summarize_stats(payload: dict[str, object]) -> dict[str, object]:
    """Reduce Docker's verbose /stats blob to CPU% and memory MB/limit/%."""
    cpu_pct: float | None = None
    try:
        cpu = payload.get("cpu_stats", {}) or {}
        pre = payload.get("precpu_stats", {}) or {}
        cpu_total = cpu.get("cpu_usage", {}).get("total_usage", 0)
        pre_total = pre.get("cpu_usage", {}).get("total_usage", 0)
        sys_total = cpu.get("system_cpu_usage", 0)
        pre_sys = pre.get("system_cpu_usage", 0)
        online = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage") or []) or 1
        cpu_delta = float(cpu_total) - float(pre_total)
        sys_delta = float(sys_total) - float(pre_sys)
        if sys_delta > 0 and cpu_delta >= 0:
            cpu_pct = round((cpu_delta / sys_delta) * online * 100.0, 2)
    except (TypeError, ValueError, AttributeError):
        cpu_pct = None

    mem = payload.get("memory_stats", {}) or {}
    usage = mem.get("usage")
    limit = mem.get("limit")
    # Docker counts page cache in usage; subtract it for a truer working set.
    cache = (mem.get("stats", {}) or {}).get("inactive_file", 0) if isinstance(mem.get("stats"), dict) else 0
    mem_bytes = (float(usage) - float(cache)) if isinstance(usage, (int, float)) else None
    mem_mb = round(mem_bytes / 1_048_576, 1) if mem_bytes is not None else None
    limit_mb = round(float(limit) / 1_048_576, 1) if isinstance(limit, (int, float)) and limit else None
    mem_pct = (
        round((mem_bytes / float(limit)) * 100.0, 1)
        if mem_bytes is not None and isinstance(limit, (int, float)) and limit
        else None
    )
    return {"cpu_pct": cpu_pct, "mem_mb": mem_mb, "mem_limit_mb": limit_mb, "mem_pct": mem_pct}


@app.get("/api/audit")
async def get_audit(tail: int = Query(default=100, ge=1, le=500)) -> dict[str, object]:
    return {"entries": await run_in_threadpool(audit.tail, tail)}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/containers/{name}/restart")
async def restart_container(
    name: str,
    ack_relogin: int = Query(default=0),
    _: None = Depends(require_write_access),
) -> dict[str, str]:
    if not is_restartable_container(name):
        raise HTTPException(status_code=404, detail=f"Container {name!r} is not restartable")
    if name.startswith("napcat") and ack_relogin != 1:
        raise HTTPException(
            status_code=400,
            detail="napcat restart is high risk; ack_relogin=1 required",
        )
    try:
        await docker_post(
            f"/containers/{name}/restart",
            timeout=docker_restart_timeout_seconds(),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Docker restart failed: {exc}") from exc
    if name.startswith("napcat"):
        LOGGER.warning(
            "pmubot audit | action=restart container=%s ack_relogin=1 "
            "warning=may_require_phone_relogin",
            name,
        )
        audit.record(
            "restart",
            target=name,
            result="ok",
            ack_relogin=1,
            warning="may_require_phone_relogin",
        )
        return {
            "status": "ok",
            "action": "restart",
            "container": name,
            "warning": "napcat 已重启，可能需手机扫码恢复，请检查登录态",
        }
    audit.record("restart", target=name, result="ok")
    return {"status": "ok", "action": "restart", "container": name}


@app.get("/api/napcat/builtin-reply")
async def get_builtin_reply() -> dict[str, object]:
    """Read-only: current `#napcat` builtin reply toggle, for UI echo."""
    client = get_napcat_client()
    try:
        data = await client.get_builtin_plugin_config()
    except NapCatError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    config = data.get("config") if isinstance(data, dict) else None
    enabled = bool(config.get("enableReply")) if isinstance(config, dict) else None
    return {"target": "napcat-plugin-builtin", "enableReply": enabled}


@app.post("/api/napcat/disable-builtin-reply")
async def disable_builtin_reply(_: None = Depends(require_write_access)) -> dict[str, object]:
    client = get_napcat_client()
    try:
        result = await client.set_builtin_reply_enabled(False)
    except NapCatError as exc:
        audit.record("disable-builtin-reply", target="napcat-plugin-builtin", result="error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit.record("disable-builtin-reply", target="napcat-plugin-builtin", result="ok")
    return {"status": "ok", "target": "napcat-plugin-builtin", "result": result}


@app.post("/api/napcat/enable-builtin-reply")
async def enable_builtin_reply(_: None = Depends(require_write_access)) -> dict[str, object]:
    client = get_napcat_client()
    try:
        result = await client.set_builtin_reply_enabled(True)
    except NapCatError as exc:
        audit.record("enable-builtin-reply", target="napcat-plugin-builtin", result="error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit.record("enable-builtin-reply", target="napcat-plugin-builtin", result="ok")
    return {"status": "ok", "target": "napcat-plugin-builtin", "result": result}


def parse_semver_tag(tag: str) -> tuple[int, int, int] | None:
    match = SEMVER_TAG_RE.match(tag.strip())
    if not match:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def parse_image_reference(image: str) -> tuple[str, str, str] | None:
    image = image.strip()
    if not image:
        return None
    last_segment = image.rsplit("/", 1)[-1]
    if ":" in last_segment:
        repo_part, tag = image.rsplit(":", 1)
    else:
        repo_part, tag = image, "latest"
    if "/" not in repo_part:
        return ("library", repo_part, tag)
    namespace, repo = repo_part.split("/", 1)
    return (namespace, repo, tag)


async def fetch_latest_docker_hub_semver_tag(namespace: str, repo: str) -> str | None:
    url = f"https://hub.docker.com/v2/namespaces/{namespace}/repositories/{repo}/tags?page_size=100"
    best_tag: str | None = None
    best_version: tuple[int, int, int] | None = None
    pages_remaining = 4
    async with httpx.AsyncClient(timeout=10.0, trust_env=True) as client:
        while url and pages_remaining > 0:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results")
            if isinstance(results, list):
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    tag = str(item.get("name") or "")
                    version = parse_semver_tag(tag)
                    if version is None:
                        continue
                    if best_version is None or version > best_version:
                        best_version = version
                        best_tag = tag
            next_url = payload.get("next")
            url = str(next_url) if next_url else ""
            pages_remaining -= 1
    return best_tag


@app.get("/api/updates/check")
async def check_updates() -> dict[str, object]:
    containers = await fetch_managed_containers()
    napcat = next((item for item in containers if item["name"] == "napcat"), None)
    bot = next((item for item in containers if item["name"] == "qq-bot"), None)
    napcat_instances = [
        {
            "name": str(item["name"]),
            "state": str(item["state"]),
            "image": str(item["image"]),
            "current_tag": str(item["image_tag"]),
        }
        for item in containers
        if str(item["name"]).startswith("napcat")
    ]
    bot_instances = [
        {
            "name": str(item["name"]),
            "state": str(item["state"]),
            "image": str(item["image"]),
            "current_tag": str(item["image_tag"]),
        }
        for item in containers
        if str(item["name"]).startswith("qq-bot")
    ]
    sidecars = [str(item["name"]) for item in containers if item["name"] == "ccip-sidecar"]

    napcat_status: dict[str, object] = {
        "strategy": "notify-only",
        "image": napcat["image"] if napcat else "",
        "current_tag": napcat["image_tag"] if napcat else "",
        "latest_tag": None,
        "update_available": None,
    }
    if napcat:
        parsed = parse_image_reference(napcat["image"])
        if parsed is not None:
            namespace, repo, current_tag = parsed
            try:
                latest_tag = await fetch_latest_docker_hub_semver_tag(namespace, repo)
            except httpx.HTTPError as exc:
                napcat_status["error"] = f"Docker Hub tag check failed: {exc}"
            else:
                napcat_status["latest_tag"] = latest_tag
                current_version = parse_semver_tag(current_tag)
                latest_version = parse_semver_tag(latest_tag or "")
                if current_version is not None and latest_version is not None:
                    napcat_status["update_available"] = latest_version > current_version

    return {
        "napcat": napcat_status,
        "napcat_instances": napcat_instances,
        "bot": {
            "strategy": "ci-build",
            "image": bot["image"] if bot else "",
            "current_tag": bot["image_tag"] if bot else "",
            "update_available": None,
            "note": "bot updates rebuild the current repo via /api/bot/update; no remote auto-check",
        },
        "bot_instances": bot_instances,
        "watchtower": {
            "strategy": "label-only",
            "label_enable": True,
            "managed_containers": sidecars,
        },
    }


@app.post("/api/bot/update")
async def update_bot(_: None = Depends(require_write_access)) -> dict[str, object]:
    try:
        result = await run_in_threadpool(run_bot_update)
    except BotUpdateError as exc:
        audit.record("bot-update", target="qq-bot", result="error", detail=str(exc)[:200])
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit.record(
        "bot-update",
        target="qq-bot",
        result="ok",
        attempts=result.get("update_attempts") if isinstance(result, dict) else None,
    )
    return {"status": "ok", "result": result}
