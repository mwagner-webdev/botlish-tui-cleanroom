#!/usr/bin/env python3
"""Lifecycle cases and fault injection for tui_event_loop_linux.

usage: python3 pty_faults.py ./evcases ./evdemo

Part 1 runs evcases (one lifecycle case per run: two owners, abandonment,
shutdown, stale use, a flood into an undrained terminal, failing descriptors)
in a pseudo-terminal and checks the CASE line it prints and the terminal after.

Part 2 injects failures into the SYSTEM CALLS of an unmodified evdemo with
strace's fault injection (`strace -f -e inject=SYSCALL:error=E:when=N`): raw
mode refused, nonblocking refused, readiness registration refused, a read that
fails, writes that fail or would block, a window size that cannot be read.  N
is found by tracing a clean run first (calibration), so the injection hits the
same call on every machine.  Without strace this part is reported UNTESTED.
"""
import os, re, shutil, subprocess, sys, time
from ptylib import *

CASES = sys.argv[1] if len(sys.argv) > 1 else './evcases'
DEMO = sys.argv[2] if len(sys.argv) > 2 else './evdemo'
STRACE = shutil.which('strace')


ONLY = [x for x in os.environ.get('FAULTS_ONLY', '').split(',') if x]


def want(group):
    """FAULTS_ONLY=busy,injection:read ... runs only those groups (default: all)."""
    return not ONLY or group in ONLY


def case_line(text, name):
    m = re.search(r'CASE ' + re.escape(name) + r'((?: [a-z_]+=[^ \r\n]*)*)', text)
    if not m:
        return None
    return dict(kv.split('=', 1) for kv in m.group(1).split(' ') if '=' in kv)


class Inherit:
    """Extra descriptors for the child: NUM -> how to make it, run in the child."""

    def __init__(self, spec):
        self.spec = spec


def run_cases(args, extra=None, pre=None, hard_limit=40):
    """Start evcases with ARGS; returns the Term (not finished)."""
    import pty
    t = Term.__new__(Term)
    t.argv = [CASES] + args
    t.pid, t.fd = pty.fork()
    if t.pid == 0:
        os.environ['TERM'] = 'xterm-256color'
        for num, how in (extra or {}).items():
            if how == 'tty-wronly':
                fd = os.open('/proc/self/fd/0', os.O_WRONLY)
            elif how == 'tty-rdonly':
                fd = os.open('/proc/self/fd/0', os.O_RDONLY)
            elif how == 'pipe-with-data':
                r, w = os.pipe()
                os.write(w, b'x' * 16)
                fd = r
            os.dup2(fd, num)
        os.execv(CASES, [CASES] + args)
    CHILDREN.append(t.pid)
    import threading
    t.exited = None
    t.master_closed = False
    t.raw = b''
    t.set_size(80, 24)
    t.emu = Emulator(80, 24)
    timer = threading.Timer(hard_limit, t.kill)
    timer.daemon = True
    timer.start()
    return t


def restored(t):
    f = t.flags()
    return f['echo'] and f['icanon'] and f['isig']


