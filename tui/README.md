# tui — a terminal UI library in pure Botlish

`tui` 0.2.0 draws text user interfaces from ordinary immutable Botlish values.
It is written entirely in Botlish (no C, no FFI, no native code, no external
tools) and is distributed as one `tui-0.2.0.botpkg`.

It has two layers:

* **Layer A — portable, pure.** `style`, `text`, `canvas`, `ansi`, `input`,
  `layout`, `widgets`, `app`, `display`, `host`. They turn values into a screen, a screen into
  ANSI bytes, and bytes into events. No terminal is needed; they run on every
  backend (`interp`, `compile`, `cranelift`, `cranelift-generic`) and are tested
  without one.
* **Layer B — the terminal session** (`tui::session`). The only module that
  touches the operating system: raw mode, window size, waiting for input,
  restoring the terminal. **Linux x86-64, native backends only**, and only in a
  real terminal. See "Terminal support" below for exactly what it relies on.

## Supported environments

| | Layer A | Layer B (`tui::session`) |
|---|---|---|
| Backends | interp, compile, cranelift, cranelift-generic | cranelift, cranelift-generic, native executables |
| OS | any the compiler runs on | Linux x86-64 (raw syscalls) |
| Needs a terminal | no | yes (stdin and stdout must be terminals) |

Tested with the single-file compiler `botlish-linux-x86_64` (development build;
`botlish -version` of the build used for 0.2.0 is recorded in the milestone report).
The package has no dependencies. 0.2.0 was also spot-checked with the older
`single-latest` build of 2026-10-21 (`bdd5e3d`): `test_host`, `test_input`, `test_session` pass.

## Install

In a project (a directory with a `botlish.json`) put the artifact next to it, or
point at it, and add it with the package manager:

```sh
./botlish-linux-x86_64 add path/to/tui-0.2.0.botpkg
./botlish-linux-x86_64 verify
```

The package is unpacked to `.bot_modules/tui/`. Its README, GUIDE, `examples/`
and `tests/` are in that directory; copy what you want into your project root
(Botlish resolves `import app_module` from the directory of the entry file).

## Import

Imports use the full module name; there are no re-exports, so import each module
you use:

```botlish
import tui::style
import tui::canvas
import tui::layout
import tui::widgets
```

`tui.bot` documents the module list; `tui::version` is `"0.2.0"`.

## A small working example

(The snippets in README and GUIDE are abbreviated; the complete files are in `examples/`.)

