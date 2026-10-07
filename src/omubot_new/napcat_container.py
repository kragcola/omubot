"""Opt-in lifecycle for one provisioned NapCat container, never a Docker shell API."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .napcat import NapCatClient, NapCatError
from .types import StrictModel


class ContainerSpec(StrictModel):
    container_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    token: str = Field(min_length=1, max_length=4096, repr=False)


class NapCatContainer:
    """Owns CLI subprocess cleanup. The caller serializes lifecycle and WebUI access."""

    endpoint = "http://127.0.0.1:6100"

    def __init__(self, spec: ContainerSpec) -> None:
        self.spec = spec

    @classmethod
    def load(cls, path: Path) -> NapCatContainer:
        try:
            with path.open("rb") as handle:
                raw = handle.read(16385)
            if len(raw) > 16384:
                raise ValueError
            return cls(ContainerSpec.model_validate_json(raw))
        except (OSError, ValueError):
            raise ValueError("invalid or unreadable NapCat container descriptor") from None

    async def _command(self, *arguments: str, wait_seconds: float = 10) -> bytes:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            raise NapCatError("docker_unavailable") from None
        try:
            async with asyncio.timeout(wait_seconds):
                output, _ = await process.communicate()
        except BaseException:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            raise
        if process.returncode != 0:
            raise NapCatError("container_command_failed")
        if len(output) > 512 * 1024:
            raise NapCatError("container_invalid_response")
        return output

    async def state(self) -> dict[str, object]:
        try:
            result = json.loads(await self._command("inspect", self.spec.container_id))
            item = result[0]
            labels = item["Config"]["Labels"]
            host = item["HostConfig"]
            mounts = item["Mounts"]
            valid = (
                item["Id"] == self.spec.container_id
                and item["Image"] == self.spec.image_id
                and labels.get("io.omubot.project") == "omubot-new"
                and labels.get("io.omubot.role") == "napcat-development"
                and host["Privileged"] is False
                and host["NetworkMode"] in {"default", "bridge"}
                and host["PortBindings"] == {
                    "6099/tcp": [{"HostIp": "127.0.0.1", "HostPort": "6100"}]
                }
                and len(mounts) == 2
                and {(m["Type"], m["Name"], m["Destination"]) for m in mounts} == {
                    ("volume", "omubot-new-napcat-qq", "/app/.config/QQ"),
                    ("volume", "omubot-new-napcat-config", "/app/napcat/config"),
                }
            )
            if not valid:
                raise NapCatError("container_identity_mismatch")
            state = item["State"]
            return {
                "available": True,
                "phase": "running" if state["Running"] else "stopped",
                "oom_killed": bool(state["OOMKilled"]),
            }
        except TimeoutError:
            raise NapCatError("docker_timeout") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise NapCatError("container_invalid_response") from None

    async def control(self, action: Literal["start", "stop"]) -> dict[str, object]:
        state = await self.state()
        if (action == "start") == (state["phase"] == "running"):
            return state
        try:
            if action == "stop":
                await self._command("stop", "--time", "10", self.spec.container_id, wait_seconds=20)
            else:
                await self._command("start", self.spec.container_id, wait_seconds=20)
        except TimeoutError:
            # CLI termination does not undo a command already accepted by the daemon.
            raise NapCatError("docker_timeout") from None
        return await self.state()

    def client(self) -> NapCatClient:
        return NapCatClient(self.endpoint, self.spec.token)
