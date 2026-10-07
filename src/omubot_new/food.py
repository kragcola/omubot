"""Consent-scoped food selection; model and outbound remain Actions' responsibility."""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from typing import Literal, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from .config import Config
from .policy import Policy
from .rich_messages import TextSegment
from .settings import SettingsService
from .store import Store, StoreConnection, request_digest
from .types import ConversationKey, ConversationScope, Event, OperationError, Scope, SendReceipt

TUTORIAL_TEXT = ("你还没设置过口味偏好呢~\n发送 /food help 查看如何设置，之后推荐会更准哦\n"
                 "（本消息只显示一次）")
PreferenceKind = Literal["like", "dislike", "location"]


def food_search_command(event: Event) -> bool | None:
    """Only an original plaintext administrator command can request a save."""
    if (event.scope.kind != "group" or event.reply_to or event.mention_targets
            or any(not isinstance(segment, TextSegment) for segment in event.rich_segments)):
        return None
    parts = event.text.strip().split()
    if len(parts) == 3 and parts[:2] in (["/food", "search"], ["/food", "搜索"]):
        if parts[2] in {"on", "off"}:
            return parts[2] == "on"
    return None


@dataclass(frozen=True, slots=True)
class FoodSearchSaved:
    saved_revision: int
    effective_revision: int
    saved_enabled: bool
    effective_enabled: bool

    @property
    def text(self) -> str:
        return (f"已保存本群食物联网设置（版本 {self.saved_revision}）："
                f"{'开启' if self.saved_enabled else '关闭'}。"
                f"当前运行版本 {self.effective_revision}："
                f"{'开启' if self.effective_enabled else '关闭'}；重载配置后生效。")


def food_search_enabled_for(config: Config, scope: ConversationScope) -> bool:
    """Private food never inherits any group search preference."""
    return bool(config.food_enabled and scope.kind == "group" and scope.group_id in config.food_groups
                and config.food_search_group_overrides.get(scope.group_id, config.food_search_enabled))


def _text(value: str, *, limit: int = 128) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise OperationError("invalid_food_input")
    value = value.strip()
    if any(ord(c) < 32 for c in value):
        raise OperationError("invalid_food_input")
    return value


@dataclass(frozen=True, slots=True)
class FoodItem:
    name: str
    tags: tuple[str, ...] = ()
    periods: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _text(self.name) != self.name or len(self.tags) > 16:
            raise OperationError("invalid_food_menu")
        for tag in self.tags:
            if _text(tag) != tag:
                raise OperationError("invalid_food_menu")
        if not set(self.periods) <= {"早餐", "早午餐", "午餐", "下午茶", "晚餐", "夜宵"}:
            raise OperationError("invalid_food_menu")

    def matches(self, term: str) -> bool:
        return term in self.name or any(term in tag for tag in self.tags)


# Public reference catalog; it describes menu items, not restaurant stock.
DEFAULT_MENU: tuple[FoodItem, ...] = tuple(
    FoodItem(record["name"], tuple(record["tags"]), tuple(record["periods"]))
    for record in json.loads(
        files("omubot_new").joinpath("food_menu.json").read_text(encoding="utf-8"))
)


@dataclass(frozen=True, slots=True)
class FoodPreferences:
    revision: int = 0
    likes: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()
    location: str = ""


@dataclass(frozen=True, slots=True)
class FoodTutorialClaim:
    code: Literal["claimed", "already_claimed", "history_claimed", "storage_unavailable"]

    @property
    def text(self) -> str:
        return TUTORIAL_TEXT if self.code == "claimed" else ""


