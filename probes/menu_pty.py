import sys, os
sys.dont_write_bytecode = True
sys.argv=['x','./menu_main']
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'consumer', '.bot_modules', 'tui', 'tests'))
import pty_acceptance as P
s=P.Session('./menu_main')
before=s.flags()
print('first frame', s.wait_for('Main menu'), s.emu.lines()[:5])
s.send(b'\x1b[B'); s.send(b'\r')
print('chosen:', s.emu.has('You chose: Load game'))
s.send(b'\x1b[F'); s.send(b'\r')
done=s.finish()
print('quit:', done, s.exited, 'restored', s.flags()==before)
