from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

_SEGMENT_SEP = "---cut---"
_BLANK_LINE_RE = re.compile(r"\n{2,}")
_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]+\]")
_CQ_KV_FIX_RE = re.compile(r",(\w+):")
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#\-+]*")
_URL_TOKEN_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_TRAILING_CLAUSE = "，；：、,;:"
_SENTENCE_ENDING = set("。！？～…」』）”’》】\"!?~)")
_SENTENCE_BREAK = set("。！？～…!?~")
_CLAUSE_BREAK = set("，；：、,;:")
_POSTFIX_CLOSERS = set("」』）”’》】)]}\"'")
_REPEATED_PUNCTUATION = {"—", "…"}
_CONTINUATION_PREFIX = set("，、。！？；：,.!?;:的地得了着过")
_OPEN_TO_CLOSE = {
    "（": "）",
    "(": ")",
    "「": "」",
    "『": "』",
    "《": "》",
    "【": "】",
    "[": "]",
    "{": "}",
    "“": "”",
    "‘": "’",
}
_CLOSE_TO_OPEN = {close: open_ for open_, close in _OPEN_TO_CLOSE.items()}
_SYMMETRIC_QUOTES = {"\""}

BreakReason = Literal[
    "explicit_cut",
    "paragraph_break",
    "newline_sentence_break",
    "semantic_newline",
    "sentence_break",
    "clause_break",
    "token_boundary",
    "hard_limit",
    "coalesced_overflow",
    "soft_limit",
]
SegmentationLimitStatus = Literal["none", "soft", "hard", "soft_then_hard"]
BoundaryBackend = Literal["pysbd_hybrid", "local"]


@dataclass(frozen=True)
class Segment:
    text: str
    reason: BreakReason


@dataclass(frozen=True)
class ReplySegmentationConfig:
    enabled: bool = True
    max_segment_chars: int = 20
    min_segment_chars: int = 6
    max_send_segments: int = 0
    soft_max_send_segments: int = 0
    soft_limit_notice: str = "先说到这里啦，不然我要刷屏了☆"
    boundary_backend: BoundaryBackend = "pysbd_hybrid"
    prefer_sentence_break: bool = True
    preserve_ascii_tokens: bool = True
    merge_short_tail: bool = True
    first_segment_humanize: str = "skip"
    later_segment_humanize: str = "normal"
    inter_segment_delay_s: float = 0.8


@dataclass(frozen=True)
class ReplySegmentationResult:
    segments: list[Segment]
    raw_count: int
    capped_count: int
    strategy: str
    break_reasons: list[BreakReason]
    limit_status: SegmentationLimitStatus = "none"

    @property
    def texts(self) -> list[str]:
        return [segment.text for segment in self.segments]


def fix_cq_codes(text: str) -> str:
    """Normalize CQ code params: [CQ:reply,id:123] -> [CQ:reply,id=123]."""
    return _CQ_CODE_RE.sub(lambda m: _CQ_KV_FIX_RE.sub(r",\1=", m.group(0)), text)


def _clean_text(text: str) -> str:
    return _BLANK_LINE_RE.sub("\n", text).strip()


def _is_ascii_token_char(ch: str) -> bool:
    return bool(ch) and ch.isascii() and (ch.isalnum() or ch in "._:/#-+")


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _CQ_CODE_RE.finditer(text):
        spans.append((match.start(), match.end()))
    for match in _URL_TOKEN_RE.finditer(text):
        spans.append((match.start(), match.end()))
    for match in _ASCII_TOKEN_RE.finditer(text):
        spans.append((match.start(), match.end()))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _find_ascii_token_bounds(text: str, index: int) -> tuple[int, int] | None:
    if index < 0 or index >= len(text):
        return None

    for start, end in _protected_spans(text):
        if start <= index < end:
            return start, end
    return None


def _inside_protected_span(index: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < index < end for start, end in spans)


def _has_unclosed_enclosure(text: str) -> bool:
    stack: list[str] = []
    quote: str | None = None
    for index, ch in enumerate(text):
        if ch in _SYMMETRIC_QUOTES and (index == 0 or text[index - 1] != "\\"):
            quote = None if quote == ch else ch
            continue
        if quote:
            continue
        if ch in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[ch])
        elif ch in _CLOSE_TO_OPEN and stack and stack[-1] == ch:
            stack.pop()
    return bool(stack or quote)


def _inside_unclosed_enclosure_at(text: str, index: int) -> bool:
    return _has_unclosed_enclosure(text[:index])


