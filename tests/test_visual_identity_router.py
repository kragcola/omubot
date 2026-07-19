"""RED→GREEN: router wires VisualIdentityStore into structured image_ref evidence.

Contract (post-implementation acceptance):
- ``_render_message`` / visual evidence path consults
  ``visual_identity_store.lookup_for_context`` with full SHA-256 plus
  privacy-safe ``current_user_id`` / ``current_group_id``.
- Same full SHA-256, same correcting user, and allowed scope place the
  human-corrected entity label into structured ``image_ref`` visual identity
  (and model-visible visual evidence) — never into user-authored text.
- Other user, other group, and private↔group leakage fail closed.
- No diagnostic threshold/distance strings enter model-visible text.
- Missing store / getattr default fails closed without crash.

These tests encode the intended behavior and stay RED until the router path
accepts and uses the privacy-safe lookup parameters.
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import aiohttp
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from kernel.router import _describe_image_data, _render_message
from services.llm.client import content_text
from services.media.visual_evidence import (
    NEUTRAL_IMAGE_PLACEHOLDER,
    collect_image_ref_sidechannels,
    format_visual_evidence_system_block,
)

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

_PAYLOAD = b"visual-identity-router-fixture-png"
_SHA = hashlib.sha256(_PAYLOAD).hexdigest()
assert len(_SHA) == 64

_ENTITY = "高松灯"
_USER = "u_speaker"
_OTHER_USER = "u_other"
_GROUP = "g_alpha"
_OTHER_GROUP = "g_beta"

_DIAGNOSTIC_MARKERS = (
    "置信阈值",
    "低置信候选",
    "threshold",
    "distance",
    "0.178",
    "0.231",
)


@dataclass(slots=True)
class _FakeIdentityRecord:
    """Minimal record shape matching VisualIdentityStore lookup hits."""

    image_sha256: str
    entity_label: str
    correcting_user_id: str
    origin_group_id: str | None
    visibility: str
    provenance: str = "user_correction"


class _FakeVisualIdentityStore:
    """Fail-closed fake: returns a hit only for exact SHA + allowed user/scope."""

    def __init__(
        self,
        *,
        image_sha256: str,
        entity_label: str,
        correcting_user_id: str,
        origin_group_id: str | None,
        visibility: str = "same_group",
    ) -> None:
        self.image_sha256 = image_sha256
        self.entity_label = entity_label
        self.correcting_user_id = correcting_user_id
        self.origin_group_id = origin_group_id
        self.visibility = visibility
        self.calls: list[dict[str, Any]] = []

    async def lookup_for_context(
        self,
        image_sha256: str,
        *,
        current_user_id: str,
        current_group_id: str | None,
    ) -> _FakeIdentityRecord | None:
        self.calls.append(
            {
                "image_sha256": image_sha256,
                "current_user_id": current_user_id,
                "current_group_id": current_group_id,
            }
        )
        sha = str(image_sha256 or "").strip().lower()
        if sha.startswith("sha256:"):
            sha = sha[7:].strip()
        if sha != self.image_sha256:
            return None
        user = str(current_user_id or "").strip()
        if not user or user != self.correcting_user_id:
            return None

        group = (
            str(current_group_id).strip()
            if current_group_id is not None and str(current_group_id).strip()
            else None
        )
        if self.visibility == "private":
            # Private corrections only surface outside group context.
            if group is not None:
                return None
            return self._record()
        if self.visibility == "same_group":
            if group is None:
                return None
            if str(self.origin_group_id or "").strip() != group:
                return None
            return self._record()
        if self.visibility == "global":
            return self._record()
        return None

    def _record(self) -> _FakeIdentityRecord:
        return _FakeIdentityRecord(
            image_sha256=self.image_sha256,
            entity_label=self.entity_label,
            correcting_user_id=self.correcting_user_id,
            origin_group_id=self.origin_group_id,
            visibility=self.visibility,
        )


class _FakeImageCache:
    def __init__(self, image_path: Path) -> None:
        self._image_path = image_path

    async def save(self, session: Any, url: str, file_id: str) -> dict[str, str]:
        del session, url, file_id
        return {
            "type": "image_ref",
            "path": str(self._image_path),
            "media_type": "image/png",
        }


class _FakeVisionClient:
    async def describe_image(
        self,
        image_data: bytes,
        media_type: str = "image/jpeg",
        prompt: str | None = None,
    ) -> str | None:
        del image_data, media_type, prompt
        return "开心地跳起来"


def _text_of(rendered: Any) -> str:
    if isinstance(rendered, str):
        return rendered
    return content_text(rendered)


def _image_refs(rendered: Any) -> list[dict[str, Any]]:
    if not isinstance(rendered, list):
        return []
    return [
        block
        for block in rendered
        if isinstance(block, dict) and block.get("type") == "image_ref"
    ]


def _identity_surface(ref: dict[str, Any]) -> str:
    labels = ref.get("visual_identity") or []
    label_text = " ".join(str(x) for x in labels if x)
    summary = str(ref.get("visual_summary") or "")
    observation = str(ref.get("visual_observation") or "")
    return f"{label_text} {summary} {observation}"


def _image_message(*, user_text: str = "这是谁") -> Message:
    segs: list[MessageSegment] = [
        MessageSegment(
            "image",
            {
                "url": "http://example.invalid/vi-router.png",
                "file": "vi-router.png",
            },
        )
    ]
    if user_text:
        segs.append(MessageSegment.text(user_text))
    return Message(segs)


def _privacy_params(
    *,
    store: Any | None,
    current_user_id: str,
    current_group_id: str | None,
) -> dict[str, Any]:
    """Intended privacy-safe kwargs (dropped until router signature accepts them)."""
    return {
        "visual_identity_store": store,
        "current_user_id": current_user_id,
        "current_group_id": current_group_id,
    }


async def _call_render_message(
    *,
    image_path: Path,
    store: Any | None,
    current_user_id: str,
    current_group_id: str | None,
    user_text: str = "这是谁",
) -> Any:
    """Call ``_render_message`` with identity kwargs when the signature allows them.

    Unknown kwargs are omitted so RED failures surface as missing structured
    identity (or missing signature assertions), not TypeError noise.
    """
    base: dict[str, Any] = {
        "session": cast(aiohttp.ClientSession, object()),
        "vision_client": _FakeVisionClient(),
        "vision_enabled": True,
        "image_cache": _FakeImageCache(image_path),
    }
    intended = _privacy_params(
        store=store,
        current_user_id=current_user_id,
        current_group_id=current_group_id,
    )
    accepted = set(inspect.signature(_render_message).parameters)
    for key, value in intended.items():
        if key in accepted:
            base[key] = value
    return await _render_message(_image_message(user_text=user_text), **base)


# ---------------------------------------------------------------------------
# API surface (must accept privacy-safe context params)
# ---------------------------------------------------------------------------


def test_render_message_accepts_visual_identity_privacy_params() -> None:
    """Router public surface must take store + current user/group for lookup."""
    sig = inspect.signature(_render_message)
    params = sig.parameters
    assert "visual_identity_store" in params, (
        "_render_message must accept visual_identity_store for privacy-safe "
        "exact visual-identity lookup"
    )
    assert "current_user_id" in params, (
        "_render_message must accept current_user_id for fail-closed scope"
    )
    assert "current_group_id" in params, (
        "_render_message must accept current_group_id for fail-closed scope"
    )


def test_describe_image_data_accepts_visual_identity_privacy_params() -> None:
    """Describe path should also be able to consult store with full context."""
    sig = inspect.signature(_describe_image_data)
    params = sig.parameters
    # Either _describe_image_data or only _render_message may own the merge;
    # if describe owns it, it must take the same privacy-safe kwargs.
    # Require at least one of describe/render to expose store wiring — render
    # is asserted above; here we assert describe can accept when used as the
    # shared enrichment helper.
    has_store = "visual_identity_store" in params
    has_user = "current_user_id" in params
    has_group = "current_group_id" in params
    assert has_store and has_user and has_group, (
        "_describe_image_data should accept visual_identity_store, "
        "current_user_id, and current_group_id so enrichment can fail closed"
    )


# ---------------------------------------------------------------------------
# POSITIVE: same user + same group → structured identity, not user text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_message_same_user_same_group_adds_corrected_label_to_image_ref(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(_PAYLOAD)
    store = _FakeVisualIdentityStore(
        image_sha256=_SHA,
        entity_label=_ENTITY,
        correcting_user_id=_USER,
        origin_group_id=_GROUP,
        visibility="same_group",
    )

    rendered = await _call_render_message(
        image_path=image_path,
        store=store,
        current_user_id=_USER,
        current_group_id=_GROUP,
        user_text="这是谁",
    )

    text = _text_of(rendered)
    assert _ENTITY not in text, (
        "human-corrected entity label must not leak into user-authored text; "
        f"got {text!r}"
    )
    assert NEUTRAL_IMAGE_PLACEHOLDER in text or "«图片»" in text
    for marker in _DIAGNOSTIC_MARKERS:
        assert marker not in text

    refs = _image_refs(rendered)
    assert refs, "expected image_ref block with structured visual side-channel"
    ref = refs[0]
    assert len(str(ref.get("image_sha256") or "")) == 64
    assert str(ref.get("image_sha256")) == _SHA
    surface = _identity_surface(ref)
    assert _ENTITY in surface, (
        f"corrected entity label must appear in structured visual identity / "
        f"model-visible evidence, got ref={ref!r}"
    )
    labels = ref.get("visual_identity") or []
    assert any(_ENTITY in str(x) for x in labels), (
        "entity label must be in image_ref['visual_identity'], not only prose"
    )
    assert ref.get("provenance") == "visual_system" or ref.get("image_sha256")

    # Privacy-safe lookup must have been consulted with full SHA + context.
    assert store.calls, "router must call visual_identity_store.lookup_for_context"
    last = store.calls[-1]
    assert last["image_sha256"] == _SHA
    assert last["current_user_id"] == _USER
    assert last["current_group_id"] == _GROUP

    # Model-visible system block also carries the label without diagnostics.
    side = collect_image_ref_sidechannels(
        rendered if isinstance(rendered, list) else []
    )
    block = format_visual_evidence_system_block(side, user_text="这是谁") or ""
    assert _ENTITY in block
    for marker in ("置信阈值", "threshold", "distance"):
        assert marker not in block


# ---------------------------------------------------------------------------
# NEGATIVE: other user / other group / private-context leakage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_message_other_user_no_label_leakage(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(_PAYLOAD)
    store = _FakeVisualIdentityStore(
        image_sha256=_SHA,
        entity_label=_ENTITY,
        correcting_user_id=_USER,
        origin_group_id=_GROUP,
        visibility="same_group",
    )

    rendered = await _call_render_message(
        image_path=image_path,
        store=store,
        current_user_id=_OTHER_USER,
        current_group_id=_GROUP,
        user_text="这是谁",
    )

    text = _text_of(rendered)
    assert _ENTITY not in text
    refs = _image_refs(rendered)
    assert refs
    surface = _identity_surface(refs[0])
    assert _ENTITY not in surface, (
        f"other user must not see correcting user's visual identity; got {surface!r}"
    )
    assert store.calls, "lookup must still be consulted with the other user id"
    assert store.calls[-1]["current_user_id"] == _OTHER_USER


@pytest.mark.asyncio
async def test_render_message_other_group_no_label_leakage(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(_PAYLOAD)
    store = _FakeVisualIdentityStore(
        image_sha256=_SHA,
        entity_label=_ENTITY,
        correcting_user_id=_USER,
        origin_group_id=_GROUP,
        visibility="same_group",
    )

    rendered = await _call_render_message(
        image_path=image_path,
        store=store,
        current_user_id=_USER,
        current_group_id=_OTHER_GROUP,
        user_text="这是谁",
    )

    text = _text_of(rendered)
    assert _ENTITY not in text
    refs = _image_refs(rendered)
    assert refs
    surface = _identity_surface(refs[0])
    assert _ENTITY not in surface, (
        f"other group must not see same_group visual identity; got {surface!r}"
    )
    assert store.calls
    assert store.calls[-1]["current_group_id"] == _OTHER_GROUP


@pytest.mark.asyncio
async def test_render_message_private_mapping_not_visible_in_group(
    tmp_path: Path,
) -> None:
    """Private-scope correction must not surface when rendering in a group."""
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(_PAYLOAD)
    store = _FakeVisualIdentityStore(
        image_sha256=_SHA,
        entity_label="私聊纠正角色",
        correcting_user_id=_USER,
        origin_group_id=None,
        visibility="private",
    )

    rendered = await _call_render_message(
        image_path=image_path,
        store=store,
        current_user_id=_USER,
        current_group_id=_GROUP,
        user_text="这是谁",
    )

    text = _text_of(rendered)
    assert "私聊纠正角色" not in text
    refs = _image_refs(rendered)
    assert refs
    surface = _identity_surface(refs[0])
    assert "私聊纠正角色" not in surface
    # Lookup must still be privacy-scoped (group context) even when the hit is denied.
    assert store.calls, "router must consult store even when visibility fails closed"
    assert store.calls[-1]["current_group_id"] == _GROUP
    assert store.calls[-1]["current_user_id"] == _USER


@pytest.mark.asyncio
async def test_render_message_group_mapping_not_visible_in_private(
    tmp_path: Path,
) -> None:
    """same_group correction must not surface in private (no group) context."""
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(_PAYLOAD)
    store = _FakeVisualIdentityStore(
        image_sha256=_SHA,
        entity_label=_ENTITY,
        correcting_user_id=_USER,
        origin_group_id=_GROUP,
        visibility="same_group",
    )

    rendered = await _call_render_message(
        image_path=image_path,
        store=store,
        current_user_id=_USER,
        current_group_id=None,
        user_text="这是谁",
    )

    text = _text_of(rendered)
    assert _ENTITY not in text
    refs = _image_refs(rendered)
    assert refs
    surface = _identity_surface(refs[0])
    assert _ENTITY not in surface, (
        f"group-scoped identity must not leak into private chat; got {surface!r}"
    )
    assert store.calls
    assert store.calls[-1]["current_group_id"] is None


@pytest.mark.asyncio
async def test_render_message_private_mapping_visible_in_private_context(
    tmp_path: Path,
) -> None:
    """Same user in private chat may see their private visual correction."""
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(_PAYLOAD)
    private_label = "私聊纠正角色"
    store = _FakeVisualIdentityStore(
        image_sha256=_SHA,
        entity_label=private_label,
        correcting_user_id=_USER,
        origin_group_id=None,
        visibility="private",
    )

    rendered = await _call_render_message(
        image_path=image_path,
        store=store,
        current_user_id=_USER,
        current_group_id=None,
        user_text="这是谁",
    )

    text = _text_of(rendered)
    assert private_label not in text
    refs = _image_refs(rendered)
    assert refs
    labels = refs[0].get("visual_identity") or []
    assert any(private_label in str(x) for x in labels)
    assert store.calls
    assert store.calls[-1]["current_group_id"] is None
    assert store.calls[-1]["image_sha256"] == _SHA


# ---------------------------------------------------------------------------
# Fail-closed defaults + no diagnostic leakage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_message_missing_store_fails_closed_without_crash(
    tmp_path: Path,
) -> None:
    """No store / None store must not crash and must not invent labels."""
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(_PAYLOAD)

    # Signature must already accept privacy params so callers can pass None safely.
    params = inspect.signature(_render_message).parameters
    assert "visual_identity_store" in params
    assert "current_user_id" in params
    assert "current_group_id" in params

    rendered = await _call_render_message(
        image_path=image_path,
        store=None,
        current_user_id=_USER,
        current_group_id=_GROUP,
        user_text="这是谁",
    )

    text = _text_of(rendered)
    assert _ENTITY not in text
    refs = _image_refs(rendered)
    # image_ref may still exist from vision path; corrected label must not.
    if refs:
        surface = _identity_surface(refs[0])
        assert _ENTITY not in surface


@pytest.mark.asyncio
async def test_corrected_identity_never_rewrites_user_text_or_diagnostics(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(_PAYLOAD)
    store = _FakeVisualIdentityStore(
        image_sha256=_SHA,
        entity_label=_ENTITY,
        correcting_user_id=_USER,
        origin_group_id=_GROUP,
        visibility="same_group",
    )

    rendered = await _call_render_message(
        image_path=image_path,
        store=store,
        current_user_id=_USER,
        current_group_id=_GROUP,
        user_text="哈哈哈",  # react intent — identity still structured only
    )

    text = _text_of(rendered)
    assert "哈哈哈" in text
    assert _ENTITY not in text
    assert "开心地跳起来" not in text
    for marker in _DIAGNOSTIC_MARKERS:
        assert marker not in text

    refs = _image_refs(rendered)
    assert refs
    for ref in refs:
        blob = " ".join(
            str(ref.get(k) or "")
            for k in (
                "visual_summary",
                "visual_observation",
                "visual_identity",
                "visual_ocr",
            )
        )
        for marker in ("置信阈值", "threshold=", "distance="):
            assert marker not in blob
        # Corrected label still on structured channel under react intent.
        labels = ref.get("visual_identity") or []
        assert any(_ENTITY in str(x) for x in labels)
