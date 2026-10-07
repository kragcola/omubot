"""Bounded text segmentation; transport and persistence remain at their owners."""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass

from .types import OperationError

_MAX_REPLY_CHARS = 2000
_MAX_SEGMENT_CHARS = 2000
_MAX_SEGMENTS = 8


def humanizer_delay(text: str, multiplier: float) -> float:
    """Legacy first-send typing pause; Climate alone owns its runtime factor."""
    extra = len(text) * 0.02 * random.uniform(0.8, 1.2)
    if any("\U0001f300" <= char <= "\U0001faff" for char in text):
        extra = max(extra, 1.0)
    return (random.uniform(0.5, 3.0) + extra) * multiplier


def inter_segment_delay(previous: str, register: str | None, multiplier: float) -> float:
    """Natural segment pause with one frozen Climate factor, without legacy mood."""
    label = {"affectionate": "playful", "distant": "polite_distant", "serious": "polite_distant"}.get(
        register or "", register
    )
    factor = {"quiet": 1.5, "polite_distant": 1.65, "playful": 0.7, "snark": 0.63}.get(
        label or "", 1.0
    )
    base = sum("\u4e00" <= char <= "\u9fff" for char in previous) * 0.15
    base += sum(char.isascii() and char.isalnum() for char in previous) * 0.07
    return max(0.5, min(3.0, base * factor)) * multiplier

