#!/bin/bash
# Run the portable test files on every backend and print one line per run:
#   file  backend  passed  failed  seconds
# usage: [BOT=/path/to/compiler] run_tests.sh PROJECT_DIR [FILES...]
# Exit status is 1 if any run failed a case or produced no report.
# PROJECT_DIR holds botlish.json, .bot_modules/tui and the test files.
# The compiler: $BOT, or botlish-linux-x86_64 at the repository root.
BOT=${BOT:-$(cd "$(dirname "$0")/.." && pwd)/botlish-linux-x86_64}
STATUS=0
cd "$1" || exit 2
shift
FILES=${@:-"test_canvas.bot test_ansi.bot test_input.bot test_layout.bot test_widgets.bot test_host.bot test.bot"}
for f in $FILES; do
  for b in interp compile cranelift cranelift-generic; do
    start=$(date +%s.%N)
    out=$(timeout 600 $BOT -warnings error -backend $b $f 2>&1 </dev/null)
    end=$(date +%s.%N)
    sum=$(echo "$out" | grep -o '[0-9]* passed, [0-9]* failed' | tail -1)
    [ -z "$sum" ] && sum="NO-REPORT: $(echo "$out" | head -c 200 | tr '\n' ' ')"
    printf '%-18s %-18s %-24s %6.1fs\n' "$f" "$b" "$sum" "$(echo "$end - $start" | bc)"
    case "$sum" in *" 0 failed") ;; *) STATUS=1 ;; esac
  done
done
# native-only adapter test (stdin redirected, no terminal)
if [ -f test_session.bot ]; then
  for b in cranelift cranelift-generic; do
    out=$(timeout 300 $BOT -warnings error -backend $b test_session.bot 2>&1 </dev/null)
    sum=$(echo "$out" | grep -o '[0-9]* passed, [0-9]* failed' | tail -1)
    [ -z "$sum" ] && sum="NO-REPORT: $(echo "$out" | head -c 200 | tr '\n' ' ')"
    printf '%-18s %-18s %s\n' test_session.bot $b "$sum"
    case "$sum" in *" 0 failed") ;; *) STATUS=1 ;; esac
  done
  out=$(timeout 120 $BOT -warnings error -backend interp test_session.bot 2>&1 </dev/null | head -c 300 | tr '\n' ' ')
  printf '%-18s %-18s %s\n' test_session.bot interp "(expected NATIVE-ONLY) $out"
fi
exit $STATUS
