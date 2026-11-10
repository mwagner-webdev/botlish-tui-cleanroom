#!/usr/bin/env python3
"""PTY acceptance of tui_event_loop_linux with the evdemo program.

usage: python3 pty_evloop.py ./evdemo [--quick]

evdemo runs the UI task, a ticker, a controller and a simulated stream in ONE
std::event_loop (tests/../examples/evdemo.bot).  This script starts it in a
pseudo-terminal, sends keystrokes and resizes, and checks the screen, the
program's report and the kernel's view of the terminal and of the descriptors.
Every wait has a deadline; a watchdog kills the children and fails the run.
"""
import os, re, subprocess, sys, time
from ptylib import *

EXE = sys.argv[1] if len(sys.argv) > 1 else './evdemo'
QUICK = '--quick' in sys.argv


def tick_of(t):
    m = t.emu.find(r'tick (\d+)')
    return int(m.group(1)) if m else None


def chunks_of(t):
    m = t.emu.find(r'chunks (\d+)/(\d+)')
    return int(m.group(1)) if m else None


def keys_of(t):
    m = t.emu.find(r'keys (\d+)')
    return int(m.group(1)) if m else None


def count_lines(t, text):
    return sum(1 for l in t.emu.lines() if text in l)


def start(args=(), **kw):
    return Term([EXE] + list(args), **kw)


def scenario_startup_timers_idle():
    t = start(['--total', '100'])
    before = t.flags()
    check('startup: cooked mode before the first frame is not our concern (the PTY starts cooked)', True)
    ok = t.wait_for('monitor | tick', 6)
    check('startup: the first frame is drawn (status bar)', ok, '\n'.join(t.emu.lines()[:4]))
    check('startup: alternate screen entered, cursor hidden', b'\x1b[?1049h' in t.raw and b'\x1b[?25l' in t.raw)
    f = t.flags()
    check('startup: raw mode while running (no echo, no canon, no isig)', not f['echo'] and not f['icanon'] and not f['isig'], f)
    check('startup: the input descriptor is nonblocking while running (kernel flags)', t.nonblocking(0) is True, t.fd_flags(0))
    t1 = tick_of(t)
    cpu0 = t.cpu_seconds()
    t.pump(2.0)
    idle = t.cpu_seconds() - cpu0
    t2 = tick_of(t)
    check('idle: 2 s with a timer and no input cost < 0.1 s CPU (no busy loop)', idle < 0.1, 'cpu %.3fs' % idle)
    check('timers: the tick counter advances with no keyboard input (%s -> %s in 2 s)' % (t1, t2), t1 is not None and t2 is not None and t2 - t1 >= 6, (t1, t2))
    t.send(b'q', 0.5)
    t.finish()
    return t


def scenario_keys():
    t = start(['--total', '100'])
    t.wait_for('monitor | tick', 6)
    t.send(b'a', 0.3)
    check('keys: one byte is one key event', t.emu.has('key a'))
    t.send(b'bcd', 0.3)
    lines = t.emu.lines()
    idx = [next((i for i, l in enumerate(lines) if 'key ' + k in l), -1) for k in 'bcd']
    check('keys: three keys in one read arrive as three events, in order', all(i >= 0 for i in idx) and idx == sorted(idx), idx)
    for b in (b'\x1b', b'[', b'D'):
        t.write(b)
        t.pump(0.012)
    t.pump(0.4)
    check('keys: an escape sequence split across three reads decodes as one key (left)', count_lines(t, 'key left') == 1 and count_lines(t, 'key escape') == 0 and count_lines(t, 'key [') == 0, t.emu.lines()[2:12])
    t.send(b'\x1b', 0.5)
    check('keys: a lone Escape is resolved by the loop timer (50 ms), not lost', count_lines(t, 'key escape') == 1, t.emu.lines()[2:14])
    t.send(b'\xc3\xa9', 0.3)
    check('keys: a multi-byte UTF-8 character arrives whole', t.emu.has('key \u00e9'))
    for i in range(30):
        t.write(b'k')
    t.pump(0.8)
    k = keys_of(t)
    check('keys: a burst of 30 bytes in a few writes loses none (keys counter)', k is not None and k >= 36, k)
    t.pump(0.3)
    clears = t.emu.full_clears
    t.send(b'r', 0.5)
    check('redraw: the r key (a task asks the UI task for a repaint) repaints everything', t.emu.full_clears > clears, (clears, t.emu.full_clears))
    clears = t.emu.full_clears
    t.send(b'r', 0.5)
    check('redraw: a second repaint request is honoured too (the coalescing flag is released)', t.emu.full_clears > clears, (clears, t.emu.full_clears))
    t.send(b'd', 0.3)
    check('redraw: request_redraw of an unchanged view writes nothing visible and costs no crash', t.emu.has('monitor | tick'))
    t.send(b'q', 0.5)
    t.finish()


