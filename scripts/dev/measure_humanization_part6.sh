#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

GROUP_ID="${GROUP_ID:-}"
LIMIT="${LIMIT:-200}"

run_section() {
  local title="$1"
  shift
  echo "## ${title}"
  if "$@"; then
    echo "section_status: ok"
  else
    local code=$?
    echo "section_status: failed"
    echo "exit_code: ${code}"
  fi
  echo
}

echo "# Humanization Part 6 Measurement"
echo "group_filter: ${GROUP_ID:-all}"
echo "limit: ${LIMIT}"
echo

run_section \
  "P6.3 streaming_vs_natural" \
  env GROUP_ID="$GROUP_ID" LIMIT="$LIMIT" bash "$ROOT/scripts/dev/measure_streaming_vs_natural.sh"

run_section \
  "P6.7 pause_then_extend" \
  env GROUP_ID="$GROUP_ID" LIMIT="$LIMIT" bash "$ROOT/scripts/dev/measure_extend_rate.sh"

run_section \
  "P6.10 plan_then_utter_pilot" \
  env GROUP_ID="$GROUP_ID" bash "$ROOT/scripts/dev/measure_plan_then_utter_pilot.sh"
