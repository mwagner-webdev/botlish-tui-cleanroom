#!/bin/bash
# Re-pack tui from ./tui into a scratch repository (with the published
# event_loop_linux 0.5.0) and reinstall the devloop project from it.
# devloop/ develops tui_event_loop_linux (an editable link) against the packed
# tui artifact, because one dependency graph cannot hold an editable tui beside
# a package that locks a release of it.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BOT=${BOT:-$ROOT/botlish}
REPO=${REPO:-/tmp/opencode/repo}
mkdir -p "$REPO"
cp "$ROOT/dist/upstream/event_loop_linux-0.5.0.botpkg" "$REPO/"
"$BOT" package pack "$ROOT/tui" -out "$REPO" -force | tail -2
cd "$ROOT/devloop"
rm -rf .bot_modules
"$BOT" install -repository "$REPO" | tail -2