_URL_RE = re.compile(r"(?i)(?<![\w])(?:https?://|ftp://|www\.)[^\s<>\u3000]+")
_CQ_RE = re.compile(r"\[CQ:[^\]\r\n]{1,4096}\]")
_FENCED_CODE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\r\n]+`")
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]\r\n]{1,256}\]\([^\)\r\n]{1,2048}\)")
_PUNCTUATION_RUN_RE = re.compile(r"[。！？!?…]{2,}")
_QUOTE_RE = re.compile(
    r"「[^」\r\n]*」|『[^』\r\n]*』|《[^》\r\n]*》|"
    r"“[^”\r\n]*”|‘[^’\r\n]*’|\"[^\"\r\n]+\"|'[^'\r\n]+'|"
    r"【引用[^】\r\n]*】|\[引用[^\]\r\n]*\]"
)
_PROPER_NAME_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:"
    r"[A-Z][A-Za-z0-9]*(?:[!?！？…]{2,}|[-_.][A-Za-z0-9]+(?:[!?！？…]+)?)"
    r"|[A-Z][A-Za-z0-9]*(?:[!?！？…]+)?"
    r"(?:[ \t]+[A-Z][A-Za-z0-9]*(?:[!?！？…]+)?)+"
    r")"
    r"(?![A-Za-z0-9])"
)

_URL_TRAILING = frozenset("，。！？；：、,;:!?)]}》」』】）")
_SENTENCE_CHARS = frozenset("。！？!?…")
_PAUSE_CHARS = frozenset("，,；;、：:")
_CLOSING_CHARS = frozenset("”’\"'」』》】)）]}")

_STREAM_PREFIX_MISMATCH = "stream_rewrite"
_STREAM_CANCELLED = "cancelled"
_STREAM_CLOSED = "stream_closed"

_OPEN_TO_CLOSE = {
    "（": "）",
    "(": ")",
    "「": "」",
    "『": "』",
    "【": "】",
    "[": "]",
    "《": "》",
    "{": "}",
}
_CLOSE_TO_OPEN = {close: open_ for open_, close in _OPEN_TO_CLOSE.items()}
_SYMMETRIC_QUOTES = frozenset({'"', "'"})
_PAIRED_QUOTES = {"“": "”", "‘": "’"}


def _terminal_runs(text: str, protected: list[tuple[int, int]]) -> dict[int, int]:
    # Only a standalone punctuation atom contributes a terminal boundary.
    # A run merged into a name, quote, code or URL keeps that atom's semantics.
    return {start: end for start, end in protected
            if all(char in _SENTENCE_CHARS for char in text[start:end])}


def _sentence_boundaries(text: str, protected: list[tuple[int, int]]) -> set[int]:
    """Return only sentence/newline boundaries outside protected spans.

    ``_boundary_metadata`` also exposes clause boundaries for the regular
    natural splitter.  Streaming must be more conservative: a clause is not
    enough evidence that the model has finished a sendable prefix.
    """

    _, protected_char, _ = _boundary_metadata(text, protected)
    boundaries: set[int] = set()
    index = 0
    while index < len(text):
        if protected_char[index]:
            index += 1
            continue
        character = text[index]
        if character == "\n":
            boundaries.add(index + 1)
            index += 1
            continue
        if character not in _SENTENCE_CHARS:
            index += 1
            continue

        end = index + 1
        while end < len(text) and text[end] in _SENTENCE_CHARS and not protected_char[end]:
            end += 1
        while end < len(text) and text[end] in _CLOSING_CHARS and not protected_char[end]:
            end += 1
        boundaries.add(end)
        index = end
    return boundaries


def _unclosed_protected_tail(text: str, protected: list[tuple[int, int]]) -> bool:
    """Conservatively detect a protected construct that crosses ``text``'s end.

    Complete spans are skipped.  The remaining lightweight scan handles the
    constructs whose closing token may arrive in a later delta.  Returning
    ``True`` on uncertainty is intentional: an incremental sender must keep
    text buffered rather than risk cutting a URL, CQ marker, quote, or code
    span.
    """

    complete: dict[int, int] = {start: end for start, end in protected}
    stack: list[str] = []
    symmetric_quote = False
    fenced = False
    inline_code = False
    index = 0

    while index < len(text):
        complete_end = complete.get(index)
        if complete_end is not None:
            index = complete_end
            continue

        if text.startswith("```", index):
            fenced = not fenced
            index += 3
            continue
        if fenced:
            index += 1
            continue
        if text[index] == "`":
            inline_code = not inline_code
            index += 1
            continue
        if inline_code:
            index += 1
            continue

        character = text[index]
        if character in _PAIRED_QUOTES:
            stack.append(_PAIRED_QUOTES[character])
        elif stack and character == stack[-1]:
            stack.pop()
        elif character in _SYMMETRIC_QUOTES:
            # An apostrophe between letters is ordinary word punctuation, not
            # an opening quote (for example ``it's``).
            previous = text[index - 1] if index else ""
            following = text[index + 1] if index + 1 < len(text) else ""
            if character == "'" and previous.isalnum() and following.isalnum():
                index += 1
                continue
            symmetric_quote = not symmetric_quote
        elif character in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[character])
        elif character in _CLOSE_TO_OPEN:
            if stack and stack[-1] == character:
                stack.pop()

        index += 1

    if stack or symmetric_quote or fenced or inline_code:
        return True

    # A CQ-like marker can be incomplete even though it is not a complete
    # ``_CQ_RE`` span yet.  Restrict this check to the marker prefix so a
    # regular bracketed sentence is not kept open forever.
    marker_start = text.casefold().rfind("[cq:")
    if marker_start >= 0 and "]" not in text[marker_start:]:
        return True

    # A markdown link may leave an opening destination parenthesis pending.
    # The general delimiter scan catches most cases; this explicit check also
    # covers ``[label](`` when the label's brackets were a completed span.
    link_start = text.rfind("](")
    if link_start >= 0 and ")" not in text[link_start + 2 :]:
        return True

    # A URL ending exactly at the candidate is not itself a sentence boundary,
    # but this guard prevents a punctuation-looking boundary from being
    # accepted if the URL parser considers the whole tail one token.
    for start, end in _protected_spans(text):
        if end == len(text) and text[start:end].lower().startswith(("http://", "https://", "ftp://", "www.")):
            return True
    return False


def _safe_stream_boundary(
    text: str, end: int, protected: list[tuple[int, int]], protected_char: bytearray
) -> bool:
    if not 0 < end < len(text) or protected_char[end]:
        return False
    if any(start < end < span_end for start, span_end in protected):
        return False
    return not _unclosed_protected_tail(text[:end], _protected_spans(text[:end]))


