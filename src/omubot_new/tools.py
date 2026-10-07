"""Reviewed in-process tools. Declarations are contracts, not a security sandbox."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, JsonValue, ValidationError

from .types import OperationError, StrictModel


class ToolInputError(OperationError):
    """An invalid, side-effect-free input that a chat continuation may explain."""


@dataclass(frozen=True)
class ToolSpec:
    id: str
    version: str
    api_version: int
    description: str
    input_schema: type[StrictModel]
    output_schema: type[StrictModel]
    requested_capabilities: frozenset[str]
    timeout_ms: int
    handler: Callable[[dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]]
    destination: str | None = None
    destination_resolver: Callable[[dict[str, JsonValue]], str] | None = None


class TimeInput(StrictModel):
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Omit or use null for the configured local timezone. Only specify a timezone "
            "when the user explicitly asks for another region, using an IANA identifier "
            "such as Asia/Shanghai or America/New_York, never a city label or UTC offset."
        ),
    )


class TimeOutput(StrictModel):
    utc: str
    local: str
    timezone: str


class Tools:
    def __init__(
        self,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timezone: str = "Asia/Shanghai",
        *,
        extra_specs: Iterable[ToolSpec] = (),
        approved_capabilities: frozenset[str] = frozenset({"clock.read"}),
    ) -> None:
        self._clock = clock
        self._timezone = timezone
        self._zone(timezone)
        self._closed = False
        self._tasks: set[asyncio.Task[dict[str, JsonValue]]] = set()
        specs = [
            ToolSpec(
                "time.now",
                "1.0.0",
                1,
                "Read the current date and time. Omit timezone for the configured local timezone "
                f"{self._timezone}; use an explicit IANA timezone only when requested by the user.",
                TimeInput,
                TimeOutput,
                frozenset({"clock.read"}),
                1000,
                self._time,
            ),
            *extra_specs,
        ]
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            if spec.id in self._specs:
                raise OperationError("duplicate_tool")
            if spec.api_version != 1:
                raise OperationError("tool_api_version")
            if spec.destination is not None and spec.destination_resolver is not None:
                raise OperationError("invalid_tool_destination")
            if not spec.requested_capabilities <= approved_capabilities:
                raise OperationError("tool_capability_denied")
            if (
                not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", spec.id)
                or not spec.version
                or not 1 <= spec.timeout_ms <= 30000
            ):
                raise OperationError("invalid_tool_spec")
            for schema in (spec.input_schema, spec.output_schema):
                if schema.model_config.get("extra") != "forbid" or not schema.model_config.get("strict"):
                    raise OperationError("invalid_tool_schema")
            self._specs[spec.id] = spec

    @staticmethod
    def _zone(value: str) -> ZoneInfo:
        try:
            return ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ToolInputError("invalid_timezone") from exc

    async def _time(self, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        timezone = arguments.get("timezone")
        if timezone is None:
            timezone = self._timezone
        if not isinstance(timezone, str):
            raise OperationError("invalid_timezone")
        now = self._clock()
        if now.tzinfo is None:
            raise OperationError("invalid_clock")
        return {
            "utc": now.astimezone(UTC).isoformat(),
            "local": now.astimezone(self._zone(timezone)).isoformat(),
            "timezone": timezone,
        }

    def schemas(self) -> list[dict[str, JsonValue]]:
        return [
            {
                "name": spec.id,
                "description": spec.description,
                "input_schema": cast(JsonValue, spec.input_schema.model_json_schema()),
            }
            for spec in self._specs.values()
        ]

    def manifests(self) -> list[dict[str, JsonValue]]:
        """Derive reviewed declarations from the one registry; these are not grants."""
        return [
            {
                "id": spec.id,
                "version": spec.version,
                "api_version": spec.api_version,
                "description": spec.description,
                "input_schema": cast(JsonValue, spec.input_schema.model_json_schema()),
                "output_schema": cast(JsonValue, spec.output_schema.model_json_schema()),
                "requested_capabilities": cast(JsonValue, sorted(spec.requested_capabilities)),
                "timeout_ms": spec.timeout_ms,
            }
            for spec in self._specs.values()
        ]

    def spec(self, name: str) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise OperationError("unknown_tool")
        return spec

    def destination(self, name: str, arguments: dict[str, JsonValue]) -> str | None:
        """Resolve a reviewed tool's actual target before the Actions permission gate."""
        spec = self.spec(name)
        if spec.destination_resolver is None:
            return spec.destination
        try:
            validated = spec.input_schema.model_validate(arguments)
        except ValidationError as exc:
            raise OperationError("invalid_tool_arguments") from exc
        return spec.destination_resolver(cast(dict[str, JsonValue], validated.model_dump()))

    async def invoke(
        self, name: str, arguments: dict[str, JsonValue], *, capabilities: frozenset[str]
    ) -> dict[str, JsonValue]:
        if self._closed:
            raise OperationError("tools_closed")
        spec = self.spec(name)
        if not spec.requested_capabilities <= capabilities:
            raise OperationError("tool_capability_denied")
        try:
            validated = spec.input_schema.model_validate(arguments)
        except ValidationError as exc:
            raise ToolInputError("invalid_tool_arguments") from exc

        async def run() -> dict[str, JsonValue]:
            return await spec.handler(cast(dict[str, JsonValue], validated.model_dump()))

        task = asyncio.create_task(run())
        self._tasks.add(task)
        try:
            done, _ = await asyncio.wait({task}, timeout=spec.timeout_ms / 1000)
            if not done:
                task.cancel()
                # Never await indefinitely if trusted code violates cooperative cancellation.
                done, _ = await asyncio.wait({task}, timeout=0.05)
                if not done:
                    self._closed = True
                    raise OperationError("tool_cancel_failed")
                raise OperationError("tool_timeout")
            result = task.result()
            try:
                output = spec.output_schema.model_validate(result)
                encoded = output.model_dump_json()
                if len(encoded.encode()) > 8192:
                    raise OperationError("tool_output_limit")
                return cast(dict[str, JsonValue], json.loads(encoded))
            except ValidationError as exc:
                raise OperationError("invalid_tool_output") from exc
        except asyncio.CancelledError:
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=0.05)
            if not done:
                self._closed = True
            raise
        finally:
            if task.done():
                self._tasks.discard(task)
                if not task.cancelled():
                    task.exception()

    async def close(self) -> None:
        self._closed = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            done, pending = await asyncio.wait(self._tasks, timeout=0.05)
            for task in done:
                if not task.cancelled():
                    task.exception()
            self._tasks = set(pending)
            if pending:
                raise OperationError("tool_cancel_failed")
