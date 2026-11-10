#!/bin/bash
# Pack both packages into dist/ (tui-VERSION.botpkg, tui_event_loop_linux-VERSION.botpkg),
# twice, and fail unless the two packs are byte-identical.  A released version is
# never overwritten: bump the version in the package's botlish.json instead
# (the existing dist/tui-0.1.0.botpkg stays as it is).
# usage: [BOT=/path/to/compiler] scripts/pack_all.sh [--force]
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BOT=${BOT:-$ROOT/botlish-linux-x86_64}
[ -x "$BOT" ] || BOT=$ROOT/botlish
find "$ROOT/tui" "$ROOT/tui_event_loop_linux" -name __pycache__ -prune -exec rm -rf {} +
FORCE=
[ "$1" = "--force" ] && FORCE=-force
for pkg in tui tui_event_loop_linux; do
  rm -rf /tmp/pack-a /tmp/pack-b
  mkdir -p /tmp/pack-a /tmp/pack-b
  "$BOT" package pack "$ROOT/$pkg" -out /tmp/pack-a | head -2
  touch "$ROOT/$pkg/README.md"
  "$BOT" package pack "$ROOT/$pkg" -out /tmp/pack-b >/dev/null
  cmp /tmp/pack-a/*.botpkg /tmp/pack-b/*.botpkg
  name=$(basename /tmp/pack-a/*.botpkg)
  if [ -e "$ROOT/dist/$name" ] && [ -z "$FORCE" ]; then
    cmp /tmp/pack-a/$name "$ROOT/dist/$name" && echo "$name: identical to dist/ (deterministic)"
  else
    cp /tmp/pack-a/$name "$ROOT/dist/$name"
    echo "$name: written to dist/"
  fi
done
sha256sum "$ROOT"/dist/*.botpkg "$ROOT"/dist/upstream/*.botpkg
