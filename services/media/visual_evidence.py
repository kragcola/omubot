"""Structured visual evidence for chat prompts (side-channel metadata).

Visual observations attach to image_ref blocks as structured metadata with
``provenance=visual_system``. They must never be rewritten into user-authored
text (no ``«图片N: desc»`` / ``[图片: desc]`` prose in content_text).

Model-visible evidence (request-local system/dynamic block) must never include
numeric thresholds, embedding distances, candidate score lists, or internal
confidence policy jargon.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from services.media.character_recognizer import CharacterRecognition
from services.media.vision import ImageIntent, classify_image_intent

# The sidecar threshold is a nearest-neighbour candidate boundary. Human-facing
# identity assertions need a stricter margin: production false positives were
# observed at 0.155 and 0.171 with a 0.178 sidecar threshold.
# Used only for internal trusted-match filtering — never rendered to the model.
_IDENTITY_ASSERTION_SAFETY_MARGIN = 0.03

VisualProvenance = Literal["visual_system"]
VISUAL_PROVENANCE: VisualProvenance = "visual_system"

# Neutral placeholders for user-authored text surfaces (content_text / Style).
NEUTRAL_IMAGE_PLACEHOLDER = "«图片»"
NEUTRAL_ANIMATED_PLACEHOLDER = "«动画表情»"
NEUTRAL_QUOTED_IMAGE_PLACEHOLDER = "[图片]"


@dataclass(frozen=True)
class StickerEvidence:
    sticker_id: str
    description: str
    usage_hint: str = ""
    ocr_text: str = ""
    source: str = ""

    @property
    def weak_authority(self) -> bool:
        """Legacy migrated descriptions are useful hints, not identity evidence."""
        source = self.source.lower()
        desc = self.description.strip()
        return source.startswith("migrated:") or desc.startswith("旧bot迁移")


@dataclass(frozen=True)
class VisualEvidence:
    image_sha256: str
    image_sha256_short: str
    sticker: StickerEvidence | None = None
    recognitions: tuple[CharacterRecognition, ...] = ()
    vision_description: str | None = None

    @property
    def matched(self) -> tuple[CharacterRecognition, ...]:
        return tuple(r for r in self.recognitions if r.matched and r.character_name)

    @property
    def trusted_matched(self) -> tuple[CharacterRecognition, ...]:
        return tuple(r for r in self.matched if _is_trusted_identity(r))

    @property
    def borderline_matched(self) -> tuple[CharacterRecognition, ...]:
        trusted = self.trusted_matched
        return tuple(r for r in self.matched if r not in trusted)

    @property
    def unmatched(self) -> tuple[CharacterRecognition, ...]:
        return tuple(r for r in self.recognitions if not (r.matched and r.character_name))

    @property
    def detection_count(self) -> int:
        counts = [r.detection_count for r in self.recognitions if r.detection_count is not None]
        if counts:
            return max(int(c) for c in counts)
        return len(self.recognitions)

    @property
    def identity_labels(self) -> tuple[str, ...]:
        return tuple(_character_labels_list(self.trusted_matched))

    @property
    def observation(self) -> str | None:
        body = _primary_body(self, has_character_match=bool(self.trusted_matched))
        return body

    @property
    def ocr_text(self) -> str:
        if self.sticker is not None and self.sticker.ocr_text:
            return str(self.sticker.ocr_text).strip()
        # Best-effort: structured VL may embed OCR=… or 图上文字：
        obs = (self.vision_description or "").strip()
        for marker in ("图上文字：", "OCR=", "OCR："):
            if marker in obs:
                return obs.split(marker, 1)[1].strip().split("；", 1)[0].strip()
        return ""


def render_visual_evidence(evidence: VisualEvidence) -> str | None:
    """Render structured evidence into compact natural-language form.

    Never emits confidence thresholds, distances, candidate score lists, or
    internal policy jargon — only natural-language identity/body evidence.
    Used for model-visible side-channel blocks, not user-authored text.
    """
    matched = evidence.trusted_matched
    body = _primary_body(evidence, has_character_match=bool(matched))

    if matched:
        labels = _character_labels(matched)
        if evidence.detection_count > len(matched):
            remaining = evidence.detection_count - len(matched)
            summary = (
                f"画面中约有{evidence.detection_count}个角色/头像；"
                f"可确认：{labels}；"
                f"其余{remaining}个未能确认身份"
            )
            return f"{summary}：{body}" if body else summary
        return f"{labels}：{body}" if body else f"{labels}表情包"

    if evidence.borderline_matched:
        count = evidence.detection_count or len(evidence.borderline_matched)
        summary = f"画面中约有{count}个角色/头像；识别结果不确定，未能确认具体角色"
        return f"{summary}：{body}" if body else summary

    if evidence.recognitions:
        count = evidence.detection_count or len(evidence.recognitions)
        summary = f"画面中约有{count}个角色/头像；未能确认具体角色"
        return f"{summary}：{body}" if body else summary

    if evidence.sticker is not None and evidence.sticker.description and not evidence.sticker.weak_authority:
        return evidence.sticker.description
    if evidence.vision_description:
        return evidence.vision_description
    if evidence.sticker is not None and evidence.sticker.description:
        return evidence.sticker.description
    return None


def model_visible_visual_summary(
    evidence: VisualEvidence,
    *,
    intent: ImageIntent | str = "react",
) -> str | None:
    """Intent-gated model-visible summary (no diagnostics).

    - react / sticker-only: identity only when trusted; do not replay VLM prose
    - identify: identity labels (+ multi-face uncertainty in natural language)
    - ocr: OCR text if present, else compact body
    - describe: full non-diagnostic observation summary
    """
    intent_key = str(intent or "react").strip().lower() or "react"
    labels = evidence.identity_labels
    observation = (evidence.observation or "").strip()
    ocr = evidence.ocr_text

    if intent_key == "react":
        if labels:
            return "、".join(labels)
        return None
    if intent_key == "identify":
        full = render_visual_evidence(evidence)
        if labels:
            return full or "、".join(labels)
        return full or "未能确认具体角色"
    if intent_key == "ocr":
        if ocr:
            return f"图上文字：{ocr}"
        return observation or render_visual_evidence(evidence)
    # describe (default explicit)
    return render_visual_evidence(evidence)


def to_image_ref_sidechannel(
    evidence: VisualEvidence,
    *,
    intent: ImageIntent | str = "react",
) -> dict[str, Any]:
    """Build backward-compatible extra keys for image_ref dicts."""
    intent_key = str(intent or "react").strip().lower() or "react"
    summary = model_visible_visual_summary(evidence, intent=intent_key)
    return {
        "image_sha256": evidence.image_sha256,
        "image_sha256_short": evidence.image_sha256_short,
        "visual_intent": intent_key,
        "visual_observation": evidence.observation or "",
        "visual_identity": list(evidence.identity_labels),
        "visual_ocr": evidence.ocr_text,
        "visual_summary": summary or "",
        "provenance": VISUAL_PROVENANCE,
    }


def attach_visual_sidechannel(
    image_ref: MutableMapping[str, Any] | Mapping[str, Any],
    evidence: VisualEvidence,
    *,
    intent: ImageIntent | str = "react",
) -> dict[str, Any]:
    """Return image_ref copy with structured visual side-channel metadata."""
    out = dict(image_ref)
    out.update(to_image_ref_sidechannel(evidence, intent=intent))
    return out


def collect_image_ref_sidechannels(content: Any) -> list[dict[str, Any]]:
    """Collect visual_system side-channels from Content / message content."""
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "image_ref":
            continue
        # Accept visual_system provenance, or partial attach with sha/summary.
        if (
            str(block.get("provenance") or "") != VISUAL_PROVENANCE
            and not block.get("image_sha256")
            and not block.get("visual_summary")
        ):
            continue
        out.append(dict(block))
    return out


def format_visual_evidence_system_block(
    refs: Sequence[Mapping[str, Any]],
    *,
    user_text: str = "",
) -> str | None:
    """Request-local model-visible visual evidence (system/dynamic block text).

    Intent is derived from user text. React/sticker-only does not default to
    replaying VLM prose. Never includes diagnostic score forms.
    """
    if not refs:
        return None
    intent = classify_image_intent(user_text)
    lines: list[str] = ["«视觉侧信道证据（系统，非用户原文）»"]
    for index, ref in enumerate(refs, start=1):
        ref_intent = str(ref.get("visual_intent") or intent).strip().lower() or intent
        # Re-gate with current user intent when present on the request
        effective = intent if intent != "react" else ref_intent
        summary = str(ref.get("visual_summary") or "").strip()
        identity = ref.get("visual_identity") or []
        observation = str(ref.get("visual_observation") or "").strip()
        ocr = str(ref.get("visual_ocr") or "").strip()
        short = str(ref.get("image_sha256_short") or "")[:8]

        if effective == "react":
            piece = (
                "、".join(str(x) for x in identity if x)
                if isinstance(identity, list) and identity
                else ""
            )
            if not piece:
                continue
            lines.append(f"图{index}({short}): {piece}" if short else f"图{index}: {piece}")
            continue
        if effective == "identify":
            if isinstance(identity, list) and identity:
                piece = summary or "、".join(str(x) for x in identity if x)
            else:
                piece = summary or "未能确认具体角色"
            lines.append(f"图{index}({short}): {piece}" if short else f"图{index}: {piece}")
            continue
        if effective == "ocr":
            piece = f"图上文字：{ocr}" if ocr else (summary or observation or "无文字")
            lines.append(f"图{index}({short}): {piece}" if short else f"图{index}: {piece}")
            continue
        # describe: prefer full observation (summary may have been react-gated at attach)
        piece = observation or summary
        if not piece:
            continue
        lines.append(f"图{index}({short}): {piece}" if short else f"图{index}: {piece}")

    if len(lines) <= 1:
        return None
    return "\n".join(lines)


def _is_trusted_identity(recognition: CharacterRecognition) -> bool:
    if not recognition.matched or not recognition.character_name:
        return False
    if recognition.difference is None or recognition.threshold is None:
        return True
    trusted_limit = max(0.0, recognition.threshold - _IDENTITY_ASSERTION_SAFETY_MARGIN)
    return recognition.difference <= trusted_limit


def _primary_body(evidence: VisualEvidence, *, has_character_match: bool) -> str | None:
    if evidence.vision_description:
        return evidence.vision_description
    if evidence.sticker is None or not evidence.sticker.description:
        return None
    if has_character_match and evidence.sticker.weak_authority:
        return None
    return evidence.sticker.description


def _character_labels_list(recognitions: tuple[CharacterRecognition, ...]) -> list[str]:
    labels: list[str] = []
    for r in recognitions:
        context_label = r.context_label or r.work
        if context_label:
            labels.append(f"{r.character_name}（{context_label}）")
        else:
            labels.append(str(r.character_name))
    return labels


def _character_labels(recognitions: tuple[CharacterRecognition, ...]) -> str:
    return "、".join(_character_labels_list(recognitions))