def _should_merge_line(prev: str, line: str, min_segment_chars: int) -> bool:
    if not prev or not line:
        return False
    if _has_unclosed_enclosure(prev):
        return True
    if line[0] in _CONTINUATION_PREFIX:
        return True
    if prev[-1] in _TRAILING_CLAUSE:
        return True
    if _is_ascii_token_char(prev[-1]) and _is_ascii_token_char(line[0]):
        return True
    return len(line) < min_segment_chars and prev[-1] not in _SENTENCE_ENDING


def _safe_boundary(text: str, index: int, protected: list[tuple[int, int]]) -> bool:
    return not _inside_protected_span(index, protected) and not _inside_unclosed_enclosure_at(text, index)


@lru_cache(maxsize=1)
def _pysbd_segmenter() -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        import pysbd

    return pysbd.Segmenter(language="zh", clean=False, char_span=True)


def _pysbd_sentence_boundaries(text: str) -> list[int]:
    boundaries: list[int] = []
    try:
        spans = _pysbd_segmenter().segment(text)
    except Exception:
        return boundaries
    for span in spans:
        end = getattr(span, "end", None)
        if isinstance(end, int) and 0 < end < len(text):
            boundaries.append(end)
    return boundaries


def _adjust_cut_for_ascii_token(text: str, cut: int) -> int:
    if cut <= 0 or cut >= len(text):
        return cut
    bounds = _find_ascii_token_bounds(text, cut)
    if bounds and bounds[0] < cut < bounds[1]:
        return bounds[0]
    bounds = _find_ascii_token_bounds(text, cut - 1)
    if bounds and bounds[0] < cut < bounds[1]:
        return bounds[1]
    return cut


def _adjust_cut_for_repeated_punctuation(text: str, cut: int) -> int:
    if cut <= 0 or cut >= len(text):
        return cut
    ch = text[cut - 1]
    if ch not in _REPEATED_PUNCTUATION or text[cut] != ch:
        return cut

    right = cut + 1
    while right < len(text) and text[right] == ch:
        right += 1
    return right


def _split_explicit_segments(text: str) -> list[Segment]:
    text = fix_cq_codes(text)
    segments: list[Segment] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == _SEGMENT_SEP:
            seg = "\n".join(current).strip()
            if seg:
                segments.append(Segment(_clean_text(seg), "explicit_cut"))
            current = []
        else:
            current.append(line)
    last = "\n".join(current).strip()
    if last:
        segments.append(Segment(_clean_text(last), "explicit_cut"))
    return segments or [Segment(_clean_text(text), "explicit_cut")]


def _sentence_break_index(text: str, start: int = 0) -> int | None:
    if not text:
        return None
    for i in range(start, len(text)):
        if text[i] not in _SENTENCE_BREAK:
            continue
        j = i + 1
        if text[i] in _REPEATED_PUNCTUATION:
            while j < len(text) and text[j] == text[i]:
                j += 1
        while j < len(text) and text[j] in _POSTFIX_CLOSERS:
            j += 1
        return j
    return None


def _iter_boundary_candidates(text: str, boundary_backend: BoundaryBackend) -> list[tuple[int, BreakReason]]:
    candidates: list[tuple[int, BreakReason]] = []
    seen: set[tuple[int, BreakReason]] = set()
    protected = _protected_spans(text)

    for match in re.finditer(r"\n{2,}", text):
        if not _safe_boundary(text, match.start(), protected):
            continue
        candidate = (match.start(), "paragraph_break")
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    for match in re.finditer(r"\n+", text):
        if not _safe_boundary(text, match.start(), protected):
            continue
        candidate = (match.start(), "semantic_newline")
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
        if match.start() > 0:
            prev = text[match.start() - 1]
            if prev in _SENTENCE_ENDING:
                candidate = (match.start(), "newline_sentence_break")
                if candidate not in seen:
                    candidates.append(candidate)
                    seen.add(candidate)

    idx = 0
    while idx < len(text):
        boundary = _sentence_break_index(text, idx)
        if boundary is None:
            break
        if not _safe_boundary(text, boundary, protected):
            idx = boundary
            continue
        candidate = (boundary, "sentence_break")
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
        idx = boundary

    if boundary_backend == "pysbd_hybrid":
        for boundary in _pysbd_sentence_boundaries(text):
            if not _safe_boundary(text, boundary, protected):
                continue
            candidate = (boundary, "sentence_break")
            if candidate not in seen:
                candidates.append(candidate)
                seen.add(candidate)

    for i, ch in enumerate(text):
        if ch in _CLAUSE_BREAK:
            if not _safe_boundary(text, i + 1, protected):
                continue
            candidate = (i + 1, "clause_break")
            if candidate not in seen:
                candidates.append(candidate)
                seen.add(candidate)

    candidates.sort(key=lambda item: item[0])
    return candidates


