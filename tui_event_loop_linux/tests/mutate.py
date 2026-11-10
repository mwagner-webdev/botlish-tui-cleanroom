#!/usr/bin/env python3
"""Mutation tests of the event-loop integration.

usage: python3 mutate.py REPO_ROOT [BOTLISH] [--jobs N] [--only ID,ID] [--baseline]

For each mutant: copy the sources into a scratch project, apply ONE edit (it
must apply exactly once), build the native programs the test needs, run the
harness scenario meant to catch it, and require that the run FAILS a check
whose name contains the expected text.  A mutant detected only through some
unrelated failure does not count (the expected check must fail).  The baseline
(no edit) must pass the same scenarios: otherwise the table means nothing.

Mutants edit tui_event_loop_linux.bot (the adapter) or tui/session.bot,
tui/host.bot (the library; the scratch project packs its own tui artifact).
"""
import concurrent.futures, json, os, re, shutil, subprocess, sys, time

ROOT = os.path.abspath(sys.argv[1])
BOT = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else os.path.join(ROOT, 'botlish')
JOBS = int(sys.argv[sys.argv.index('--jobs') + 1]) if '--jobs' in sys.argv else 6
ONLY = sys.argv[sys.argv.index('--only') + 1].split(',') if '--only' in sys.argv else None
WORK = os.environ.get('MUT_WORK', '/tmp/opencode/mut')
UPSTREAM = os.path.join(ROOT, 'dist', 'upstream', 'event_loop_linux-0.5.0.botpkg')
TESTS = os.path.join(ROOT, 'tui_event_loop_linux', 'tests')

ADAPTER = 'tui_event_loop_linux/tui_event_loop_linux.bot'
SESSION = 'tui/tui/session.bot'
HOST = 'tui/tui/host.bot'

