"""Per-axis cache diagnostic — Claude Code's ``Ti9`` equivalent.

Background
----------
DeepSeek's prompt cache is token-prefix matched: if any byte in the
serialized prefix changes, every following token is a cache miss. When
the aggregate hit rate suddenly drops we need to know *which axis*
broke — system blocks, tool schemas, or the message list — and *which
block within that axis* drifted. Without that we burn hours pulling
DeepSeek-side reports and bisecting.

Claude Code's ``Ti9`` (line 333352 of the 2.1.143 native binary)
addresses this by computing six independent hashes per request:
``systemHash``, ``toolsHash``, ``cacheControlHash``, ``perBlockHashes``,
``perToolHashes``, ``messageHashes``. We adapt that idea to the spine:
:func:`compute_cache_diagnostic` returns a structured snapshot of the
hashes plus per-block lengths. ``LLMClient._call`` will diff successive
snapshots and persist them so the admin panel can answer "what
changed?" in seconds.

Hash sanitization
-----------------
Hashes intentionally strip a few fields that change for non-cache
reasons:

- ``cache_control`` is removed from system blocks (TTL changes should
  not look like content drift).
- ``_omu_segment`` is removed from system blocks (it's a debug tag added
  by :class:`LLMRequest`, not real content).
- Image base64 longer than 256 chars is hashed by ``len(...)`` only —
  the same image redownloaded shouldn't read as a different block.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

_LARGE_BASE64_THRESHOLD = 256


def _sanitize_text_block(block: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(block)
    cleaned.pop("cache_control", None)
    cleaned.pop("_omu_segment", None)
    return cleaned


def _sanitize_image_block(block: dict[str, Any]) -> dict[str, Any]:
    """Replace large base64 payloads with a length placeholder.

    Re-downloading the same image yields a different bytestream after
    transcoding, but conceptually it's the same block. Hashing on
    length keeps the diagnostic stable.
    """
    cleaned = dict(block)
    cleaned.pop("cache_control", None)
    cleaned.pop("_omu_segment", None)
    source = cleaned.get("source")
    if isinstance(source, dict):
        data = source.get("data")
        if isinstance(data, str) and len(data) > _LARGE_BASE64_THRESHOLD:
            cleaned["source"] = {**source, "data": f"<base64 len={len(data)}>"}
    return cleaned


def _sanitize_block(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text":
        return _sanitize_text_block(block)
    if block_type == "image":
        return _sanitize_image_block(block)
    return _sanitize_text_block(block)


def _stable_hash(payload: Any) -> str:
    """SHA-256 of the canonical JSON form. Short hex prefix for storage."""
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _block_text_length(block: Any) -> int:
    if not isinstance(block, dict):
        return 0
    if block.get("type") == "text":
        return len(str(block.get("text", "") or ""))
    return 0


@dataclass
class CacheDiagnostic:
    """Structured per-request cache fingerprint.

    Two snapshots from successive calls of the same task can be diffed
    field-by-field to point at the broken axis.
    """

    task: str
    profile: str
    system_hash: str
    tools_hash: str
    messages_hash: str
    per_block_hashes: list[str] = field(default_factory=list)
    per_block_lengths: list[int] = field(default_factory=list)
    per_block_segments: list[str] = field(default_factory=list)
    per_tool_hashes: dict[str, str] = field(default_factory=dict)
    per_message_hashes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "profile": self.profile,
            "system_hash": self.system_hash,
            "tools_hash": self.tools_hash,
            "messages_hash": self.messages_hash,
            "per_block_hashes": list(self.per_block_hashes),
            "per_block_lengths": list(self.per_block_lengths),
            "per_block_segments": list(self.per_block_segments),
            "per_tool_hashes": dict(self.per_tool_hashes),
            "per_message_hashes": list(self.per_message_hashes),
        }


def compute_cache_diagnostic(
    *,
    task: str,
    profile: str,
    system_blocks: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    messages: list[Any],
) -> CacheDiagnostic:
    """Compute the per-axis cache fingerprint for a single request."""
    sanitized_system = [_sanitize_block(b) for b in system_blocks]
    sanitized_tools = [_sanitize_block(t) for t in (tools or [])]
    sanitized_messages = [_sanitize_block(m) for m in messages]

    per_block_hashes = [_stable_hash(b) for b in sanitized_system]
    per_block_lengths = [_block_text_length(b) for b in sanitized_system]
    per_block_segments = [
        str(b.get("_omu_segment", "")) if isinstance(b, dict) else ""
        for b in system_blocks
    ]

    per_tool_hashes: dict[str, str] = {}
    for tool in sanitized_tools:
        if isinstance(tool, dict):
            name = str(tool.get("name", "") or "")
            if name:
                per_tool_hashes[name] = _stable_hash(tool)

    per_message_hashes = [_stable_hash(m) for m in sanitized_messages]

    return CacheDiagnostic(
        task=task,
        profile=profile,
        system_hash=_stable_hash(sanitized_system),
        tools_hash=_stable_hash(sanitized_tools),
        messages_hash=_stable_hash(sanitized_messages),
        per_block_hashes=per_block_hashes,
        per_block_lengths=per_block_lengths,
        per_block_segments=per_block_segments,
        per_tool_hashes=per_tool_hashes,
        per_message_hashes=per_message_hashes,
    )


@dataclass
class CacheDiagnosticDiff:
    """Structured diff between two consecutive snapshots of the same task."""

    task: str
    profile: str
    system_changed: bool
    tools_changed: bool
    messages_changed: bool
    changed_block_indices: list[int] = field(default_factory=list)
    added_tools: list[str] = field(default_factory=list)
    removed_tools: list[str] = field(default_factory=list)
    changed_tools: list[str] = field(default_factory=list)
    first_changed_message_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "profile": self.profile,
            "system_changed": self.system_changed,
            "tools_changed": self.tools_changed,
            "messages_changed": self.messages_changed,
            "changed_block_indices": list(self.changed_block_indices),
            "added_tools": list(self.added_tools),
            "removed_tools": list(self.removed_tools),
            "changed_tools": list(self.changed_tools),
            "first_changed_message_index": self.first_changed_message_index,
        }


def diff_cache_diagnostics(prev: CacheDiagnostic, curr: CacheDiagnostic) -> CacheDiagnosticDiff:
    """Diff two snapshots of the same task, pointing at the broken axis."""
    if prev.task != curr.task:
        raise ValueError(
            f"cannot diff snapshots of different tasks: {prev.task!r} vs {curr.task!r}"
        )

    changed_block_indices: list[int] = []
    overlap = min(len(prev.per_block_hashes), len(curr.per_block_hashes))
    for i in range(overlap):
        if prev.per_block_hashes[i] != curr.per_block_hashes[i]:
            changed_block_indices.append(i)
    if len(prev.per_block_hashes) != len(curr.per_block_hashes):
        for i in range(overlap, max(len(prev.per_block_hashes), len(curr.per_block_hashes))):
            changed_block_indices.append(i)

    prev_tool_names = set(prev.per_tool_hashes)
    curr_tool_names = set(curr.per_tool_hashes)
    added_tools = sorted(curr_tool_names - prev_tool_names)
    removed_tools = sorted(prev_tool_names - curr_tool_names)
    changed_tools = sorted(
        name
        for name in (prev_tool_names & curr_tool_names)
        if prev.per_tool_hashes[name] != curr.per_tool_hashes[name]
    )

    first_changed_message_index: int | None = None
    msg_overlap = min(len(prev.per_message_hashes), len(curr.per_message_hashes))
    for i in range(msg_overlap):
        if prev.per_message_hashes[i] != curr.per_message_hashes[i]:
            first_changed_message_index = i
            break
    if first_changed_message_index is None and len(prev.per_message_hashes) != len(curr.per_message_hashes):
        first_changed_message_index = msg_overlap

    return CacheDiagnosticDiff(
        task=curr.task,
        profile=curr.profile,
        system_changed=prev.system_hash != curr.system_hash,
        tools_changed=prev.tools_hash != curr.tools_hash,
        messages_changed=prev.messages_hash != curr.messages_hash,
        changed_block_indices=changed_block_indices,
        added_tools=added_tools,
        removed_tools=removed_tools,
        changed_tools=changed_tools,
        first_changed_message_index=first_changed_message_index,
    )
