"""Online reply segmenter for SSE token/chunk streams."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_CQ_RE = re.compile(r"\[CQ:[^\]]+\]")
_URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%\-]+", re.IGNORECASE)
_ASCII_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#\-+]*")
_SENTENCE_BREAK = set("。！？!?~～…")
_CLAUSE_BREAK = set("，,；;、")
_TRAILING = set("」』）】》”’\"')")
_OPEN_TO_CLOSE = {"（": "）", "(": ")", "「": "」", "『": "』", "【": "】", "[": "]", "《": "》"}
_CLOSE_TO_OPEN = {close: open_ for open_, close in _OPEN_TO_CLOSE.items()}
_REGISTER_FACTORS = {
    "quiet": 1.25,
    "polite_distant": 1.2,
    "neutral": 1.0,
    "neutral_default": 1.0,
    "playful": 0.86,
    "snark": 0.82,
}
_MOOD_FACTORS = {"cold": 1.25, "tired": 1.12, "neutral": 1.0, "playful": 0.9, "high": 0.88}


@dataclass(frozen=True)
class StreamingSegmenterConfig:
    min_chars: int = 6
    soft_chars: int = 24
    hard_chars: int = 54
    max_segments: int = 0


class StreamingSegmenter:
    """Incrementally split generated text as natural boundaries arrive."""

    def __init__(
        self,
        config: StreamingSegmenterConfig | None = None,
        *,
        register: Any | None = None,
        mood: Any | None = None,
    ) -> None:
        self.config = config or StreamingSegmenterConfig()
        self.register = register
        self.mood = mood
        self._buffer = ""
        self._emitted = 0

    @property
    def buffered_text(self) -> str:
        return self._buffer

    def push(self, chunk: str) -> list[str]:
        if not chunk:
            return []
        self._buffer += str(chunk)
        return self._flush_ready(final=False)

    def finish(self) -> list[str]:
        return self._flush_ready(final=True)

    def cancel(self) -> list[str]:
        drained = self.finish()
        self._buffer = ""
        return drained

    def reset(self) -> None:
        self._buffer = ""
        self._emitted = 0

    @property
    def target_chars(self) -> int:
        factor = _label_factor(self.register, _REGISTER_FACTORS) * _label_factor(self.mood, _MOOD_FACTORS)
        minimum = max(1, self.config.min_chars)
        return max(minimum, round(self.config.soft_chars * factor))

    def _flush_ready(self, *, final: bool) -> list[str]:
        segments: list[str] = []
        while self._buffer:
            if self.config.max_segments and self._emitted >= self.config.max_segments:
                break
            boundary = len(self._buffer) if final else self._find_boundary(self._buffer)
            if boundary is None:
                break
            segment = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if not segment:
                continue
            segments.append(segment)
            self._emitted += 1
        return segments

    def _find_boundary(self, text: str) -> int | None:
        protected = _protected_spans(text)
        min_chars = max(1, self.config.min_chars)
        target = self.target_chars
        for index, ch in enumerate(text):
            pos = _consume_trailing(text, index + 1)
            if pos < min_chars or not _safe_boundary(text, pos, protected):
                continue
            if ch in _SENTENCE_BREAK:
                return pos
            if ch == "\n" and (pos >= target or text[max(0, index - 1):index] in _SENTENCE_BREAK):
                return pos
            if pos >= target and ch in _CLAUSE_BREAK:
                return pos
        if len(text) >= max(min_chars, self.config.hard_chars):
            return _hard_boundary(text, min(len(text), self.config.hard_chars), min_chars, protected)
        return None


def _label_factor(value: Any | None, mapping: dict[str, float]) -> float:
    label = ""
    if isinstance(value, str):
        label = value
    elif isinstance(value, dict):
        label = str(value.get("label") or value.get("mood") or value.get("register") or "")
    elif value is not None:
        label = str(getattr(value, "label", "") or getattr(value, "mood", "") or getattr(value, "register", ""))
    return mapping.get(label.strip().lower(), 1.0)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in _CQ_RE.finditer(text)]
    spans.extend((m.start(), m.end()) for m in _URL_RE.finditer(text))
    spans.extend((m.start(), m.end()) for m in _ASCII_RE.finditer(text))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _consume_trailing(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in _TRAILING:
        pos += 1
    return pos


def _safe_boundary(text: str, pos: int, protected: list[tuple[int, int]]) -> bool:
    if any(start < pos < end for start, end in protected):
        return False
    return not _inside_unclosed_enclosure(text[:pos])


def _inside_unclosed_enclosure(text: str) -> bool:
    stack: list[str] = []
    quote = False
    for index, ch in enumerate(text):
        if ch == "\"" and (index == 0 or text[index - 1] != "\\"):
            quote = not quote
            continue
        if quote:
            continue
        if ch in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[ch])
        elif ch in _CLOSE_TO_OPEN and stack and stack[-1] == ch:
            stack.pop()
    return bool(stack or quote)


def _hard_boundary(text: str, limit: int, min_chars: int, protected: list[tuple[int, int]]) -> int | None:
    for pos in range(limit, min_chars - 1, -1):
        prev = text[pos - 1] if pos > 0 else ""
        if prev.isspace() and _safe_boundary(text, pos, protected):
            return pos
        if prev in _SENTENCE_BREAK | _CLAUSE_BREAK and _safe_boundary(text, pos, protected):
            return _consume_trailing(text, pos)
    for pos in range(limit + 1, len(text) + 1):
        prev = text[pos - 1]
        if prev.isspace() and _safe_boundary(text, pos, protected):
            return pos
    return limit if _safe_boundary(text, limit, protected) else None
