#!/usr/bin/env python3
"""External PTY test driver for the tui package's demo application.

This script only orchestrates: it starts a native executable built from
examples/demo.bot inside a pseudo-terminal, injects keystrokes and resizes,
and checks what the program wrote.  It contains no TUI logic: the tiny
`Emulator` below merely replays the cursor-move/clear/text sequences the
library emits, to look at the resulting screen.

Usage:  python3 pty_acceptance.py ./demo

Exit status 0 only if every check passed.  A PTY that cannot be created is
reported as UNTESTED, never as a pass.
"""
import fcntl, os, re, select, struct, sys, termios, time

EXE = sys.argv[1] if len(sys.argv) > 1 else './demo'
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok or not detail else '  -- ' + detail))


class Emulator:
    """Replays CUP / ED / SGR / text; ignores every other CSI sequence."""

    def __init__(self, cols, rows):
        self.resize(cols, rows)

    def resize(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.grid = [[' '] * cols for _ in range(rows)]
        self.x = self.y = 0

    def feed(self, data):
        text = data.decode('utf-8', 'replace')
        i = 0
        while i < len(text):
            c = text[i]
            if c == '\x1b' and text[i + 1:i + 2] == '[':
                m = re.compile(r'\x1b\[([0-9;?]*)([ -/]*)([@-~])').match(text, i)
                if not m:
                    i += 1
                    continue
                params, final = m.group(1), m.group(3)
                nums = [int(p) if p.isdigit() else 0 for p in params.split(';')] if params and '?' not in params else []
                if final == 'H':
                    self.y = (nums[0] if nums else 1) - 1
                    self.x = (nums[1] if len(nums) > 1 else 1) - 1
                elif final == 'J' and nums[:1] == [2]:
                    self.grid = [[' '] * self.cols for _ in range(self.rows)]
                i = m.end()
                continue
            if c == '\r':
                self.x = 0
            elif c == '\n':
                self.y += 1
            elif c >= ' ':
                if 0 <= self.y < self.rows and 0 <= self.x < self.cols:
                    self.grid[self.y][self.x] = c
                self.x += 1
            i += 1

    def lines(self):
        return [''.join(r).rstrip() for r in self.grid]

    def has(self, s):
        return any(s in l for l in self.lines())


class Session:
    def __init__(self, exe, cols=80, rows=24):
        import pty
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ['TERM'] = 'xterm-256color'
            os.execv(exe, [exe])
        self.set_size(cols, rows)
        self.emu = Emulator(cols, rows)
        self.raw = b''
        self.exited = None

    def set_size(self, cols, rows):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def resize(self, cols, rows):
        self.set_size(cols, rows)
        self.emu.resize(cols, rows)

    def pump(self, seconds=0.4):
        end = time.time() + seconds
        got = b''
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    d = os.read(self.fd, 65536)
                except OSError:
                    break
                if not d:
                    break
                got += d
        self.raw += got
        self.emu.feed(got)
        return got

    def send(self, data, settle=0.35):
        os.write(self.fd, data)
        return self.pump(settle)

    def wait_for(self, text, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.emu.has(text):
                return True
            self.pump(0.1)
        return self.emu.has(text)

    def flags(self):
        a = termios.tcgetattr(self.fd)
        return {'echo': bool(a[3] & termios.ECHO), 'icanon': bool(a[3] & termios.ICANON),
                'isig': bool(a[3] & termios.ISIG), 'icrnl': bool(a[0] & termios.ICRNL)}

    def cpu_seconds(self):
        with open('/proc/%d/stat' % self.pid) as f:
            parts = f.read().rsplit(')', 1)[1].split()
        return (int(parts[11]) + int(parts[12])) / os.sysconf('SC_CLK_TCK')

    def finish(self, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.1)
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.exited = os.waitstatus_to_exitcode(status)
                self.pump(0.2)
                return True
        os.kill(self.pid, 9)
        os.waitpid(self.pid, 0)
        return False


def scenario_main():
    s = Session(EXE)
    before = s.flags()
    check('PTY: cooked mode before start (echo, icanon, isig)', before['echo'] and before['icanon'] and before['isig'], str(before))
    ok = s.wait_for('Tasks: 3, done: 1')
    check('first frame drawn (title bar)', ok, '\n'.join(s.emu.lines()[:5]))
    check('alternate screen entered, cursor hidden', b'\x1b[?1049h' in s.raw and b'\x1b[?25l' in s.raw)
    check('first frame shows the selected row', s.emu.has('> [x] Read the guide'))
    raw = s.flags()
    check('raw mode while running (no echo, no canon, no isig)', not raw['echo'] and not raw['icanon'] and not raw['isig'], str(raw))

    cpu0 = s.cpu_seconds()
    s.pump(2.0)
    idle = s.cpu_seconds() - cpu0
    check('idle for 2 s costs < 0.1 s CPU (no busy polling)', idle < 0.1, 'cpu %.3fs' % idle)

    s.send(b'\x1b[B')
    check('Down arrow moves the selection', s.emu.has('> [ ] Write a task app'))
    s.send(b' ')
    check('Space toggles the task (state + status line)', s.emu.has('[x] Write a task app') and s.emu.has("Marked 'Write a task app' as done") and s.emu.has('Tasks: 3, done: 2'))

    for b in (b'\x1b', b'[', b'B'):
        os.write(s.fd, b)
        time.sleep(0.01)
    s.pump(0.4)
    check('escape sequence split across three writes decodes as Down', s.emu.has('> [ ] Run it in a terminal'))

    s.send(b'\t')
    for ch in b'milk':
        s.send(bytes([ch]), settle=0.05)
    s.pump(0.3)
    check('Tab focuses the entry; typed text appears in the editor', s.emu.has('New task: milk'))
    s.send(b'\x7f')
    check('Backspace edits the entry', s.emu.has('New task: mil') and not s.emu.has('New task: milk'))
    s.send(b'k\r')
    check('Enter adds the task (status, count, list)', s.emu.has("Added 'milk'") and s.emu.has('Tasks: 4, done: 2') and s.emu.has('[ ] milk'))
    s.send(b'x')
    s.send(b'\x1b', settle=0.5)
    check('a lone Escape (timeout policy) clears the entry', s.emu.has('Entry cleared') and not s.emu.has('New task: x'))

    s.resize(100, 30)
    ok = s.wait_for('Resized to 100x30', 3.0)
    check('resize is noticed and reported to the application', ok, '\n'.join(s.emu.lines()[-4:]))
    check('after resize the frame fills the new size (status bar on row 30)', s.emu.lines()[29].startswith('Resized to 100x30'))
    s.resize(60, 20)
    check('shrinking is handled too', s.wait_for('Resized to 60x20', 3.0))

    s.send(b'\x1b[Z')
    s.send(b'q')
    done = s.finish()
    tail = s.raw[-300:]
    check('q exits; process terminated by itself within the timeout', done and s.exited == 0, 'exit=%s' % s.exited)
    check('leave sequences sent (cursor shown, alternate screen left)', b'\x1b[?25h' in tail and b'\x1b[?1049l' in tail)
    check('final message printed after the restore', b'demo: finished; the terminal has been restored' in s.raw.split(b'\x1b[?1049l')[-1])
    after = s.flags()
    check('terminal state restored after normal exit (echo, icanon, isig, icrnl)', after == before, '%s vs %s' % (after, before))


def scenario_error():
    s = Session(EXE)
    before = s.flags()
    s.wait_for('Tasks: 3, done: 1')
    s.send(b'\x18')
    done = s.finish()
    after = s.raw.split(b'\x1b[?1049l')[-1]
    check('error path: application failure leaves the session and the process ends', done and s.exited == 0, 'exit=%s' % s.exited)
    check('error path: message printed on the restored main screen', b'demo: the application failed: Ctrl+X pressed' in after, repr(after[-200:]))
    check('error path: terminal state restored', s.flags() == before)


def scenario_ctrl_c_and_eof():
    s = Session(EXE)
    before = s.flags()
    s.wait_for('Tasks: 3, done: 1')
    s.send(b'\x03')
    done = s.finish()
    check('Ctrl+C is delivered as a key (ISIG off) and quits cleanly', done and s.exited == 0 and s.flags() == before)
    s = Session(EXE)
    s.wait_for('Tasks: 3, done: 1')
    os.write(s.fd, b'\x04')
    s.pump(0.5)
    check('Ctrl+D is an ordinary key (no EOF in raw mode); app still running', s.emu.has('Tasks: 3'))
    s.send(b'q')
    s.finish()


def scenario_not_a_tty():
    import subprocess
    p = subprocess.run([EXE], stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    out = p.stdout.decode('utf-8', 'replace')
    check('without a terminal: explicit NotATerminal path, one plain frame, no raw mode', 'is not a terminal' in out and 'Tasks: 3, done: 1' in out and '\x1b[?1049h' not in out, out[:200])


if __name__ == '__main__':
    try:
        import pty
        pty.openpty()
    except Exception as e:
        print('UNTESTED: no pseudo-terminal available (%s)' % e)
        sys.exit(2)
    for scenario in (scenario_main, scenario_error, scenario_ctrl_c_and_eof, scenario_not_a_tty):
        try:
            scenario()
        except Exception as e:
            check(scenario.__name__ + ' ran to completion', False, repr(e))
    failed = [n for n, ok in results if not ok]
    print('\n%d checks, %d passed, %d failed' % (len(results), len(results) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)
