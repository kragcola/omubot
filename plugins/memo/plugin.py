"""MemoPlugin: 记忆卡片系统。

通过 on_pre_prompt 注入全局索引和实体卡片到 system prompt，
通过 on_post_reply 在回复后提取新记忆。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, cast

from loguru import logger
from pydantic import BaseModel

from kernel.types import (
    AmadeusPlugin,
    PluginContext,
    PromptContext,
    ReplyContext,
)
from services.llm.llm_request import LLMRequest
from services.memory.card_store import Card, CardStore, NewCard
from services.memory.visibility import default_visibility_for_extraction
from services.memory.visual_identity import (
    VisualIdentityRecord,
    VisualIdentityStore,
    normalize_image_sha256,
)
from services.memory.write_policy import (
    allows_supersede,
    find_duplicate_target,
    is_high_confidence_duplicate,
)


class MemoConfig(BaseModel):
    """备忘录系统配置。"""

    dir: str = "storage/memories"
    user_max_chars: int = 300
    group_max_chars: int = 500
    index_max_lines: int = 200
    history_enabled: bool = True
    write_policy_enabled: bool = True


_L = logger.bind(channel="system")

_VALID_CATEGORIES = frozenset(
    {
        "preference",
        "boundary",
        "relationship",
        "event",
        "promise",
        "fact",
        "status",
    }
)

_MAX_ACTIVE_CONTEXT = 24
_MAX_WRITES_PER_TURN = 3

_EXTRACT_SYSTEM_LEGACY = """你是一个信息观察助手。分析以下一轮对话，提取关于用户的需要记住的新事实。

规则：
- 只提取事实性信息：称呼偏好、性格特点、兴趣爱好、身份背景、重要观点、关系变化
- 每条一行，格式："[category] 事实描述"
- category 必须是以下之一：
  preference（偏好/喜好/称呼）| boundary（边界/不喜欢的）| relationship（关系/角色）
  | event（发生的重要事情）| promise（承诺/答应了什么）| fact（一般事实/背景/兴趣）| status（当前状态/情绪）
- 不要记流水账（如"用户打了招呼"、"用户问了问题"）
- 不要重复显而易见的信息（如"用户正在和助手聊天"）
- 如果没有值得记录的新信息，输出"无"（就一个字）
- 最多输出3条，宁缺毋滥

示例输出：
[preference] 用户偏好被称呼为"帆酱"
[fact] 用户喜欢玩音游，最近在玩啤酒烧烤
[relationship] 用户是群管理员，负责维护秩序"""

_EXTRACT_SYSTEM_WRITE_POLICY = """你是一个信息观察助手。分析以下一轮对话，并对照用户已有记忆卡片，决定如何写入。

规则：
- 只处理关于当前用户的事实；不要写群级/全局记忆
- 每条输出一行 JSON 对象（JSONL），字段：
  {"category":"...","content":"...","action":"add|reinforce|supersede|skip","target_card_id":"...可选"}
- category 必须是：preference | boundary | relationship | event | promise | fact | status
- action 含义：
  - add：全新事实，尚无对应 active 卡
  - reinforce：再次确认已有事实（target 指向同 category 的 active 卡）
  - supersede：事实更新/纠正，替换同 category 的旧卡（需要用户明确更新信号，并给 target）
  - skip：不值得写入
- target_card_id 只能引用下方「已有记忆」列表中的 card_id，且必须同 category
- 不要记流水账；无新信息时只输出一个字：无
- 最多 3 行 JSON，宁缺毋滥
- 兼容旧格式时也可输出 [category] 内容（视为 add）