@dataclass(frozen=True, slots=True)
class FoodSelection:
    token: str
    event: Event
    candidates: tuple[FoodItem, ...]
    preferred_names: tuple[str, ...]
    period: str
    hint: str
    preferences_revision: int
    tutorial: FoodTutorialClaim

    @property
    def code(self) -> str:
        return "ready" if self.candidates else "no_candidates"

    def model_input(self) -> str:
        """Only the constrained menu goes to a group model; no preference/location details."""
        rows = [f"{item.name} [{ ' | '.join(item.tags) }]" for item in self.candidates]
        preferred = "优先候选：" + "、".join(self.preferred_names) + "\n" if self.preferred_names else ""
        return (f"当前时段：{self.period}\n用户需求：{self.hint}\n"
                + preferred + "只能从以下候选中选择一个完整食物名称，不要添加理由：\n" + "\n".join(rows))


@dataclass(frozen=True, slots=True)
class FoodServed:
    name: str
    event_id: str
    receipt_id: str
    served_at: float


class FoodOwner:
    """Owns durable preferences/claims and ephemeral (user, group) feedback leases.

    The application must cancel its runtime task and call cancel_selection in a finally
    block. This owner launches no model, transport, or independent background task.
    """

    def __init__(self, store: Store, policy: Policy, menu: tuple[FoodItem, ...] = DEFAULT_MENU,
                 *, clock: Callable[[], float] = time.time, settings: SettingsService | None = None) -> None:
        if policy.store is not store:
            raise OperationError("food_owner_mismatch")
        if not menu or len(menu) > 2048 or len({item.name for item in menu}) != len(menu):
            raise OperationError("invalid_food_menu")
        self.store, self.policy, self.menu, self._clock = store, policy, menu, clock
        self.settings = settings
        self._active: dict[tuple[ConversationKey, str], str | FoodSelection] = {}
        self._pending: dict[tuple[ConversationKey, str], tuple[float, str]] = {}
        self._recent: dict[tuple[ConversationKey, str], list[tuple[float, str]]] = {}
        self._closed = False

    def _person(self, actor: str, scope: ConversationScope, user_id: str) -> tuple[ConversationKey, str]:
        if self._closed:
            raise OperationError("food_closed")
        if (_text(actor) != actor or _text(user_id) != user_id or actor != user_id
                or scope.bot_id != self.policy.bot_id
                or scope.kind == "private" and scope.private_user_id != user_id):
            raise OperationError("denied")
        return scope.key, user_id

    async def save_search(self, event: Event, *, expected_revision: int) -> FoodSearchSaved:
        enabled = food_search_command(event)
        if enabled is None or not isinstance(event.scope, Scope):
            raise OperationError("invalid_food_search_command")
        self._person(event.user_id, event.scope, event.user_id)
        settings = self.settings
        if settings is None or settings.store is not self.store:
            raise OperationError("food_settings_not_connected")
        async with self.policy.dispatch_boundary:
            for action in ("message.read", "food.search.configure"):
                await self.policy.check(event.user_id, event.scope, action, provider="", model="")
            snapshot = await settings.snapshot()
            if snapshot["revision"] != expected_revision:
                raise OperationError("revision_conflict")
            document = cast(dict[str, JsonValue], snapshot["config"])
            overrides = dict(cast(dict[str, JsonValue], document["food_search_group_overrides"]))
            overrides[event.scope.group_id] = enabled
            document["food_search_group_overrides"] = overrides
            saved = await settings.save(document, expected_revision=expected_revision, actor=event.user_id)
            return FoodSearchSaved(
                int(cast(int, saved["revision"])), int(cast(int, saved["effective_revision"])),
                enabled, food_search_enabled_for(settings.config, event.scope),
            )

    @staticmethod
    def _where(scope: ConversationScope, user_id: str) -> tuple[str, tuple[str, str, str]]:
        if scope.kind == "group":
            return "bot_id=? AND group_id=? AND user_id=?", (scope.bot_id, scope.group_id, user_id)
        return ("bot_id=? AND private_user_id=? AND user_id=?",
                (scope.bot_id, scope.private_user_id, user_id))

    @staticmethod
    def _table(scope: ConversationScope, *, served: bool = False) -> str:
        return ("food_" if scope.kind == "group" else "food_private_") + (
            "served" if served else "preferences")

    @classmethod
    def _write_preferences(
        cls, db: StoreConnection, scope: ConversationScope, user_id: str, preferences: FoodPreferences,
    ) -> None:
        target = scope.group_id if scope.kind == "group" else scope.private_user_id
        target_field = "group_id" if scope.kind == "group" else "private_user_id"
        db.execute(f"INSERT INTO {cls._table(scope)} VALUES (?,?,?,?,?,?,?) "
            f"ON CONFLICT(bot_id,{target_field},user_id) DO UPDATE SET revision=excluded.revision,"
            "likes=excluded.likes,dislikes=excluded.dislikes,location=excluded.location",
            (scope.bot_id, target, user_id, preferences.revision,
             json.dumps(preferences.likes, ensure_ascii=False),
             json.dumps(preferences.dislikes, ensure_ascii=False), preferences.location))

    def _now(self) -> float:
        now = self._clock()
        if not math.isfinite(now):
            raise OperationError("invalid_food_clock")
        return now

    def _cleanup(self, now: float) -> None:
        for key, (created, _) in tuple(self._pending.items()):
            if now - created >= 120:
                del self._pending[key]
        for key, items in tuple(self._recent.items()):
            current = [(created, name) for created, name in items if now - created < 1800]
            if current:
                self._recent[key] = current
            else:
                del self._recent[key]

    def _authorize(self, db: StoreConnection, actor: str, scope: ConversationScope, action: str) -> None:
        self.policy.check_transaction(db, actor, scope, action, "", "", False, False)

    @classmethod
    def _preferences(cls, db: StoreConnection, scope: ConversationScope, user_id: str) -> FoodPreferences:
        where, values = cls._where(scope, user_id)
        row = db.execute(f"SELECT * FROM {cls._table(scope)} WHERE {where}", values).fetchone()
        return (FoodPreferences(int(row["revision"]), tuple(json.loads(row["likes"])),
                                tuple(json.loads(row["dislikes"])), str(row["location"]))
                if row else FoodPreferences())

    @staticmethod
    def _silent_claim(db: StoreConnection, user_id: str, now: float) -> None:
        db.execute("INSERT OR IGNORE INTO food_tutorial_claims VALUES (?,?,?)",
                   (f"food_tutorial:{user_id}", user_id, now))

    async def preferences(self, *, actor: str, scope: ConversationScope, user_id: str) -> FoodPreferences:
        self._person(actor, scope, user_id)
        def read(db: StoreConnection) -> FoodPreferences:
            self._authorize(db, actor, scope, "memory.retrieve")
            return self._preferences(db, scope, user_id)
        return await self.store.transaction(read)

    async def set_preference(self, *, actor: str, scope: ConversationScope, user_id: str,
                             kind: PreferenceKind, value: str) -> FoodPreferences:
        self._person(actor, scope, user_id)
        value = _text(value)
        if kind not in {"like", "dislike", "location"}:
            raise OperationError("invalid_food_input")
        now = self._now()
        def write(db: StoreConnection) -> FoodPreferences:
            self._authorize(db, actor, scope, "memory.learn")
            old = self._preferences(db, scope, user_id)
            likes, dislikes, location = list(old.likes), list(old.dislikes), old.location
            if kind == "location":
                location = value
            else:
                target, opposite = (likes, dislikes) if kind == "like" else (dislikes, likes)
                if value in opposite:
                    opposite.remove(value)
                if value not in target:
                    if len(target) >= 32:
                        raise OperationError("food_preference_budget")
                    target.append(value)
            result = FoodPreferences(old.revision + 1, tuple(likes), tuple(dislikes), location)
            self._write_preferences(db, scope, user_id, result)
            self._silent_claim(db, user_id, now)
            return result
        return await self.store.transaction(write)

    async def reset_preferences(
        self, *, actor: str, scope: ConversationScope, user_id: str,
    ) -> FoodPreferences:
        self._person(actor, scope, user_id)
        now = self._now()
        def write(db: StoreConnection) -> FoodPreferences:
            self._authorize(db, actor, scope, "memory.learn")
            old = self._preferences(db, scope, user_id)
            result = FoodPreferences(old.revision + 1)
            self._write_preferences(db, scope, user_id, result)
            self._silent_claim(db, user_id, now)
            return result
        return await self.store.transaction(write)

    def assert_preferences_transaction(self, db: StoreConnection, *, event: Event,
                                      revision: int) -> None:
        scope = event.scope
        self._person(event.user_id, scope, event.user_id)
        self._authorize(db, event.user_id, scope, "memory.learn")
        if self._preferences(db, scope, event.user_id).revision != revision:
            raise OperationError("stale_food_preferences")

    async def claim_tutorial(
        self, *, actor: str, scope: ConversationScope, user_id: str,
    ) -> FoodTutorialClaim:
        self._person(actor, scope, user_id)
        now = self._now()
        def claim(db: StoreConnection) -> FoodTutorialClaim:
            self._authorize(db, actor, scope, "memory.learn")
            inserted = db.execute("INSERT OR IGNORE INTO food_tutorial_claims VALUES (?,?,?)",
                (f"food_tutorial:{user_id}", user_id, now)).rowcount
            if not inserted:
                return FoodTutorialClaim("already_claimed")
            where, values = self._where(scope, user_id)
            has_history = db.execute(f"SELECT 1 FROM {self._table(scope)} WHERE {where} "
                f"UNION ALL SELECT 1 FROM {self._table(scope, served=True)} WHERE {where} LIMIT 1",
                values * 2).fetchone()
            return FoodTutorialClaim("history_claimed" if has_history else "claimed")
        try:
            return await self.store.transaction(claim)
        except OperationError as exc:
            if exc.code == "storage_unavailable":
                return FoodTutorialClaim("storage_unavailable")
            raise

    def _period(self, now: float) -> str:
        hour = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).hour
        return ("早餐" if 5 <= hour < 10 else "早午餐" if hour == 10 else "午餐" if 11 <= hour < 13
                else "下午茶" if 13 <= hour < 17 else "晚餐" if 17 <= hour < 21 else "夜宵")

    async def prepare_selection(self, *, actor: str, event: Event, hint: str = "",
                                include: tuple[str, ...] = (),
                                exclude: tuple[str, ...] = ()) -> FoodSelection:
        scope = event.scope
        key = self._person(actor, scope, event.user_id)
        if key in self._active:
            raise OperationError("food_busy")
        if len(hint) > 512 or len(include) + len(exclude) > 32:
            raise OperationError("invalid_food_input")
        for term in (*include, *exclude):
            _text(term)
        token = uuid4().hex
        self._active[key] = token
        try:
            now = self._now()
            self._cleanup(now)
            prefs = await self.preferences(actor=actor, scope=scope, user_id=event.user_id)
            period = self._period(now)
            excluded = list(exclude) + list(prefs.dislikes)
            positive = hint.strip()
            negative_tastes = "麻辣|酸辣|甜辣|酸甜|咸鲜|清淡|咖喱|麻酱|辣|甜|酸|咸|苦|麻"
            for match in re.finditer(
                    rf"(?:不要|不想吃|不吃|不喜欢|别吃|讨厌|别|不)({negative_tastes})"
                    r"的?(?=$|[，。!！?？、,\s])", positive):
                excluded.append(match.group(1))
                positive = positive.replace(match.group(0), "")
            for match in re.finditer(r"(?:不要|不想吃|不吃|不喜欢|别吃)([^，。!！?？\s]+)", positive):
                excluded.append(match.group(1))
                positive = positive.replace(match.group(0), "")
            terms = list(include)
            taste_terms = ("麻辣", "酸辣", "甜辣", "酸甜", "清淡", "辣", "甜", "酸", "素食")
            if not terms:
                terms = [term for term in taste_terms if term in positive]
            recent = {name for _, name in self._recent.get(key, [])}
            candidates = tuple(item for item in self.menu
                if (not item.periods or period in item.periods)
                and item.name not in recent and not any(item.matches(term) for term in excluded)
                and all(item.matches(term.removesuffix("食") if term == "素食" else term) for term in terms))
            preferred = tuple(item.name for item in candidates
                              if any(item.matches(term) for term in prefs.likes))
            if len(candidates) > 40:
                liked = frozenset(preferred)
                candidates = tuple(sorted(candidates, key=lambda item: (
                    item.name not in liked,
                    hashlib.sha256((event.event_id + "\0" + item.name).encode("utf-8")).digest(),
                ))[:40])
                preferred = tuple(item.name for item in candidates if item.name in liked)
            tutorial = await self.claim_tutorial(actor=actor, scope=scope, user_id=event.user_id)
            self._person(actor, scope, event.user_id)
            selection = FoodSelection(token, event, candidates, preferred, period, hint,
                                      prefs.revision, tutorial)
            self._active[key] = selection
            return selection
        except BaseException:
            if self._active.get(key) == token:
                del self._active[key]
            raise

    def validate_selection(self, selection: FoodSelection, name: str) -> FoodItem:
        event = selection.event
        scope = event.scope
        key = self._person(event.user_id, scope, event.user_id)
        if self._active.get(key) is not selection:
            raise OperationError("stale_food_selection")
        for item in selection.candidates:
            if name == item.name:
                return item
        raise OperationError("food_not_in_candidates")

    def assert_context_transaction(self, db: StoreConnection, *, actor: str,
                                   selection: FoodSelection) -> None:
        event = selection.event
        scope = event.scope
        key = self._person(actor, scope, event.user_id)
        if self._active.get(key) is not selection:
            raise OperationError("stale_food_selection")
        for action in ("memory.retrieve", "memory.learn", "message.reply"):
            self._authorize(db, actor, scope, action)
        if self._preferences(db, scope, event.user_id).revision != selection.preferences_revision:
            raise OperationError("stale_food_preferences")

    def assert_selection_transaction(self, db: StoreConnection, *, actor: str,
                                     selection: FoodSelection, name: str) -> FoodItem:
        """Use as Actions.preflight_transaction to bind current consent and preferences."""
        event = selection.event
        scope = event.scope
        self._person(actor, scope, event.user_id)
        item = self.validate_selection(selection, name)
        self.assert_context_transaction(db, actor=actor, selection=selection)
        return item

    async def confirm_selection(self, *, actor: str, selection: FoodSelection, name: str) -> FoodItem:
        """Read-only current confirmation; Actions must also use assert_selection_transaction."""
        item = await self.store.transaction(lambda db: self.assert_selection_transaction(
            db, actor=actor, selection=selection, name=name))
        self.validate_selection(selection, name)
        return item

    def choose_local(self, selection: FoodSelection) -> FoodItem:
        """Explicit local selection, using allowed names and stored likes; no model fallback."""
        if not selection.candidates:
            raise OperationError("food_no_candidates")
        pool = tuple(item for item in selection.candidates if item.name in selection.preferred_names)
        pool = pool or selection.candidates
        index = int(hashlib.sha256(selection.event.event_id.encode()).hexdigest()[:8], 16) % len(pool)
        return self.validate_selection(selection, pool[index].name)

    def cancel_selection(self, selection: FoodSelection) -> None:
        key = selection.event.scope.key, selection.event.user_id
        if self._active.get(key) is selection:
            del self._active[key]

    async def record_served(self, *, actor: str, selection: FoodSelection, name: str,
                            action_id: str, receipt: SendReceipt) -> FoodServed:
        event = selection.event
        scope = event.scope
        key = self._person(actor, scope, event.user_id)
        self.validate_selection(selection, name)
        now = self._now()
        def write(db: StoreConnection) -> FoodServed:
            self._authorize(db, actor, scope, "memory.learn")
            self._authorize(db, actor, scope, "memory.retrieve")
            if self._preferences(db, scope, event.user_id).revision != selection.preferences_revision:
                raise OperationError("stale_food_preferences")
            if scope.kind == "group":
                self.store.assert_successful_reply_transaction(db, bot_id=scope.bot_id,
                    group_id=scope.group_id, subject_id=event.user_id, event_id=event.event_id,
                    source_digest=request_digest(event), action_id=action_id, receipt_id=receipt.message_id)
            else:
                self.store.assert_successful_reply_transaction(db, bot_id=scope.bot_id,
                    private_user_id=scope.private_user_id, subject_id=event.user_id, event_id=event.event_id,
                    source_digest=request_digest(event), action_id=action_id, receipt_id=receipt.message_id)
            where, values = self._where(scope, event.user_id)
            table = self._table(scope, served=True)
            prior = db.execute(f"SELECT * FROM {table} WHERE {where} AND event_id=?",
                               (*values, event.event_id)).fetchone()
            if prior:
                if prior["name"] != name or prior["receipt_id"] != receipt.message_id:
                    raise OperationError("food_served_conflict")
                return FoodServed(name, event.event_id, str(prior["receipt_id"]), float(prior["served_at"]))
            target = scope.group_id if scope.kind == "group" else scope.private_user_id
            db.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?)", (scope.bot_id,
                target, event.user_id, event.event_id, name, action_id, receipt.message_id, now))
            self._silent_claim(db, event.user_id, now)
            db.execute(f"DELETE FROM {table} WHERE {where} AND event_id "
                f"NOT IN (SELECT event_id FROM {table} WHERE {where} "
                "ORDER BY served_at DESC,rowid DESC LIMIT 20)", values * 2)
            return FoodServed(name, event.event_id, receipt.message_id, now)
        served = await self.store.transaction(write)
        if not self._closed and self._active.get(key) is selection:
            self._pending[key] = now, name
            self._recent.setdefault(key, []).append((now, name))
            self._recent[key] = self._recent[key][-5:]
        self.cancel_selection(selection)
        return served

    async def served(self, *, actor: str, scope: ConversationScope, user_id: str) -> tuple[FoodServed, ...]:
        self._person(actor, scope, user_id)
        def read(db: StoreConnection) -> tuple[FoodServed, ...]:
            self._authorize(db, actor, scope, "memory.retrieve")
            where, values = self._where(scope, user_id)
            return tuple(FoodServed(str(row["name"]), str(row["event_id"]), str(row["receipt_id"]),
                                   float(row["served_at"])) for row in db.execute(
                f"SELECT * FROM {self._table(scope, served=True)} WHERE {where} ORDER BY served_at DESC",
                values))
        return await self.store.transaction(read)

    def accepts_feedback(self, event: Event) -> bool:
        pending = self._pending.get((event.scope.key, event.user_id))
        return (not self._closed and pending is not None and self._now() - pending[0] < 120
                and self.is_rejection(event.text))

    @staticmethod
    def is_rejection(text: str) -> bool:
        text = text.strip().lower()
        return (text in {"不", "别", "换", "pass"} or any(term in text for term in
            ("不要", "不想", "不吃", "讨厌", "拒绝", "算了", "换一个", "换别的", "再换")))

    async def prepare_feedback(self, *, actor: str, event: Event) -> FoodSelection | None:
        scope = event.scope
        key = self._person(actor, scope, event.user_id)
        await self.policy.check(actor, scope, "message.read")
        self._cleanup(self._now())
        pending = self._pending.get(key)
        if pending is None:
            return None
        if key in self._active:
            raise OperationError("food_busy")
        self._pending.pop(key)
        if not self.is_rejection(event.text):
            return None
        return await self.prepare_selection(actor=actor, event=event, hint=event.text)

    async def close(self) -> None:
        self._closed = True
        self._active.clear()
        self._pending.clear()
        self._recent.clear()
