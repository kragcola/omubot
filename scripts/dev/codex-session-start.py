#!/usr/bin/env python3
"""Emit Codex SessionStart context for Omubot.

The hook may run with cwd at the repository root or at the parent directory.
Detect the repo root instead of assuming paths like ``omubot/maintenance-log.md``.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path


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


def main() -> None:
    root = _find_repo_root()
    ctx: list[str] = []

    try:
        with (root / "maintenance-log.md").open(encoding="utf-8") as f:
            capture = False
            count = 0
            for line in f:
                if line.startswith("## 202"):
                    if capture:
                        break
                    capture = True
                if capture:
                    ctx.append(line)
                    count += 1
                    if count > 60:
                        break
    except Exception as exc:
        ctx.append(f"(读取维护日志失败: {exc})")

    try:
        logs = sorted(glob.glob(str(root / "storage/logs/bot_*.log")), reverse=True)
        if logs:
            log_path = Path(logs[0])
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines(True)
            ctx.append(f"\n--- 最新日志 ({os.path.basename(log_path)}, 最后40行) ---\n")
            ctx.extend(lines[-40:])
        else:
            ctx.append("\n(暂无 bot 日志文件)")
    except Exception as exc:
        ctx.append(f"\n(读取日志失败: {exc})")

    try:
        lines = (root / "docs/project-info.md").read_text(encoding="utf-8").splitlines()
        for line in lines:
            if "Bot QQ 号" in line and "待填写" in line:
                ctx.append("\nBot QQ 号尚未填写，需要扫码登录后更新 docs/project-info.md")
                break
    except Exception:
        pass

    if ctx:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "项目状态快照 — 请先了解当前状态再开始工作:\n\n"
                + "".join(ctx),
            }
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
