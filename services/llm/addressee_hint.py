"""Build request-time addressee hints for group replies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.name_registry import MemberInfo, NameVariationRegistry


@dataclass(frozen=True, slots=True)
class AddresseeHintResult:
    target_uid: int
    nickname: str
    qq: int
    confidence: float
    provenance: str


class AddresseeHintDetector:
    def __init__(self, registry: NameVariationRegistry) -> None:
        self._registry = registry

    def detect(
        self,
        *,
        group_id: str,
        trigger: Any | None,
        fallback_user_id: str = "",
        bot_self_id: str = "",
    ) -> AddresseeHintResult | None:
        reply_sender_id = _safe_str(_extra(trigger, "reply_sender_id"))
        if reply_sender_id and reply_sender_id != str(bot_self_id or ""):
            member = self._lookup_member(group_id, reply_sender_id)
            if member is not None:
                return _result(member, confidence=1.0, provenance="reply_trigger")
        if trigger is not None and str(getattr(trigger, "mode", "") or "") == "at_mention":
            target_user_id = _safe_str(getattr(trigger, "target_user_id", ""))
            member = self._lookup_member(group_id, target_user_id)
            if member is not None:
                return _result(member, confidence=1.0, provenance="at_trigger")
        member = self._lookup_member(group_id, fallback_user_id)
        if member is not None:
            return _result(member, confidence=0.7, provenance="last_speaker")
        for recent in self._registry.recent_speakers(group_id, limit=5):
            if str(recent.user_id) != str(bot_self_id or ""):
                return _result(recent, confidence=0.6, provenance="recent_speaker")
        return None

    @staticmethod
    def build_hint(result: AddresseeHintResult) -> str:
        return f"[当前你在回复：{result.nickname}（QQ: {result.qq}）]"

    def _lookup_member(self, group_id: str, user_id: str) -> MemberInfo | None:
        try:
            uid = int(str(user_id or "").strip())
        except (TypeError, ValueError):
            return None
        if uid <= 0:
            return None
        return self._registry.lookup_by_uid(group_id, uid)


def _extra(trigger: Any | None, key: str) -> Any:
    extra = getattr(trigger, "extra", None)
    if isinstance(extra, dict):
        return extra.get(key)
    return None


def _safe_str(value: object) -> str:
    return str(value or "").strip()


def _result(member: MemberInfo, *, confidence: float, provenance: str) -> AddresseeHintResult:
    nickname = member.card or member.nickname or str(member.user_id)
    return AddresseeHintResult(
        target_uid=member.user_id,
        nickname=nickname,
        qq=member.user_id,
        confidence=confidence,
        provenance=provenance,
    )
