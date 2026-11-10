#!/usr/bin/env python3
"""Responsiveness / CPU benchmark of the standalone and event-loop modes in a PTY.

usage: python3 bench_pty.py OLD_DEMO NEW_DEMO STANDALONE EVDEMO [--short]

  OLD_DEMO     examples/demo built against tui 0.1.0   (the original standalone loop)
  NEW_DEMO     the same demo built against tui 0.2.0   (standalone loop on tui::host)
  STANDALONE   tests/bench_standalone.bot              (monitor.bot under tui::session::run)
  EVDEMO       examples/evdemo.bot                     (monitor.bot under the event-loop host)

Measured from outside, with the same harness for every program: CPU time and
wakeups while idle, key-to-screen latency, a large input burst, resize latency
and a large repaint.  For EVDEMO alone: event-to-screen latency of the stream's
timestamps, and the lateness of its 10 ms timer task (the program's own report)
while idle, under a flood of keys, under a 3 kHz stream, and both.
"""
import glob, os, re, select, sys, time
from ptylib import *

OLD, NEW, STANDALONE, EVDEMO = sys.argv[1:5]
SHORT = '--short' in sys.argv
out_lines = []


def emit(line=''):
    print(line, flush=True)
    out_lines.append(line)


def sched(pid):
    """(on-CPU seconds, voluntary context switches) summed over the process's threads."""
    cpu = 0
    sw = 0
    for task in glob.glob('/proc/%d/task/*' % pid):
        try:
            with open(task + '/schedstat') as f:
                cpu += int(f.read().split()[0])
            with open(task + '/status') as f:
                for l in f:
                    if l.startswith('voluntary_ctxt_switches'):
                        sw += int(l.split()[1])
        except OSError:
            pass
    return cpu / 1e9, sw


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


def stats_ms(values):
    return 'p50 %6.2f  p95 %6.2f  max %6.2f ms  (n=%d)' % (pct(values, 0.5) * 1e3, pct(values, 0.95) * 1e3, max(values) * 1e3, len(values))


def first_output_after(t, data, timeout=2.0):
    """Write DATA; seconds until the program writes anything."""
    t0 = time.perf_counter()
    t.write(data)
    end = t0 + timeout
    while time.perf_counter() < end:
        r, _, _ = select.select([t.fd], [], [], 0.0005)
        if r:
            try:
                d = os.read(t.fd, 65536)
            except OSError:
                break
            t.raw += d
            t.emu.feed(d)
            return time.perf_counter() - t0
    return None


def drain(t, seconds=0.05):
    t.pump(seconds)


def start(exe, args=(), cols=80, rows=24):
    t = Term([exe] + list(args), cols=cols, rows=rows, hard_limit=300)
    return t


def ready(t, text):
    if not t.wait_for(text, 15):
        raise RuntimeError('no first frame: %s' % t.emu.lines()[:3])
    t.pump(0.3)


def idle_cpu(label, exe, args, text):
    t = start(exe, args)
    ready(t, text)
    c0, s0 = sched(t.pid)
    t0 = time.time()
    t.pump(5.0)
    c1, s1 = sched(t.pid)
    dt = time.time() - t0
    emit('%-34s idle: %6.2f ms CPU/s, %5.1f wakeups/s' % (label, (c1 - c0) / dt * 1e3, (s1 - s0) / dt))
    t.kill()
    t.alive()


def key_latency(label, exe, args, text, prepare, key):
    t = start(exe, args)
    ready(t, text)
    prepare(t)
    lat = []
    for i in range(40 if SHORT else 80):
        drain(t, 0.06)
        d = first_output_after(t, key(i))
        if d is not None:
            lat.append(d)
    emit('%-34s key->screen: %s' % (label, stats_ms(lat)))
    t.kill()
    t.alive()