def _is_content_character(character: str) -> bool:
    return not character.isspace() and not unicodedata.category(character).startswith("P")


def _has_content(text: str) -> bool:
    return any(_is_content_character(character) for character in text)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in (
        _URL_RE,
        _CQ_RE,
        _FENCED_CODE_RE,
        _INLINE_CODE_RE,
        _MARKDOWN_LINK_RE,
        _PUNCTUATION_RUN_RE,
        _QUOTE_RE,
        _PROPER_NAME_RE,
    ):
        for match in pattern.finditer(text):
            start, end = match.span()
            if pattern is _URL_RE:
                while end > start and text[end - 1] in _URL_TRAILING:
                    end -= 1
            if start < end:
                spans.append((start, end))

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _boundary_metadata(
    text: str, spans: list[tuple[int, int]]
) -> tuple[bytearray, bytearray, dict[int, int]]:
    blocked = bytearray(len(text) + 1)
    protected_char = bytearray(len(text))
    for start, end in spans:
        protected_char[start:end] = b"\x01" * (end - start)
        if end - start > 1:
            blocked[start + 1 : end] = b"\x01" * (end - start - 1)

    boundaries: dict[int, int] = {}
    terminal_runs = _terminal_runs(text, spans)

    def mark(position: int, weight: int) -> None:
        if not blocked[position]:
            boundaries[position] = max(boundaries.get(position, 0), weight)

    index = 0
    while index < len(text):
        if protected_char[index]:
            end = terminal_runs.get(index)
            if end is not None:
                while end < len(text) and text[end] in _CLOSING_CHARS and not protected_char[end]:
                    end += 1
                while end < len(text) and text[end].isspace() and not protected_char[end]:
                    end += 1
                mark(end, 3)
                index = terminal_runs[index]
                continue
            index += 1
            continue
        character = text[index]
        if character == "\n":
            mark(index + 1, 4)
        elif character in _SENTENCE_CHARS:
            end = index + 1
            while end < len(text) and text[end] in _SENTENCE_CHARS and not protected_char[end]:
                end += 1
            while end < len(text) and text[end] in _CLOSING_CHARS and not protected_char[end]:
                end += 1
            mark(end, 3)
            while end < len(text) and text[end] in " \t" and not protected_char[end]:
                mark(end + 1, 3)
                end += 1
        elif character in _PAUSE_CHARS:
            mark(index + 1, 2)
            if index + 1 < len(text) and text[index + 1] in " \t":
                mark(index + 2, 2)
        index += 1
    return blocked, protected_char, boundaries


