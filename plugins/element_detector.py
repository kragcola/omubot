"""ElementDetectorPlugin: 要素察觉 — 群聊中识别特定句式 → 预设回复或LLM生成。

Pipeline interceptor (priority=210), runs after EchoPlugin (200).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass

from loguru import logger

from kernel.config import ElementRule
from kernel.types import AmadeusPlugin, MessageContext, PluginContext

_log = logger.bind(channel="message_out")


@dataclass
class ElementMatch:
    """Result of element detection. reply_template is the rule's reply field
    (formatted with match groups), and use_llm indicates whether the caller
    should feed it as a system prompt to the LLM."""

    reply_template: str
    use_llm: bool


class ElementDetector:
    """Compile regex rules and match against group message text."""

    def __init__(self, rules: list[ElementRule]) -> None:
        self._rules: list[tuple[re.Pattern[str], str, bool]] = []
        for r in rules:
            with contextlib.suppress(re.error):
                self._rules.append((re.compile(r.pattern), r.reply, r.use_llm))

    def detect(self, text: str, *, nickname: str, user_id: str) -> ElementMatch | None:
        """Return ElementMatch if any rule matches, or None."""
        for pattern, reply_tmpl, use_llm in self._rules:
            m = pattern.search(text)
            if m:
                try:
                    formatted = reply_tmpl.format(
                        nickname=nickname,
                        user_id=user_id,
                        match=m.group(),
                        **m.groupdict(),
                    )
                except (KeyError, ValueError, IndexError):
                    formatted = reply_tmpl
                    for k, v in m.groupdict().items():
                        formatted = formatted.replace(f"{{{k}}}", str(v))
                    formatted = formatted.replace("{nickname}", nickname)
                    formatted = formatted.replace("{user_id}", user_id)
                    formatted = formatted.replace("{match}", m.group())
                return ElementMatch(reply_template=formatted, use_llm=use_llm)
        return None


class ElementDetectorPlugin(AmadeusPlugin):
    name = "element_detector"
    description = "要素察觉：识别特定句式并回复（预设模板或LLM生成）"
    version = "1.0.0"
    priority = 210

    async def on_startup(self, ctx: PluginContext) -> None:
        config = ctx.config
        if config.element_detection.enabled and config.element_detection.rules:
            self._detector = ElementDetector(config.element_detection.rules)
            logger.info("element detection enabled | rules={}", len(config.element_detection.rules))
        else:
            self._detector = None
        self._humanizer = ctx.humanizer
        self._scheduler = ctx.scheduler
        self._timeline = ctx.timeline
        self._llm_client = ctx.llm_client
        self._identity_mgr = ctx.identity_mgr

    async def on_message(self, ctx: MessageContext) -> bool:
        if ctx.is_private or self._detector is None:
            return False

        plain_text: str = ctx.raw_message.get("plain_text", "")
        if not plain_text:
            return False

        match = self._detector.detect(
            plain_text, nickname=ctx.nickname, user_id=ctx.user_id,
        )
        if match is None:
            return False

        group_id = ctx.group_id or ""
        self._scheduler.cancel_debounce(group_id)
        self._timeline.add(
            group_id,
            role="user",
            speaker=f"{ctx.nickname}({ctx.user_id})",
            content=plain_text,
            message_id=ctx.message_id or 0,
        )

        if match.use_llm:
            identity = self._identity_mgr.resolve()
            directive = "直接输出回复内容，禁止括号、禁止内心独白、禁止解释。"
            system_text = f"你是{identity.name}。{directive}\n\n{match.reply_template}"
            system = [{"type": "text", "text": system_text}]
            msgs = [{"role": "user", "content": plain_text}]
            reply_text = ""
            try:
                result = await self._llm_client._call(system, msgs, tools=None, max_tokens=256)
                reply_text = (result.get("text") or "").strip()
            except Exception:
                logger.exception("element llm call failed")
            if not reply_text:
                reply_text = "确实 (｡･ω･｡)"
            await self._humanizer.delay(reply_text)
            await ctx.bot.send_group_msg(group_id=int(group_id), message=reply_text)
            self._timeline.add(
                group_id, role="assistant", speaker="", content=reply_text, message_id=0,
            )
            _log.info(
                "element+llm | group={} {}({}) reply={!r}",
                group_id, ctx.nickname, ctx.user_id, reply_text[:80],
            )
        else:
            reply_text = match.reply_template
            await self._humanizer.delay(reply_text)
            await ctx.bot.send_group_msg(group_id=int(group_id), message=reply_text)
            self._timeline.add(
                group_id, role="assistant", speaker="", content=reply_text, message_id=0,
            )
            _log.info("element | group={} {}({}) matched", group_id, ctx.nickname, ctx.user_id)

        return True