def resize_latency(label, exe, args, text):
    t = start(exe, args)
    ready(t, text)
    lat = []
    sizes = [(100, 30), (80, 24)]
    for i in range(10 if SHORT else 20):
        cols, rows = sizes[i % 2]
        t.pump(0.15)
        t0 = time.perf_counter()
        t.resize(cols, rows)
        end = t0 + 2
        while time.perf_counter() < end:
            t.pump(0.002)
            lines = t.emu.lines()
            if len(lines) == rows and lines[rows - 1] != '' and (t.emu.has('keys') or t.emu.has('Resized') or lines[rows - 1].strip() != ''):
                break
        lat.append(time.perf_counter() - t0)
    emit('%-34s resize->frame: %s' % (label, stats_ms(lat)))
    t.kill()
    t.alive()


def big_repaint(label, exe, args, text, fill=b''):
    t = start(exe, args)
    ready(t, text)
    if fill:
        t.send(fill, 0.5)
    raw0 = len(t.raw)
    t0 = time.perf_counter()
    t.resize(300, 100)
    end = t0 + 5
    seen = None
    while time.perf_counter() < end:
        t.pump(0.002)
        if t.emu.lines()[99].strip() != '':
            seen = time.perf_counter() - t0
            break
    t.pump(0.3)
    emit('%-34s 300x100 repaint: %s, %d bytes' % (label, ('%.1f ms' % (seen * 1e3)) if seen else 'not seen', len(t.raw) - raw0))
    t.kill()
    t.alive()


def fcntl_nonblock(fd):
    import fcntl
    fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)


def burst(label, exe, args, text, total=100000):
    t = start(exe, args)
    ready(t, text)
    c0, _ = sched(t.pid)
    t0 = time.perf_counter()
    data = b'k' * total
    sent = 0
    got = None
    deadline = t0 + 60
    # write and read at once: a program blocked on a full output would otherwise
    # deadlock with a harness blocked on a full input
    fcntl_nonblock(t.fd)
    while time.perf_counter() < deadline:
        r, w, _ = select.select([t.fd], [t.fd] if sent < total else [], [], 0.01)
        if r:
            try:
                d = os.read(t.fd, 65536)
            except (BlockingIOError, InterruptedError):
                d = b''
            except OSError:
                break
            t.raw += d
            t.emu.feed(d)
        if w:
            try:
                sent += os.write(t.fd, data[sent:sent + 4096])
            except BlockingIOError:
                pass
        if sent >= total:
            m = t.emu.find(r'keys (\d+)')
            if m and int(m.group(1)) >= total:
                got = time.perf_counter() - t0
                break
    c1, _ = sched(t.pid)
    m = t.emu.find(r'keys (\d+)')
    emit('%-34s %d-byte burst: %s, final keys=%s, %.2f s CPU' % (label, total, ('%.2f s (%.0f kB/s)' % (got, total / got / 1e3)) if got else 'incomplete', m.group(1) if m else '?', c1 - c0))
    t.kill()
    t.alive()


def event_latency(label, args):
    t = start(EVDEMO, ['--total', '1000000', '--autostart', '--stamp', '--tick', '1000000'] + args)
    ready(t, 'stream running')
    lat = []
    seen = None
    end = time.time() + (5 if SHORT else 10)
    while time.time() < end:
        t.pump(0.001)
        m = t.emu.find(r'stamp (\d+)')
        if m and m.group(1) != seen and int(m.group(1)) > 0:
            seen = m.group(1)
            now = time.clock_gettime(time.CLOCK_MONOTONIC)
            lat.append(now - int(seen) / 1e6)
    emit('%-34s event->screen: %s' % (label, stats_ms([max(l, 0) for l in lat])))
    t.write(b'c')
    t.pump(0.3)
    t.write(b'q')
    t.finish(10)
    t.kill()


