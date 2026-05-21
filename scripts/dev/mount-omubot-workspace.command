#!/bin/zsh
set -euo pipefail

export OMUBOT_WORKSPACE_FS='JHFS+'
export OMUBOT_WORKSPACE_BUNDLE='/Volumes/我的电脑/omubot/.workspace/OmubotWorkspace.sparseimage'
export OMUBOT_WORKSPACE_MOUNT_POINT='/Users/kragcola/OmubotWorkspace'

MOUNT_SCRIPT='/Volumes/我的电脑/omubot/scripts/dev/mount-workspace.sh'
REPO_PATH='/Users/kragcola/OmubotWorkspace/omubot'
ENV_SCRIPT='/Users/kragcola/OmubotWorkspace/omubot/scripts/dev/env.sh'
DOCTOR_SCRIPT='/Users/kragcola/OmubotWorkspace/omubot/scripts/dev/doctor.sh'

echo '[omubot] Mounting existing workspace image...'
echo "[omubot] bundle: $OMUBOT_WORKSPACE_BUNDLE"
echo "[omubot] mount : $OMUBOT_WORKSPACE_MOUNT_POINT"
echo

if [[ ! -f "$OMUBOT_WORKSPACE_BUNDLE" ]]; then
  echo '[omubot] ERROR: workspace image not found.'
  echo '[omubot] Make sure the external disk "我的电脑" is connected.'
  echo
  read -r '?Press Enter to close...'
  exit 1
fi

if [[ ! -x "$MOUNT_SCRIPT" ]]; then
  echo '[omubot] ERROR: mount script is missing or not executable.'
  echo "[omubot] expected: $MOUNT_SCRIPT"
  echo
  read -r '?Press Enter to close...'
  exit 1
fi

/bin/bash "$MOUNT_SCRIPT"

echo
if [[ -d "$REPO_PATH" ]]; then
  echo "[omubot] Workspace repo is available: $REPO_PATH"
else
  echo "[omubot] ERROR: repo path still missing after mount: $REPO_PATH"
  echo
  read -r '?Press Enter to close...'
  exit 1
fi

if [[ -f "$ENV_SCRIPT" && -x "$DOCTOR_SCRIPT" ]]; then
  echo
  echo '[omubot] Running workspace doctor...'
  cd "$REPO_PATH"
  source "$ENV_SCRIPT"
  "$DOCTOR_SCRIPT"
fi

echo
echo '[omubot] Done.'
echo
read -r '?Press Enter to close...'