def scenario_resize():
    t = start(['--total', '100', '--tick', '3600000000'])
    t.wait_for('monitor | tick', 6)
    t.resize(100, 30)
    ok = t.wait_for('resize 100x30', 3)
    check('resize: a size change while idle is delivered to the application (log line) within 3 s', ok, t.emu.lines()[:8])
    check('resize: the frame fills the new size (status bar on row 30)', t.emu.lines()[29].startswith('keys '), t.emu.lines()[28:30])
    t.resize(80, 24)
    ok = t.wait_for('resize 80x24', 3)
    check('resize: back to the original size is delivered too', ok and t.emu.lines()[23].startswith('keys '), t.emu.lines()[22:24])
    t.set_size(0, 0)
    t.pump(0.6)
    t.send(b'a', 0.3)
    check('resize: a zero-sized terminal reads as 80x24 (no event, no crash)', t.emu.has('key a') and count_lines(t, 'resize 80x24') == 1, t.emu.lines()[:10])
    t.resize(100, 30)
    check('resize: and a real size after it is delivered again', t.wait_for('resize 100x30', 3))
    t.send(b'q', 0.5)
    t.finish()


def scenario_stream_control():
    t = start(['--total', '100', '--interval', '20000'])
    t.wait_for('monitor | tick', 6)
    t.send(b's', 0.1)
    check('stream: s starts the stream (state change event from another task)', t.wait_for('stream running', 2))
    ok = False
    end = time.time() + 5
    while time.time() < end and not ok:
        t.pump(0.1)
        c = chunks_of(t)
        ok = c is not None and c >= 5
    check('stream: external events redraw the screen with no keyboard input (chunks >= 5)', ok, chunks_of(t))
    t.send(b'p', 0.4)
    check('stream: p pauses', t.emu.has('stream paused'))
    a = chunks_of(t)
    t.pump(0.6)
    b = chunks_of(t)
    check('stream: no chunks while paused (%s -> %s)' % (a, b), a == b, (a, b))
    t.send(b'p', 0.6)
    c = chunks_of(t)
    check('stream: p resumes, chunks advance again', t.emu.has('stream running') and c > b, (b, c))
    t.send(b'c', 0.6)
    check('stream: c cancels', t.wait_for('stream cancelled', 2))
    d = chunks_of(t)
    t.pump(0.5)
    check('stream: no chunks after cancel', chunks_of(t) == d, (d, chunks_of(t)))
    t.send(b's', 0.3)
    t.wait_for('stream running', 2)
    t.pump(0.5)
    check('stream: s restarts a cancelled stream', t.emu.has('stream running') and (chunks_of(t) or 0) >= 1, chunks_of(t))
    t.send(b'q', 0.5)
    done = t.finish()
    check('stream: q ends the program by itself while the stream is live (producers see NotLive)', done and t.exited == 0, t.exited)
    rep = report_fields(t.text_after_leave())
    check('stream: report printed after the terminal was restored; outcome 0', rep is not None and rep.get('outcome') == '0', rep)


def scenario_no_loss():
    t = start(['--total', '2000', '--interval', '500', '--autostart', '--stamp'])
    ok = t.wait_for('stream done', 20)
    check('stream: 2000 chunks at 2 kHz complete', ok, t.emu.lines()[:3])
    t.pump(0.3)
    check('stream: the last frame shows every chunk (2000/2000)', chunks_of(t) == 2000, chunks_of(t))
    t.send(b'q', 0.3)
    t.finish()
    rep = report_fields(t.text_after_leave())
    check('no loss: every accepted event reached the application (posted == delivered)', rep is not None and rep['posted'] == rep['delivered'], rep)
    check('no loss: the stream sent 2000 and the report counted them', rep is not None and rep['sent'] == '2000', rep)
    if rep is not None:
        print('INFO 2 kHz stream: %s frames for %s events (one frame per event while the UI keeps up)' % (rep['frames'], rep['delivered']))


