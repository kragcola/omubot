"""Issue 15 — instruction authority gate.

Decides whether the bot should obey a directive-style user message based on a
numeric authority level (0-4). Each severity class (low/medium/high) has a
required level; a user passes only when their authority >= required.

Architecture (see docs/tracking/omubot-grayscale-issue15-instruction-gate-landing-design.md):

- severity = max(regex fast-path, thinker `instruction_signal`)   # most-restrictive
- authority = 4 for admins, else override table, else default (2)
- Layer 1 (authority check): user_authority < required -> DENY
- Layer 2 (mood modulation, low severity only): comply / refuse_soft
- DENY does NOT call the main LLM — caller sends a fixed in-character line.
- ALLOW/COMPLY/REFUSE_SOFT inject a hint into the main prompt (plugin_dynamic).

`InstructionAuthorityGate.evaluate` is pure (no I/O). Persistence of per-user
overrides lives in `AuthorityStore`, which the caller reads from before
invoking the gate.
"""

from __future__ import annotations

import contextlib
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from loguru import logger

_L = logger.bind(channel="instruction_gate")

GateAction = Literal["pass", "allow", "deny", "comply", "refuse_soft"]

MIN_AUTHORITY = 0
MAX_AUTHORITY = 4
ADMIN_AUTHORITY = MAX_AUTHORITY

# severity ordering for most-restrictive merge between regex + thinker signal
_SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
_SEVERITY_BY_RANK = {rank: name for name, rank in _SEVERITY_ORDER.items()}


def merge_severity(a: str, b: str) -> str:
    """Return the more severe of two severity labels (none < low < medium < high)."""
    rank = max(_SEVERITY_ORDER.get(a, 0), _SEVERITY_ORDER.get(b, 0))
    return _SEVERITY_BY_RANK[rank]


@dataclass(frozen=True)
class InstructionGateResult:
    """Outcome of one gate evaluation. Immutable so callers can't mutate trace."""

    action: GateAction
    severity: str = "none"
    user_authority: int = 0
    required_authority: int = 0
    reason: str = ""
    response_hint: str = ""  # ALLOW/COMPLY/REFUSE_SOFT -> injected into plugin_dynamic
    deny_text: str = ""      # DENY -> sent directly via on_segment (no main LLM)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "instruction_gate_action": self.action,
            "instruction_gate_severity": self.severity,
            "instruction_gate_user_authority": self.user_authority,
            "instruction_gate_required_authority": self.required_authority,
            "instruction_gate_reason": self.reason,
        }


