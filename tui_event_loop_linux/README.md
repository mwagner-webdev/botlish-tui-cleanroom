# tui_event_loop_linux — the terminal inside your event loop

`tui_event_loop_linux` 0.1.0 lets a Botlish program drive a `tui` terminal
application from **its own `std::event_loop`**: keyboard input, timers, streams
and anything else are events of one loop; the terminal is one participant, not
the owner. Linux x86-64, native backends.

> A terminal UI is a participant in an application's event loop, not
> necessarily the owner of that loop.

It depends on `tui` 0.2 (the library: renderer, decoder, state machine) and on
`event_loop_linux` 0.5 (the published Linux driver of `std::event_loop`). It
adds about 600 lines: no renderer, no parser, no scheduler.

## Two operating modes

| | standalone (`tui::session::run`) | externally driven (this package) |
|---|---|---|
| who owns the loop | `run` (poll, read, update, draw) | **your program** (`std::event_loop::run`) |
| other activity | none: only the terminal | timers, streams, tasks — anything the loop does |
| how input arrives | `poll(2)` with a timeout | readiness of the descriptor, via the loop's epoll |
| redraw reasons | an event reached the application | an event, **or any task asking** |
| resize | sampled every 100 ms by the loop | sampled every 100 ms by a task on the loop's timer |
| package | `tui` | `tui_event_loop_linux` (+ `tui`, `event_loop_linux`) |
| decoder, renderer, state machine, ownership, cleanup | **the same code** | **the same code** |

Use the standalone mode for a program whose only ongoing activity is the
terminal (`tui`'s README and GUIDE). Use this package when the program already
has — or will grow — other asynchronous work.

## Requirements

* Linux x86-64, native backends (the Linux driver is C; the Tcl backends refuse a C call).
* A compiler whose package manager shares one instance of a dependency between a
  package and the program (`botlish -help cli.packages`, "ONE GRAPH"). The program
  installs `event_loop_linux` itself (it creates the driver context) and this package
  depends on the same `event_loop_linux`: with a package manager that installs a copy
  per dependency path the two copies' native archives conflict (`LINK-CONFLICT`). Tested
  with the development build `0a518ba`; the `single-latest` build of 2026-10-21
  (`bdd5e3d`) installs per path and **cannot** link this package (it can use `tui` 0.2.0
  itself: spot-checked there).
* For the tests: Python 3, and `strace` for the fault-injection part.

## Install

```sh
botlish add tui_event_loop_linux-0.1.0.botpkg -repository REPO   # REPO: tui, event_loop_linux
botlish add event_loop_linux -repository REPO                    # the program installs the driver itself
botlish add tui -repository REPO                                 # ... and imports tui
botlish verify
```

The package locks `tui 0.2.0` and `event_loop_linux 0.5.0` (one dependency
graph: the program shares those instances). `REPO` is a directory of
`NAME-VERSION.botpkg` files; the repository's `dist/` has all three.

## The shortest program

`examples/minimal.bot` (everything it uses is shown):

```botlish
fn worker(inbox: tui_event_loop_linux::Inbox, n: int, context ev: std::event_loop::Loop, ui: tui_event_loop_linux::Ui) -> int errors std::event_loop::SchedulerFault:
    std::event_loop::sleep(200000)
    if inbox.notify(code_progress, n + 1) == Posted::NotLive:
        return n
    inbox.worker(n + 1)

fn main(context ev: std::event_loop::Loop, d: event_loop_linux::Linux, n: event_loop_linux::Native, term: tui::session::Terminal, ui: tui_event_loop_linux::Ui) -> int errors ...:
    std::event_loop::sync()
    inbox = tui_event_loop_linux::start(Counter {progress: 0, keys: 0, quit: false}): ...
    coroutine {step, first} = worker(inbox, 0)
    std::event_loop::adopt(step, first)
    std::event_loop::run()
    inbox.outcome().code

with context std::event_loop::create(64)
with context event_loop_linux::create()
with context event_loop_linux::native()
with context tui::session::create()
with context tui_event_loop_linux::create()
main(): ...
```

`Counter` is a plain `tui` application (`update`, `view`, `finished?`), the
same kind `tui::session::run` takes. `worker` stands for whatever produces
events in a real program. `std::event_loop::run()` is the **program's only
loop**.

## The API

Contexts the program installs (once, in the entry program): the loop
(`std::event_loop::create`), the driver (`event_loop_linux::create()`), its
integration capability (`event_loop_linux::native()`), the terminal authority
(`tui::session::create()`) and this package's shared counters
(`tui_event_loop_linux::create()` — a collection is a value in Botlish, so
storage several tasks share is a context).

| call | what |
|---|---|
| `start(app) -> Inbox` | open the terminal, register its readiness, adopt the UI task and the size ticker. The first frame is written before it returns. Fails with `NotATerminal`, `IoFailed`, `TerminalBusy`, `DriverFault`; on failure nothing is left changed. |
| `Inbox` | a copyable handle for **every other task**; needs only the Loop (and `Ui`) |
| `send(inbox, event)`, `notify(inbox, code, value)` | deliver an event to the application's `update`, in order with the keys. `Accepted`, `Refused` (inbox full: retry or drop), `NotLive` (the UI task ended: stop) |
| `request_redraw(inbox)` | ask for a repaint after state the application keeps elsewhere changed; coalesced |
| `request_repaint(inbox)` | repaint every cell (the terminal can no longer be trusted); coalesced |
| `request_close(inbox)` | ask the UI task to leave |
| `tap(inbox) -> Reg` | a copy of every decoded terminal event, packed (`tui::input::pack`), for a controller task |
| `running?(inbox)`, `outcome(inbox)`, `stats(inbox)` | how it is going / how it ended / counters |
| `open`, `serve` | `start` in two steps, for programs that build their own coroutine |

`outcome(inbox).code`: 0 the application finished, 1 the input ended (end of
file or hangup), 2 the application's `update` failed (`tui::app::Abort`; the
reason is in `detail`), 3 the terminal failed (`IoFailed`; the operation is in
`detail`), 4 close requested, 5 the loop revoked the inbox (`shutdown`), 6 the
driver failed, 7 not a terminal.