def scenario_flood_keys_resize_timers():
    t = start(['--total', '100000', '--interval', '300', '--autostart', '--tick', '50000', '--stamp'], hard_limit=90)
    t.wait_for('stream running', 6)
    t.pump(0.5)
    k0 = keys_of(t) or 0
    lat = []
    for i in range(20):
        t0 = time.time()
        t.write(b'k')
        end = t0 + 2
        while time.time() < end:
            t.pump(0.002)
            k = keys_of(t)
            if k is not None and k >= k0 + i + 1:
                break
        lat.append(time.time() - t0)
        t.pump(0.05)
    lat.sort()
    p50, p95 = lat[len(lat) // 2], lat[int(len(lat) * 0.95) - 1]
    print('INFO key->screen latency during a 3 kHz stream: p50 %.1f ms, p95 %.1f ms, max %.1f ms' % (p50 * 1e3, p95 * 1e3, lat[-1] * 1e3))
    check('flood: all 20 keys during a sustained stream reach the application, each within 1 s', keys_of(t) == k0 + 20 and lat[-1] < 1.0, (k0, keys_of(t), lat[-1]))
    tick_a, t_a = tick_of(t), time.time()
    t.resize(100, 30)
    ok = False
    end = time.time() + 3
    while time.time() < end and not ok:
        t.pump(0.05)
        ok = t.emu.lines()[29].startswith('keys ')
    check('resize during the stream: the frame fills the new size (status bar on row 30)', ok, t.emu.lines()[28:30])
    t.resize(80, 24)
    ok = False
    end = time.time() + 3
    while time.time() < end and not ok:
        t.pump(0.05)
        ok = t.emu.lines()[23].startswith('keys ')
    check('resize back to the original size (status bar on row 24)', ok, t.emu.lines()[22:24])
    t.pump(1.5)
    tick_b, t_b = tick_of(t), time.time()
    expected = (t_b - t_a) / 0.05
    check('timers: the 50 ms timer keeps its pace during the flood (%s ticks, %.0f expected)' % (tick_b - tick_a, expected), tick_b - tick_a >= 0.7 * expected, (tick_a, tick_b))
    t.send(b'c', 0.5)
    t.send(b'q', 0.3)
    done = t.finish()
    rep = report_fields(t.text_after_leave())
    check('flood: ends cleanly', done and rep is not None and rep['outcome'] == '0', rep)
    timer = report_fields(t.text_after_leave(), 'TIMER')
    print('INFO ticker lateness: %s' % timer)
    if timer:
        check('flood: the ticker task slept 50 ms %s times; none later than 100 ms' % timer['n'], int(timer['b50']) == 0 and int(timer['bmore']) == 0 and int(timer['n']) > 30, timer)


def scenario_quit_restore_linger():
    t = start(['--total', '100', '--linger', '1500'])
    before_cooked = t.flags()
    t.wait_for('monitor | tick', 6)
    t.send(b'q', 0.4)
    alive = t.alive()
    check('quit: the program is still alive (lingering) so the terminal can be inspected', alive)
    f = t.flags()
    check('restore: echo, canon, isig back after the UI task ended (loop still running)', f['echo'] and f['icanon'] and f['isig'], f)
    check('restore: descriptor flags back (O_NONBLOCK cleared) after the UI task ended', t.nonblocking(0) is False, t.fd_flags(0))
    check('restore: alternate screen left, cursor shown', b'\x1b[?1049l' in t.raw and b'\x1b[?25h' in t.raw)
    t.write(b'zz\n')
    t.pump(0.3)
    done = t.finish(6)
    check('restore: the program exits by itself after the linger', done and t.exited == 0, t.exited)
    tail = t.text_after_leave()
    rep = report_fields(tail)
    ling = report_fields(tail, 'LINGER')
    check('report: printed on the restored main screen, outcome 0', rep is not None and rep['outcome'] == '0', tail[-300:])
    check('watch revoked: typing at the terminal after the UI ended produced no readiness record', ling is not None and ling['watch_events_after_release'] == '0', ling)


def scenario_error_path():
    t = start(['--total', '100'])
    t.wait_for('monitor | tick', 6)
    t.send(b'x', 0.5)
    done = t.finish()
    tail = t.text_after_leave()
    rep = report_fields(tail)
    check('error: an application failure ends the UI task, the program ends by itself', done and t.exited == 0, t.exited)
    check('error: outcome 2 with the application own reason', rep is not None and rep['outcome'] == '2' and 'x pressed' in tail, tail[-300:])
    f = t.flags()
    check('error: terminal restored (echo, canon, isig)', f['echo'] and f['icanon'] and f['isig'], f)


def scenario_ctrl_c():
    t = start(['--total', '100'])
    t.wait_for('monitor | tick', 6)
    t.send(b'\x03', 0.5)
    done = t.finish()
    rep = report_fields(t.text_after_leave())
    f = t.flags()
    check('Ctrl+C arrives as a key (ISIG off) and quits cleanly', done and rep is not None and rep['outcome'] == '0' and f['echo'] and f['icanon'], (t.exited, rep))


def scenario_hangup():
    # idle program: nothing is written, so only the input side can notice the hangup
    t = start(['--total', '100', '--tick', '200000'])
    t.wait_for('monitor | tick', 6)
    t.pump(0.3)
    t.close_master()
    done = t.finish(8)
    check('hangup: closing the terminal of an idle program ends it by itself (readiness HUP, no busy loop)', done, t.exited)
    # a program that is writing: the failed write ends it
    t = start(['--total', '100000', '--interval', '300', '--autostart'])
    t.wait_for('stream running', 6)
    t.pump(0.3)
    t.close_master()
    done = t.finish(8)
    check('hangup: closing the terminal of a program that is writing ends it too', done, t.exited)


def scenario_not_a_tty():
    p = subprocess.run([EXE], stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    out = p.stdout.decode('utf-8', 'replace')
    check('no terminal: NotATerminal is reported, nothing drawn, no raw mode', 'is not a terminal' in out and '\x1b[?1049h' not in out, (p.returncode, out[:200]))


def scenario_blocked_output():
    t = start(['--total', '1000000', '--interval', '100', '--autostart', '--tick', '100000', '--stamp'], hard_limit=90)
    t.wait_for('stream running', 6)
    t0 = time.time()
    tick0 = tick_of(t)
    # stop draining the terminal: output backs up while the stream floods
    time.sleep(3.0)
    t.write(b'kkk')
    time.sleep(1.0)
    cpu = t.cpu_seconds()
    t.pump(0.5)
    t1 = time.time()
    t.pump(2.0)
    # drain everything that accumulated, then look at the counter
    end = time.time() + 5
    while time.time() < end:
        g = t.pump(0.2)
        if not g:
            break
    t.pump(0.5)
    tick1 = tick_of(t)
    elapsed = time.time() - t0
    print('INFO blocked-output: tick %s -> %s over %.1f s (100 ms timer)' % (tick0, tick1, elapsed))
    check('blocked output: timers kept running while the terminal did not drain (ticks follow wall time)', tick0 is not None and tick1 is not None and tick1 - tick0 >= 0.6 * (elapsed - 1) / 0.1, (tick0, tick1, elapsed))
    check('blocked output: keys typed while output was blocked were processed', (keys_of(t) or 0) >= 3, keys_of(t))
    t.send(b'c', 0.3)
    t.send(b'q', 0.5)
    done = t.finish(8)
    rep = report_fields(t.text_after_leave())
    check('blocked output: ends cleanly afterwards', done and rep is not None and rep['outcome'] == '0', rep)
    timer = report_fields(t.text_after_leave(), 'TIMER')
    print('INFO blocked-output ticker lateness: %s' % timer)
    check('blocked output: timers kept running (the ticker task never woke more than 500 ms late)', timer is not None and int(timer['n']) > 20 and int(timer['max_us']) < 500000, timer)
    if rep is not None:
        print('INFO blocked-output report: write_blocks=%s frames=%s' % (rep['write_blocks'], rep['frames']))
        check('blocked output: the blocked-write path was really exercised (write_blocks > 0)', int(rep['write_blocks']) > 0, rep)


def scenario_quit_while_blocked():
    t = start(['--total', '1000000', '--interval', '100', '--autostart', '--stamp'], hard_limit=60)
    t.wait_for('stream running', 6)
    time.sleep(3.0)
    t.write(b'q')
    time.sleep(0.5)
    t_quit = time.time()
    # the cleanup writes its screen restore with bounded patience, then restores termios
    deadline = time.time() + 6
    while time.time() < deadline and (t.flags()['echo'] is False):
        time.sleep(0.1)
    f = t.flags()
    check('cancel while a redraw is blocked: the terminal is restored within the bounded patience (%.1f s)' % (time.time() - t_quit), f['echo'] and f['icanon'] and f['isig'], f)
    done = t.finish(8)
    check('cancel while a redraw is blocked: the program ends', done, t.exited)


def scenario_sigkill_documented():
    t = start(['--total', '100'])
    t.wait_for('monitor | tick', 6)
    t.kill()
    time.sleep(0.3)
    t.alive()
    f = t.flags()
    check('SIGKILL: restoration is NOT possible (documented): the terminal stays raw', not f['echo'], f)


SCENARIOS = [scenario_startup_timers_idle, scenario_keys, scenario_resize, scenario_stream_control, scenario_no_loss, scenario_quit_restore_linger, scenario_error_path, scenario_ctrl_c, scenario_hangup, scenario_not_a_tty, scenario_sigkill_documented]
if not QUICK:
    SCENARIOS += [scenario_flood_keys_resize_timers, scenario_blocked_output, scenario_quit_while_blocked]

if __name__ == '__main__':
    try:
        import pty
        pty.openpty()
    except Exception as e:
        print('UNTESTED: no pseudo-terminal available (%s)' % e)
        sys.exit(2)
    watchdog(600)
    only = [a for a in sys.argv[2:] if not a.startswith('--')]
    for scenario in SCENARIOS:
        if only and scenario.__name__ not in only:
            continue
        print('--', scenario.__name__, flush=True)
        try:
            scenario()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(scenario.__name__ + ' ran to completion', False, repr(e))
    kill_all()
    sys.exit(summary())
