#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Keep local caches and the virtual environment anchored to the active workspace.
source "$ROOT_DIR/scripts/dev/env.sh"

REQUIRED_TARGETS=(
  "admin"

  # M1: process and chat composition roots.
  "bootstrap/application.py"
  "bootstrap/chat_runtime.py"
  "bot.py"
  "kernel/router.py"
  "kernel/types.py"
  "plugins/chat/plugin.py"

  # M2: typed router, scheduler delivery, and visible LLM guardrail stages.
  "services/routing/connection_pipeline.py"
  "services/scheduler_pipeline/outbound_delivery.py"
  "services/llm/reply_guardrail_stage.py"
  "services/llm/client.py"
  "services/scheduler.py"

  # M3: database catalog, migration, connection, backup, and governed stores.
  "services/storage/catalog.py"
  "services/storage/migrations.py"
  "services/storage/schema_contracts.py"
  "services/storage/sqlite.py"
  "services/storage/backup.py"
  "services/health.py"
  "services/block_trace/store.py"
  "services/llm/usage.py"
  "services/episodic/store.py"
  "services/group/research_event_store.py"
  "services/storage/retention.py"
  "services/storage/status.py"

  # M4: process-wide background task lifecycle owner.
  "kernel/background_tasks.py"
  "kernel/bus.py"
  "services/storage/backup_scheduler.py"
  "services/humanization/health_guard.py"
  "services/scheduler_hawkes/offline.py"
  "plugins/dream/plugin.py"
  "plugins/schedule/plugin.py"
  "plugins/schedule/generator.py"

  # Plugin remediation: independent memory consolidation lifecycle owner.
  "services/memory_consolidator/event_boundary.py"
  "services/memory_consolidator/lifecycle.py"

  # M5: typed owners extracted from Admin routes.
  "services/plugin_toggle.py"
  "services/learning_extract_coordinator.py"
  "services/style/manual_extract.py"
  "plugins/affection/plugin.py"
  "plugins/debug_commands/plugin.py"
  "plugins/group_admin/plugin.py"

  # Canonical Tool ABI and typed plugin consumer.
  "services/tools/base.py"
  "services/tools/context.py"
  "services/tools/registry.py"
  "plugins/sticker/plugin.py"
)

TARGETS=()
for target in "${REQUIRED_TARGETS[@]}"; do
  if [[ ! -e "$target" ]]; then
    echo "[typed-boundaries] required target missing: $target" >&2
    exit 2
  fi
  TARGETS+=("$target")
done

echo "[typed-boundaries] checking ${#TARGETS[@]} targets"
uv run pyright "${TARGETS[@]}"
