#!/usr/bin/env python3
"""Authoring helper: rewrite f(x, ...) to x.f(...) where the compiler's
METHOD-ELIGIBLE warning asks for it.  Usage: fix_method_eligible.py FILE.bot
(run from a project that can compile FILE.bot)."""
import os, re, subprocess, sys, collections
B = os.environ.get('BOT') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'botlish-linux-x86_64')
entry = sys.argv[1]
out = subprocess.run([B, '-check', entry], capture_output=True, text=True)
text = out.stdout + out.stderr
warn = collections.defaultdict(list)
for m in re.finditer(r'^(\S+?):(\d+):(\d+): warning: call to `[^`]+` is eligible for method syntax', text, re.M):
    warn[m.group(1)].append((int(m.group(2)), int(m.group(3))))
def split_args(s):
    depth = 0; cur = ''; args = []; instr = False; i = 0
    while i < len(s):
        ch = s[i]
        if instr:
            cur += ch
            if ch == '\\': cur += s[i+1]; i += 1
            elif ch == '"': instr = False
        elif ch == '"': instr = True; cur += ch
        elif ch in '([{': depth += 1; cur += ch
        elif ch in ')]}': depth -= 1; cur += ch
        elif ch == ',' and depth == 0: args.append(cur.strip()); cur = ''
        else: cur += ch
        i += 1
    if cur.strip(): args.append(cur.strip())
    return args
def balanced_end(s, start):
    depth = 0; instr = False; i = start
    while i < len(s):
        ch = s[i]
        if instr:
            if ch == '\\': i += 1
            elif ch == '"': instr = False
        elif ch == '"': instr = True
        elif ch in '([{': depth += 1
        elif ch in ')]}':
            depth -= 1
            if depth == 0: return i
        i += 1
    return -1
chain = re.compile(r'^(-?\d+|"([^"\\]|\\.)*"|\[.*\]|[\w:?]+)(\(.*\))?(\.[\w?]+(\(.*\))?)*$', re.S)
changed = 0
for path, locs in warn.items():
    lines = open(path).read().split('\n')
    for (L, C) in sorted(set(locs), reverse=True):
        line = lines[L-1]
        m = re.match(r'[\w:]+\??', line[C-1:])
        if not m: continue
        name = m.group(0)
        p = C-1+len(name)
        if line[p:p+1] != '(': continue
        e = balanced_end(line, p)
        if e < 0: continue
        args = split_args(line[p+1:e])
        if len(args) < 2: continue
        recv = args[0]
        if not chain.match(recv) or recv.startswith('-'): recv = '(' + recv + ')'
        short = name.split('::')[-1]
        new = recv + '.' + short + '(' + ', '.join(args[1:]) + ')'
        lines[L-1] = line[:C-1] + new + line[e+1:]
        changed += 1
    open(path, 'w').write('\n'.join(lines))
print('rewrote', changed)
