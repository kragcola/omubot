"""Birthday greeter: sends @mention birthday wishes to configured QQ members."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
TZ = timezone(timedelta(hours=8))


def _empty_data() -> dict[str, Any]:
    """Fresh default state. Must be a new object per call — a shared dict/list
    default would leak member/sent_log mutations across instances."""
    return {"members": [], "sent_log": {}}


class BirthdayGreeter:
    def __init__(self, data_path: Path) -> None:
        self._path = data_path
        self._data: dict[str, Any] = _empty_data()
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text("utf-8"))
            except Exception as exc:
                logger.warning("birthday_greeter load failed: %s", exc)
                self._data = _empty_data()
        else:
            self._data = _empty_data()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8")

    def _today_key(self) -> str:
        return datetime.now(TZ).strftime("%Y-%m-%d")

    def _today_mmdd(self) -> str:
        return datetime.now(TZ).strftime("%m-%d")

    def _cleanup_old_logs(self) -> None:
        cutoff = (datetime.now(TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
        log = self._data.get("sent_log", {})
        self._data["sent_log"] = {k: v for k, v in log.items() if k >= cutoff}

    @property
    def members(self) -> list[dict[str, Any]]:
        return self._data.get("members", [])

    @property
    def sent_log(self) -> dict[str, list[str]]:
        return self._data.get("sent_log", {})

    def add_member(self, qq: str, name: str, birthday_mmdd: str, groups: list[str]) -> None:
        members = self._data.setdefault("members", [])
        for m in members:
            if m["qq"] == qq:
                m["name"] = name
                m["birthday_mmdd"] = birthday_mmdd
                m["groups"] = groups
                self._save()
                return
        members.append({"qq": qq, "name": name, "birthday_mmdd": birthday_mmdd, "groups": groups})
        self._save()

    def remove_member(self, qq: str) -> bool:
        members = self._data.get("members", [])
        before = len(members)
        self._data["members"] = [m for m in members if m["qq"] != qq]
        if len(self._data["members"]) < before:
            self._save()
            return True
        return False

    async def check_and_greet(self, bot: Any, llm_client: Any = None) -> list[str]:
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        self._load()
        today_mmdd = self._today_mmdd()
        today_key = self._today_key()
        sent_today = set(self._data.get("sent_log", {}).get(today_key, []))
        greeted: list[str] = []

        for member in self._data.get("members", []):
            qq = member["qq"]
            if member.get("birthday_mmdd") != today_mmdd:
                continue
            if qq in sent_today:
                continue
            groups = member.get("groups", [])
            name = member.get("name", "")
            wish_text = await self._generate_wish(name, llm_client)
            for group_id in groups:
                try:
                    msg = Message()
                    msg += MessageSegment.at(int(qq))
                    msg += MessageSegment.text(f" {wish_text}")
                    await bot.send_group_msg(group_id=int(group_id), message=msg)
                    logger.info("birthday_greeter sent to qq=%s group=%s", qq, group_id)
                except Exception as exc:
                    logger.warning("birthday_greeter send failed qq=%s group=%s: %s", qq, group_id, exc)
            greeted.append(qq)
            sent_today.add(qq)

        if greeted:
            log = self._data.setdefault("sent_log", {})
            log[today_key] = list(sent_today)
            self._cleanup_old_logs()
            self._save()

        return greeted

    async def _generate_wish(self, name: str, llm_client: Any) -> str:
        if llm_client is None or not hasattr(llm_client, "_call"):
            return f"{name}，生日快乐！🎂"
        try:
            from services.llm.llm_request import LLMRequest
            request = LLMRequest(
                task="birthday_wish",
                static_blocks=[],
                stable_blocks=[
                    "你正在给群友发生日祝福。用你自己的语气和风格写一句简短的生日祝福，"
                    "自然、真诚、有你的个人特色。不要太长，1-2句话即可。不要用引号包裹。"
                ],
                user_messages=[{"role": "user", "content": f"今天是{name}的生日，写一句祝福"}],
                max_tokens=100,
                requires_capabilities=("chat",),
            )
            result = await llm_client._call(request)
            text = ""
            if isinstance(result, dict):
                text = str(result.get("text") or result.get("content") or "")
            elif isinstance(result, str):
                text = result
            else:
                text = str(result)
            text = text.strip().strip('"').strip("'").strip()
            if text:
                return text
        except Exception as exc:
            logger.warning("birthday_greeter LLM wish failed: %s", exc)
        return f"{name}，生日快乐！🎂"
