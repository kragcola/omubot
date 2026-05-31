from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx


class NapCatError(RuntimeError):
    pass


class NapCatClient:
    def __init__(self, base_url: str, token_file: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_file = Path(token_file)

    def _read_token(self) -> str:
        try:
            payload = json.loads(self._token_file.read_text(encoding="utf-8"))
        except OSError as exc:
            raise NapCatError(f"Failed to read NapCat token file: {exc}") from exc
        token = str(payload.get("token") or "").strip()
        if not token:
            raise NapCatError("NapCat WebUI token is empty")
        return token

    def _token_hash(self) -> str:
        token = self._read_token()
        return hashlib.sha256(f"{token}.napcat".encode()).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        bearer: str | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
        allow_messages: set[str] | None = None,
    ) -> dict[str, object]:
        headers: dict[str, str] = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        async with httpx.AsyncClient(base_url=self._base_url, timeout=5.0, trust_env=False) as client:
            response = await client.request(method, path, headers=headers, params=params, json=json_body)
        try:
            payload = response.json()
        except ValueError as exc:
            raise NapCatError(f"NapCat returned non-JSON response for {path}: {response.text}") from exc
        if response.status_code >= 400:
            raise NapCatError(f"NapCat HTTP {response.status_code} for {path}: {payload}")
        if payload.get("code") != 0:
            message = str(payload.get("message") or "")
            if allow_messages and message in allow_messages:
                return payload
            raise NapCatError(f"NapCat API error for {path}: {payload}")
        return payload

    async def login(self) -> str:
        payload = await self._request("POST", "/api/auth/login", json_body={"hash": self._token_hash()})
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("Credential"):
            raise NapCatError(f"NapCat login missing Credential: {payload}")
        return str(data["Credential"])

    async def register_manager(self, credential: str) -> None:
        payload = await self._request(
            "POST",
            "/api/Plugin/RegisterManager",
            bearer=credential,
            json_body={},
            allow_messages={"插件管理器已经注册"},
        )
        _ = payload

    async def _get_builtin_plugin_config(self, credential: str) -> dict[str, object]:
        await self.register_manager(credential)
        payload = await self._request(
            "GET",
            "/api/Plugin/Config",
            bearer=credential,
            params={"id": "napcat-plugin-builtin"},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise NapCatError(f"NapCat plugin config payload malformed: {payload}")
        return data

    async def get_builtin_plugin_config(self) -> dict[str, object]:
        credential = await self.login()
        return await self._get_builtin_plugin_config(credential)

    async def set_builtin_reply_enabled(self, enabled: bool) -> dict[str, object]:
        credential = await self.login()
        data = await self._get_builtin_plugin_config(credential)
        config = data.get("config")
        if not isinstance(config, dict):
            raise NapCatError(f"NapCat plugin config missing config object: {data}")
        next_config = dict(config)
        next_config["enableReply"] = enabled
        payload = await self._request(
            "POST",
            "/api/Plugin/Config",
            bearer=credential,
            json_body={"id": "napcat-plugin-builtin", "config": next_config},
        )
        return {
            "enableReply": enabled,
            "message": payload.get("message", "success"),
        }
