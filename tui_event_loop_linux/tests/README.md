# Tests of tui_event_loop_linux

Everything here runs against the **installed packages** (nothing refers to the
source checkout), with the single-file compiler, in a real pseudo-terminal.

| file | what |
|---|---|
| `test_monitor.bot` | pure tests of the demonstration application: events in, screens out (any backend) |
| `evcases.bot` | lifecycle cases, one per run: two owners, repeated open/close, abandon, shutdown, stale use, a flood into an undrained terminal, failing descriptors |
| `evlinger.bot` | keeps a case alive (poll(2) of nothing) so the harness can inspect the terminal afterwards |
| `bench_standalone.bot` | the monitor application under `tui::session::run`, for the benchmark |
| `ptylib.py` | the PTY harness: starts a program, sends bytes and resizes, replays the output into a tiny screen emulator, reads the kernel's view (termios, `/proc/PID/fdinfo` flags, CPU), kills every child on a watchdog |
| `pty_evloop.py` | acceptance of `examples/evdemo.bot` (UI task + ticker + controller + simulated stream in one loop): input, timers, redraws without input, resize, restore, no loss, flood, blocked output |
| `pty_faults.py` | `evcases` lifecycle cases, and **strace fault injection** into an unmodified `evdemo` (raw mode refused, nonblocking refused, readiness registration refused, reads and writes that fail or would block, an unreadable window size) |
| `bench_pty.py` | CPU, latency and timer-lateness measurements, standalone against event loop |
| `mutate.py` | mutation tests: each edit of the integration must be caught by the check meant for it |

```sh
botlish -emit-native-executable evdemo.bot
botlish -emit-native-executable evcases.bot
python3 -B pty_evloop.py ./evdemo            # add --quick to skip the long scenarios
python3 -B pty_faults.py ./evcases ./evdemo  # fault injection needs strace (else reported UNTESTED)
```

Every wait in the harness has a deadline and a global watchdog kills the
children and fails the run: a hang is a failure, never a stuck test. A PTY that
cannot be created is reported as UNTESTED, never as a pass.