def part_cases():
    if want('busy'):
        # busy: two owners, repeated open/close
        t = run_cases(['--case', 'busy'])
        done = t.finish(30)
        c = case_line(t.text_after_leave(), 'busy')
        check('busy: the program finishes by itself', done, t.exited)
        check('busy: a second owner is refused (TerminalBusy) while the first is running', c is not None and c.get('second') == 'busy', c)
        check('busy: the standalone open is refused too, as IoFailed "busy"', c is not None and c.get('standalone') == 'busy', c)
        check('busy: the first session ended normally (close requested, code 4) and the terminal is free again', c is not None and c.get('first_outcome') == '4' and c.get('owned_after') == 'no', c)
        check('busy: three more open/close rounds succeed; nothing owns the terminal at the end', c is not None and c.get('rounds') == '3' and c.get('owned_end') == 'no', c)
        check('busy: terminal restored after repeated initialisation and shutdown', restored(t), t.flags())

    if want('abandon'):
        # abandon / shutdown / stale use
        t = run_cases(['--case', 'abandon', '--linger', '1500'])
        t.wait_raw(b'CASE abandon', 15)
        t.pump(0.3)
        c = case_line(t.text_after_leave(), 'abandon')
        check('abandon: the abandoned UI task released the terminal (loop abandon)', c is not None and int(c.get('abandoned', '0')) >= 1 and c.get('owned_after') == 'no' and c.get('running_before') == 'yes', c)
        check('abandon: raw mode undone by the abandoned coroutine\'s cleanup', restored(t), t.flags())
        check('abandon: descriptor flags restored (O_NONBLOCK cleared)', t.nonblocking(0) is False, t.fd_flags(0))
        check('abandon: screen restore written (alternate screen left)', b'\x1b[?1049l' in t.raw)
        t.finish(8)

    if want('shutdown'):
        t = run_cases(['--case', 'shutdown', '--linger', '1500'])
        t.wait_raw(b'CASE shutdown', 15)
        t.pump(0.3)
        c = case_line(t.text_after_leave(), 'shutdown')
        check('shutdown: shutdown() revoked the inbox; the UI task ended itself with code 5', c is not None and c.get('outcome') == '5' and c.get('running') == 'no' and c.get('owned_after') == 'no', c)
        check('shutdown: terminal and descriptor restored', restored(t) and t.nonblocking(0) is False, (t.flags(), t.fd_flags(0)))
        t.finish(8)

    if want('stale'):
        t = run_cases(['--case', 'stale'])
        done = t.finish(20)
        c = case_line(t.text_after_leave(), 'stale')
        check('stale: after the UI ended, send / redraw / repaint / close all say NotLive; the outcome is stable', c is not None and c.get('outcome') == '4' and all(c.get(k) == 'yes' for k in ('send', 'redraw', 'repaint', 'close', 'outcome_stable')) and c.get('running') == 'no', c)
        check('stale: nothing owns the terminal afterwards', c is not None and c.get('owned_after') == 'no' and restored(t), c)

    if want('watchfail'):
        # failing descriptors
        t = run_cases(['--case', 'watchfail', '--watch', '999', '--linger', '300'])
        done = t.finish(20)
        c = case_line(t.text_after_leave(), 'watchfail')
        check('watch registration failure: DriverFault (EBADF) is reported, nothing is left owned', c is not None and c.get('open') == 'driver' and c.get('code') == '-9' and c.get('owned') == 'no', c)
        check('watch registration failure: raw mode and flags undone, screen left', restored(t) and b'\x1b[?1049l' in t.raw, t.flags())

    if want('readfail'):
        t = run_cases(['--case', 'readfail', '--in', '3', '--out', '1', '--watch', '4', '--linger', '300'], extra={3: 'tty-wronly', 4: 'pipe-with-data'})
        done = t.finish(20)
        c = case_line(t.text_after_leave(), 'readfail')
        check('terminal read failure: the UI task ends with outcome 3 "read"', c is not None and c.get('outcome') == '3' and c.get('detail') == 'read' and c.get('owned_after') == 'no', c)
        check('terminal read failure: terminal restored', restored(t), t.flags())

    if want('writefail'):
        t = run_cases(['--case', 'writefail', '--in', '0', '--out', '5', '--linger', '300'], extra={5: 'tty-rdonly'})
        done = t.finish(20)
        c = case_line(t.text_after_leave(), 'writefail')
        check('terminal write failure: open fails with IoFailed "write"; nothing left owned', c is not None and c.get('open') == 'iofailed' and c.get('operation') == 'write' and c.get('owned') == 'no', c)
        check('terminal write failure: terminal restored', restored(t), t.flags())

    if want('notty'):
        t = run_cases(['--case', 'notty-in', '--in', '99', '--linger', '100'])
        done = t.finish(20)
        c = case_line(t.text_after_leave(), 'notty-in')
        check('terminal descriptor unavailable (input): NotATerminal naming "input"; nothing owned', c is not None and c.get('open') == 'notatty' and c.get('stream') == 'input' and c.get('owned') == 'no', c)
        t = run_cases(['--case', 'notty-out', '--out', '99', '--linger', '100'])
        done = t.finish(20)
        c = case_line(t.text_after_leave(), 'notty-out')
        check('terminal descriptor unavailable (output): NotATerminal naming "output"; raw mode never entered', c is not None and c.get('open') == 'notatty' and c.get('stream') == 'output' and restored(t) and b'\x1b[?1049h' not in t.raw, (c, t.raw[:60]))
        p = subprocess.run([CASES, '--case', 'devnull'], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        out = p.stdout.decode('utf-8', 'replace')
        check('stdin not a terminal: NotATerminal "input", nothing drawn', 'open=notatty stream=input' in out and '\x1b' not in out, out[:200])

    if want('flood'):
        # an undrained terminal, then abandonment in the middle of a frame
        t = run_cases(['--case', 'flood', '--stop', '2500000', '--linger', '800'], hard_limit=60)
        time.sleep(4.5)
        # nothing has read the terminal for 4.5 s: the abandon's cleanup had to give up on its screen restore
        state = restored(t)
        check('cancel during a blocked redraw: termios restored although the screen restore could not be written (bounded patience)', state, t.flags())
        t.pump(2.0)
        done = t.finish(10)
        c = case_line(t.text_after_leave(), 'flood')
        if c is None:
            c = case_line(t.raw.decode('utf-8', 'replace'), 'flood')
        check('cancel during a blocked redraw: the flood really blocked output (write_blocks > 0) and the UI task was abandoned', c is not None and int(c.get('blocks', '0')) > 0 and int(c.get('abandoned', '0')) >= 1 and c.get('owned_after') == 'no', c)
        check('cancel during a blocked redraw: the program ends', done, t.exited)


# ---------------------------------------------------------------- fault injection

def trace_clean():
    """Run evdemo under strace without injecting; return the syscall log lines."""
    log = '/tmp/pty-faults-clean-%d.txt' % os.getpid()
    # -P: only the calls that touch the terminal are traced (and, later, injected into),
    # so the call numbers do not depend on the runtime's other descriptors
    t = Term(lambda: [STRACE, '-f', '-o', log, '-P', os.ttyname(0), '-e', 'trace=read,write,ioctl,fcntl', DEMO, '--total', '10', '--tick', '100000'])
    t.wait_for('monitor | tick', 15)
    t.pump(0.6)
    t.send(b'a', 0.4)
    t.send(b'q', 0.5)
    t.finish(10)
    with open(log) as f:
        lines = [l.rstrip('\n') for l in f if 'resumed' not in l]
    os.unlink(log)
    return lines


def trace_clean_epoll():
    log = '/tmp/pty-faults-clean-ep-%d.txt' % os.getpid()
    t = Term([STRACE, '-f', '-o', log, '-e', 'trace=epoll_ctl', DEMO, '--total', '10', '--tick', '100000'])
    t.wait_for('monitor | tick', 15)
    t.pump(0.6)
    t.send(b'a', 0.4)
    t.send(b'q', 0.5)
    t.finish(10)
    with open(log) as f:
        lines = [l.rstrip('\n') for l in f if 'resumed' not in l]
    os.unlink(log)
    return lines


def nth(lines, name, predicate):
    """The 1-based number, among the calls of syscall NAME made by the SAME THREAD, of
    the first call that matches PREDICATE (strace counts `when=` per thread)."""
    seen = {}
    for l in lines:
        tid, _, call = l.partition(' ')
        call = call.lstrip()
        if call.startswith(name + '('):
            seen[tid] = seen.get(tid, 0) + 1
            if predicate(call):
                return seen[tid]
    return None


def injected(spec, args=(), hard_limit=40, tty_only=True):
    log = '/tmp/pty-faults-inj-%d.txt' % os.getpid()
    name = spec.split(':')[0]
    def argv():
        pre = [STRACE, '-f', '-o', log]
        if tty_only:
            pre += ['-P', os.ttyname(0)]
        return pre + ['-e', 'trace=' + name, '-e', 'inject=' + spec, DEMO] + list(args)
    return Term(argv, hard_limit=hard_limit)


def part_injection():
    if not STRACE:
        print('UNTESTED: strace is not installed; the fault-injection cases were not run')
        return
    if ONLY and not any(x.startswith('injection:') for x in ONLY):
        return
    lines = trace_clean()
    elines = trace_clean_epoll()
    idx = {
        'tcsets': nth(lines, 'ioctl', lambda l: 'TCSETS' in l),
        'setfl': nth(lines, 'fcntl', lambda l: 'F_SETFL' in l),
        'add_in': nth(elines, 'epoll_ctl', lambda l: 'EPOLL_CTL_ADD, 0,' in l),
        'add_out': nth(elines, 'epoll_ctl', lambda l: 'EPOLL_CTL_ADD, 1,' in l),
        'mod_in': nth(elines, 'epoll_ctl', lambda l: 'EPOLL_CTL_MOD, 0,' in l),
        'read_in': nth(lines, 'read', lambda l: l.startswith('read(0,')),
        'frame2': nth(lines, 'write', lambda l: l.startswith('write(1,') and '\\33[1;17H' in l),
        'winsz': nth(lines, 'ioctl', lambda l: 'TIOCGWINSZ' in l),
    }
    print('INFO calibration (call numbers within their syscall): %s' % idx)
    check('calibration: every call the injections need was found in the clean trace', all(v is not None for v in idx.values()), idx)
    if not all(v is not None for v in idx.values()):
        return

    # --- start-up faults: nothing may be left changed
    if want('injection:raw'):
        t = injected('ioctl:error=EIO:when=%d' % idx['tcsets'])
        done = t.finish(30)
        out = t.raw.decode('utf-8', 'replace')
        check('raw-mode failure (TCSETS refused): reported as IoFailed "configure"', 'terminal I/O failed during: configure' in out, out[-200:])
        check('raw-mode failure: no screen change was made and the terminal is as it was', b'\x1b[?1049h' not in t.raw and restored(t), t.flags())

    if want('injection:nonblocking'):
        t = injected('fcntl:error=EPERM:when=%d' % idx['setfl'])
        done = t.finish(30)
        out = t.raw.decode('utf-8', 'replace')
        check('nonblocking refused (F_SETFL fails): reported as IoFailed "nonblocking"', 'terminal I/O failed during: nonblocking' in out, out[-200:])
        check('nonblocking refused: raw mode undone, screen left, nothing owned', restored(t) and b'\x1b[?1049l' in t.raw, t.flags())

    if want('injection:input-registration'):
        t = injected('epoll_ctl:error=ENOSPC:when=%d' % idx['add_in'], tty_only=False)
        done = t.finish(30)
        out = t.raw.decode('utf-8', 'replace')
        check('readiness registration of the input refused: DriverFault reported', 'readiness watch failed: driver fault -28' in out, out[-200:])
        check('readiness registration refused: terminal fully restored (raw mode, flags, screen)', restored(t) and b'\x1b[?1049l' in t.raw and done, t.flags())

    if want('injection:output-registration'):
        t = injected('epoll_ctl:error=ENOSPC:when=%d' % idx['add_out'], tty_only=False)
        done = t.finish(30)
        out = t.raw.decode('utf-8', 'replace')
        check('readiness registration of the output refused: the input watch already made is withdrawn, terminal restored', 'readiness watch failed' in out and restored(t) and done, out[-200:])

        # --- run-time faults

    if want('injection:read'):
        t = injected('read:error=EBADF:when=%d' % idx['read_in'], ['--total', '10'])
        t.wait_for('monitor | tick', 15)
        t.pump(0.5)
        t.send(b'a', 0.5)
        done = t.finish(15)
        rep = report_fields(t.text_after_leave())
        check('terminal read failure (EBADF): the UI task ends, outcome 3 "read", program exits', done and rep is not None and rep['outcome'] == '3' and rep['detail'] == 'read', rep)
        check('terminal read failure: terminal restored', restored(t), t.flags())

    if want('injection:hangup'):
        t = injected('read:error=EIO:when=%d' % idx['read_in'], ['--total', '10'])
        t.wait_for('monitor | tick', 15)
        t.pump(0.5)
        t.send(b'a', 0.5)
        done = t.finish(15)
        rep = report_fields(t.text_after_leave())
        check('terminal hangup (EIO on read): the input has ended (outcome 1), no busy loop, program exits', done and rep is not None and rep['outcome'] == '1' and restored(t), rep)

    if want('injection:rearm'):
        t = injected('epoll_ctl:error=EBADF:when=%d' % idx['mod_in'], ['--total', '10'], tty_only=False)
        t.wait_for('monitor | tick', 15)
        t.pump(0.5)
        t.send(b'a', 0.5)
        done = t.finish(15)
        rep = report_fields(t.text_after_leave())
        check('readiness re-arm refused: the UI task ends with outcome 6 (driver), terminal restored', done and rep is not None and rep['outcome'] == '6' and restored(t), rep)

    if want('injection:write'):
        t = injected('write:error=EIO:when=%d' % idx['frame2'], ['--total', '10', '--tick', '100000'])
        t.wait_for('monitor | tick', 15)
        done = t.finish(15)
        rep = report_fields(t.text_after_leave())
        check('failed output (EIO on a frame): outcome 3 "write", program exits, terminal restored', done and rep is not None and rep['outcome'] == '3' and rep['detail'] == 'write' and restored(t), rep)

    if want('injection:eagain'):
        # an output that would block, three times: the frame is completed when the terminal is writable
        t = injected('write:error=EAGAIN:when=%d..%d' % (idx['frame2'], idx['frame2'] + 2), ['--total', '10', '--tick', '100000'])
        t.wait_for('monitor | tick', 15)
        t.pump(1.0)
        ticks = None
        m = t.emu.find(r'tick (\d+)')
        ticks = int(m.group(1)) if m else None
        t.send(b'abc', 0.8)
        check('interrupted output (three writes refused with EAGAIN): the screen is still correct and keeps updating', ticks is not None and ticks >= 3 and t.emu.has('key c'), (ticks, t.emu.lines()[:8]))
        t.send(b'q', 0.5)
        done = t.finish(10)
        rep = report_fields(t.text_after_leave())
        check('interrupted output: the blocked-write path was taken (write_blocks >= 1) and the session ended cleanly', done and rep is not None and int(rep['write_blocks']) >= 1 and rep['outcome'] == '0', rep)

        # --- a terminal whose size cannot be read any more

    if want('injection:size'):
        t = injected('ioctl:error=ENOTTY:when=%d+' % (idx['winsz'] + 6), ['--total', '10', '--tick', '100000'])
        t.wait_for('monitor | tick', 15)
        t.pump(1.0)
        t.resize(100, 30)
        t.pump(1.0)
        t.send(b'abc', 0.5)
        check('size unavailable (TIOCGWINSZ fails from now on): no crash, no resize event, the application keeps running', t.emu.has('key c') and not t.emu.has('resize 100x30'), t.emu.lines()[:10])
        t.send(b'q', 0.5)
        done = t.finish(10)
        rep = report_fields(t.text_after_leave())
        check('size unavailable: ends cleanly', done and rep is not None and rep['outcome'] == '0', rep)


if __name__ == '__main__':
    try:
        import pty
        pty.openpty()
    except Exception as e:
        print('UNTESTED: no pseudo-terminal available (%s)' % e)
        sys.exit(2)
    watchdog(900)
    for part in (part_cases, part_injection):
        print('--', part.__name__, flush=True)
        try:
            part()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(part.__name__ + ' ran to completion', False, repr(e))
    kill_all()
    sys.exit(summary())