## Who owns what

* **Terminal state is owned by the UI task**: the `tui::session::Session`
  (raw mode, nonblocking input descriptor, alternate screen), the readiness
  watches and the registrations they deliver to are one affine resource
  (`Attached`) held by the task. Nothing else can read the input descriptor or
  write terminal bytes: other tasks hold only an `Inbox`.
* **Scheduling is owned by your program.** This package never runs, turns or
  waits for the loop; it registers sources and adopts tasks. There is no thread,
  no signal handler, no private loop, no second timer mechanism.
* One `Terminal` context has **one owner at a time**: a second `start`, `open`
  or `tui::session::open` fails (`TerminalBusy`, or `IoFailed "busy"` from the
  older functions) until the first session ends. Ownership is per VM: another
  thread's own `Terminal` is not stopped. The terminal is not sendable.

## How things arrive (and who does what)

The UI task waits on **one** registration (the inbox) and nothing else. Into it
go:

* **terminal readable** — a native record from the loop's epoll watch of the
  input descriptor. The task reads (nonblocking, at most 128 bytes per wake),
  feeds the *same decoder as the standalone loop* (`tui::host::feed`), applies
  the events in order and re-arms the watch. A partial escape sequence stays in
  the decoder across reads. Readiness is level-triggered per arming: bytes left
  in the kernel's buffer re-report immediately, nothing is lost.
* **a held ESC** — one 50 ms timer task of the loop; when it posts and the
  decoder still holds the ESC, it is the Escape key. Bytes that arrive first
  continue the sequence.
* **application events** — `send`/`notify` from any task.
* **resize** — a ticker task on the loop's timer (every 100 ms) asks
  `TIOCGWINSZ` and posts only *changes*; the application receives an ordinary
  `Resize` event. A terminal that cannot be asked keeps its last size; a
  zero-sized one is read as 80x24 (as in the standalone mode). No signal
  handler is installed.
* **redraw / repaint / close requests**.

After a batch of records (at most 64 per wake) the task plans **one** frame
from the application's current state; requests and events that arrived
meanwhile share it. That is the coalescing: redundant *render requests* are
merged, application events never are.

