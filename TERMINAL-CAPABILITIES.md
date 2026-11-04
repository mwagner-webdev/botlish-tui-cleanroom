# Terminal capability census

Compiler: `botlish-linux-x86_64` (development build, source revision 2a10335, front end
5975423604a0e312-5043339, Tcl 9.0.1). Host: Linux x86-64. Everything below was checked
with the compiler's own help pages (`-help stdlib`, `std::linux::io`, `std::linux`,
`std::linux::abi::x86_64`, `capability.input`, `concept.direct-modules`,
`backend.native-only`) and with small programs run in a real pseudo-terminal.

**Headline.** The standard library has **no terminal API**: no termios, no `isatty`, no
window size, no `poll`, no signals, no line/text input (`capability.input` says so). What
it does have is a documented, raw Linux system-call transport
(`std::linux::abi::x86_64::syscall`, a *direct* namespace, native backends only) and a
context mechanism for mediating raw authority. The `tui` package's Layer B
(`tui::session`) is built on exactly that: ioctl / poll / read / write through the
documented transport, wrapped in its own `Terminal` context. No `stty`, `tput`, C, FFI or
undocumented runtime internals are involved.

Classification: **V** = available and verified with a working program;
**V\*** = verified, but only through the raw syscall transport (no high-level stdlib
function exists); **D** = documented but not verified; **U** = apparently unavailable;
**X** = unsuitable for the intended implementation.

| # | Capability | Class | How, and what was verified |
|---|-----------|-------|----------------------------|
| 1 | Write arbitrary bytes to a terminal | **V** | `std::io::print`/`print_line` (text, via the `LinuxIO` context) and `std::linux::write(fd, Bytes)` / the raw `write` syscall (any byte string). Verified natively; `interp` and `compile` refuse it (`NATIVE-ONLY`). |
| 2 | Read bytes from standard input | **V** | `std::linux::io::read(MutableBytes)` — one `read(2)`, returns `{result, data}`. Verified: reading a pipe returned 6 bytes; reading a cooked-mode pty returned nothing until the line was finished. |
| 3 | Read input without waiting for a newline | **V\*** | Not possible with `std::linux::io::read` alone (cooked pty: the read completed only after Enter). Possible after switching the terminal to raw mode with `ioctl(TCSETS)` through the syscall transport. Verified in a pty: a single key press is delivered immediately. |
| 4 | Switch canonical ↔ raw/cbreak | **V\*** | `ioctl(TCGETS)` / `ioctl(TCSETS)` with a 36-byte `layout` of `struct termios` (`std::binary::parse`/`write` edit single fields). No stdlib termios API exists. Verified: `ICANON`, `ISIG`, `IXON`, `ICRNL`, `IEXTEN`, `OPOST`-preserving raw mode; `VMIN=1,VTIME=0`. |
| 5 | Disable / restore echo | **V\*** | Same ioctls (`ECHO` bit). Verified by reading the pty's termios from the master side before, during and after a session. |
| 6 | Detect whether I/O is a terminal | **V\*** | `ioctl(TCGETS)` returns `-ENOTTY` (-25) on a pipe/file, 0 on a tty. Verified both ways (`tui::session::tty?`, and the `NotATerminal` path of the demo run with stdin from `/dev/null`). |
| 7 | Read terminal dimensions | **V\*** | `ioctl(TIOCGWINSZ)` into an 8-byte `layout`. Verified: 24x80 → 30x100 → 20x60 after `TIOCSWINSZ` on the master. (A 0x0 answer is replaced by 80x24.) |
| 8 | Detect a resize | **V\*** (polling) / **X** (signal) | `SIGWINCH` through a `signalfd` was tried first and **does not work**: programs run on a worker thread (`-native-startup worker`; `main-thread` is rejected on Linux x86-64 with `NATIVE UNSUPPORTED startup`), the signal is process-directed, the main thread leaves it unblocked with the default "ignore" disposition, so the kernel discards it before the `signalfd` can see it (verified: no readiness after `TIOCSWINSZ`). The library therefore **samples `TIOCGWINSZ` every 100 ms while idle**; resizes are noticed within that delay (verified). |
| 9 | Wait for input without busy-polling | **V\*** | `poll(2)` through the transport, timeout 100 ms (50 ms while an escape sequence is pending). Verified: 2 s idle in a pty cost < 0.1 s CPU (`/proc/PID/stat`). |
| 10 | Restore terminal state after normal exit and handled errors | **V** | An `opaque resource struct` (`Session`) whose `cleanup fn` restores the screen and termios runs where its scope ends, including when a `fail` leaves it (concept.resources). Verified in a pty: normal exit via `q`, exit via Ctrl+C-as-a-key, and an application `fail Abort` all end with the original `echo/icanon/isig/icrnl` flags. |
| — | Restore after uncatchable termination | **X** | Not possible: there is no signal-handler facility. Verified in a pty that `SIGTERM`, `SIGINT` (sent with `kill`) and `SIGKILL` all leave the terminal in raw mode. The library does **not** claim otherwise (`reset` / `stty sane` is the user's recovery). |
| — | Event loop / timers from stdlib | **D** | `std::event_loop` exists, but its production Linux driver is a separate package (`packages/event_loop_linux`) that is not part of the single-file compiler; not used. |

## Consequences for the design

* Layer A (canvas, style, text, ansi, input, layout, widgets, app) is pure Botlish and
  runs on all four backends.
* Layer B (`tui::session`) is one module, needs `-backend cranelift` /
  `cranelift-generic` or a native executable, and is entered only through
  `with context tui::session::create()`; nothing else in the package imports it.
* Smallest standard-library additions that would remove the raw-syscall dependency are
  listed in the completion report (§F): a terminal context with `size`, `raw mode`
  guard, `wait_readable(timeout)` and a resize event source.

## Probes (in `probes/`)

* `tp.bot`: `ioctl(TCGETS)` on a non-tty -> `-25`; `layout` parse of the result.
* `sp.bot` + `pty1.py`: raw mode, `poll`, `read`, `signalfd` (negative result for resize).
  (`sp.bot` was the exploratory version; `pty2.py` drives the first session prototype.)
* `rd.bot`: `std::linux::io::read` on a pipe and on a cooked pty.
* `sigterm.py`: terminal state after `SIGTERM`/`SIGINT`/`SIGKILL` (never restored).
* `menu_pty.py`: PTY run of the consumer's own application.
* The full acceptance run is `tui/tests/pty_acceptance.py` (26 checks).
