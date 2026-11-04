#!/usr/bin/env python3
"""Authoring helper: print the FAIL entries (with expected/actual) of a std::test report on stdin."""
import sys, re
t = sys.stdin.read()
items = re.findall(r'"((?:[^"\\]|\\.)*)"', t)
for i, it in enumerate(items):
    if it.startswith('FAIL'):
        print('\n'.join(items[i:i+3]))
m = re.findall(r'\d+ passed, \d+ failed', t)
print(m[-1] if m else 'NO SUMMARY\n' + t[:500])
