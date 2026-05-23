#!/usr/bin/env bash
# Docker housekeeping for Omubot dev hosts.
#
# Reclaims disk used by dangling images and stale buildkit cache that pile up
# from repeated `docker compose up -d --build`. Designed to be safe to run on a
# live dev/prod host:
#
#   - never touches volumes (omubot-storage holds 4+ GB of persistent data)
#   - never recreates containers; running images are pinned by docker
#   - D6 reaffirmed: this script must NOT `docker compose down` napcat;
#     napcat 的 device fingerprint 一旦失效 = 反风控触发 = 上线失败
#
# Usage: scripts/dev/docker-cleanup.sh
#
# Exit codes: 0 success, non-zero on any failed docker call.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

log() { printf '[docker-cleanup] %s\n' "$*"; }

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "[docker-cleanup] docker CLI not found in PATH" >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "[docker-cleanup] docker daemon not reachable" >&2
    exit 1
  fi
}

git_hygiene_check() {
  # D7: surface git stash + uncommitted state before destructive operations.
  # Info-only — never blocks. Operator decides whether to proceed.
  if [ -d "$ROOT_DIR/.git" ]; then
    log "git status -uno:"
    git -C "$ROOT_DIR" status -uno --short || true
    log "git stash list:"
    git -C "$ROOT_DIR" stash list || true
  fi
}

print_disk_report() {
  log "$1"
  docker system df || true
}

main() {
  require_docker
  git_hygiene_check
  print_disk_report "before cleanup"

  log "running: docker image prune -a -f  (preserves images of running containers)"
  docker image prune -a -f

  log "running: docker builder prune -f  (clears buildkit cache only)"
  docker builder prune -f

  print_disk_report "after cleanup"

  log "post-cleanup container health:"
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' || true

  log "done. volumes left untouched. napcat container preserved (D6)."
}

main "$@"
