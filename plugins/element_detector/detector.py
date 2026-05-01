"""要素察觉：群聊中识别特定句子 → 预设格式回复 或 LLM 生成。

与复读 (echo) 同级优先，在 LLM 调度之前触发。
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass

from kernel.config import ElementRule


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
