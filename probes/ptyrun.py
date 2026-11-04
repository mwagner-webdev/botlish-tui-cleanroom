#!/usr/bin/env python3
"""External PTY test driver (orchestration only; implements no TUI behavior)."""
import os, pty, sys, time, select, fcntl, termios, struct

def run(argv, steps=(), rows=24, cols=80, timeout=15):
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(argv[0], argv)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    out = b""
    t0 = time.time()
    def drain(wait):
        nonlocal out
        end = time.time() + wait
        while True:
            r, _, _ = select.select([fd], [], [], max(0, end - time.time()))
            if not r: break
            try: d = os.read(fd, 65536)
            except OSError: return False
            if not d: return False
            out += d
        return True
    for s in steps:
        if isinstance(s, (int, float)): drain(s)
        elif isinstance(s, bytes): os.write(fd, s)
        elif isinstance(s, tuple) and s[0] == "resize":
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", s[1], s[2], 0, 0))
            os.kill(pid, 28)
    status = None
    while time.time() - t0 < timeout:
        drain(0.1)
        p, st = os.waitpid(pid, os.WNOHANG)
        if p: status = st; break
    if status is None:
        os.kill(pid, 9); os.waitpid(pid, 0); status = "TIMEOUT"
    drain(0.1)
    return out, status

if __name__ == "__main__":
    o, st = run(sys.argv[1:])
    sys.stdout.buffer.write(o); print("\nstatus", st)
