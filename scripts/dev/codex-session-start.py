#!/usr/bin/env python3
"""Emit Codex SessionStart context for Omubot.

The hook may run with cwd at the repository root or at the parent directory.
Detect the repo root instead of assuming paths like ``omubot/maintenance-log.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

MAX_ADDITIONAL_CONTEXT_BYTES = 4096
MAX_LIVE_STATE_SECTION_LINES = 8
MAX_ACTIVE_LINES = 36
MAX_TRACKER_CAPSULE_LINES = 32


def _find_repo_root() -> Path:
    cwd = Path.cwd().resolve()
    candidates = [cwd, cwd / "omubot", cwd.parent / "omubot", *cwd.parents]
    for candidate in candidates:
        if (
            (candidate / "maintenance-log.md").is_file()
            and (candidate / "docs/project-info.md").is_file()
        ):
            return candidate
    return cwd


def _read_limited_lines(
    path: Path,
    *,
    max_lines: int,
    stop_before_heading: str | None = None,
) -> list[str]:
    lines: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if stop_before_heading and line.startswith(stop_before_heading):
                break
            lines.append(line)
            if len(lines) >= max_lines:
                lines.append(f"\n(... truncated to {max_lines} lines ...)\n")
                break
    return lines


def _read_section(path: Path, heading: str, *, max_lines: int) -> list[str]:
    lines: list[str] = []
    capture = False
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.rstrip() == heading:
                capture = True
                lines.append(line)
                continue
            if capture and line.startswith("## "):
                break
            if capture:
                lines.append(line)
                if len(lines) >= max_lines:
                    lines.append(f"\n(... truncated to {max_lines} lines ...)\n")
                    break
    return lines


def _repo_child(root: Path, value: str) -> Path | None:
    if not value:
        return None
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _active_tracker_from_lines(lines: list[str]) -> str | None:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- tracker:"):
            value = stripped.split(":", 1)[1].strip()
            if value and value.lower() not in {"none", "null", "-"}:
                return value
    return None


def _append_continuity_context(root: Path, ctx: list[str]) -> None:
    ctx.append("--- Agent Continuity Snapshot (compact) ---\n")

    live_state = root / ".workspace/agent-session-state.md"
    if live_state.is_file():
        try:
            ctx.append("\n[Live state summary: .workspace/agent-session-state.md]\n")
            focus = _read_section(
                live_state,
                "## Current Focus",
                max_lines=MAX_LIVE_STATE_SECTION_LINES,
            )
            next_step = _read_section(
                live_state,
                "## Next Step",
                max_lines=MAX_LIVE_STATE_SECTION_LINES,
            )
            gotchas = _read_section(
                live_state,
                "## Gotchas / Do Not Redo",
                max_lines=MAX_LIVE_STATE_SECTION_LINES,
            )
            ctx.extend(focus or ["- See .workspace/agent-session-state.md when resuming.\n"])
            ctx.extend(next_step)
            ctx.extend(gotchas)
        except Exception as exc:
            ctx.append(f"\n(读取 live state 失败: {exc})\n")

    active_path = root / "docs/tracking/ACTIVE.md"
    active_lines: list[str] = []
    if active_path.is_file():
        try:
            active_lines = _read_limited_lines(
                active_path,
                max_lines=MAX_ACTIVE_LINES,
                stop_before_heading="## Phase Progress",
            )
            ctx.append("\n[Active tracker index: docs/tracking/ACTIVE.md]\n")
            ctx.extend(active_lines)
        except Exception as exc:
            ctx.append(f"\n(读取 ACTIVE.md 失败: {exc})\n")
    else:
        ctx.append(
            "\n(docs/tracking/ACTIVE.md 不存在；如需恢复任务，请回退到 maintenance-log.md 和 git status 窄查。)\n"
        )

    tracker_value = _active_tracker_from_lines(active_lines)
    if tracker_value:
        tracker_path = _repo_child(root, tracker_value)
        if tracker_path is None:
            ctx.append(f"\n(active tracker 路径无效或越界: {tracker_value})\n")
        elif tracker_path.is_file():
            try:
                capsule = _read_section(
                    tracker_path,
                    "## Resume Capsule",
                    max_lines=MAX_TRACKER_CAPSULE_LINES,
                )
                if not capsule:
                    capsule = _read_section(
                        tracker_path,
                        "## Next Session Starts Here",
                        max_lines=MAX_TRACKER_CAPSULE_LINES,
                    )
                ctx.append(f"\n[Active tracker capsule: {tracker_value}]\n")
                ctx.extend(
                    capsule
                    or [
                        "- No resume capsule found; read the tracker directly if this task continues.\n"
                    ]
                )
            except Exception as exc:
                ctx.append(f"\n(读取 active tracker 失败: {exc})\n")
        else:
            ctx.append(f"\n(active tracker 不存在: {tracker_value})\n")

    ctx.append(
        "\nContinuity instruction: use this compact index first; "
        "read the tracker or ledger only when the task actually continues.\n"
    )


def _trim_to_byte_budget(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value

    suffix = (
        "\n(... snapshot truncated by byte budget; read docs/tracking/ACTIVE.md "
        "and the active tracker on demand ...)\n"
    )
    suffix_bytes = suffix.encode("utf-8")
    budget = max(0, max_bytes - len(suffix_bytes))
    return encoded[:budget].decode("utf-8", errors="ignore") + suffix


def main() -> None:
    root = _find_repo_root()
    ctx: list[str] = []

    _append_continuity_context(root, ctx)

    try:
        lines = (root / "docs/project-info.md").read_text(encoding="utf-8").splitlines()
        for line in lines:
            if "Bot QQ 号" in line and "待填写" in line:
                ctx.append("\nBot QQ 号尚未填写，需要扫码登录后更新 docs/project-info.md")
                break
    except Exception:
        pass

    if ctx:
        additional_context = _trim_to_byte_budget(
            "项目状态快照 — 紧凑恢复索引:\n\n" + "".join(ctx),
            MAX_ADDITIONAL_CONTEXT_BYTES,
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": additional_context,
            }
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
