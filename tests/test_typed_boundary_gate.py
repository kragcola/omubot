from __future__ import annotations

import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "check-typed-boundaries.sh"


def test_typed_boundary_gate_has_exact_m1_to_m6_manifest() -> None:
    assert GATE.exists(), "typed boundary gate script must exist"
    assert os.access(GATE, os.X_OK), "typed boundary gate script must be executable"

    source = GATE.read_text(encoding="utf-8")
    expected_targets = (
        "admin",
        "bootstrap/application.py",
        "bootstrap/chat_runtime.py",
        "bot.py",
        "kernel/router.py",
        "kernel/types.py",
        "plugins/chat/plugin.py",
        "services/routing/connection_pipeline.py",
        "services/scheduler_pipeline/outbound_delivery.py",
        "services/llm/reply_guardrail_stage.py",
        "services/llm/client.py",
        "services/scheduler.py",
        "services/storage/catalog.py",
        "services/storage/migrations.py",
        "services/storage/schema_contracts.py",
        "services/storage/sqlite.py",
        "services/storage/backup.py",
        "services/health.py",
        "services/block_trace/store.py",
        "services/llm/usage.py",
        "services/episodic/store.py",
        "services/group/research_event_store.py",
        "services/storage/retention.py",
        "services/storage/status.py",
        "kernel/background_tasks.py",
        "kernel/bus.py",
        "services/storage/backup_scheduler.py",
        "services/humanization/health_guard.py",
        "services/scheduler_hawkes/offline.py",
        "plugins/dream/plugin.py",
        "plugins/schedule/plugin.py",
        "plugins/schedule/generator.py",
        "services/memory_consolidator/event_boundary.py",
        "services/memory_consolidator/lifecycle.py",
        "services/plugin_toggle.py",
        "services/learning_extract_coordinator.py",
        "services/style/manual_extract.py",
        "plugins/affection/plugin.py",
        "plugins/debug_commands/plugin.py",
        "plugins/group_admin/plugin.py",
        "services/tools/base.py",
        "services/tools/context.py",
        "services/tools/registry.py",
        "plugins/sticker/plugin.py",
    )
    manifest_match = re.search(
        r"REQUIRED_TARGETS=\(\n(?P<body>.*?)\n\)",
        source,
        flags=re.DOTALL,
    )
    assert manifest_match is not None, "typed boundary manifest must remain explicit"
    actual_targets = tuple(
        re.findall(r'^\s*"([^\"]+)"\s*$', manifest_match.group("body"), flags=re.MULTILINE)
    )

    assert len(expected_targets) == 44
    assert actual_targets == expected_targets


def test_tool_plugins_do_not_cast_around_the_canonical_abi() -> None:
    plugin_paths = (
        ROOT / "plugins" / "affection" / "plugin.py",
        ROOT / "plugins" / "group_admin" / "plugin.py",
        ROOT / "plugins" / "sticker" / "plugin.py",
    )

    for plugin_path in plugin_paths:
        tree = ast.parse(plugin_path.read_text(encoding="utf-8"))
        cast_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "cast"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "cast"
                )
            )
        ]
        assert not cast_calls, f"{plugin_path} must not cast around the Tool ABI"


def test_ci_automation_runs_typed_boundary_gate() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "typed-boundaries.yml"
    assert workflow_path.exists(), "typed boundary CI workflow must exist"

    workflow = workflow_path.read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "bash scripts/check-typed-boundaries.sh" in workflow
