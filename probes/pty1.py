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
        r,_,_=select.select([fd],[],[],0.1)
        if r:
            try: d=os.read(fd,4096)
            except OSError: break
            if not d: break
            out+=d
    return out
print(repr(rd(1.0)))
os.write(fd, b'ab'); print(repr(rd(0.5)))
os.write(fd, b'\x1b[A'); print(repr(rd(0.5)))
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0)); print('resize', repr(rd(0.8)))
os.write(fd, b'q'); print(repr(rd(1.0)))
print(os.waitpid(pid,0))