# id, what, file, old text, new text, needs, harness (script, scenarios), the check that must fail
MUTANTS = [
    ('input-watch-never-rearmed', 'the input readiness watch is not re-armed after a read', ADAPTER,
     "    event_loop_linux::rearm(ports.input_watch)\n    if tui::host::pending_input?(drained.host):", "    if tui::host::pending_input?(drained.host):",
     ['evdemo'], ('evloop', ['scenario_keys']), 'three keys in one read'),
    ('terminal-read-blocking', 'the input descriptor is not made nonblocking', SESSION,
     "term.input_fd.fcntl(4, bit_or(original, 2048)) < 0", "term.input_fd.fcntl(4, original) < 0",
     ['evdemo'], ('evloop', ['scenario_startup_timers_idle']), 'nonblocking while running'),
    ('parser-discards-partial-sequence', 'the host resolves a partial escape sequence at the end of every read', HOST,
     "    fed = host.decoder.feed(bytes)\n    host.produced(fed.decoder, fed.events)",
     "    fed = host.decoder.feed(bytes)\n    flushed = tui::input::flush(fed.decoder)\n    host.produced(flushed.decoder, [fed.events, flushed.events].flat())",
     ['evdemo'], ('evloop', ['scenario_keys']), 'split across three reads'),
    ('external-updates-never-redraw', 'an external event reaches the application but does not make the view dirty', ADAPTER,
     "        return Step {run: run.with_host(fed.host), events: fed.events, keys: [], code: -1}\n    if kind == tag_redraw:",
     "        return Step {run: run, events: fed.events, keys: [], code: -1}\n    if kind == tag_redraw:",
     ['evdemo'], ('evloop', ['scenario_stream_control']), 'redraw the screen with no keyboard input'),
    ('pending-redraw-starves-timers', 'a blocked write is retried in a tight loop instead of waiting for writability', ADAPTER,
     "            ports.await_output()\n            return pump(app, run.with_blocked(true), empty_event())", "            return pump(app, run, empty_event())",
     ['evdemo'], ('evloop', ['scenario_blocked_output']), 'never woke more than 500 ms late'),
    ('terminal-mode-not-restored', 'the cleanup of a driven session does not restore the termios', SESSION,
     "    apply_termios(std::abi::bytes::from_bytes(saved))\n    set_owner(0)", "    if flags < 0:\n        apply_termios(std::abi::bytes::from_bytes(saved))\n    set_owner(0)",
     ['evdemo', 'evcases'], ('evloop', ['scenario_quit_restore_linger']), 'echo, canon, isig back'),
    ('descriptor-flags-not-restored', 'the cleanup does not restore the input descriptor flags', SESSION,
     "        term.input_fd.fcntl(4, flags)\n", "        term.input_fd.fcntl(4, bit_or(flags, 2048))\n",
     ['evdemo'], ('evloop', ['scenario_quit_restore_linger']), 'O_NONBLOCK cleared'),
    ('watch-not-revoked-on-release', 'the readiness watch is not withdrawn when the session is released', ADAPTER,
     "    event_loop_linux::unwatch(input_watch)\n    if separate_output:", "    if separate_output:",
     ['evdemo'], ('evloop', ['scenario_quit_restore_linger']), 'no readiness record'),
    ('two-owners-accepted', 'a second session on the same Terminal is accepted', SESSION,
     "    if owner_state() != 0:\n        return false\n    set_owner(1)\n    true", "    set_owner(1)\n    true",
     ['evcases'], ('faults', ['busy']), 'second owner is refused'),
    ('resize-never-delivered', 'size changes are sampled but never delivered to the application', ADAPTER,
     "        fed = run.host.resized(e.b.shift_right(16), bit_and(e.b, 65535))", "        fed = run.host.resized(run.host.width, run.host.height)",
     ['evdemo'], ('evloop', ['scenario_resize']), 'size change while idle is delivered'),
    ('escape-timer-ignored', 'the escape timeout never resolves a held ESC', ADAPTER,
     "    if kind == tag_escape:\n", "    if kind == tag_escape and kind < 0:\n",
     ['evdemo'], ('evloop', ['scenario_keys']), 'lone Escape'),
    ('writable-never-seen', 'a writable record does not unblock a blocked frame', ADAPTER,
     "    free = if run.blocked and bit_and(bits, 2) != 0:", "    free = if run.blocked and bit_and(bits, 2) != 0 and bits < 0:",
     ['evdemo'], ('evloop', ['scenario_blocked_output']), 'timers kept running'),
    ('repaint-flag-never-cleared', 'the coalescing flag of repaint requests is never cleared', ADAPTER,
     "        set_cell(cell_repaint, 0)\n        return quiet", "        return quiet",
     ['evdemo'], ('evloop', ['scenario_keys']), 'second repaint request is honoured'),
    ('tap-never-posts', 'the controller task receives no key', ADAPTER,
     "    if cell(cell_tap) == 0:\n        return unit", "    if cell(cell_tap) == 0 or cell(cell_tap) == 1:\n        return unit",
     ['evdemo'], ('evloop', ['scenario_stream_control']), 'p pauses'),
    ('hangup-not-recognised', 'a hung-up terminal (read fails with EIO) is taken for an idle one', ADAPTER,
     "    gone = drained.state == tui::session::Reading::Closed or drained.state == tui::session::Reading::Hangup or (drained.state == tui::session::Reading::Idle and bit_and(bits, 12) != 0)",
     "    gone = drained.state == tui::session::Reading::Closed",
     ['evdemo'], ('faults', ['injection:hangup']), 'terminal hangup (EIO on read)'),
]