def _merge_lines(text: str, min_segment_chars: int) -> str:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return text

    merged_paragraphs: list[str] = []
    for para in paragraphs:
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue
        merged: list[str] = []
        for line in lines:
            if not merged:
                merged.append(line)
                continue
            prev = merged[-1]
            if _should_merge_line(prev, line, min_segment_chars):
                merged[-1] += line
            else:
                merged.append(line)
        merged_paragraphs.append("\n".join(merged))
    return "\n\n".join(merged_paragraphs)


def _best_cut(
    text: str,
    max_len: int,
    min_len: int,
    prefer_sentence_break: bool,
    preserve_ascii_tokens: bool,
    boundary_backend: BoundaryBackend,
) -> tuple[int, BreakReason]:
    if len(text) <= max_len:
        return len(text), "sentence_break"

    candidates = _iter_boundary_candidates(text, boundary_backend)
    protected = _protected_spans(text)
    half = max(1, max_len // 2)
    overflow_limit = min(len(text), max_len + max(6, int(max_len * 0.35)))
    max_inside_enclosure = _inside_unclosed_enclosure_at(text, max_len)
    if max_inside_enclosure:
        overflow_limit = min(len(text), max_len + max(20, max_len))
    primary_reasons: tuple[BreakReason, ...]
    if prefer_sentence_break:
        primary_reasons = (
            "paragraph_break",
            "semantic_newline",
            "newline_sentence_break",
            "sentence_break",
            "clause_break",
        )
    else:
        primary_reasons = (
            "paragraph_break",
            "semantic_newline",
            "newline_sentence_break",
            "clause_break",
            "sentence_break",
        )

    for reason in primary_reasons:
        viable = [idx for idx, why in candidates if why == reason and half <= idx <= max_len]
        if viable:
            return _adjust_cut_for_repeated_punctuation(text, viable[-1]), reason

    for reason in primary_reasons:
        overflow = [idx for idx, why in candidates if why == reason and max_len < idx <= overflow_limit]
        if overflow:
            return _adjust_cut_for_repeated_punctuation(text, overflow[0]), reason

    if max_inside_enclosure and len(text) <= overflow_limit:
        return len(text), "sentence_break"

    if preserve_ascii_tokens:
        span_covering_max = None
        for start, end in protected:
            if start < max_len < end:
                span_covering_max = (start, end)
                break
        if span_covering_max is not None and span_covering_max[0] >= min_len:
            cut = span_covering_max[0]
            if _safe_boundary(text, cut, protected):
                return _adjust_cut_for_repeated_punctuation(text, cut), "token_boundary"
        if span_covering_max is not None:
            overflow_limit = min(len(text), span_covering_max[1] + min_len)
            for reason in primary_reasons:
                overflow = [idx for idx, why in candidates if why == reason and max_len < idx <= overflow_limit]
                if overflow:
                    return _adjust_cut_for_repeated_punctuation(text, overflow[0]), reason

        for probe in range(max_len, half - 1, -1):
            cut = _adjust_cut_for_ascii_token(text, probe)
            if cut != probe and min_len <= cut <= len(text) and cut >= half and _safe_boundary(text, cut, protected):
                return _adjust_cut_for_repeated_punctuation(text, cut), "token_boundary"

        for start, end in protected:
            if start >= max_len:
                break
            if start >= min_len and _safe_boundary(text, start, protected):
                return _adjust_cut_for_repeated_punctuation(text, start), "token_boundary"
            if end > max_len and end < len(text) and _safe_boundary(text, end, protected):
                return _adjust_cut_for_repeated_punctuation(text, end), "token_boundary"

    return _adjust_cut_for_repeated_punctuation(text, max_len), "hard_limit"


def _split_long_text(text: str, cfg: ReplySegmentationConfig) -> list[Segment]:
    pending = text
    segments: list[Segment] = []
    while pending:
        if len(pending) <= cfg.max_segment_chars:
            segments.append(Segment(pending, "sentence_break"))
            break
        cut, reason = _best_cut(
            pending,
            max_len=cfg.max_segment_chars,
            min_len=cfg.min_segment_chars,
            prefer_sentence_break=cfg.prefer_sentence_break,
            preserve_ascii_tokens=cfg.preserve_ascii_tokens,
            boundary_backend=cfg.boundary_backend,
        )
        head = pending[:cut].rstrip(_TRAILING_CLAUSE)
        tail = pending[cut:].lstrip()
        if not head:
            head = pending[: cfg.max_segment_chars]
            tail = pending[cfg.max_segment_chars :].lstrip()
            reason = "hard_limit"
        segments.append(Segment(head, reason))
        pending = tail

    if cfg.merge_short_tail and len(segments) >= 2 and len(segments[-1].text) < cfg.min_segment_chars:
        merged = segments[-2].text + segments[-1].text
        segments[-2] = Segment(merged, segments[-2].reason)
        segments.pop()
    return segments or [Segment(text, "hard_limit")]


def _merge_short_prefix_segments(segments: list[Segment], min_segment_chars: int) -> list[Segment]:
    if len(segments) < 2:
        return segments

    merged: list[Segment] = []
    index = 0
    while index < len(segments):
        current = segments[index]
        if (
            index + 1 < len(segments)
            and len(current.text) <= min_segment_chars + 2
            and current.text.endswith(("“", "\"", "「", "『", "：\"", ":\""))
        ):
            next_segment = segments[index + 1]
            merged.append(Segment(current.text + next_segment.text, next_segment.reason))
            index += 2
            continue
        merged.append(current)
        index += 1
    return merged


def _segment_text(text: str, cfg: ReplySegmentationConfig) -> list[Segment]:
    normalized = _merge_lines(fix_cq_codes(text), cfg.min_segment_chars)
    if not normalized.strip():
        return [Segment("", "sentence_break")]

    paragraphs = [p.strip() for p in normalized.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [normalized.strip()]

    segments: list[Segment] = []
    for para in paragraphs:
        lines = [line.strip() for line in para.split("\n") if line.strip()] if "\n" in para else [para]
        for line_index, line in enumerate(lines):
            has_semantic_newline_after = line_index < len(lines) - 1
            if len(line) <= cfg.max_segment_chars:
                reason: BreakReason = "semantic_newline" if has_semantic_newline_after else "sentence_break"
                segments.append(Segment(line.rstrip(_TRAILING_CLAUSE), reason))
            else:
                line_segments = _split_long_text(line, cfg)
                if has_semantic_newline_after and line_segments:
                    line_segments[-1] = Segment(line_segments[-1].text, "semantic_newline")
                segments.extend(line_segments)
    segments = [segment for segment in segments if segment.text]
    return _merge_short_prefix_segments(segments, cfg.min_segment_chars)


def _coalesce_segments(segments: list[Segment], max_segments: int) -> list[Segment]:
    if max_segments <= 0:
        return segments
    if len(segments) <= max_segments:
        return segments
    if max_segments == 1:
        joined = "\n".join(segment.text for segment in segments)
        return [Segment(joined, "coalesced_overflow")]
    head = segments[: max_segments - 1]
    tail = "\n".join(segment.text for segment in segments[max_segments - 1 :])
    return [*head, Segment(tail, "coalesced_overflow")]


def _apply_soft_segment_limit(
    segments: list[Segment],
    max_segments: int,
    notice: str,
) -> list[Segment]:
    if max_segments <= 0 or len(segments) <= max_segments:
        return segments

    notice_text = notice.strip() or "先说到这里啦。"
    if max_segments == 1:
        return [Segment(notice_text, "soft_limit")]
    return [*segments[: max_segments - 1], Segment(notice_text, "soft_limit")]


def segment_reply(reply: str, cfg: ReplySegmentationConfig) -> ReplySegmentationResult:
    if not cfg.enabled:
        text = _clean_text(fix_cq_codes(reply))
        segments = [Segment(text, "sentence_break")]
        return ReplySegmentationResult(
            segments=segments,
            raw_count=1,
            capped_count=1,
            strategy="disabled",
            break_reasons=[segment.reason for segment in segments],
            limit_status="none",
        )

    if any(line.strip() == _SEGMENT_SEP for line in reply.split("\n")):
        raw_segments = _split_explicit_segments(reply)
        strategy = "explicit_cut"
    else:
        raw_segments = _segment_text(reply, cfg)
        strategy = "two_stage"

    soft_limited_segments = _apply_soft_segment_limit(
        raw_segments,
        cfg.soft_max_send_segments,
        cfg.soft_limit_notice,
    )
    capped_segments = _coalesce_segments(soft_limited_segments, cfg.max_send_segments)
    soft_limited = len(soft_limited_segments) < len(raw_segments)
    hard_limited = len(capped_segments) < len(soft_limited_segments)
    limit_status: SegmentationLimitStatus
    if soft_limited and hard_limited:
        limit_status = "soft_then_hard"
    elif soft_limited:
        limit_status = "soft"
    elif hard_limited:
        limit_status = "hard"
    else:
        limit_status = "none"
    return ReplySegmentationResult(
        segments=capped_segments,
        raw_count=len(raw_segments),
        capped_count=len(capped_segments),
        strategy=strategy,
        break_reasons=[segment.reason for segment in raw_segments],
        limit_status=limit_status,
    )