示例：
{"category":"preference","content":"用户偏好被称呼为帆酱","action":"add"}
{"category":"fact","content":"用户住在上海","action":"supersede","target_card_id":"card_xxxx"}
"""

# Keep historical name used by older callers / docs.
_EXTRACT_SYSTEM = _EXTRACT_SYSTEM_LEGACY

_BRACKET_LINE_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
_VISUAL_PLACEHOLDER_RE = re.compile(r"(?:«图片»|«动画表情»|\[图片\])")
_VISUAL_IDENTITY_CORRECTION_RE = re.compile(
    r"^(?:这|这个|这张(?:图|图片)?|图里(?:这个)?|图片里(?:这个)?)"
    r"\s*(?:是|叫)\s*(?P<label>.+?)\s*[。.!！]?$"
)
_VISUAL_LABEL_FORBIDDEN_RE = re.compile(r"[，,。！？!?；;：:\n\r\t<>\[\]{}]")
_VISUAL_LABEL_TRAILING_HEDGE = frozenset(("吧", "吗", "呢", "啊", "呀", "哦", "嘛"))
_VISUAL_LABEL_GENERIC = frozenset(
    ("人", "人物", "角色", "这个", "图片", "照片", "表情包", "不知道", "不确定")
)


def _parse_visual_identity_correction(
    ctx: ReplyContext,
) -> tuple[str, str, dict[str, Any]] | None:
    """Return one explicit full-hash image correction, otherwise fail closed."""
    if str(ctx.trigger_mode or "").strip() != "correction":
        return None
    evidence_items = list(ctx.visual_evidence or [])
    if len(evidence_items) != 1:
        return None
    evidence = evidence_items[0]
    if not isinstance(evidence, dict):
        return None
    if str(evidence.get("provenance") or "") != "visual_system":
        return None
    image_sha256 = normalize_image_sha256(
        str(evidence.get("image_sha256") or "")
    )
    if image_sha256 is None:
        return None

    user_text = _VISUAL_PLACEHOLDER_RE.sub("", str(ctx.user_msg or ""))
    user_text = " ".join(user_text.split()).strip()
    match = _VISUAL_IDENTITY_CORRECTION_RE.fullmatch(user_text)
    if match is None:
        return None
    label = match.group("label").strip().strip("\"'“”‘’「」『』")
    if not label or len(label) > 40:
        return None
    if _VISUAL_LABEL_FORBIDDEN_RE.search(label):
        return None
    if label in _VISUAL_LABEL_GENERIC or label[-1:] in _VISUAL_LABEL_TRAILING_HEDGE:
        return None
    return image_sha256, label, dict(evidence)


class MemoExtractor:
    """Extract observations after each conversation turn, writing typed cards to CardStore."""

    def __init__(
        self,
        card_store: CardStore,
        api_call: Any,
        config: MemoConfig | None = None,
        *,
        visual_identity_store: VisualIdentityStore | None = None,
        retrieval: Any = None,
    ) -> None:
        self._store = card_store
        self._call = api_call
        self._config = config if config is not None else MemoConfig()
        self._visual_identity_store = visual_identity_store
        self._retrieval = retrieval
        self._stats: dict[str, int] = {
            "add": 0,
            "reinforce": 0,
            "supersede": 0,
            "skip": 0,
            "invalid": 0,
            "visual_correction": 0,
        }

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def get_write_stats(self) -> dict[str, int]:
        return dict(self._stats)

    def _bump(self, key: str, n: int = 1) -> None:
        self._stats[key] = int(self._stats.get(key, 0)) + n

    def bind_visual_identity_store(
        self, store: VisualIdentityStore | None
    ) -> None:
        """Optional late-bind for visual-identity store (Codex wiring)."""
        self._visual_identity_store = store

    def bind_retrieval(self, retrieval: Any) -> None:
        """Optional late-bind so extractor completion can invalidate caches."""
        self._retrieval = retrieval

    def _invalidate_after_write(
        self,
        *,
        user_id: str,
        group_id: str | None,
    ) -> None:
        """Invalidate user entity cache and group path when present."""
        retrieval = self._retrieval
        if retrieval is None:
            return
        invalidate = getattr(retrieval, "invalidate_entity", None)
        if not callable(invalidate):
            return
        uid = str(user_id or "").strip()
        if uid:
            invalidate("user", uid)
        gid = str(group_id).strip() if group_id else ""
        if gid:
            invalidate("group", gid)

    async def store_visual_correction(
        self,
        *,
        image_sha256: str,
        entity_label: str,
        user_id: str,
        group_id: str | None = None,
        source_message_id: Any = None,
        visibility: str | None = None,
        provenance: str = "user_correction",
        visual_evidence: dict[str, Any] | None = None,
        trigger: dict[str, Any] | None = None,
    ) -> VisualIdentityRecord | None:
        """Store an explicit visual identity correction (not a preference card).

        Bounded public API for later Codex ABI wiring. Does **not** write a
        preference/fact card — only the exact visual-identity store.
        Accepts optional visual_evidence/trigger side-channel parameters.
        """
        store = self._visual_identity_store
        if store is None:
            _L.debug(
                "visual correction skipped | no VisualIdentityStore bound | user={}",
                user_id,
            )
            return None
        uid = str(user_id or "").strip()
        if not uid or uid == "0":
            return None
        origin = str(group_id).strip() if group_id else None
        if origin == "":
            origin = None
        src = (
            str(source_message_id).strip()
            if source_message_id is not None
            else None
        )
        if src == "":
            src = None
        try:
            record = await store.store_correction(
                image_sha256=image_sha256,
                entity_label=entity_label,
                correcting_user_id=uid,
                origin_group_id=origin,
                visibility=visibility,
                source_message_id=src,
                provenance=provenance,
                visual_evidence=visual_evidence,
                trigger=trigger,
            )
        except ValueError as exc:
            _L.debug(
                "visual correction rejected | user={} error={}",
                uid,
                exc,
            )
            self._bump("invalid")
            return None
        self._bump("visual_correction")
        self._invalidate_after_write(user_id=uid, group_id=origin)
        _L.info(
            "visual correction stored | user={} sha={} label={!r}",
            uid,
            record.image_sha256[:12],
            record.entity_label,
        )
        return record

    async def extract_after_turn(
        self,
        user_id: str,
        group_id: str | None,
        user_msg: str,
        bot_reply: str,
        source_message_id: Any = None,
    ) -> None:
        """Extract key facts from a turn and write typed cards.

        Runs as a fire-and-forget background task — failures are logged
        but never surface to the user.
        """
        if not user_id or user_id == "0":
            return

        src = (
            str(source_message_id).strip()
            if source_message_id is not None
            else None
        )
        if src == "":
            src = None

        if self._config.write_policy_enabled:
            written = await self._extract_with_write_policy(
                user_id=user_id,
                group_id=group_id,
                user_msg=user_msg,
                bot_reply=bot_reply,
                source_message_id=src,
            )
        else:
            written = await self._extract_legacy_add_only(
                user_id=user_id,
                group_id=group_id,
                user_msg=user_msg,
                bot_reply=bot_reply,
                source_message_id=src,
            )
        if written:
            self._invalidate_after_write(user_id=user_id, group_id=group_id)

    async def _extract_legacy_add_only(
        self,
        *,
        user_id: str,
        group_id: str | None,
        user_msg: str,
        bot_reply: str,
        source_message_id: str | None,
    ) -> int:
        # Storage scope remains user; visibility/origin capture conversation
        # source so group recall can fail closed for private/other-group facts.
        origin_group_id = str(group_id).strip() if group_id else None
        if origin_group_id == "":
            origin_group_id = None
        visibility = default_visibility_for_extraction(group_id=origin_group_id)
        user_msg_clean = user_msg[:300].replace("\n", " ")
        bot_reply_clean = bot_reply[:300].replace("\n", " ")
        conversation = (
            f"用户({user_id}): {user_msg_clean}\n"
            f"助手: {bot_reply_clean}"
        )

        try:
            request = LLMRequest(
                task="memo",
                user_id=str(user_id),
                static_blocks=[_EXTRACT_SYSTEM_LEGACY],
                user_messages=[{"role": "user", "content": conversation}],
                max_tokens=256,
                requires_capabilities=("chat",),
            )
            result = await self._call(request)
        except Exception:
            _L.debug("memo extractor LLM call failed | user={}", user_id)
            return 0

        text: str = result.get("text", "").strip()
        if not text or text == "无":
            return 0

        written = 0
        for line in text.split("\n"):
            if written >= _MAX_WRITES_PER_TURN:
                break
            parsed = self._parse_line(line)
            if parsed is None:
                continue
            category, content, action, _target = parsed
            if action not in (None, "add"):
                if action == "skip":
                    self._bump("skip")
                continue
            if not category or category not in _VALID_CATEGORIES or not content:
                self._bump("invalid")
                continue
            try:
                await self._store.add_card(
                    NewCard(
                        category=category,
                        scope="user",
                        scope_id=user_id,
                        content=content,
                        confidence=0.6,
                        source="extractor",
                        origin_group_id=origin_group_id,
                        visibility=visibility,
                        subject_user_id=user_id,
                    ),
                    source_msg_id=source_message_id,
                    captured_by="memo_extractor",
                )
                written += 1
                self._bump("add")
            except (ValueError, Exception):
                self._bump("invalid")
                _L.warning(
                    "memo extractor add_card failed | user={} category={}",
                    user_id,
                    category,
                )

        if written:
            _L.info("cards extracted | user={} count={}", user_id, written)
        return written

    async def _extract_with_write_policy(
        self,
        *,
        user_id: str,
        group_id: str | None,
        user_msg: str,
        bot_reply: str,
        source_message_id: str | None,
    ) -> int:
        # Never auto-write group/global storage scope; still preserve origin
        # conversation group_id for same_group visibility.
        origin_group_id = str(group_id).strip() if group_id else None
        if origin_group_id == "":
            origin_group_id = None
        visibility = default_visibility_for_extraction(group_id=origin_group_id)
        user_msg_clean = user_msg[:300].replace("\n", " ")
        bot_reply_clean = bot_reply[:300].replace("\n", " ")
        conversation = (
            f"用户({user_id}): {user_msg_clean}\n"
            f"助手: {bot_reply_clean}"
        )

        # Full active set for deterministic duplicate protection; LLM prompt
        # still sees only the top-_MAX_ACTIVE_CONTEXT window.
        all_active_cards = await self._store.get_entity_cards(
            "user", user_id, status="active"
        )
        prompt_cards = self._cap_active_context(all_active_cards)
        # Explicit target_card_id allowlist = only cards rendered in the
        # 24-cap prompt window. Full active set stays on full_active for
        # deterministic find_duplicate_target (add→reinforce protection).
        active_by_id = {c.card_id: c for c in prompt_cards}
        context_block = self._format_active_context(prompt_cards)

        try:
            request = LLMRequest(
                task="memo",
                user_id=str(user_id),
                static_blocks=[_EXTRACT_SYSTEM_WRITE_POLICY],
                stable_blocks=[context_block] if context_block else [],
                user_messages=[{"role": "user", "content": conversation}],
                max_tokens=512,
                requires_capabilities=("chat",),
            )
            result = await self._call(request)
        except Exception:
            _L.debug("memo extractor LLM call failed | user={}", user_id)
            return 0

        text: str = (result.get("text") or "").strip()
        if not text or text == "无":
            return 0

        decisions = self._parse_llm_decisions(text)
        writes = 0
        # Mutable working set for full-scope dup checks within this turn.
        full_active = list(all_active_cards)
        for decision in decisions:
            if writes >= _MAX_WRITES_PER_TURN:
                break
            applied = await self._apply_decision(
                decision,
                user_id=user_id,
                user_msg=user_msg,
                source_message_id=source_message_id,
                active_by_id=active_by_id,
                active_cards=full_active,
                origin_group_id=origin_group_id,
                visibility=visibility,
            )
            if applied:
                writes += 1
                card = applied.get("card")
                if isinstance(card, Card) and card.status == "active":
                    active_by_id[card.card_id] = card
                    if all(c.card_id != card.card_id for c in full_active):
                        full_active.append(card)

        if writes:
            _L.info(
                "cards write-policy applied | user={} writes={}",
                user_id,
                writes,
            )
        return writes

    @staticmethod
    def _cap_active_context(cards: list[Card]) -> list[Card]:
        """Prefer higher confidence / priority for the 24-cap LLM window."""
        cards_sorted = sorted(
            cards,
            key=lambda c: (c.confidence, c.priority, c.updated_at),
            reverse=True,
        )
        return cards_sorted[:_MAX_ACTIVE_CONTEXT]

    async def _load_active_context(self, user_id: str) -> list[Card]:
        cards = await self._store.get_entity_cards("user", user_id, status="active")
        return self._cap_active_context(cards)

    @staticmethod
    def _format_active_context(cards: list[Card]) -> str:
        if not cards:
            return "【已有记忆】\n（无）"
        lines = ["【已有记忆】"]
        for c in cards:
            lines.append(
                f"- id={c.card_id} category={c.category} content={c.content}"
            )
        return "\n".join(lines)

    def _parse_llm_decisions(self, text: str) -> list[dict[str, Any]]:
        """Parse JSONL objects and legacy [category] lines into decision dicts."""
        out: list[dict[str, Any]] = []
        stripped = text.strip()
        # Accept a single JSON array of objects.
        if stripped.startswith("["):
            try:
                arr = json.loads(stripped)
                if isinstance(arr, list):
                    for item in arr:
                        if isinstance(item, dict):
                            out.append(self._normalize_decision_dict(item))
                        if len(out) >= _MAX_WRITES_PER_TURN:
                            break
                    return [d for d in out if d]
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        for raw_line in text.splitlines():
            if len(out) >= _MAX_WRITES_PER_TURN:
                break
            line = raw_line.strip()
            if not line or line == "无":
                continue
            # JSON object line
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    self._bump("invalid")
                    continue
                if isinstance(obj, dict):
                    out.append(self._normalize_decision_dict(obj))
                else:
                    self._bump("invalid")
                continue
            # Legacy bracket line → add
            m = _BRACKET_LINE_RE.match(line)
            if m:
                category = m.group(1).strip()
                content = m.group(2).strip()
                out.append(
                    {
                        "category": category,
                        "content": content,
                        "action": "add",
                        "target_card_id": None,
                    }
                )
                continue
        return [d for d in out if d]

    @staticmethod
    def _normalize_decision_dict(obj: dict[str, Any]) -> dict[str, Any]:
        category = str(obj.get("category") or "").strip()
        content = str(obj.get("content") or "").strip()
        action = str(obj.get("action") or "add").strip().lower()
        target = obj.get("target_card_id") or obj.get("target") or None
        target_s = str(target).strip() if target is not None else None
        if target_s == "":
            target_s = None
        return {
            "category": category,
            "content": content,
            "action": action,
            "target_card_id": target_s,
        }

    def _parse_line(
        self, line: str
    ) -> tuple[str, str, str | None, str | None] | None:
        line = line.strip()
        if not line:
            return None
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
            if not isinstance(obj, dict):
                return None
            d = self._normalize_decision_dict(obj)
            return d["category"], d["content"], d["action"], d["target_card_id"]
        m = _BRACKET_LINE_RE.match(line)
        if not m:
            return None
        return m.group(1).strip(), m.group(2).strip(), "add", None

    async def _apply_decision(
        self,
        decision: dict[str, Any],
        *,
        user_id: str,
        user_msg: str,
        source_message_id: str | None,
        active_by_id: dict[str, Card],
        active_cards: list[Card],
        origin_group_id: str | None = None,
        visibility: str | None = None,
    ) -> dict[str, Any] | None:
        category = decision.get("category") or ""
        content = decision.get("content") or ""
        action = (decision.get("action") or "add").lower()
        raw_target = decision.get("target_card_id")
        target_id = str(raw_target).strip() if raw_target is not None else None
        if target_id == "":
            target_id = None
        # Explicit target was supplied by the LLM (even if it fails validation).
        explicit_target_provided = target_id is not None
        write_visibility = visibility or default_visibility_for_extraction(
            group_id=origin_group_id
        )

        if action == "skip":
            self._bump("skip")
            return None

        if category not in _VALID_CATEGORIES:
            self._bump("invalid")
            return None
        if not content or not str(content).strip():
            self._bump("invalid")
            return None
        content = str(content).strip()

        # Resolve target only if present, same user scope, same category, active.
        target: Card | None = None
        if target_id:
            cand = active_by_id.get(str(target_id))
            if (
                cand is not None
                and cand.scope == "user"
                and cand.scope_id == user_id
                and cand.category == category
                and cand.status == "active"
            ):
                target = cand

        # Deterministic high-confidence duplicate may override add → reinforce
        # using the FULL active set (not just the 24-card prompt window).
        if action == "add":
            dup = find_duplicate_target(
                content, active_cards, category=category
            )
            if isinstance(dup, Card):
                action = "reinforce"
                target = dup

        if action == "reinforce":
            # Explicit reinforce with an invalid target_card_id: reject only.
            # Do NOT retarget via find_duplicate_target and do NOT fall through
            # to add — even when content matches a local same-category card.
            if target is None and explicit_target_provided:
                self._bump("invalid")
                return None
            # No explicit target: may resolve via same-category full-scope dup.
            if target is None:
                dup = find_duplicate_target(
                    content, active_cards, category=category
                )
                if isinstance(dup, Card):
                    target = dup
            if target is None:
                # Cannot reinforce without a valid same-category target.
                # If content is truly new, fall through to add; else skip.
                has_dup = any(
                    c.category == category
                    and is_high_confidence_duplicate(content, c.content)
                    for c in active_cards
                )
                if has_dup:
                    self._bump("skip")
                    return None
                action = "add"
            else:
                try:
                    ok = await self._store.reinforce(
                        target.card_id,
                        boost=0.1,
                        evidence_text=content,
                        source_message_id=source_message_id,
                        captured_by="memo_extractor",
                        decision="reinforce",
                    )
                except Exception:
                    self._bump("invalid")
                    return None
                if ok:
                    self._bump("reinforce")
                    refreshed = await self._store.get_card(target.card_id)
                    return {"action": "reinforce", "card": refreshed}
                self._bump("skip")
                return None

        if action == "supersede":
            # User message alone authorizes supersede (cue + lexical support).
            # NEVER let LLM content self-authorize via has_explicit_update_signal.
            if not allows_supersede(user_msg, content) or target is None:
                self._bump("skip")
                return None
            try:
                new_id = await self._store.supersede_card(
                    target.card_id,
                    NewCard(
                        category=category,
                        scope="user",
                        scope_id=user_id,
                        content=content,
                        confidence=0.7,
                        source="extractor",
                        supersedes=target.card_id,
                        origin_group_id=origin_group_id,
                        visibility=write_visibility,
                        subject_user_id=user_id,
                    ),
                    source_msg_id=source_message_id,
                    captured_by="memo_extractor",
                    evidence_text=user_msg[:300] if user_msg else content,
                )
            except Exception:
                self._bump("invalid")
                return None
            self._bump("supersede")
            active_by_id.pop(target.card_id, None)
            active_cards[:] = [
                c for c in active_cards if c.card_id != target.card_id
            ]
            new_card = await self._store.get_card(new_id)
            return {"action": "supersede", "card": new_card}

        if action == "add":
            try:
                new_id = await self._store.add_card(
                    NewCard(
                        category=category,
                        scope="user",
                        scope_id=user_id,
                        content=content,
                        confidence=0.6,
                        source="extractor",
                        origin_group_id=origin_group_id,
                        visibility=write_visibility,
                        subject_user_id=user_id,
                    ),
                    source_msg_id=source_message_id,
                    captured_by="memo_extractor",
                )
            except (ValueError, Exception):
                self._bump("invalid")
                return None
            self._bump("add")
            new_card = await self._store.get_card(new_id)
            return {"action": "add", "card": new_card}

        # Unknown action
        self._bump("invalid")
        return None


class MemoPlugin(AmadeusPlugin):
    name = "memo"
    description = "记忆系统：卡片索引、实体记忆注入、对话后提取"
    version = "1.1.6"
    priority = 30

    def __init__(self) -> None:
        super().__init__()
        self._card_store = None
        self._retrieval = None
        self._memo_extractor = None
        self._pending_extractions: set[asyncio.Task[None]] = set()
        self._context_takeover = False
        # Cache for global index (TTL-based)
        self._index_cache: tuple[str, float] | None = None
        self._index_ttl: float = 300.0  # 5 minutes

    async def on_startup(self, ctx: PluginContext) -> None:
        self._card_store = ctx.card_store
        self._retrieval = ctx.retrieval
        self._memo_extractor = ctx.memo_extractor
        self._context_takeover = getattr(ctx, "context_prompt_owner", "") == "context"
        # Late-bind retrieval so extractor completion invalidates user+group caches.
        if self._memo_extractor is not None and self._retrieval is not None:
            bind = getattr(self._memo_extractor, "bind_retrieval", None)
            if callable(bind):
                bind(self._retrieval)
        visual_store = getattr(ctx, "visual_identity_store", None)
        if self._memo_extractor is not None and visual_store is not None:
            bind_vi = getattr(self._memo_extractor, "bind_visual_identity_store", None)
            if callable(bind_vi):
                bind_vi(visual_store)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        tasks = tuple(self._pending_extractions)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending_extractions.clear()
        self._memo_extractor = None

    def register_tools(self) -> list[Any]:
        if self._card_store is None:
            return []
        from services.tools.memo_tools import CardLookupTool, CardUpdateTool
        return [CardLookupTool(self._card_store), CardUpdateTool(self._card_store)]

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self._card_store is None:
            return

        # Global index (stable — rarely changes)
        index_text = await self._build_global_index()
        if index_text:
            ctx.add_block(
                text=index_text,
                label="全局索引",
                position="stable",
                priority=15,
                source="memory",
            )

        if self._context_takeover:
            return

        # Entity memo (dynamic — per-session gating)
        if self._retrieval is not None and ctx.session_id:
            memo_text = await self._retrieval.build_memo_block(
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                conversation_text=ctx.conversation_text,
            )
        elif self._card_store is not None:
            if ctx.group_id:
                body = await self._card_store.build_entity_prompt("group", ctx.group_id)
                memo_text = f"【当前在群 #{ctx.group_id} 中对话】\n{body}"
            else:
                body = await self._card_store.build_entity_prompt("user", ctx.user_id)
                memo_text = f"【当前私聊 @{ctx.user_id}】\n{body}"
        else:
            memo_text = ""

        if memo_text:
            ctx.add_block(
                text=memo_text,
                label="记忆卡片",
                position="dynamic",
                priority=25,
                source="memory",
            )

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        if self._memo_extractor is None or not ctx.user_id or ctx.user_id == "0":
            return
        visual_correction = _parse_visual_identity_correction(ctx)
        if visual_correction is not None:
            image_sha256, entity_label, visual_evidence = visual_correction
            store_correction = getattr(
                self._memo_extractor,
                "store_visual_correction",
                None,
            )
            if callable(store_correction):
                await cast(Any, store_correction)(
                    image_sha256=image_sha256,
                    entity_label=entity_label,
                    user_id=ctx.user_id,
                    group_id=ctx.group_id,
                    source_message_id=ctx.source_message_id,
                    visual_evidence=visual_evidence,
                    trigger={
                        "mode": ctx.trigger_mode,
                        "evidence_count": len(ctx.visual_evidence or []),
                    },
                )
                self._index_cache = None
            else:
                _L.warning(
                    "visual correction skipped | extractor has no durable store API | user={}",
                    ctx.user_id,
                )
            # Never pass an explicit visual identity correction to the generic
            # card extractor, where it could become a preference/fact card.
            return
        task = asyncio.create_task(
            self._memo_extractor.extract_after_turn(
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                user_msg=ctx.user_msg,
                bot_reply=ctx.reply_content,
                source_message_id=ctx.source_message_id,
            ),
            name=f"plugin:memo:extract:{ctx.group_id or 'private'}:{ctx.user_id}",
        )

        scope = "group" if ctx.group_id else "user"
        scope_id = ctx.group_id if ctx.group_id else ctx.user_id

        def _on_extraction_done(t: asyncio.Task[None]) -> None:
            self._pending_extractions.discard(t)
            if not t.cancelled():
                error = t.exception()
                if error is not None:
                    _L.error(
                        "memo extraction task failed | error={}: {}",
                        type(error).__name__,
                        error,
                    )
            self._index_cache = None
            if self._retrieval is not None:
                # Always invalidate the user entity path; group path when present.
                # Extractor may also invalidate, but plugin completion is the
                # durable post-task boundary.
                self._retrieval.invalidate_entity("user", ctx.user_id)
                if scope == "group" and scope_id:
                    self._retrieval.invalidate_entity(scope, scope_id)

        self._pending_extractions.add(task)
        task.add_done_callback(_on_extraction_done)

    async def _build_global_index(self) -> str:
        """Build the global card index with a short-lived in-memory cache."""
        now = time.monotonic()
        if self._index_cache is not None:
            cached_text, cached_at = self._index_cache
            if now - cached_at < self._index_ttl:
                return cached_text
        if self._card_store is None:
            return ""
        index_text = await self._card_store.build_global_index()
        self._index_cache = (index_text, now)
        return index_text