def split_reply(
    text: str, segment_chars: int, max_segments: int, *, preserve_newlines: bool = False,
) -> list[str]:
    if (
        not text.strip()
        or not _has_content(text)
        or len(text) > _MAX_REPLY_CHARS
        or not 1 <= segment_chars <= _MAX_SEGMENT_CHARS
        or not 1 <= max_segments <= _MAX_SEGMENTS
    ):
        raise OperationError("output_limit")

    spans = _protected_spans(text)
    if any(end - start > segment_chars for start, end in spans):
        raise OperationError("output_limit")
    if not preserve_newlines and len(text) <= segment_chars:
        return [text]

    blocked, _, boundaries = _boundary_metadata(text, spans)
    length = len(text)
    next_content = [length] * (length + 1)
    next_index = length
    for index in range(length - 1, -1, -1):
        if _is_content_character(text[index]):
            next_index = index
        next_content[index] = next_index

    previous_open = [-1] * (length + 1)
    last_open = -1
    for index, is_blocked in enumerate(blocked):
        if not is_blocked:
            last_open = index
        previous_open[index] = last_open

    def furthest_legal_end(position: int) -> int | None:
        end = previous_open[min(length, position + segment_chars)]
        while end < length and end > position and next_content[end] >= length:
            end = previous_open[end - 1]
        if end <= position or next_content[position] >= end:
            return None
        return end

    def can_finish(position: int, slots: int) -> bool:
        if position == length:
            return True
        if slots <= 0 or length - position > slots * segment_chars:
            return False
        for _ in range(slots):
            end = furthest_legal_end(position)
            if end is None:
                return False
            position = end
            if position == length:
                return True
        return False

    def minimum_segments(position: int) -> int | None:
        for slots in range(1, max_segments + 1):
            if can_finish(position, slots):
                return slots
        return None

    segments: list[str] = []
    offset = 0
    while offset < length:
        slots = max_segments - len(segments)
        minimum = minimum_segments(offset)
        if minimum is None or minimum > slots:
            raise OperationError("output_limit")

        max_end = min(length, offset + segment_chars)
        selected_end: int | None = None
        natural_start = min(max_end, offset + max(1, segment_chars // 2))

        # Model-authored line breaks describe chat bubbles, even below the
        # hard character cap. Protected spans and the remaining batch budget
        # still decide whether a break is sendable.
        if preserve_newlines:
            for end in range(offset + 1, max_end + 1):
                if (boundaries.get(end) == 4 and next_content[offset] < end
                        and can_finish(end, slots - 1)):
                    selected_end = end
                    break

        for start in (natural_start, offset + 1):
            if selected_end is not None:
                break
            for end in range(max_end, start - 1, -1):
                weight = boundaries.get(end, 0)
                if not weight or blocked[end] or next_content[offset] >= end:
                    continue
                if not can_finish(end, slots - 1):
                    continue
                candidate_minimum = minimum_segments(end)
                if candidate_minimum is None:
                    continue
                if weight < 3 and candidate_minimum + 1 > minimum:
                    continue
                selected_end = end
                break

        if selected_end is None:
            for end in range(max_end, offset, -1):
                if blocked[end] or next_content[offset] >= end:
                    continue
                if can_finish(end, slots - 1):
                    selected_end = end
                    break

        if selected_end is None:
            raise OperationError("output_limit")
        segments.append(text[offset:selected_end])
        offset = selected_end

    if not segments or len(segments) > max_segments:
        raise OperationError("output_limit")
    return segments


@dataclass(frozen=True)
class ReplyLayout:
    """A complete text candidate and its wire bubbles, before any admission."""

    text: str
    segments: tuple[str, ...]


def layout_reply(text: str, segment_chars: int, max_segments: int) -> ReplyLayout:
    """Project authored line boundaries without sending layout separators.

    The raw splitter retains its lossless contract for streaming prefixes,
    echoes and literal diagnostics. Complete chat candidates instead use this
    projection: blank lines are separators, while code/quote interiors remain
    literal. Capacity is checked for the whole candidate, never by dropping a tail.
    """
    if len(text) > _MAX_REPLY_CHARS:
        raise OperationError("output_limit")
    protected = _protected_spans(text)
    pieces: list[str] = []
    offset = 0
    for match in re.finditer(r"\n(?:[ \t\r]*\n)+", text):
        if any(start < match.end() and end > match.start() for start, end in protected):
            continue
        pieces.extend((text[offset:match.start()], "\n"))
        offset = match.end()
    pieces.append(text[offset:])
    normalized = "".join(pieces).strip("\r\n")
    segments = split_reply(normalized, segment_chars, max_segments, preserve_newlines=True)
    return ReplyLayout(normalized, tuple(segment.strip("\r\n") for segment in segments))


class IncrementalSegmenter:
    """Bounded, conservative segmentation for ordered model text deltas.

    The class owns only text buffering.  It does not send, call a model, or
    make a delivery decision.  ``push`` emits a prefix only after a complete
    sentence/newline boundary has a following suffix that lets us rule out an
    open protected span.  ``finish`` validates the provider's final text and
    supplies the un-emitted suffix to the existing natural splitter.
    """

    def __init__(
        self,
        segment_chars: int = _MAX_SEGMENT_CHARS,
        max_segments: int = _MAX_SEGMENTS,
    ) -> None:
        if (
            not 1 <= segment_chars <= _MAX_SEGMENT_CHARS
            or not 1 <= max_segments <= _MAX_SEGMENTS
        ):
            raise OperationError("output_limit")
        self.segment_chars = segment_chars
        self.max_segments = max_segments
        self._text = ""
        self._emitted_offset = 0
        self._emitted_segments: list[str] = []
        self._cancelled = False
        self._finished = False

    @property
    def buffered_text(self) -> str:
        """Return text received but not returned from ``push``/``finish``."""

        return self._text[self._emitted_offset :]

    @property
    def emitted_text(self) -> str:
        return self._text[: self._emitted_offset]

    @property
    def emitted_segments(self) -> tuple[str, ...]:
        return tuple(self._emitted_segments)

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def finished(self) -> bool:
        return self._finished

    def push(self, delta: object) -> list[str]:
        """Append one ordered text delta and return newly safe complete segments."""

        self._ensure_open()
        if not isinstance(delta, str):
            raise OperationError("invalid_delta")
        if not delta:
            return []
        if len(self._text) + len(delta) > _MAX_REPLY_CHARS:
            raise OperationError("output_limit")

        candidate_text = self._text + delta
        emitted: list[str] = []
        emitted_offset = self._emitted_offset
        emitted_count = len(self._emitted_segments)

        while emitted_count < self.max_segments and emitted_offset < len(candidate_text):
            pending = candidate_text[emitted_offset:]
            protected = _protected_spans(pending)
            blocked, protected_char, _ = _boundary_metadata(pending, protected)
            boundaries = sorted(_sentence_boundaries(pending, protected))
            selected_end: int | None = None

            for end in boundaries:
                if end > self.segment_chars:
                    break
                if (
                    blocked[end]
                    or not _has_content(pending[:end])
                    or not _has_content(pending[end:])
                ):
                    continue
                if not _safe_stream_boundary(pending, end, protected, protected_char):
                    continue
                selected_end = end
                break

            if selected_end is None:
                break

            segment = pending[:selected_end]
            emitted.append(segment)
            emitted_offset += selected_end
            emitted_count += 1

        self._text = candidate_text
        if emitted:
            self._emitted_offset = emitted_offset
            self._emitted_segments.extend(emitted)
        return emitted

    def validate_final(self, final_text: object) -> str:
        """Validate the original provider text without allocating delivery slots."""
        if self._cancelled:
            raise OperationError(_STREAM_CANCELLED)
        if not isinstance(final_text, str):
            raise OperationError("invalid_final_text")
        if len(final_text) > _MAX_REPLY_CHARS:
            raise OperationError("output_limit")

        emitted_prefix = self.emitted_text
        if not final_text.startswith(emitted_prefix):
            raise OperationError(_STREAM_PREFIX_MISMATCH)
        return final_text

    def finish(self, final_text: object) -> list[str]:
        """Validate final model text and return each still-unsent legal segment."""

        if self._finished:
            return []
        final_text = self.validate_final(final_text)
        emitted_prefix = self.emitted_text

        if not self._emitted_segments:
            segments = split_reply(
                final_text, self.segment_chars, self.max_segments, preserve_newlines=True,
            )
        else:
            remaining = final_text[len(emitted_prefix) :]
            if not remaining:
                segments = []
            else:
                remaining_slots = self.max_segments - len(self._emitted_segments)
                if remaining_slots < 1:
                    raise OperationError("output_limit")
                segments = split_reply(
                    remaining, self.segment_chars, remaining_slots, preserve_newlines=True,
                )

        self._text = final_text
        self._emitted_offset = len(final_text)
        self._emitted_segments.extend(segments)
        self._finished = True
        return segments

    def cancel(self) -> list[str]:
        """Terminate the stream without flushing its un-emitted tail."""

        if self._finished:
            return []
        self._cancelled = True
        self._text = self.emitted_text
        return []

    def _ensure_open(self) -> None:
        if self._cancelled:
            raise OperationError(_STREAM_CANCELLED)
        if self._finished:
            raise OperationError(_STREAM_CLOSED)


# The streaming name is kept as a small compatibility alias for callers that
# use the legacy terminology; the new class intentionally has the stricter
# final-text contract above.
StreamingSegmenter = IncrementalSegmenter
