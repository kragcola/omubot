"""Structured visual evidence rendering for chat prompt input.

The router still returns a compact text preview for downstream prompt builders,
but this module keeps the intermediate evidence explicit: sticker lookup,
character recognition, low-confidence candidates, and VL description are
annotations on the same image rather than mutually exclusive shortcuts.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.media.character_recognizer import CharacterRecognition


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
    image_sha256_short: str
    sticker: StickerEvidence | None = None
    recognitions: tuple[CharacterRecognition, ...] = ()
    vision_description: str | None = None

    @property
    def matched(self) -> tuple[CharacterRecognition, ...]:
        return tuple(r for r in self.recognitions if r.matched and r.character_name)

    @property
    def unmatched(self) -> tuple[CharacterRecognition, ...]:
        return tuple(r for r in self.recognitions if not (r.matched and r.character_name))

    @property
    def detection_count(self) -> int:
        counts = [r.detection_count for r in self.recognitions if r.detection_count is not None]
        if counts:
            return max(int(c) for c in counts)
        return len(self.recognitions)


def render_visual_evidence(evidence: VisualEvidence) -> str | None:
    """Render structured evidence into the compact image preview used in prompts."""
    matched = evidence.matched
    body = _primary_body(evidence, has_character_match=bool(matched))

    if matched:
        labels = _character_labels(matched)
        if evidence.detection_count > len(matched):
            summary = (
                f"检测到{evidence.detection_count}个角色/头像；"
                f"可信识别：{labels}；"
                f"其余{evidence.detection_count - len(matched)}个未达到置信阈值"
            )
            candidates = _low_confidence_candidates(evidence.unmatched)
            if candidates:
                summary = f"{summary}（低置信候选：{candidates}）"
            return f"{summary}：{body}" if body else summary
        return f"{labels}：{body}" if body else f"{labels}表情包"

    if evidence.recognitions:
        count = evidence.detection_count or len(evidence.recognitions)
        summary = f"检测到{count}个角色/头像；未能可信识别具体角色"
        candidates = _low_confidence_candidates(evidence.unmatched)
        if candidates:
            summary = f"{summary}（低置信候选：{candidates}）"
        return f"{summary}：{body}" if body else summary

    if evidence.sticker is not None and evidence.sticker.description and not evidence.sticker.weak_authority:
        return evidence.sticker.description
    if evidence.vision_description:
        return evidence.vision_description
    if evidence.sticker is not None and evidence.sticker.description:
        return evidence.sticker.description
    return None


def _primary_body(evidence: VisualEvidence, *, has_character_match: bool) -> str | None:
    if evidence.vision_description:
        return evidence.vision_description
    if evidence.sticker is None or not evidence.sticker.description:
        return None
    if has_character_match and evidence.sticker.weak_authority:
        return None
    return evidence.sticker.description


def _character_labels(recognitions: tuple[CharacterRecognition, ...]) -> str:
    labels: list[str] = []
    for r in recognitions:
        context_label = r.context_label or r.work
        if context_label:
            labels.append(f"{r.character_name}（{context_label}）")
        else:
            labels.append(str(r.character_name))
    return "、".join(labels)


def _low_confidence_candidates(recognitions: tuple[CharacterRecognition, ...]) -> str:
    labels: list[str] = []
    seen: set[str] = set()
    for r in recognitions:
        name = r.candidate_character_name or r.candidate_character_id
        if not name or name in seen:
            continue
        seen.add(name)
        if r.difference is not None:
            labels.append(f"{name} {r.difference:.3f}")
        else:
            labels.append(str(name))
        if len(labels) >= 3:
            break
    return "、".join(labels)