def timer_lateness(label, args, keys=False):
    t = start(EVDEMO, ['--tick', '10000'] + args)
    ready(t, 'monitor |')
    end = time.time() + (4 if SHORT else 6)
    while time.time() < end:
        if keys:
            t.write(b'k' * 512)
        t.pump(0.002)
    t.write(b'c')
    t.pump(0.3)
    t.write(b'q')
    t.finish(15)
    rep = report_fields(t.text_after_leave(), 'TIMER')
    r2 = report_fields(t.text_after_leave())
    if rep:
        emit('%-34s timer 10 ms: n=%s mean %s us  max %s us  <1ms:%d  <5ms:%s  <20ms:%s  >=20ms:%d   (frames %s, events %s)' % (
            label, rep['n'], rep['mean_us'], rep['max_us'], int(rep['b0']) + int(rep['b1']), int(rep['b2']) + int(rep['b5']), int(rep['b10']) + int(rep['b20']),
            int(rep['b50']) + int(rep['bmore']), r2['frames'] if r2 else '?', r2['delivered'] if r2 else '?'))
    else:
        emit('%-34s timer: no report' % label)
    t.kill()


def many_updates():
    t = start(EVDEMO, ['--total', '50000', '--interval', '1', '--batch', '100', '--autostart', '--tick', '1000000'])
    ready(t, 'stream')
    t0 = time.perf_counter()
    ok = t.wait_for('stream done', 60)
    dt = time.perf_counter() - t0
    t.pump(0.3)
    t.write(b'q')
    t.finish(10)
    rep = report_fields(t.text_after_leave())
    emit('%-34s 50000 posts in bursts of 100: %s in %.2f s; frames %s for %s events; sent %s delivered %s' % ('event loop', 'done' if ok else 'INCOMPLETE', dt, rep['frames'] if rep else '?', rep['delivered'] if rep else '?', rep['sent'] if rep else '?', rep['delivered'] if rep else '?'))
    t.kill()


def taskapp_prepare(t):
    t.send(b'\t', 0.2)


def main():
    emit('# tui_event_loop_linux benchmark, %s, %d cores' % (time.strftime('%Y-%m-%d'), os.cpu_count()))
    progs = [('0.1.0 demo (original loop)', OLD, [], 'Tasks: 3, done: 1'), ('0.2.0 demo (shared host)', NEW, [], 'Tasks: 3, done: 1')]
    emit('\n## the original standalone loop against its refactoring (taskapp demo, entry field focused)')
    for label, exe, args, text in progs:
        idle_cpu(label, exe, args, text)
    for label, exe, args, text in progs:
        key_latency(label, exe, args, text, taskapp_prepare, lambda i: bytes([97 + i % 26]))
    for label, exe, args, text in progs:
        resize_latency(label, exe, args, text)
    for label, exe, args, text in progs:
        big_repaint(label, exe, args, text)

    emit('\n## the same monitor application, standalone loop against event-loop host')
    modes = [('standalone (tui::session::run)', STANDALONE, [], 'monitor |'), ('event loop (stream idle)', EVDEMO, ['--total', '100', '--tick', '3600000000'], 'monitor |')]
    for label, exe, args, text in modes:
        idle_cpu(label, exe, args, text)
    for label, exe, args, text in modes:
        key_latency(label, exe, args, text, lambda t: None, lambda i: b'k')
    for label, exe, args, text in modes:
        resize_latency(label, exe, args, text)
    for label, exe, args, text in modes:
        big_repaint(label, exe, args, text, fill=b'abcdefghij' * 20)
    for label, exe, args, text in modes:
        burst(label, exe, args, text, 50000 if SHORT else 200000)

    emit('\n## the event-loop host with unrelated work')
    event_latency('stream 100 Hz', ['--interval', '10000'])
    event_latency('stream 3 kHz', ['--interval', '300'])
    timer_lateness('idle', [])
    timer_lateness('continuous key input', [], keys=True)
    timer_lateness('3 kHz stream (sustained redraws)', ['--total', '1000000', '--interval', '300', '--autostart'])
    timer_lateness('3 kHz stream + key input', ['--total', '1000000', '--interval', '300', '--autostart'], keys=True)
    many_updates()
    with open(os.environ.get('BENCH_OUT', 'bench-results.evloop.txt'), 'w') as f:
        f.write('\n'.join(out_lines) + '\n')


if __name__ == '__main__':
    watchdog(1500)
    try:
        main()
    finally:
        kill_all()