## Rendering, and why tasks cannot interleave terminal writes

Only the UI task writes. `tui::display` remembers what the terminal shows;
`tui::host::frame` diffs the application's view against it (the standalone
renderer: incremental, full on a size change). The bytes are written **in
chunks of 16 KiB**: after each partial write the task yields to the loop and
reads the next pending record, so a large frame never starves a timer. When the
terminal cannot take more (`EAGAIN`) the task arms a writability watch and
carries on with its events; unrelated tasks run as usual. A frame is committed
to the renderer's memory when planned; if the session ends or a write fails
mid-frame the memory is irrelevant (the session is over); `tui::host::frame_failed`
/ `repaint` exist for a host that continues after an interrupted frame (the next
frame repaints every cell).

## What may block

Nothing in this package blocks. `start` makes a few system calls (termios,
`fcntl`, `epoll_ctl`) and writes the first frame (the descriptor is blocking
for that one write, as in the standalone mode). Everything afterwards is
nonblocking reads and writes plus the loop's waits. The one place the package
waits *for the terminal* is the cleanup after cancellation: the screen restore
is written with a patience of at most about one second (see below). A task that
runs a long computation between `wait`s holds the thread: the loop is
cooperative, so keep `update` and `view` cheap (a frame of 80x24 costs about
1 ms; see the milestone report).

## Cancellation, shutdown, abandonment

All of these restore the terminal — termios, the descriptor's status flags, the
alternate screen, the cursor, the readiness watches, ownership — because the
restoration is the **cleanup of a resource** that the UI task owns:

| how it ends | what happens |
|---|---|
| the application finishes (`finished?`), the input ends, `request_close` | the task returns; the cleanup runs; the producers' next post says `NotLive` |
| a declared failure (`Abort`, `IoFailed`, a driver fault) | recorded in `outcome`; the task ends normally after the cleanup |
| `std::event_loop::shutdown(n)` | revokes the inbox; the UI task wakes `Revoked`, ends (code 5) |
| `std::event_loop::abandon()` | the suspended coroutine is released and its frames' cleanups run: the terminal is restored without the task running again |
| `start` fails half-way | what was acquired is released before the error is reported |

If the terminal's output is not being drained (a stuck pipe) the screen restore
gives up after about a second and the termios and flags are restored anyway; a
terminal left on the alternate screen is repaired by `reset`. `SIGKILL` and a
crash cannot be restored by anyone.

**The loop ends when nothing is live — your own tasks count.** When the UI task
ends, `std::event_loop::run()` returns once every other task has ended too. A
producer that stops at its next `NotLive` post ends within one period; a task
that sleeps for an hour keeps the loop (and the process) for an hour. Give long
sleeps a way out (wait on a registration you revoke, or poll `running?(inbox)`).

**Call `std::event_loop::sync()` before `start`** (and before any timer is made
outside the loop): the loop's clock is read there, and a timer made earlier is
due at once.

## Failure behaviour at a glance (tested; see the milestone report)

| situation | result |
|---|---|
| stdin/stdout not a terminal | `start` fails `NotATerminal "input"/"output"`; nothing drawn |
| raw mode refused | `IoFailed "configure"`; the terminal is as it was |
| nonblocking mode refused | `IoFailed "nonblocking"`; raw mode undone |
| readiness registration refused | `DriverFault`; everything acquired is released |
| a read fails (not EIO) | UI task ends, code 3 `"read"` |
| hangup (`EIO`, end of file) | UI task ends, code 1; no busy loop |
| a write fails | UI task ends, code 3 `"write"` |
| a write would block | wait for writability, keep processing events |
| window size unavailable | the last size stays; no event |
| two owners | the second is refused |

## Testing an application

Everything but the terminal is pure: test `update`/`view` with
`tui::app::apply_events` (`tests/test_monitor.bot`). The package's own tests run
`evdemo` and `evcases` in a pseudo-terminal under an external watchdog
(`tests/pty_evloop.py`, `tests/pty_faults.py`: lifecycle cases, and strace
fault injection into the system calls) — see `tests/README.md`.
