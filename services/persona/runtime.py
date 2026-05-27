"""Persona v2 runtime — single source of prompt/identity for the bot.

A long-lived singleton owned by the kernel context. Loads
``_pending_freeze/<id>/`` once at startup, exposes prompt-block accessors
for ``PromptBuilder`` / ``LLMClient``, and supports atomic hot reload from
admin SPA without restarting the bot.

Public surface (everything else is an implementation detail):

- :func:`load_pending_freeze` — read-only loader, returns
  :class:`PersonaRuntimeBundle` (kept for compatibility with B1/B2 tests).
- :class:`PersonaRuntime` — the singleton; provides ``static_text`` /
  ``block_for(module_id)`` / ``group_profile_text(group_id)`` /
  ``identity_snapshot()`` / ``bind_bot_self_id(self_id)`` /
  ``swap_bundle(persona_id)``.

Last-known-good behaviour:

- ``load(persona_id)`` returns False on bundle failure but **keeps** the
  previously good bundle so the bot can keep serving.
- On the very first load (no LKG yet) failure is fatal: callers MUST
  treat ``load`` returning False with no LKG as a startup error.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compiler import CompilePromptBlock, CompileResult, compile_persona_runtime
from .writer import PENDING_FREEZE_SCHEMA_VERSION, PersonaDraftWriter, persona_namespace

_SCHEMA_MAJOR = PENDING_FREEZE_SCHEMA_VERSION.split(".", 1)[0]

_STATIC_BLOCK_ORDER: tuple[str, ...] = (
    "core.identity",
    "runtime.adapter",
    "core.guard",
    "core.voice",
    "core.knowledge",
    "core.examples",
)

_BOT_SELF_ID_PLACEHOLDER = "{bot_self_id}"


@dataclass(frozen=True)
class PersonaRuntimeBundle:
    """Outcome of loading ``_pending_freeze/<id>/``."""

    persona_id: str
    schema_version: str
    source_sha256: str
    compile_result: CompileResult
    pending_freeze_dir: Path
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors and self.compile_result.ok


@dataclass(frozen=True)
class IdentitySnapshot:
    """Lightweight identity model exposed to plugins/services.

    Carries the fields callers read off ``ctx.identity``. Constructed from
    ``persona.yaml.identity`` at load time.
    """

    id: str
    name: str
    personality: str
    proactive: str | None = None
    description: str = ""


def load_pending_freeze(
    persona_id: str,
    *,
    persona_root: str | Path = "config/persona",
    defaults_dir: str | Path = "config/persona/_defaults/v2",
) -> PersonaRuntimeBundle | None:
    """Load ``_pending_freeze/<id>/`` into a :class:`PersonaRuntimeBundle`.

    Returns ``None`` when the pending freeze directory does not exist.
    Returns a bundle with ``ok=False`` on schema / compile errors. Never
    raises. Read-only.
    """
    namespace = persona_namespace(persona_id)
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults_dir)
    pending_dir = writer.pending_freeze_dir(persona_id)
    if not pending_dir.is_dir():
        return None

    warnings: list[str] = []
    errors: list[str] = []

    meta_path = pending_dir / "_persona_runtime.json"
    schema_version = ""
    source_sha256 = ""
    if not meta_path.is_file():
        errors.append("runtime_meta_missing")
    else:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"runtime_meta_parse_error: {exc}")
            meta = {}
        if isinstance(meta, dict):
            schema_version = str(meta.get("schema_version", "")).strip()
            source_sha256 = str(meta.get("source_sha256", "")).strip()
            if schema_version:
                meta_major = schema_version.split(".", 1)[0]
                if meta_major != _SCHEMA_MAJOR:
                    errors.append(
                        f"schema_version_major_mismatch: expected {_SCHEMA_MAJOR}.x got {schema_version}"
                    )
            else:
                errors.append("schema_version_missing")

    source_frozen = pending_dir / "source.frozen.md"
    if source_frozen.is_file() and source_sha256:
        try:
            actual_sha = hashlib.sha256(source_frozen.read_bytes()).hexdigest()
        except OSError as exc:
            warnings.append(f"source_sha256_read_error: {exc}")
        else:
            if actual_sha != source_sha256:
                warnings.append("source_sha256_drift")

    compile_result = compile_persona_runtime(
        persona_id, persona_root=persona_root, defaults_dir=defaults_dir
    )

    return PersonaRuntimeBundle(
        persona_id=namespace,
        schema_version=schema_version,
        source_sha256=source_sha256,
        compile_result=compile_result,
        pending_freeze_dir=pending_dir,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def join_static_blocks(bundle: PersonaRuntimeBundle) -> str:
    """Join the bundle's ``position == "static"`` prompt blocks into one text.

    Order is fixed so byte comparison and runtime substitution share the same
    projection. Empty blocks and unknown module ids are silently dropped.
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


