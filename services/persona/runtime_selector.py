"""Persona v2 runtime selector (B3).

Per-turn decision point that decides whether the current chat turn should
build its system prompt using the v2 ``_pending_freeze`` bundle or fall back
to the v1 ``PromptBuilder._static_block``.

The selector is built once per ``on_bot_connect`` (alongside the B2 shadow
engine) and held by both ``PromptBuilder`` and ``PluginContext``. Each turn
``PromptBuilder.resolve_static_block`` calls :meth:`resolve_for_group` and
substitutes the first system block with the v2 text when ``use_v2`` is True.

This module is the *only* place that decides v1 vs v2 at the turn level. The
decision is gated by ``BotConfig.persona_v2.runtime_consume`` and limited to
groups listed in ``BotConfig.persona_v2.runtime_groups`` — private chats
always stay on v1 in B3 (per docs/tracking/persona-runtime-cutover-B3-execution.md
§0). When the v2 bundle is missing or its compile failed,
``fallback_on_compile_error`` (defaults True) keeps callers on v1 so the bot
keeps speaking with the v1 persona instead of erroring out.

The selector never raises; all exceptional paths are returned as
:class:`RuntimeSelection` with a non-empty ``fallback_reason``.
"""

from __future__ import annotations

from dataclasses import dataclass

from kernel.config import PersonaV2Config

from .runtime import PersonaRuntimeBundle

_STATIC_BLOCK_ORDER: tuple[str, ...] = (
    "core.identity",
    "runtime.adapter",
    "core.guard",
    "core.voice",
    "core.knowledge",
    "core.examples",
)


def join_static_blocks(bundle: PersonaRuntimeBundle) -> str:
    """Join the bundle's ``position == "static"`` prompt blocks into one text.

    The order is fixed by :data:`_STATIC_BLOCK_ORDER` so v1↔v2 byte
    comparison (used by B2 shadow compare) and v2 runtime substitution share
    the exact same projection. Empty blocks and unknown module ids are
    silently dropped.
    """
    by_id = {
        block.module_id: block
        for block in bundle.compile_result.prompt_blocks
        if block.position == "static"
    }
    parts: list[str] = []
    for module_id in _STATIC_BLOCK_ORDER:
        block = by_id.get(module_id)
        if block is not None and block.text:
            parts.append(block.text)
    return "\n\n".join(parts)


@dataclass(frozen=True)
class RuntimeSelection:
    """Outcome of one ``resolve_for_group`` call.

    ``use_v2`` is True only when every gate passed: flag on, group listed,
    bundle loaded, and v2 static text non-empty. Otherwise ``fallback_reason``
    is set to one of: ``flag_off`` / ``private_chat`` / ``group_not_listed``
    / ``bundle_missing`` / ``compile_error`` / ``empty_v2_text``.
    """

    use_v2: bool
    v2_static_text: str = ""
    fallback_reason: str = ""


@dataclass
class RuntimeSelectorCounter:
    """In-memory turn counters. Reset on bot restart."""

    v2: int = 0
    v1_fallback: int = 0
    v1_default: int = 0
    last_error: str = ""
    last_reason: str = ""

    def reset(self) -> None:
        self.v2 = 0
        self.v1_fallback = 0
        self.v1_default = 0
        self.last_error = ""
        self.last_reason = ""


class PersonaRuntimeSelector:
    """Decide v1 vs v2 per turn. Pure, synchronous, never raises."""

    def __init__(
        self,
        *,
        cfg: PersonaV2Config,
        bundle: PersonaRuntimeBundle | None,
        v2_static_text: str = "",
    ) -> None:
        self._cfg = cfg
        self._bundle = bundle
        self._v2_static_text = v2_static_text
        self._runtime_groups: frozenset[str] = frozenset(cfg.runtime_groups)
        self._counter = RuntimeSelectorCounter()

    @property
    def counter(self) -> RuntimeSelectorCounter:
        return self._counter

    @property
    def bundle(self) -> PersonaRuntimeBundle | None:
        return self._bundle

    @property
    def v2_static_text(self) -> str:
        return self._v2_static_text

    def resolve_for_group(self, group_id: str | None) -> RuntimeSelection:
        if not self._cfg.runtime_consume:
            return self._record_default("flag_off")
        if group_id is None:
            return self._record_default("private_chat")
        if str(group_id) not in self._runtime_groups:
            return self._record_default("group_not_listed")
        if self._bundle is None:
            return self._record_fallback("bundle_missing", error="")
        if not self._bundle.ok:
            error = ", ".join(self._bundle.errors) or ", ".join(
                self._bundle.compile_result.errors
            )
            return self._record_fallback("compile_error", error=error)
        if not self._v2_static_text:
            return self._record_fallback("empty_v2_text", error="")

        self._counter.v2 += 1
        self._counter.last_reason = "v2"
        return RuntimeSelection(
            use_v2=True,
            v2_static_text=self._v2_static_text,
            fallback_reason="",
        )

    def _record_default(self, reason: str) -> RuntimeSelection:
        self._counter.v1_default += 1
        self._counter.last_reason = reason
        return RuntimeSelection(use_v2=False, fallback_reason=reason)

    def _record_fallback(self, reason: str, *, error: str) -> RuntimeSelection:
        self._counter.v1_fallback += 1
        self._counter.last_reason = reason
        if error:
            self._counter.last_error = error
        return RuntimeSelection(use_v2=False, fallback_reason=reason)
