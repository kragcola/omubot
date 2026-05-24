"""Persona v2 shadow compare engine (B2).

Computes v1 vs v2 prompt-block diff at runtime and appends a JSONL line to
``storage/persona_shadow_diff.log``. Read-only. Does not send any LLM
requests, does not mutate ``PromptBuilder``, does not mutate
``_pending_freeze/``.

Gated by ``BotConfig.persona_v2.shadow_compare``. When the flag is off,
:meth:`ShadowCompareEngine.run_once` returns ``None`` immediately and writes
nothing.

The diff is computed locally by:

1. Reading v1 ``PromptBuilder._static_block["text"]`` (must be non-empty;
   ``build_static`` runs in ``on_bot_connect`` before the engine is invoked).
2. Calling :func:`services.persona.runtime.load_pending_freeze` to obtain a
   v2 :class:`PersonaRuntimeBundle`.
3. Reusing :func:`services.persona.parity_audit.compare_v1_vs_v2_dry_run` to
   classify the 6 parity axes and harvest ``divergent_axes``.

Each call appends one JSON line; format see
``docs/tracking/persona-runtime-cutover-B2-execution.md`` §3.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from kernel.config import PersonaV2Config
from services.identity import Identity

from .compiler import CompilePromptBlock
from .parity_audit import GroupOverrideSnapshot, compare_v1_vs_v2_dry_run
from .runtime import PersonaRuntimeBundle, load_pending_freeze

_L = logger.bind(channel="persona_shadow")

DEFAULT_SHADOW_LOG_PATH = Path("storage/persona_shadow_diff.log")

_STATIC_BLOCK_ORDER: tuple[str, ...] = (
    "core.identity",
    "runtime.adapter",
    "core.guard",
    "core.voice",
    "core.knowledge",
    "core.examples",
)


@dataclass(frozen=True)
class ShadowDiffReport:
    """Single shadow-compare run outcome."""

    timestamp: str
    persona_id: str
    source_sha256: str
    compile_signature: str
    v1_signature: str
    has_divergence: bool
    divergent_axes: tuple[str, ...]
    v1_text_len: int
    v2_text_len: int
    notes: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "persona_id": self.persona_id,
            "source_sha256": self.source_sha256,
            "compile_signature": self.compile_signature,
            "v1_signature": self.v1_signature,
            "has_divergence": self.has_divergence,
            "divergent_axes": list(self.divergent_axes),
            "v1_text_len": self.v1_text_len,
            "v2_text_len": self.v2_text_len,
            "notes": list(self.notes),
            "errors": list(self.errors),
            "ok": not self.errors,
        }


@dataclass
class ShadowCounter:
    ok: int = 0
    divergent: int = 0
    error: int = 0
    last_error: str = ""
    last_run_at: str = ""

    def reset(self) -> None:
        self.ok = 0
        self.divergent = 0
        self.error = 0
        self.last_error = ""
        self.last_run_at = ""


class ShadowCompareEngine:
    """Compute v1 vs v2 prompt-block diffs and append to a JSONL log."""

    def __init__(
        self,
        *,
        cfg: PersonaV2Config,
        v1_static_text: str,
        v1_identity: Identity | None = None,
        v1_instruction_text: str = "",
        v1_admins: dict[str, str] | None = None,
        v1_proactive: str = "",
        v1_group_override: GroupOverrideSnapshot | None = None,
        v1_bot_self_id: str = "",
        log_path: Path = DEFAULT_SHADOW_LOG_PATH,
        persona_root: str | Path = "config/persona",
        defaults_dir: str | Path = "config/persona/_defaults/v2",
    ) -> None:
        self._cfg = cfg
        self._v1_static_text = v1_static_text
        self._v1_identity = v1_identity
        self._v1_instruction_text = v1_instruction_text
        self._v1_admins = dict(v1_admins or {})
        self._v1_proactive = v1_proactive
        self._v1_group_override = v1_group_override
        self._v1_bot_self_id = v1_bot_self_id
        self._log_path = Path(log_path)
        self._persona_root = persona_root
        self._defaults_dir = defaults_dir
        self._counter = ShadowCounter()

    @property
    def counter(self) -> ShadowCounter:
        return self._counter

    async def run_once(self) -> ShadowDiffReport | None:
        """Compute one v1 vs v2 diff and append to the JSONL log.

        Returns ``None`` when ``shadow_compare`` is off or the pending freeze
        directory does not exist. Returns a report otherwise (with ``errors``
        populated on failure). Never raises.
        """
        if not self._cfg.shadow_compare:
            return None

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        persona_id = self._cfg.persona_id or "default"

        bundle = load_pending_freeze(
            persona_id,
            persona_root=self._persona_root,
            defaults_dir=self._defaults_dir,
        )
        if bundle is None:
            return self._record_error(
                timestamp=timestamp,
                persona_id=persona_id,
                v1_signature=_sha256_text(self._v1_static_text),
                errors=("pending_freeze_dir_missing",),
                v1_text_len=len(self._v1_static_text),
            )

        if bundle.errors or not bundle.compile_result.ok:
            errors = bundle.errors or tuple(bundle.compile_result.errors)
            return self._record_error(
                timestamp=timestamp,
                persona_id=bundle.persona_id,
                v1_signature=_sha256_text(self._v1_static_text),
                errors=errors,
                source_sha256=bundle.source_sha256,
                v1_text_len=len(self._v1_static_text),
            )

        v2_text = _join_static_blocks(bundle.compile_result.prompt_blocks)
        v1_signature = _sha256_text(self._v1_static_text)
        v2_signature = _sha256_text(v2_text)

        divergent_axes, notes = self._collect_divergent_axes(bundle)
        has_divergence = bool(divergent_axes)

        report = ShadowDiffReport(
            timestamp=timestamp,
            persona_id=bundle.persona_id,
            source_sha256=bundle.source_sha256,
            compile_signature=v2_signature,
            v1_signature=v1_signature,
            has_divergence=has_divergence,
            divergent_axes=tuple(divergent_axes),
            v1_text_len=len(self._v1_static_text),
            v2_text_len=len(v2_text),
            notes=tuple(notes),
            errors=(),
        )

        self._append_log(report)
        if has_divergence:
            self._counter.divergent += 1
        else:
            self._counter.ok += 1
        self._counter.last_run_at = timestamp
        return report

    def _collect_divergent_axes(
        self, bundle: PersonaRuntimeBundle
    ) -> tuple[list[str], list[str]]:
        identity = self._v1_identity or _empty_identity()
        try:
            parity = compare_v1_vs_v2_dry_run(
                identity=identity,
                bot_self_id=self._v1_bot_self_id,
                instruction_text=self._v1_instruction_text,
                admins=self._v1_admins or None,
                proactive=self._v1_proactive,
                group_override=self._v1_group_override,
                compile_result=bundle.compile_result,
            )
        except Exception as exc:
            _L.warning("parity audit unexpected error: {}", exc)
            return [], [f"parity_audit_error: {exc}"]

        axes: list[str] = []
        notes: list[str] = []
        for finding in parity.findings:
            if finding.status not in ("divergent", "v1_only"):
                continue
            axes.append(str(finding.axis))
            tag = finding.notes or finding.status
            notes.append(f"{finding.axis}: {tag[:200]}")
        return axes, notes

    def _record_error(
        self,
        *,
        timestamp: str,
        persona_id: str,
        v1_signature: str,
        errors: tuple[str, ...],
        v1_text_len: int,
        source_sha256: str = "",
    ) -> ShadowDiffReport:
        report = ShadowDiffReport(
            timestamp=timestamp,
            persona_id=persona_id,
            source_sha256=source_sha256,
            compile_signature="",
            v1_signature=v1_signature,
            has_divergence=False,
            divergent_axes=(),
            v1_text_len=v1_text_len,
            v2_text_len=0,
            notes=(),
            errors=errors,
        )
        self._append_log(report)
        self._counter.error += 1
        self._counter.last_error = ", ".join(errors)
        self._counter.last_run_at = timestamp
        return report

    def _append_log(self, report: ShadowDiffReport) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(report.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            _L.warning("shadow log write failed | path={} err={}", self._log_path, exc)
            self._counter.error += 1
            self._counter.last_error = f"log_write_error: {exc}"


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _join_static_blocks(blocks: tuple[CompilePromptBlock, ...]) -> str:
    by_id = {block.module_id: block for block in blocks if block.position == "static"}
    parts: list[str] = []
    for module_id in _STATIC_BLOCK_ORDER:
        block = by_id.get(module_id)
        if block is not None and block.text:
            parts.append(block.text)
    return "\n\n".join(parts)


def _empty_identity() -> Identity:
    return Identity(
        id="default",
        name="unknown",
        personality="",
        proactive=None,
    )