def _identity_snapshot_from_bundle(bundle: PersonaRuntimeBundle) -> IdentitySnapshot:
    """Build an :class:`IdentitySnapshot` from the bundle's persona.yaml.

    Reads ``persona.yaml`` directly off disk so we get the structured fields
    (``canonical_name``, ``proactive_rules``, etc.) not just the joined text.
    """
    persona_path = bundle.pending_freeze_dir / "persona.yaml"
    name = bundle.persona_id
    personality = ""
    proactive: str | None = None
    if persona_path.is_file():
        try:
            import yaml

            data = yaml.safe_load(persona_path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        identity = data.get("identity") if isinstance(data, dict) else None
        if isinstance(identity, dict):
            name = str(identity.get("canonical_name") or name).strip() or name
            personality = str(identity.get("personality") or "").strip()
            proactive_raw = str(identity.get("proactive_rules") or "").strip()
            proactive = proactive_raw or None
    return IdentitySnapshot(
        id=bundle.persona_id,
        name=name,
        personality=personality,
        proactive=proactive,
    )


@dataclass(frozen=True)
class _GroupProfileFragment:
    """Internal helper for rendering one group override into prompt text."""

    group_id: str
    text: str


_GROUP_REPLY_STYLE_HINTS: dict[str, str] = {
    "gentle": "回复风格偏柔和、耐心、安抚感更强，避免过硬或过冲的表达。",
    "playful": "回复风格可以更轻松俏皮，允许一点点玩梗和抖机灵，但不要失控。",
    "concise": "回复尽量短一些，优先直接结论，减少过长铺垫和重复解释。",
    "energetic": "回复可以更有活力和在场感，语气积极，但不要变得吵闹失真。",
    "steady": "回复保持平稳、克制、可靠，少用夸张语气和过度情绪化表达。",
}


def _render_group_profile_text(group_profile: Any | None) -> str:
    """Render a single resolved group profile into the prompt fragment.

    Mirrors the v1 ``LLMClient._build_group_profile_block`` projection so
    the v2 path produces identical text for the reply style hint and custom
    prompt — only the *plumbing* changes (runtime owns it now), not the
    output.
    """
    if group_profile is None:
        return ""
    lines: list[str] = []
    reply_style = str(getattr(group_profile, "reply_style", "default") or "default")
    style_hint = _GROUP_REPLY_STYLE_HINTS.get(reply_style)
    if style_hint:
        lines.append(style_hint)
    custom_prompt = str(getattr(group_profile, "custom_prompt", "") or "").strip()
    if custom_prompt:
        lines.append(f"【本群附加要求】\n{custom_prompt}")
    if not lines:
        return ""
    return "【群聊回复偏好】\n" + "\n".join(lines)


class PersonaRuntime:
    """Singleton persona runtime owned by the kernel context.

    Thread-safe (held by an RLock so admin hot-swap and per-turn reads can
    interleave). Each accessor returns a self-consistent snapshot of one
    bundle — never a half-loaded mix.

    Lifecycle:

    1. ``load(persona_id)`` — startup: read pending freeze, set as current.
       Returns False on failure. Caller decides whether to abort.
    2. ``bind_bot_self_id(self_id)`` — called from ``_on_connect`` once the
       NoneBot driver gives us the real QQ self id. Replaces the
       ``{bot_self_id}`` placeholder in cached static text.
    3. ``swap_bundle(persona_id)`` — admin hot reload: load a fresh bundle,
       and atomically swap if compile succeeded; previous bundle stays on
       compile failure (LKG protection).
    """

    def __init__(
        self,
        *,
        persona_root: str | Path = "config/persona",
        defaults_dir: str | Path = "config/persona/_defaults/v2",
        group_config_resolver: Any | None = None,
    ) -> None:
        self._persona_root = persona_root
        self._defaults_dir = defaults_dir
        self._group_config_resolver = group_config_resolver
        self._lock = threading.RLock()
        self._bundle: PersonaRuntimeBundle | None = None
        self._identity: IdentitySnapshot | None = None
        self._raw_static_text: str = ""
        self._bot_self_id: str = ""
        self._cached_static_text: str = ""
        self._last_error: str = ""

    # ------------------------------------------------------------------
    # Load / swap
    # ------------------------------------------------------------------

    def load(self, persona_id: str) -> bool:
        """Initial load. Returns True iff bundle is usable.

        On failure, the runtime is left empty (caller should abort startup
        unless it has a fallback strategy). For atomic hot reload after the
        first successful load, use :meth:`swap_bundle` instead.
        """
        bundle = load_pending_freeze(
            persona_id,
            persona_root=self._persona_root,
            defaults_dir=self._defaults_dir,
        )
        with self._lock:
            if bundle is None:
                self._last_error = "pending_freeze_missing"
                return False
            if not bundle.ok:
                self._last_error = ", ".join(bundle.errors) or ", ".join(
                    bundle.compile_result.errors
                ) or "compile_failed"
                return False
            self._adopt_bundle(bundle)
            self._last_error = ""
            return True

    def swap_bundle(self, persona_id: str) -> bool:
        """Hot reload. Returns True on swap; previous bundle survives failures."""
        bundle = load_pending_freeze(
            persona_id,
            persona_root=self._persona_root,
            defaults_dir=self._defaults_dir,
        )
        with self._lock:
            if bundle is None or not bundle.ok:
                self._last_error = (
                    "pending_freeze_missing"
                    if bundle is None
                    else (", ".join(bundle.errors) or ", ".join(bundle.compile_result.errors))
                )
                return False
            self._adopt_bundle(bundle)
            self._last_error = ""
            return True

    def _adopt_bundle(self, bundle: PersonaRuntimeBundle) -> None:
        """Caller holds the lock."""
        self._bundle = bundle
        self._identity = _identity_snapshot_from_bundle(bundle)
        self._raw_static_text = join_static_blocks(bundle)
        self._refresh_cached_static_text_locked()

    def _refresh_cached_static_text_locked(self) -> None:
        """Recompute the static text with the current bot_self_id substitution."""
        text = self._raw_static_text
        if self._bot_self_id:
            text = text.replace(_BOT_SELF_ID_PLACEHOLDER, self._bot_self_id)
        self._cached_static_text = text

    def bind_bot_self_id(self, self_id: str) -> None:
        """Replace the ``{bot_self_id}`` placeholder in cached static text.

        Called from ``_on_connect`` once NoneBot gives us the real driver id.
        Idempotent — safe to call on every reconnect.
        """
        normalized = str(self_id or "").strip()
        with self._lock:
            self._bot_self_id = normalized
            self._refresh_cached_static_text_locked()

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def loaded(self) -> bool:
        with self._lock:
            return self._bundle is not None

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def bundle(self) -> PersonaRuntimeBundle | None:
        with self._lock:
            return self._bundle

    @property
    def static_text(self) -> str:
        """Return the joined v2 static prompt text with placeholders filled.

        Empty string when no bundle has been loaded — callers MUST treat
        empty as a hard failure (the kernel ensures load() succeeded before
        wiring the runtime into PromptBuilder).
        """
        with self._lock:
            return self._cached_static_text

    @property
    def bot_self_id(self) -> str:
        with self._lock:
            return self._bot_self_id

    def identity_snapshot(self) -> IdentitySnapshot:
        """Return an immutable snapshot of identity fields.

        Returns an empty snapshot when no bundle is loaded so callers can
        defensively call .name / .personality without conditional logic.
        """
        with self._lock:
            if self._identity is None:
                return IdentitySnapshot(id="default", name="bot", personality="")
            return self._identity

    def block_for(self, module_id: str) -> CompilePromptBlock | None:
        """Return the compiled prompt block for ``module_id`` (or None)."""
        with self._lock:
            if self._bundle is None:
                return None
            for block in self._bundle.compile_result.prompt_blocks:
                if block.module_id == module_id:
                    return block
            return None

    def group_profile_text(self, group_id: str | int | None) -> str:
        """Return the group-profile prompt fragment for ``group_id``.

        Sources:

        1. If a ``group_config_resolver`` was injected at construction time
           (i.e. ``BotConfig.group``), resolve the override there and render
           it via the v1-compatible projection.
        2. Otherwise fall back to the static fragment in the bundle's
           ``runtime.group_profile`` block (which the importer/compiler
           generates from ``runtime.yaml.per_group_overrides``).

        Returns ``""`` for private chats and groups without overrides.
        """
        if group_id is None:
            return ""
        gid_str = str(group_id).strip()
        if not gid_str:
            return ""
        if self._group_config_resolver is not None:
            try:
                profile = self._group_config_resolver(int(gid_str))
            except Exception:
                profile = None
            text = _render_group_profile_text(profile)
            if text:
                return text
        block = self.block_for("runtime.group_profile")
        if block is None or not block.text:
            return ""
        prefix = f"{gid_str}："
        for line in block.text.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):]
        return ""


# ----------------------------------------------------------------------
# Async hot-reload helper
# ----------------------------------------------------------------------


async def hot_reload(
    runtime: PersonaRuntime,
    persona_id: str,
) -> bool:
    """Coroutine-friendly wrapper around :meth:`PersonaRuntime.swap_bundle`.

    Performs the (potentially blocking) yaml read on a worker thread so
    admin API handlers don't block the event loop on large bundles.
    """
    return await asyncio.to_thread(runtime.swap_bundle, persona_id)
