#!/bin/bash
# Push wiki pages to GitHub. Requires: gh CLI authenticated.
# Run this AFTER visiting https://github.com/kragcola/omubot/wiki in a browser
# (which creates the first page and initializes the wiki git repo).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WIKI_DIR="$SCRIPT_DIR/../docs/wiki"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "=== Cloning wiki repo ==="
cd "$TMPDIR"
git clone "https://$(gh auth token):x-oauth-basic@github.com/kragcola/omubot.wiki.git" wiki 2>&1 || {
    echo ""
    echo "ERROR: Wiki git repo not found."
    echo "Please first visit https://github.com/kragcola/omubot/wiki in a browser"
    echo "and click 'Create the first page' to initialize the wiki."
    echo "Then re-run this script."
    exit 1
}

echo ""
echo "=== Copying wiki pages ==="
cp -v "$WIKI_DIR"/*.md wiki/

echo ""
echo "=== Committing and pushing ==="
cd wiki
git add -A
git commit -m "Update wiki: architecture, plugins, config, commands, deployment" || true
git push origin main

echo ""
echo "=== Done! ==="
echo "Wiki: https://github.com/kragcola/omubot/wiki"
