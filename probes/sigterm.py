import sys, os, signal, time
sys.argv=['x','./demo']
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tui', 'tests'))
import pty_acceptance as P
for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGKILL):
    s=P.Session('./demo')
    before=s.flags()
    s.wait_for('Tasks: 3')
    os.kill(s.pid, sig)
    time.sleep(0.5); s.pump(0.3)
    try:
        pid,status=os.waitpid(s.pid, os.WNOHANG)
    except Exception as e:
        pid,status=0,None
    print(signal.Signals(sig).name, 'terminated:', bool(pid), 'status', status, 'flags after:', s.flags(), 'restored' if s.flags()==before else 'NOT restored')
    if not pid:
        os.kill(s.pid,9); os.waitpid(s.pid,0)