def sh(cmd, cwd=None, timeout=900, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, p.stdout + p.stderr


def project(mid, edit):
    d = os.path.join(WORK, mid)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    shutil.copytree(os.path.join(ROOT, 'tui_event_loop_linux'), os.path.join(d, 'adapter'), ignore=shutil.ignore_patterns('tests', 'examples', '__pycache__'))
    shutil.copytree(os.path.join(ROOT, 'tui'), os.path.join(d, 'tui'), ignore=shutil.ignore_patterns('tests', 'examples', '__pycache__'))
    for f in ('evdemo', 'monitor', 'control'):
        shutil.copy(os.path.join(ROOT, 'tui_event_loop_linux', 'examples', f + '.bot'), d)
    for f in ('evcases', 'evlinger'):
        shutil.copy(os.path.join(TESTS, f + '.bot'), d)
    if edit:
        path, old, new = edit
        if path.startswith('tui_event_loop_linux/'):
            full = os.path.join(d, 'adapter', os.path.basename(path))
        else:
            full = os.path.join(d, path)
        with open(full) as f:
            text = f.read()
        if text.count(old) != 1:
            raise RuntimeError('%s: the edit applies %d times, not once' % (mid, text.count(old)))
        with open(full, 'w') as f:
            f.write(text.replace(old, new))
    repo = os.path.join(d, 'repo')
    os.makedirs(repo)
    shutil.copy(UPSTREAM, repo)
    rc, out = sh([BOT, 'package', 'pack', os.path.join(d, 'tui'), '-out', repo])
    if rc:
        raise RuntimeError('pack tui: ' + out)
    with open(os.path.join(d, 'botlish.json'), 'w') as f:
        json.dump({'schema': 1, 'name': 'mut', 'version': '0.0.0', 'dependencies': {'tui_event_loop_linux': '0.1'},
                   'sources': {'tui_event_loop_linux': {'directory': 'adapter'}}}, f)
    rc, out = sh([BOT, 'install', '-repository', repo], cwd=d)
    if rc:
        raise RuntimeError('install: ' + out)
    rc, out = sh([BOT, 'add', 'event_loop_linux', '-repository', repo], cwd=d)
    rc, out = sh([BOT, 'add', 'tui', '-repository', repo], cwd=d)
    return d


def build(d, name):
    rc, out = sh([BOT, '-emit-native-executable', name + '.bot'], cwd=d, timeout=900)
    return rc == 0 and os.path.exists(os.path.join(d, name)), out


def harness(d, kind, scenarios):
    env = dict(os.environ)
    if kind == 'evloop':
        cmd = [sys.executable, '-u', os.path.join(TESTS, 'pty_evloop.py'), os.path.join(d, 'evdemo')] + scenarios
    else:
        env['FAULTS_ONLY'] = ','.join(scenarios)
        cmd = [sys.executable, '-u', os.path.join(TESTS, 'pty_faults.py'), os.path.join(d, 'evcases'), os.path.join(d, 'evdemo')]
    rc, out = sh(cmd, cwd=d, timeout=900, env=env)
    return out


def run_one(m):
    mid, what, path, old, new, needs, (kind, scenarios), expect = m
    t0 = time.time()
    try:
        d = project(mid, (path, old, new) if mid != 'baseline' else None)
        for n in needs:
            ok, out = build(d, n)
            if not ok:
                return mid, what, 'BUILD-FAILED', out[-300:], time.time() - t0
        out = harness(d, kind, scenarios)
    except Exception as e:
        return mid, what, 'ERROR', repr(e), time.time() - t0
    fails = [l for l in out.splitlines() if l.startswith('FAIL')]
    hit = [l for l in fails if expect in l]
    if mid == 'baseline':
        return mid, what, 'BASELINE-OK' if not fails else 'BASELINE-FAILS', '\n'.join(fails)[:400], time.time() - t0
    if hit:
        return mid, what, 'KILLED', hit[0][:160], time.time() - t0
    if fails:
        return mid, what, 'DETECTED-ELSEWHERE', fails[0][:160], time.time() - t0
    return mid, what, 'SURVIVED', out[-200:], time.time() - t0


def main():
    todo = [m for m in MUTANTS if not ONLY or m[0] in ONLY]
    if '--baseline' in sys.argv:
        # the unmutated build against every scenario the mutants use
        every = sorted({s for m in MUTANTS if m[6][0] == 'evloop' for s in m[6][1]})
        todo = [('baseline', 'no edit', ADAPTER, '', '', ['evdemo', 'evcases'], ('evloop', every), '')]
    results = []
    with concurrent.futures.ThreadPoolExecutor(JOBS) as pool:
        for r in pool.map(run_one, todo):
            results.append(r)
            print('%-34s %-20s %5.0fs  %s' % (r[0], r[2], r[4], r[3].replace('\n', ' | ')[:150]), flush=True)
    bad = [r for r in results if r[2] not in ('KILLED', 'BASELINE-OK')]
    print('\n%d mutants, %d killed by the test meant for them, %d not' % (len(results), len(results) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
