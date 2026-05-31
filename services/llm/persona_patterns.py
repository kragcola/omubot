"""Shared persona declaration patterns for runtime and compile-time guards."""

from __future__ import annotations

import re
from typing import Final

DECLARATION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"我(?:就)?是(?!说\b).{1,8}(?:，|。|！|!|$)"),
    re.compile(r"作为\s*(?:WxS|W×S|wxs).{0,10}(?:成员|一员)", re.IGNORECASE),
    re.compile(r"我(?:的)?(?:名字|本名)(?:是|叫).{1,10}"),
    re.compile(r"(?:我是|作为)(?:一个?)?(?:AI|人工智能|语言模型|机器人)", re.IGNORECASE),
    re.compile(r"(?:我是|我叫)\s*(?:Claude|GPT|Anthropic|OpenAI)", re.IGNORECASE),
    re.compile(r"我的(?:设定|人设|角色|身份)(?:是|为)"),
)
