"""Post-LLM persona drift detector with bounded repair."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from services.llm.anchor_reinjection import _token_set
from services.llm.persona_patterns import DECLARATION_PATTERNS

DriftAction = Literal["pass", "repair", "block"]


@dataclass(frozen=True, slots=True)
class DriftScore:
    raw: float
    ewma: float
    action: DriftAction


class DriftDetector:
    def __init__(
        self,
        *,
        bot_name: str,
        personality: str = "",
        voice_text: str = "",
        examples_text: str = "",
        lambda_: float = 0.3,
        theta_repair: float = 0.6,
        theta_block: float = 0.85,
        repair_max_retries: int = 1,
        enabled: bool = False,
    ) -> None:
        self._enabled = bool(enabled)
        self._bot_name = str(bot_name or "").strip()
        self._lambda = max(0.0, min(1.0, float(lambda_)))
        self._theta_repair = max(0.0, min(1.0, float(theta_repair)))
        self._theta_block = max(self._theta_repair, min(1.0, float(theta_block)))
        self._repair_max_retries = max(0, int(repair_max_retries))
        baseline_text = "\n".join(
            part.strip()
            for part in (
                self._bot_name,
                str(personality or "").strip(),
                str(voice_text or "").strip(),
                str(examples_text or "").strip(),
            )
            if str(part or "").strip()
        )
        self._baseline_tokens = _token_set(baseline_text)
        self._baseline_length = max(1, len(self._baseline_tokens))
        self._state: dict[str, float] = {}
        self._ai_markers = ("我是ai", "语言模型", "anthropic", "openai", "claude", "gpt")
        self._member_markers = ("wxs", "w×s", "成员", "设定", "角色", "身份")

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._baseline_tokens)

    @property
    def repair_max_retries(self) -> int:
        return self._repair_max_retries

    def _scope_key(self, group_id: str | None, session_id: str = "") -> str:
        if group_id:
            return f"group:{group_id}"
        return f"session:{session_id or 'default'}"

    def _raw_score(self, reply_text: str) -> float:
        reply_tokens = _token_set(reply_text)
        if not reply_tokens or not self._baseline_tokens:
            return 0.0
        lowered = reply_text.lower()
        overlap = len(reply_tokens & self._baseline_tokens) / max(1, len(reply_tokens))
        missing_baseline = 1.0 - min(1.0, len(reply_tokens & self._baseline_tokens) / self._baseline_length)
        score = max(0.0, min(0.45, 0.35 * missing_baseline + 0.2 * (1.0 - overlap)))
        if any(pattern.search(reply_text) for pattern in DECLARATION_PATTERNS):
            score = max(score, 0.72)
        if self._bot_name and re.search(rf"我(?:是|叫){re.escape(self._bot_name)}", reply_text):
            score = max(score, 0.74)
        if (
            self._bot_name
            and self._bot_name in reply_text
            and any(marker in lowered for marker in self._member_markers)
        ):
            score = max(score, 0.76)
        if any(marker in lowered for marker in self._ai_markers):
            score = max(score, 0.92)
        return max(0.0, min(1.0, score))

    def evaluate(
        self,
        reply_text: str,
        *,
        group_id: str | None = None,
        session_id: str = "",
    ) -> DriftScore:
        if not self.enabled:
            return DriftScore(raw=0.0, ewma=0.0, action="pass")
        key = self._scope_key(group_id, session_id=session_id)
        raw = self._raw_score(reply_text)
        previous = self._state.get(key, 0.0)
        ewma = raw if previous <= 0 else (self._lambda * raw + (1.0 - self._lambda) * previous)
        if ewma >= self._theta_block:
            action: DriftAction = "block"
        elif ewma >= self._theta_repair:
            action = "repair"
        else:
            action = "pass"
        self._state[key] = ewma
        return DriftScore(raw=raw, ewma=ewma, action=action)

    def build_repair_instruction(self, reply_text: str) -> str:
        identity_hint = self._bot_name or "你自己"
        return (
            f"请保留原回复的事实、态度、数字和意图，但重新表达得更像{identity_hint}本人在自然聊天。\n"
            "不要自我介绍，不要解释设定，不要提及 AI / 模型 / prompt，只输出改写后的可直接发送文本。\n\n"
            f"原回复：\n{reply_text}"
        )

    def reset(self, *, group_id: str | None = None, session_id: str = "") -> None:
        key = self._scope_key(group_id, session_id=session_id)
        self._state.pop(key, None)

    async def evaluate_cancel_safe(
        self,
        reply_text: str,
        *,
        group_id: str | None = None,
        session_id: str = "",
    ) -> DriftScore:
        if not self.enabled:
            return DriftScore(raw=0.0, ewma=0.0, action="pass")
        key = self._scope_key(group_id, session_id=session_id)
        raw = self._raw_score(reply_text)
        previous = self._state.get(key, 0.0)
        ewma = raw if previous <= 0 else (self._lambda * raw + (1.0 - self._lambda) * previous)
        if ewma >= self._theta_block:
            action: DriftAction = "block"
        elif ewma >= self._theta_repair:
            action = "repair"
        else:
            action = "pass"
        await asyncio.sleep(0)
        self._state[key] = ewma
        return DriftScore(raw=raw, ewma=ewma, action=action)