class InstructionAuthorityGate:
    """Pure decision gate. Construct from BotConfig.instruction_gate."""

    def __init__(self, config: Any, *, rng: random.Random | None = None) -> None:
        self._config = config
        self._rng = rng or random.Random()
        self._compiled: dict[str, list[re.Pattern[str]]] = self._compile_patterns(
            getattr(config, "severity_patterns", {}) or {}
        )

    @staticmethod
    def _compile_patterns(raw: dict[str, list[str]]) -> dict[str, list[re.Pattern[str]]]:
        compiled: dict[str, list[re.Pattern[str]]] = {}
        for severity in ("high", "medium", "low"):
            patterns: list[re.Pattern[str]] = []
            for expr in raw.get(severity, []) or []:
                try:
                    patterns.append(re.compile(str(expr), re.IGNORECASE))
                except re.error as exc:
                    _L.warning("invalid severity pattern | severity={} expr={!r} err={}", severity, expr, exc)
            compiled[severity] = patterns
        return compiled

    # -- Layer 0: severity detection ----------------------------------------

    def scan_severity(self, user_message: str) -> str:
        """Regex fast-path. Returns the highest-severity class that matches."""
        text = str(user_message or "")
        if not text.strip():
            return "none"
        # high > medium > low: return the first (most severe) that hits.
        for severity in ("high", "medium", "low"):
            for pattern in self._compiled.get(severity, []):
                if pattern.search(text):
                    return severity
        return "none"

    # -- Authority resolution -----------------------------------------------

    def resolve_authority(
        self,
        user_id: str,
        admins: dict[str, str] | None,
        authority_overrides: dict[str, int] | None,
    ) -> int:
        """admin -> 4; else override table; else default_authority."""
        uid = str(user_id or "").strip()
        if admins and uid in admins:
            return ADMIN_AUTHORITY
        overrides = authority_overrides or {}
        if uid in overrides:
            return max(MIN_AUTHORITY, min(MAX_AUTHORITY, int(overrides[uid])))
        return self._default_authority()

    def _default_authority(self) -> int:
        return max(MIN_AUTHORITY, min(MAX_AUTHORITY, int(getattr(self._config, "default_authority", 2))))

    def _required_for(self, severity: str) -> int:
        table = getattr(self._config, "required_authority", {}) or {}
        defaults = {"low": 2, "medium": 3, "high": 4}
        return int(table.get(severity, defaults.get(severity, 4)))

    # -- Main evaluation ----------------------------------------------------

    def evaluate(
        self,
        *,
        user_message: str,
        user_id: str,
        admins: dict[str, str] | None = None,
        authority_overrides: dict[str, int] | None = None,
        mood: Any = None,
        thinker_signal: str = "none",
    ) -> InstructionGateResult:
        severity = merge_severity(self.scan_severity(user_message), str(thinker_signal or "none"))
        if severity == "none":
            return InstructionGateResult(action="pass", severity="none", reason="no_directive")

        user_authority = self.resolve_authority(user_id, admins, authority_overrides)
        required = self._required_for(severity)

        # Layer 1: authority check (deterministic).
        if user_authority < required:
            return InstructionGateResult(
                action="deny",
                severity=severity,
                user_authority=user_authority,
                required_authority=required,
                reason=f"authority {user_authority} < required {required} for {severity}",
                deny_text=self._pick(getattr(self._config, "deny_responses", []), "不想。"),
            )

        # Passed the authority gate.
        if severity != "low":
            # medium/high directives from a sufficiently-authorized user: comply,
            # injecting directive context so the main LLM honours it in-character.
            return InstructionGateResult(
                action="allow",
                severity=severity,
                user_authority=user_authority,
                required_authority=required,
                reason=f"authority {user_authority} >= required {required}",
                response_hint=self._allow_hint(severity),
            )

        # Layer 2: mood modulation (low severity only).
        return self._modulate_by_mood(severity, user_authority, required, mood)

    def _modulate_by_mood(
        self, severity: str, user_authority: int, required: int, mood: Any,
    ) -> InstructionGateResult:
        thresholds = getattr(self._config, "mood_threshold", {}) or {}
        openness_min = float(thresholds.get("openness_min", 0.6))
        valence_min = float(thresholds.get("valence_min", 0.3))
        energy_floor = float(thresholds.get("energy_floor", 0.3))
        tension_ceiling = float(thresholds.get("tension_ceiling", 0.8))

        openness = _mood_attr(mood, "openness", 0.5)
        valence = _mood_attr(mood, "valence", 0.0)
        energy = _mood_attr(mood, "energy", 0.5)
        tension = _mood_attr(mood, "tension", 0.0)

        def comply(reason: str) -> InstructionGateResult:
            return InstructionGateResult(
                action="comply", severity=severity, user_authority=user_authority,
                required_authority=required, reason=reason, response_hint=self._comply_hint(),
            )

        def refuse(reason: str) -> InstructionGateResult:
            return InstructionGateResult(
                action="refuse_soft", severity=severity, user_authority=user_authority,
                required_authority=required, reason=reason,
                response_hint=self._pick(getattr(self._config, "refuse_soft_responses", []), "现在没心情……"),
            )

        if energy < energy_floor or tension > tension_ceiling:
            return refuse(f"low energy ({energy:.2f}) / high tension ({tension:.2f})")
        if openness > openness_min and valence > valence_min:
            return comply(f"good mood (openness={openness:.2f} valence={valence:+.2f})")
        # Borderline mood: probabilistic, biased by openness.
        if self._rng.random() < openness:
            return comply(f"probabilistic comply (openness={openness:.2f})")
        return refuse(f"probabilistic refuse (openness={openness:.2f})")

    # -- hint builders ------------------------------------------------------

    @staticmethod
    def _allow_hint(severity: str) -> str:
        return (
            "【指令授权】用户向你下达了一个请求，且其权限足够。"
            "你可以在符合人设的前提下配合完成，但保持你自己的语气和分寸。"
        )

    @staticmethod
    def _comply_hint() -> str:
        return (
            "【心情提示】用户提了个轻松的小要求，你现在心情不错，可以配合一下，"
            "但用你自己的方式，别太刻意。"
        )

    def _pick(self, options: list[str], fallback: str) -> str:
        items = [str(o) for o in (options or []) if str(o).strip()]
        return self._rng.choice(items) if items else fallback


def _mood_attr(mood: Any, name: str, default: float) -> float:
    """Duck-typed mood field read — never imports plugins.schedule."""
    if mood is None:
        return default
    try:
        return float(getattr(mood, name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class AuthorityStore:
    """Per-user authority override persistence (JSON file + in-memory cache).

    Tiny, write-cold, read-hot map (qq_id -> level). JSON file rather than
    SQLite to match the learning_settings.json precedent and dodge the macOS
    Docker bind-mount + WAL corruption hazard documented in slang/store.py.
    """

    storage_dir: str = "storage"
    seed: dict[str, int] = field(default_factory=dict)
    _cache: dict[str, int] = field(default_factory=dict, init=False)
    _loaded: bool = field(default=False, init=False)

    @property
    def _path(self) -> Path:
        return Path(self.storage_dir) / "instruction_authority.json"

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        data: dict[str, int] = {}
        path = self._path
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        with contextlib.suppress(TypeError, ValueError):
                            data[str(key)] = max(MIN_AUTHORITY, min(MAX_AUTHORITY, int(value)))
            except (json.JSONDecodeError, OSError) as exc:
                _L.warning("authority store load failed: {}", exc)
        # config seed fills gaps but never overrides a persisted value.
        for key, value in (self.seed or {}).items():
            data.setdefault(str(key), max(MIN_AUTHORITY, min(MAX_AUTHORITY, int(value))))
        self._cache = data
        self._loaded = True

    def snapshot(self) -> dict[str, int]:
        """Return the current override map (config seed ∪ persisted, persisted wins)."""
        self._ensure_loaded()
        return dict(self._cache)

    def get(self, user_id: str) -> int | None:
        self._ensure_loaded()
        return self._cache.get(str(user_id))

    def set(self, user_id: str, authority: int) -> int:
        self._ensure_loaded()
        level = max(MIN_AUTHORITY, min(MAX_AUTHORITY, int(authority)))
        self._cache[str(user_id)] = level
        self._flush()
        return level

    def clear(self, user_id: str) -> bool:
        self._ensure_loaded()
        if str(user_id) in self._cache:
            del self._cache[str(user_id)]
            self._flush()
            return True
        return False

    def _flush(self) -> None:
        path = self._path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            _L.warning("authority store flush failed: {}", exc)
