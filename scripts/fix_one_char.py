#!/usr/bin/env python3
"""Authoring helper: replace one-character String literals flagged by the
compiler (ONE-CHAR-STRING-LITERAL) with s('c'), a helper the file defines."""
import os, re, subprocess, sys, collections
B = os.environ.get('BOT') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'botlish-linux-x86_64')
entry = sys.argv[1]
out = subprocess.run([B, '-check', entry], capture_output=True, text=True)
text = out.stdout + out.stderr
warn = collections.defaultdict(list)
for m in re.finditer(r'^(\S+?):(\d+):(\d+): warning: the String literal `([^`]*)` is one character long', text, re.M):
    warn[m.group(1)].append((int(m.group(2)), int(m.group(3)), m.group(4)))
n = 0
for path, locs in warn.items():
    if path.startswith('/') and '.bot_modules' in path: continue
    lines = open(path).read().split('\n')
    for (L, C, lit) in sorted(set(locs), reverse=True):
        line = lines[L-1]
        if line[C-1:C-1+len(lit)] != lit: continue
        inner = lit[1:-1]
        if inner == "'": inner = "\\'"
        lines[L-1] = line[:C-1] + "s('" + inner + "')" + line[C-1+len(lit):]
        n += 1
    open(path, 'w').write('\n'.join(lines))
print('rewrote', n)
