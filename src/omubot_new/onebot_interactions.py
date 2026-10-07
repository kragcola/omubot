"""Current-event OneBot interactions through the single durable Actions exit."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

from pydantic import Field, JsonValue, ValidationError

from .actions import Actions
from .onebot_payload import canonical_onebot_params, onebot_response_error, qq_write_spec
from .runtime import Turn
from .store import StoreConnection, request_digest
from .types import (
    ActionCall,
    Event,
    OperationError,
    QQAdmission,
    QQWriteGrant,
    QQWriteSpec,
    Scope,
    StrictModel,
)

InteractionAction = Literal["manage_group", "poke", "reaction"]
OneBotRequest = Callable[[str, dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]]


class OneBotWriteRequest(Protocol):
    def __call__(
        self, api: str, params: dict[str, JsonValue], *, grant: QQWriteGrant,
    ) -> Awaitable[dict[str, JsonValue]]: ...


@dataclass(frozen=True, slots=True)
class PreparedInteraction:
    event: Event
    action: InteractionAction
    call: ActionCall
    spec: QQWriteSpec
    target_ref: str
    arguments_hash: str
    params_json: str = field(repr=False)

    @property
    def params(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json.loads(self.params_json))


class GroupBanInput(StrictModel):
    user_id: str = Field(pattern=r"^[1-9][0-9]{0,31}$")
    duration: int = Field(ge=0, le=2_592_000)


class PokeInput(StrictModel):
    user_id: str = Field(pattern=r"^[1-9][0-9]{0,31}$")


class ReactionInput(StrictModel):
    message_id: str = Field(pattern=r"^-?[0-9]{1,32}$")
    emoji_id: str = Field(pattern=r"^[1-9][0-9]{0,31}$")


class InteractionAck(StrictModel):
    """A verified provider acknowledgment, not a fabricated message receipt."""

    status: Literal["accepted"]
    action: InteractionAction
    api: Literal["set_group_ban", "send_poke", "set_msg_emoji_like"]
    target_ref: str


# Reviewed static declarations belong to this action owner; these are not
# generic Tools handlers, which do not receive a trusted current event.
_TOOLS: tuple[tuple[str, InteractionAction, type[StrictModel], str], ...] = (
    ("onebot.manage_group", "manage_group", GroupBanInput,
     "Mute only the current message author in this group; duration=0 unmutes."),
    ("onebot.poke", "poke", PokeInput, "Poke only the current message author in this group."),
    ("onebot.reaction", "reaction", ReactionInput, "React only to the current group message."),
)


def interaction_action(name: str) -> InteractionAction | None:
    return next((action for tool, action, _, _ in _TOOLS if tool == name), None)


def interaction_transport_identity(instance_id: str, bot_id: str, endpoint: str | None) -> str:
    if endpoint is None:
        return f"onebot:rws:{instance_id}:{bot_id}"
    return f"onebot:http:{instance_id}:{bot_id}:{endpoint.rstrip('/')}"


def interaction_schemas(event: Event, capabilities: frozenset[str]) -> list[dict[str, JsonValue]]:
    schemas: list[dict[str, JsonValue]] = []
    for name, _, input_type, description in _TOOLS:
        if name not in capabilities:
            continue
        schema = input_type.model_json_schema()
        if input_type is ReactionInput:
            if not event.message_id:
                continue
            schema["properties"]["message_id"]["const"] = event.message_id
        else:
            schema["properties"]["user_id"]["const"] = event.user_id
        schemas.append({"name": name, "description": description,
                        "input_schema": cast(JsonValue, schema)})
    return schemas


class OneBotInteractions:
    """Bind only a claimed current group event to its existing transport owner.

    Bootstrap supplies the fixed bot/transport identity and an envelope-returning
    request method from the existing HTTP or reverse-WebSocket owner. No client,
    socket, retry loop, moderation policy, or second action ledger is created.
    """

    def __init__(
        self,
        actions: Actions,
        *,
        bot_id: str,
        transport_identity: str,
        request: OneBotRequest,
        transport_ready: Callable[[], None] | None = None,
    ) -> None:
        self.actions = actions
        self.bot_id = bot_id
        self.transport_identity = transport_identity
        self._request = request
        self._transport_ready = transport_ready

    def prepare(
        self, *, event: Event, action: InteractionAction,
        arguments: dict[str, JsonValue], key: str,
    ) -> PreparedInteraction:
        if not isinstance(event.scope, Scope):
            raise OperationError("unsupported_scope")
        if event.scope.bot_id != self.bot_id:
            raise OperationError("wrong_bot")
        # These are transport identities, not model-selected destinations.
        # Reuse the input grammar for the current peer and group identifiers.
        try:
            PokeInput(user_id=event.scope.group_id)
            peer = PokeInput(user_id=event.user_id)
            params: dict[str, JsonValue]
            api: Literal["set_group_ban", "send_poke", "set_msg_emoji_like"]
            if action == "manage_group":
                ban = GroupBanInput.model_validate(arguments)
                if ban.user_id != peer.user_id:
                    raise OperationError("interaction_target_mismatch")
                api = "set_group_ban"
                params = {"group_id": event.scope.group_id, "user_id": peer.user_id,
                          "duration": ban.duration}
                target_ref = (
                    f"onebot:{self.bot_id}:group:{event.scope.group_id}:member:{peer.user_id}:mute"
                )
            elif action == "poke":
                poke = PokeInput.model_validate(arguments)
                if poke.user_id != peer.user_id:
                    raise OperationError("interaction_target_mismatch")
                api = "send_poke"
                params = {"group_id": event.scope.group_id, "user_id": peer.user_id}
                target_ref = (
                    f"onebot:{self.bot_id}:group:{event.scope.group_id}:member:{peer.user_id}:poke"
                )
            elif action == "reaction":
                reaction = ReactionInput.model_validate(arguments)
                if reaction.message_id != event.message_id:
                    raise OperationError("interaction_target_mismatch")
                api = "set_msg_emoji_like"
                params = {"message_id": reaction.message_id, "emoji_id": reaction.emoji_id, "set": True}
                target_ref = (
                    f"onebot:{self.bot_id}:group:{event.scope.group_id}:message:"
                    f"{reaction.message_id}:reaction:{reaction.emoji_id}"
                )
            else:
                raise OperationError("unsupported_interaction")
        except ValidationError as exc:
            raise OperationError("invalid_interaction_arguments") from exc

        payload = json.dumps(
            {"scope": event.scope.model_dump(), "target_ref": target_ref, "api": api, "params": params},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        call = ActionCall(
            key=key, request_id=event.event_id, subject=event.user_id, scope=event.scope,
            action=action, payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
            provider=self.transport_identity, model=api,
        )

        return PreparedInteraction(
            event=event, action=action, call=call, spec=qq_write_spec(event.scope, api, params),
            target_ref=target_ref,
            arguments_hash=hashlib.sha256(canonical_onebot_params(arguments).encode()).hexdigest(),
            params_json=canonical_onebot_params(params),
        )

    async def execute(
        self,
        *,
        event: Event,
        action: InteractionAction,
        arguments: dict[str, JsonValue],
        key: str,
        prepared: PreparedInteraction | None = None,
        qq_admission: QQAdmission | None = None,
        timeout: float,  # noqa: ASYNC109 - forwarded to the single Actions deadline owner
        turn: Turn | None = None,
        before_intent: Callable[[], None | Awaitable[None]] | None = None,
        before_operation: Callable[[], None] | None = None,
        preflight_transaction: Callable[[StoreConnection], None] | None = None,
    ) -> InteractionAck:
        if prepared is None:
            prepared = self.prepare(event=event, action=action, arguments=arguments, key=key)
        elif (prepared.event != event or prepared.action != action or prepared.call.key != key
              or prepared.call.provider != self.transport_identity
              or prepared.arguments_hash != hashlib.sha256(
                  canonical_onebot_params(arguments).encode()).hexdigest()):
            raise OperationError("interaction_preparation_changed")
        if qq_admission is not None and (
            qq_admission.spec != prepared.spec or qq_admission.binding.action_key != key
        ):
            raise OperationError("qq_admission_changed")
        call, api, params, target_ref = prepared.call, prepared.spec.api, prepared.params, prepared.target_ref

        def preflight(db: StoreConnection) -> None:
            claimed = db.execute("SELECT digest FROM requests WHERE id=?", (event.event_id,)).fetchone()
            if claimed is None or claimed["digest"] != request_digest(event):
                raise OperationError("interaction_event_changed")
            if preflight_transaction is not None:
                preflight_transaction(db)

        def check_dispatch() -> None:
            if self._transport_ready is not None:
                self._transport_ready()
            if before_operation is not None:
                before_operation()

        async def operation(grant: QQWriteGrant | None = None) -> InteractionAck:
            def dispatch_preflight(db: StoreConnection) -> None:
                preflight(db)
                for permission in ("message.read", action):
                    self.actions.policy.check_transaction(
                        db, event.user_id, event.scope, permission,
                        self.transport_identity, api, False, False,
                    )
            await self.actions.store.transaction(dispatch_preflight)
            if turn is not None:
                turn.check()
            check_dispatch()
            response = (
                await self._request(api, params) if grant is None
                else await cast(OneBotWriteRequest, self._request)(api, params, grant=grant)
            )
            retcode = response.get("retcode")
            status = response.get("status")
            if type(retcode) is not int or status not in ("ok", "failed", "async"):
                raise onebot_response_error("invalid_protocol", response, "ws")
            if status == "failed" and retcode != 0:
                # NapCat wraps handler exceptions as failed. It does not prove
                # that no side effect started, so Actions retains unknown.
                raise onebot_response_error("interaction_rejected", response, "ws")
            if status != "ok" or retcode != 0:
                raise onebot_response_error("invalid_protocol", response, "ws")
            # Ban returns null; poke may omit data; reaction returns an opaque
            # NTQQ result. None is a message ID or a visible-effect assertion.
            return InteractionAck(
                status="accepted", action=action,
                api=cast(Literal["set_group_ban", "send_poke", "set_msg_emoji_like"], api),
                target_ref=target_ref,
            )

        return await self.actions.execute(
            call, operation, external=True, turn=turn, timeout=timeout,
            before_intent=before_intent, before_operation=check_dispatch,
            preflight_transaction=preflight, qq_admission=qq_admission,
        )