`examples/hello.bot` (also in this README's directory after installation):

```botlish
import tui::style
import tui::canvas
import tui::layout
import tui::widgets

fn view(width: int, height: int) -> tui::canvas::Canvas:
    plain = tui::style::plain()
    body = tui::layout::vstack([tui::layout::label("Hello from the tui package", plain.bold()), tui::layout::label("Layouts are values; the size is data.", plain)])
    ui = tui::layout::vstack([tui::widgets::status_bar(" hello ", plain), body.boxed("demo", tui::canvas::BorderKind::Rounded, plain).with_flex(1)])
    tui::canvas::new(width, height).draw(ui)

view(40, 6).rasterize().lines()
```

```sh
cp .bot_modules/tui/examples/hello.bot .
./botlish-linux-x86_64 hello.bot
```

prints (the program's value, a list of screen rows):

```text
[" hello                                  ", "╭─ demo ───────────────────────────────╮", "│Hello from the tui package            │", "│Layouts are values; the size is data. │", "│                                      │", "╰──────────────────────────────────────╯"]
```

## An interactive application

`examples/` holds a complete task-list application split the way the library
intends:

| file | role |
|---|---|
| `taskapp.bot` | the state machine and the view; no terminal; defines `update`, `view`, `finished?` for its `State` |
| `test.bot` | deterministic tests of `taskapp` (19 cases) — events in, screens out |
| `demo.bot` | the terminal entry point: `tui::session::run(taskapp::initial())` |

```sh
cp .bot_modules/tui/examples/{taskapp,demo,test}.bot .
./botlish-linux-x86_64 test.bot                       # any backend
./botlish-linux-x86_64 -emit-native-executable demo.bot && ./demo   # in a terminal
```

Keys: Up/Down/Home/End/PageUp/PageDown select, Space/Enter toggle, `d` delete,
Tab/Shift+Tab move the focus (list → new-task field → Add button), `q` or Ctrl+C
quit, Ctrl+X fails on purpose to show that the terminal is restored before the
error is reported.

Without a terminal (`./demo < /dev/null`) the demo prints one frame as plain text
and says so.

## Two ways to run an application

* **Standalone** — `tui::session::run(app)`: the library owns a small loop
  (poll, read, update, draw). Right for a program whose only ongoing activity is
  the terminal. Everything above is unchanged from 0.1.0.
* **Inside the program's own event loop** — the optional package
  **`tui_event_loop_linux`** (depends on this package and on the published
  `event_loop_linux` driver) makes the terminal one participant of a
  `std::event_loop`: terminal readiness, timers, streams and other tasks are
  events of one loop and any of them can request a redraw. This package stays
  dependency-free; the loop integration is a separate package so that
  standalone consumers do not take on `event_loop_linux`'s native archive.

Both modes use the same decoder (`tui::input`), the same state machine
(`tui::host`), the same renderer (`tui::display`, `tui::ansi`), the same terminal
ownership and cleanup (`tui::session`). What 0.2.0 adds for the second mode,
without a scheduler anywhere in this package:

| addition | what |
|---|---|
| `tui::input::Kind::Custom`, `custom(code, value)` | an application-defined event, delivered by an event loop to `update` like a key |
| `tui::input::pack` / `unpack` | an event as two ints (to cross an event loop's `post(reg, a, b)`) |
| `tui::display` | the renderer's memory between frames: `plan` (diff), `invalidate` |
| `tui::host` | the pure bookkeeping of a session: `feed`, `silence`, `resized`, `deliver`, `end_of_input`, `frame`, `frame_failed`, `repaint` |
| `tui::session::open_driven` | the Session for an externally driven program: nonblocking input descriptor (flags restored on release) |
| `tui::session::read_bytes`, `write_some` | one nonblocking read / one write step: never wait |
| `tui::session::create_on`, `owned?`, `TerminalBusy` | a Terminal on other descriptors; exclusive ownership (a second `open` fails: `IoFailed "busy"` from the 0.1.0 functions, `TerminalBusy` from `open_driven`) |

0.1.0 programs compile and behave as before: no existing signature changed
(the full 0.1.0 acceptance suite, 26 PTY checks, passes unmodified on 0.2.0).

## What is portable and what is not

Portable (Layer A): everything except `tui::session`. Importing any Layer A
module never pulls in operating-system code, and `taskapp`/`test` above run
unchanged on all four backends.

Linux-specific (Layer B): `tui::session` uses the raw Linux x86-64 syscall
transport that the standard library documents (`std::linux::abi::x86_64::syscall`)
because the standard library has no terminal API: `ioctl` (termios, window size),
`poll`, `read`, `write`. Authority is mediated by a context you install
explicitly:

```botlish
with context tui::session::create()
tui::session::run(my_app)
```

Install the context in the entry program only. Do not import `tui::session` from
code that runs without that context installed on a native backend (see Known
limitations).

## Known limitations

* **Width.** The standard library has no character-width data, so the library
  supports an explicit single-cell subset (ASCII, Latin-1 and extended Latin,
  Greek, Cyrillic, box drawing, arrows, ... see `tui::text`). Everything else —
  combining marks, CJK/wide and fullwidth characters, emoji, control characters —
  is displayed as `U+FFFD` (one cell). It never claims Unicode correctness.
* **Resize** is noticed by sampling the window size every 100 ms while idle (a
  `SIGWINCH` signal is not available to programs running on the worker thread);
  `tui_event_loop_linux` samples on the application's loop timer instead.
* **A lone Escape** is resolved after 50 ms of silence (`tui::input` documents
  the policy).
* **Terminal restore** happens on normal exit and on errors that leave `run`
  (including an application's `fail tui::app::Abort`). It cannot happen after
  `SIGKILL`/`SIGTERM`; recover with `reset`.
* **One owner per `Terminal` context** (per VM): a second session in the same VM is
  refused; another thread's own `Terminal` is not stopped.
* **No mouse, no bracketed paste, no 24-bit colour, no Windows/macOS.**
* **Compiler quirk (native backends).** Because `tui::session` declares a
  resource whose cleanup requires a context, a native-backend program that
  *imports* `tui::session` but never installs `tui::session::create()` fails
  with `NATIVE BUG: context load ... which the program does not install`. Install
  the context (entry programs that use the session always do), or keep portable
  modules such as `taskapp` free of that import — `tui::app` exists for that
  reason.
* `update` functions that may fail must declare `errors tui::app::Abort`; callers
  of such an `update` must handle it.

## Testing

`tests/` holds the library's own test programs (std::test reports, "N passed, M
failed") and an external PTY driver:

```sh
cp .bot_modules/tui/tests/test_*.bot .
for t in test_canvas test_ansi test_input test_layout test_widgets test_host; do
  ./botlish-linux-x86_64 -backend cranelift $t.bot | grep -o '[0-9]* passed, [0-9]* failed'
done
./botlish-linux-x86_64 -backend cranelift test_session.bot < /dev/null   # adapter without a terminal
./botlish-linux-x86_64 -emit-native-executable demo.bot
python3 .bot_modules/tui/tests/pty_acceptance.py ./demo                  # real PTY acceptance
```

`pty_acceptance.py` only starts the executable in a pseudo-terminal, sends keys and
resizes, and inspects what was written; it contains no UI logic.

## Documentation

* `GUIDE.md` — canvas, styles, layout, widgets, input, state transitions, the
  terminal lifecycle, testing without a terminal, error handling.
* `tui.bot` — the module index.
* `examples/` — `hello.bot`, `g1`…`g8` (the guide's programs), `taskapp.bot`,
  `demo.bot`, `test.bot`, `bench.bot`.
