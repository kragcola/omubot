#!/usr/bin/env bash
# Calm Ops admin frontend compliance metrics.
#
# Counts inline-style residue, AppCard vs. AppPanelSection adoption,
# and global !important density across admin/frontend/src/views.
# Suitable for monthly maintenance-log snapshots — see
# docs/tracking/web-refactor.md (阶段 4 收口).
set -uo pipefail

# grep returns 1 on zero matches — count_or_zero swallows that into a numeric 0.
count_or_zero() {
  local n
  n=$("$@" 2>/dev/null | wc -l | tr -d ' ')
  echo "${n:-0}"
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VIEWS_DIR="$ROOT_DIR/admin/frontend/src/views"
GLOBAL_CSS="$ROOT_DIR/admin/frontend/src/styles/global.css"

if [ ! -d "$VIEWS_DIR" ]; then
  echo "[check-ui-compliance] views dir not found: $VIEWS_DIR" >&2
  exit 1
fi

# Static inline style="..." sites — should trend toward 0 outside known
# whitelist (NModal width, dynamic color binding via :style).
STATIC_STYLE=$(count_or_zero grep -RnoE '(^|[^:])style="[^"]+"' "$VIEWS_DIR" --include='*.vue')

# Dynamic :style="..." bindings — allowed (color-by-category, computed widths).
DYNAMIC_STYLE=$(count_or_zero grep -RnoE ':style="[^"]+"' "$VIEWS_DIR" --include='*.vue')

# Whitelist subset: width-only inline (modals, drawers, fixed-width inputs).
# Subtracted from STATIC_STYLE for the "true residue" line.
# Pattern: style="width: ..." with no other property in the same attribute.
WIDTH_ONLY=$(count_or_zero grep -RnoE '(^|[^:])style="width:[^";]*"' "$VIEWS_DIR" --include='*.vue')
RESIDUE=$((STATIC_STYLE - WIDTH_ONLY))

# AppCard occurrences (still acceptable for list-item cards, embedded children).
APPCARD_FILES=$(count_or_zero grep -RlE '<AppCard\b' "$VIEWS_DIR" --include='*.vue')

# AppPanelSection occurrences — preferred panel head wrapper.
APPPANEL_FILES=$(count_or_zero grep -RlE '<AppPanelSection\b' "$VIEWS_DIR" --include='*.vue')

# Global stylesheet !important count — Stage 1 baseline preserved.
if [ -f "$GLOBAL_CSS" ]; then
  GLOBAL_IMPORTANT=$(grep -cE '!important' "$GLOBAL_CSS" 2>/dev/null || echo 0)
  GLOBAL_IMPORTANT=$(echo "$GLOBAL_IMPORTANT" | tr -d ' ')
else
  GLOBAL_IMPORTANT="n/a"
fi

cat <<EOF
[check-ui-compliance] $(date '+%Y-%m-%d %H:%M:%S')

src/views (admin frontend):
  static  style="..." sites     : $STATIC_STYLE
    └─ width-only (whitelist)   : $WIDTH_ONLY
    └─ residue (target → 0)     : $RESIDUE
  dynamic :style="..." bindings : $DYNAMIC_STYLE   (allowed)
  AppCard files                 : $APPCARD_FILES
  AppPanelSection files         : $APPPANEL_FILES

global.css:
  !important count              : $GLOBAL_IMPORTANT
EOF
