import pty, os, sys, time, select, fcntl, termios, struct, signal
exe = sys.argv[1]
pid, fd = pty.fork()
if pid == 0:
    os.execv(exe, [exe])
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
def rd(t=1.0):
    out=b''
    end=time.time()+t
    while time.time()<end:
        r,_,_=select.select([fd],[],[],0.05)
        if r:
            try: d=os.read(fd,65536)
            except OSError: break
            if not d: break
            out+=d
    return out
def flags():
    a=termios.tcgetattr(fd)
    return (bool(a[3]&termios.ECHO), bool(a[3]&termios.ICANON), bool(a[3]&termios.ISIG))
print('initial flags', flags())
time.sleep(0.5)
print('raw?', flags())
o=rd(0.5); print(repr(o[:400]))
os.write(fd, b'\x1b[A'); o=rd(0.5); print('up', repr(o))
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0)); o=rd(0.8); print('resize', repr(o))
os.write(fd, b'\x1b'); o=rd(0.5); print('esc', repr(o))
os.write(fd, b'q'); o=rd(0.8); print('q', repr(o))
try:
    print(os.waitpid(pid,0))
except Exception as e: print(e)
print('final flags', flags())
