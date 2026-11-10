"""A small PTY harness for the event-loop tests.

It only starts a program in a pseudo-terminal, sends bytes, resizes, and looks at
what the program wrote and at the kernel's view of the terminal and of the
program's descriptors.  It contains no TUI logic: the Emulator replays the
cursor-move / clear / text sequences the library emits so a test can look at the
resulting screen.  Every wait has a deadline, and a global watchdog kills every
child it started and fails the run if the whole run takes too long: a hanging
program is a failure, never a stuck test.
"""
import fcntl, os, re, select, signal, struct, sys, termios, threading, time

O_NONBLOCK = 0o4000
CHILDREN = []
RESULTS = []
VERBOSE = bool(os.environ.get('PTY_VERBOSE'))


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok or not detail else '  -- ' + str(detail)), flush=True)
    return bool(ok)


def summary():
    failed = [n for n, ok in RESULTS if not ok]
    print('\n%d checks, %d passed, %d failed' % (len(RESULTS), len(RESULTS) - len(failed), len(failed)), flush=True)
    return 1 if failed else 0


def kill_all():
    for pid in list(CHILDREN):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def watchdog(seconds):
    """Kill every child and fail the run after SECONDS in all."""
    def fire(signum, frame):
        kill_all()
        print('FAIL watchdog: the run took longer than %d s; children killed' % seconds, flush=True)
        os._exit(3)
    signal.signal(signal.SIGALRM, fire)
    signal.alarm(seconds)


class Emulator:
    """Replays CUP / ED / SGR / text; ignores every other CSI sequence."""
    CSI = re.compile(r'\x1b\[([0-9;?]*)([ -/]*)([@-~])')

    def __init__(self, cols, rows):
        self.resize(cols, rows)
        self.leftover = ''

    def resize(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.grid = [[' '] * cols for _ in range(rows)]
        self.x = self.y = 0
        self.alt = False
        self.full_clears = 0

    def feed(self, data):
        text = self.leftover + data.decode('utf-8', 'replace')
        self.leftover = ''
        i = 0
        while i < len(text):
            c = text[i]
            if c == '\x1b':
                m = self.CSI.match(text, i)
                if not m:
                    if len(text) - i < 24:
                        self.leftover = text[i:]
                        return
                    i += 1
                    continue
                params, final = m.group(1), m.group(3)
                nums = [int(p) if p.isdigit() else 0 for p in params.split(';')] if params and '?' not in params else []
                if final == 'H':
                    self.y = (nums[0] if nums else 1) - 1
                    self.x = (nums[1] if len(nums) > 1 else 1) - 1
                elif final == 'J' and nums[:1] == [2]:
                    self.grid = [[' '] * self.cols for _ in range(self.rows)]
                    self.full_clears += 1
                elif params == '?1049' and final == 'h':
                    self.alt = True
                elif params == '?1049' and final == 'l':
                    self.alt = False
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

    def find(self, pattern):
        for l in self.lines():
            m = re.search(pattern, l)
            if m:
                return m
        return None


class Term:
    """A child process on a pseudo-terminal."""

    def __init__(self, argv, cols=80, rows=24, extra_fds=None, hard_limit=60):
        """ARGV is a list, or a function run in the child (after the fork, so
        it can use os.ttyname(0)) that returns the list."""
        import pty
        self.argv = argv
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ['TERM'] = 'xterm-256color'
            for target, src in (extra_fds or {}).items():
                os.dup2(src, target)
            if callable(argv):
                argv = argv()
            os.execv(argv[0], argv)
        CHILDREN.append(self.pid)
        self.set_size(cols, rows)
        self.emu = Emulator(cols, rows)
        self.raw = b''
        self.exited = None
        self.master_closed = False
        timer = threading.Timer(hard_limit, self.kill)
        timer.daemon = True
        timer.start()

    def kill(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except OSError:
            pass

    def set_size(self, cols, rows):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def resize(self, cols, rows):
        self.set_size(cols, rows)
        self.emu.resize(cols, rows)

    def pump(self, seconds=0.3):
        """Read what the program writes for SECONDS; returns the new bytes."""
        end = time.time() + seconds
        got = b''
        while time.time() < end and not self.master_closed:
            r, _, _ = select.select([self.fd], [], [], max(0.0, min(0.05, end - time.time())))
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

    def write(self, data):
        os.write(self.fd, data)

    def send(self, data, settle=0.3):
        self.write(data)
        return self.pump(settle)

    def wait_for(self, text, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.emu.has(text):
                return True
            self.pump(0.05)
        return self.emu.has(text)

    def wait_raw(self, needle, timeout=5.0):
        """Wait until NEEDLE (bytes) appears in everything written so far."""
        end = time.time() + timeout
        while time.time() < end:
            if needle in self.raw:
                return True
            self.pump(0.05)
        return needle in self.raw

    def wait_match(self, pattern, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            m = self.emu.find(pattern)
            if m:
                return m
            self.pump(0.05)
        return self.emu.find(pattern)

    def flags(self):
        a = termios.tcgetattr(self.fd)
        return {'echo': bool(a[3] & termios.ECHO), 'icanon': bool(a[3] & termios.ICANON), 'isig': bool(a[3] & termios.ISIG), 'icrnl': bool(a[0] & termios.ICRNL)}

    def fd_flags(self, n=0):
        """The child's descriptor N status flags (kernel's /proc view), or None."""
        try:
            with open('/proc/%d/fdinfo/%d' % (self.pid, n)) as f:
                for line in f:
                    if line.startswith('flags:'):
                        return int(line.split()[1], 8)
        except OSError:
            return None
        return None

    def nonblocking(self, n=0):
        f = self.fd_flags(n)
        return None if f is None else bool(f & O_NONBLOCK)

    def cpu_seconds(self):
        with open('/proc/%d/stat' % self.pid) as f:
            parts = f.read().rsplit(')', 1)[1].split()
        return (int(parts[11]) + int(parts[12])) / os.sysconf('SC_CLK_TCK')

    def alive(self):
        if self.exited is not None:
            return False
        pid, status = os.waitpid(self.pid, os.WNOHANG)
        if pid:
            self.exited = os.waitstatus_to_exitcode(status)
            return False
        return True

    def finish(self, timeout=5.0):
        """Wait for the program to exit by itself; kill it if it does not."""
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.05)
            if not self.alive():
                self.pump(0.2)
                return True
        self.kill()
        try:
            os.waitpid(self.pid, 0)
        except OSError:
            pass
        self.exited = 'killed'
        return False

    def close_master(self):
        os.close(self.fd)
        self.master_closed = True

    def text_after_leave(self):
        """What was printed after the alternate screen was left (the report)."""
        return self.raw.split(b'\x1b[?1049l')[-1].decode('utf-8', 'replace')


def report_fields(text, tag='REPORT'):
    """Parse 'REPORT a=1 b=x ...' into a dict of strings."""
    for line in text.replace('\r', '').split('\n'):
        if line.startswith(tag + ' '):
            return dict(kv.split('=', 1) for kv in line[len(tag) + 1:].split(' ') if '=' in kv)
    return None
