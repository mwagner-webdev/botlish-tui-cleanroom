# TUI clean-room package — Milestone 1 completion report

Result in one line: a pure-Botlish terminal UI library, `tui` 0.1.0, was written, tested on all four
backends, packed deterministically as `dist/tui-0.1.0.botpkg`, installed into fresh consumer projects
(one written by me, one by an independent agent) and — through a raw-syscall adapter that the standard
library documents but does not wrap — drove a real Linux pseudo-terminal end to end (26/26 PTY checks).
**Both success levels are reached, with one qualification that matters** (§F, §K): full interactive support
depends on the standard library's *raw syscall transport*, because the standard library has no terminal API.

## A. Environment

* Initially present: exactly `botlish-linux-x86_64` (single-file compiler, development build, source
  revision 2a10335, front end 5975423604a0e312-5043339, Tcl 9.0.1, packaged native backend). `-version` confirms.
* System tools used: bash, coreutils, `python3` (only as an external PTY test driver and for small
  authoring helpers in `scripts/`; none of it implements UI behaviour), `unzip`/`sha256sum`/`cmp`/`bc`/`time`.
  No `stty`, `tput`, `infocmp` or other executable is used by the library at run time.
* Not introduced: no Botlish checkout, no other compiler, no other package, no preinstalled TUI framework,
  no C/Rust/Python/JS in the library, no FFI, no native libraries, no compiler plugins or patches.
  The library's only non-standard dependency is `std::linux::abi::x86_64::syscall`, which is part of the
  distributed standard library (a documented "direct" intrinsic namespace); see §F for why and how it is isolated.
* Note: `/tmp/opencode` contained pre-existing files (`r1.bot`, `sim.*.bot`, `t1.bot`, `pkgview/`); they were not
  touched or used.

## B. Language and terminal capability discovery

Help topics read (all via `-help`): `primer`, `best practices`, `stdlib`, `concepts` index, `concept.syntax`,
`structs`, `enums`, `functions`, `errors`, `lists`, `strings`, `loops`, `folds`, `modules`, `imports`,
`contexts`, `testing`, `resources`, `private`, `closures`, `function-types`, `specialization`,
`parameterized-structs`, `destructuring`, `definition-order`, `mutual-recursion`, `tree-structs`, `traits`,
`place-parameters`, `mutable-array`, `list-length`, `lockstep`, `binary-layouts`, `integers`, `direct-modules`,
`error-payloads`, `rule.package-modules`, `cli.packages`, `proof.list-bounds`, `std::linux`, `std::linux::io`,
`std::linux::abi::x86_64`, `std::linux::abi::x86_64::syscall`, `std::abi`, `std::abi::bytes`, `std::abi::x86_64`,
`std::binary`, `std::event_loop`, `std::io`, `capability.input`, `backend.native-only`, and individual symbols.

Working probes (kept in `probes/`): hello world on all backends; raw `ioctl` on a non-tty (`-25`); raw mode,
`poll`, `read`, `signalfd` in a pty; `std::linux::io::read` on a pipe and a cooked pty; terminal state after
`SIGTERM/SIGINT/SIGKILL`. Language facts established by probes: no assignment (fold/recursion/collecting loops),
no integer division (own `quotient`), no int→char conversion and no `\u`/`\x`/`\e` string escapes (characters are
built through `decode_utf8`), `List[int[Byte]]` is not a `List[int]`, `repeated append` on Tcl backends is
quadratic (6.9 s for 5000 appends vs 0.25 s native), collecting loops are linear.

Terminal capability census (full table with evidence in `TERMINAL-CAPABILITIES.md`):

| # | capability | status |
|---|---|---|
| 1 | write arbitrary bytes | available, verified (`std::io::print`, `std::linux::write`; native only) |
| 2 | read bytes from stdin | available, verified (`std::linux::io::read`) |
| 3 | read without newline | verified only via raw syscall transport (`ioctl TCSETS`) — no stdlib API |
| 4 | canonical ↔ raw | verified via raw syscall only |
| 5 | echo off / restore | verified via raw syscall only |
| 6 | isatty | verified via raw syscall (`TCGETS` → `-ENOTTY`) |
| 7 | terminal size | verified via raw syscall (`TIOCGWINSZ`) |
| 8 | resize detection | `SIGWINCH`/`signalfd` verified **not** to work (worker thread; `main-thread` startup rejected on Linux); 100 ms `TIOCGWINSZ` sampling verified |
| 9 | wait without busy polling | verified via raw `poll(2)`; 2 s idle = < 0.1 s CPU |
| 10 | restore on normal exit / handled error | verified (resource cleanup); not possible after SIGKILL/SIGTERM (verified) |

## C. Library architecture

Package `tui` 0.1.0, root manifest `{"schema":1,"name":"tui","version":"0.1.0"}`, no dependencies, no native metadata.

```text
Layer A (portable, pure, every backend)              Layer B (Linux terminal adapter, native only)
tui::style   colours, attributes                      tui::session  Terminal context, run(app), Session
tui::text    text safety + width policy                  raw mode (ioctl), size, poll/read/write,
tui::canvas  Rect, Canvas(ops), Screen(cells), borders   restore via resource cleanup
tui::ansi    full redraw / incremental diff
tui::input   Event, incremental decoder
tui::layout  immutable layout tree + allocation
tui::widgets Focus, button, TextInput, ListState, status bar
tui::app     Application interface, Abort error
tui.bot      version + module index
```

* **Rendering representation.** A `Canvas` is an immutable list of drawing operations (text, fill, border, each with
  a clip rectangle); `rasterize` paints them in order (painter's algorithm) into a `MutableArray` local to one
  function and freezes it to a `Screen` (row-major `List[Cell {ch, fg, bg, attrs}]`). This keeps each operation O(area)
  instead of O(screen) and avoids quadratic list building. No global mutable state anywhere.
* **State model.** Applications are plain structs; `update(state, event) -> state` and
  `view(state, width, height) -> Canvas` are ordinary functions. Widgets are structs + pure functions.
* **Context boundaries.** Only `tui::session` touches the OS. Every function there that issues a syscall requires
  the module's own `opaque context struct Terminal` (holding the two descriptors), so the module is *not* a
  `direct module`; programs must write `with context tui::session::create()`. Terminal state is an
  `opaque resource struct Session` whose `cleanup fn restore` runs when `run`'s scope ends, on any exit, including `fail`.
* **Interface without registration.** `tui::app::Application` is a structural `trait` (update/view/finished?). `run` takes a
  trait-typed application; the concrete type is monomorphised.
* **Terminal adapter** (details §F): `open` → raw termios by writing individual fields of the saved termios bytes
  (`std::binary::write` on a 36-byte `layout`), alternate screen, hidden cursor, wrap off; loop: diff-render, `poll`
  (100 ms, 50 ms while an ESC is pending), read 64-byte chunks, decode, apply events, `Resize` event when
  `TIOCGWINSZ` changed.

## D. Public API (compact)

```text
style    plain() transparent() colors(fg,bg) s.with_fg/with_bg/bold/dim/italic/underline/reverse  colour constants
text     width(s) sanitize(s) codepoints(s) from_codepoints(cps) code('q') char_string(cp) raw_codepoints(s) widen(x)
canvas   new(w,h) rect(x,y,w,h) Rect.intersect/empty?/contains?  c.text/text_in/fill/border/add/add_all  c.rasterize()
         Screen.cell_at/row_text/lines  blank(w,h)  error OutOfBounds   BorderKind::{Single,Rounded,Double,Ascii}
ansi     full(screen) diff(prev,next) visible(s) esc() csi() cursor_to() sgr() enter/leave_alternate_screen() ...
input    Event{kind,ch,ctrl,alt,shift,width,height} Kind::{Char,Enter,Tab,...,Resize,Unknown}  key() char_event() ctrl_char()
         resize() with_ctrl/alt/shift  is_char?/is_ctrl?/is_key?  new_decoder() feed() flush() pending?() decode_all() decode_text()
layout   label spacer fill vstack hstack  n.boxed padded padded_all on_color with_width with_height with_flex
         canvas.draw(node) draw_in  n.measure() n.rects(rect) n.ops(rect)
widgets  focus(n) focus_next/prev/set focused? focus_update  activated? button status_bar
         text_input(s) with_max_length input_update input_node input_text   list_state(items) with_page list_select
         list_replace selected_item list_update list_node
app      trait Application{update,view,finished?}  error Abort{reason}  apply_events() frame()
session  create() run(app) tty?() size() write_text()  errors NotATerminal{stream} IoFailed{operation}  constants
```

**Consumer example 1** (written by the consumer project, `consumer/menu.bot`, test `consumer/menu_test.bot`,
entry `consumer/menu_main.bot`):

```botlish
fn update(menu: Menu, event: tui::input::Event) -> Menu:
    if tui::widgets::activated?(event):
        picked = menu.list.selected_item()
        return Menu {list: menu.list, chosen: picked, done: picked == "Quit"}
    Menu {list: menu.list.list_update(event), chosen: menu.chosen, done: menu.done}
...
tui::session::run(menu::initial()):
    on tui::session::NotATerminal {stream}: ...
```

**Consumer example 2**: the independent agent's task manager (§I) and `examples/taskapp.bot` + `demo.bot` (task list with
list, text field, button, focus ring, status line, resize handler, deliberate failing key).

## E. Implementation

Completed:

* **Canvas/geometry/clipping:** zero-size, negative, out-of-range, over-wide, over-tall, overlapping, empty-string, empty-rect
  cases are specified and tested (43 tests); borders (4 kinds) with degenerate sizes; transparent colours; `OutOfBounds`
  on reads only.
* **Text safety:** controls (including ESC, newline) and anything outside an explicit single-cell subset become U+FFFD;
  tested including an adversarial `ESC[2J` string. **No Unicode-width claim**: the stdlib has no width tables.
* **ANSI:** cursor moves, SGR (standard, bright, 256, attributes), alt screen, cursor, full redraw (skips blank cells),
  incremental diff (runs by style; unchanged ⇒ `""`; resize ⇒ full), every run self-resetting, output ends with reset,
  deterministic. A terminal model written in Botlish inside `test_ansi.bot` replays the bytes: 16 transitions give the same
  screen via diff and via full redraw.
* **Input decoding:** pure incremental state machine; UTF-8, controls, CSI/SS3, modifiers, function keys, Alt+key,
  Shift+Tab; every split position of mixed streams (all pairs of boundaries) equals the unsplit result; one byte per read
  equals whole; ESC ambiguity resolved by hold + `flush` (session: 50 ms); malformed sequences → `Unknown`/U+FFFD; pending
  size < 32 bytes (tested with 100 000-byte garbage); 5000 events in one chunk.
* **Layout:** vertical/horizontal stacks, padding, boxes with titles, fixed/flex sizing with exact integer distribution,
  truncation rules, clipping, dimensions-as-data; `rects` for inspection.
* **Widgets:** focus ring, button, single-line text editor (scrolling window, caret, Ctrl-A/E/K/U, max length), scrolling
  selection list, status bar.
* **Example:** task list, state machine separate from entry point.

Deliberately excluded (as required): mouse, bracketed paste, true colour, wide/combining/grapheme support, Windows/macOS,
animation, reactive runtime, plugin system, general event loop.

## F. Terminal integration

What works with the existing standard library (all verified in a real PTY, §G): raw mode without line buffering, immediate key
delivery, ECHO/ICANON/ISIG/ICRNL restore, window size, resize handling, idle waiting through `poll`, alternate screen,
screen restore on normal exit, on `finished?`, on an application `fail` and on I/O errors.

**The qualification.** No standard-library terminal API exists, so Layer B uses `std::linux::abi::x86_64::syscall`
(documented as "raw authority", native only, x86-64 only) for `ioctl`, `poll`, `read`, `write`, wrapped by `Terminal`. This is
the documented mechanism, not a hidden workaround, but it is a raw transport, not a terminal facility; the task text lists
"direct raw FFI invented in the application" as forbidden and I judged a documented stdlib intrinsic different from FFI — a
reviewer may rule otherwise, in which case the honest status is "Layer A complete; Layer B demonstrates what a stdlib terminal API
would need". Not available: SIGWINCH delivery (resize is sampled), restoration after SIGKILL/SIGTERM (verified: not restored).

**Smallest standard-library additions that would remove the raw-syscall dependency** (conceptual, does not compile today):

```botlish
# std::terminal  (conceptual)
with context std::terminal::open_stdio()                # a Terminal context, tty? checked
size = std::terminal::size()                            # rows/cols, errors NotATerminal
guard = std::terminal::enter_raw()                      # resource: restores the saved mode when its scope ends
event = std::terminal::wait(timeout_ms)                 # Input(bytes) | Resized(size) | Timeout, no busy polling
```
i.e. (1) termios get/set (or `enter_raw` as a resource), (2) window size, (3) `wait_readable(timeout)` / a resize event
source (signal-derived), (4) a way to run a program on a thread that receives `SIGWINCH` (or a signal event source),
(5) optionally a restore hook for fatal signals.

## G. Tests

Commands: `scripts/run_tests.sh PROJECT [FILES]` (dev project and the consumer project); PTY: `python3 tests/pty_acceptance.py ./demo`.

| file | cases | interp | compile | cranelift | cranelift-generic |
|---|---|---|---|---|---|
| tests/test_canvas.bot | 43 | pass | pass | pass | pass |
| tests/test_ansi.bot | 21 | pass | pass | pass | pass |
| tests/test_input.bot | 34 | pass | pass | pass | pass |
| tests/test_layout.bot | 21 | pass | pass | pass | pass |
| tests/test_widgets.bot | 29 | pass | pass | pass | pass |
| examples/test.bot (task app) | 19 | pass | pass | pass | pass |
| consumer menu_test.bot | 3 | pass | pass | pass | pass |
| tests/test_session.bot (no terminal) | 2 | NATIVE-ONLY (expected) | n/a | pass | pass |

167 library/example cases × 4 backends, 0 failures, all compiled with `-warnings error` (no warnings). Raw timing lines are in
`test-results.dev-final.txt` (authoring tree) and `test-results.consumer.txt` (installed package). The interpreter is slow
(test_input 77 s, test_ansi 47 s versus ~8 s for the others, which are dominated by compile time). No backend
difference in results was observed; the only difference is that `tui::session` and the `std::io` output used by the demos need
a native backend (`NATIVE-ONLY` on interp/compile, as documented).

PTY acceptance (`pty-results.consumer.txt`, run against an executable built in the consumer project from the *installed* package):
26 checks, 26 passed: cooked → raw → restored termios flags; first frame; alt screen + hidden cursor; Down/Space/Tab/typing/
Backspace/Enter; escape sequence split over three writes; lone Escape resolved by the timeout; resize 80x24 → 100x30 → 60x20 noticed
and frame refilled; idle CPU < 0.1 s per 2 s; `q` exit; leave sequences; final message after restore; application `fail`
(Ctrl+X) → message on the restored screen and flags restored; Ctrl+C as a key; Ctrl+D inert; no-terminal path prints one plain
frame. Consumer's own `menu_main` was also driven in a PTY (choose entry, quit, flags restored). Not tested / known: SIGTERM,
SIGINT-by-kill and SIGKILL leave the terminal raw (verified, reported as a limitation).

## H. Package

* Name/version: `tui` 0.1.0. Manifest in the source tree: `{"schema":1,"name":"tui","version":"0.1.0"}` (pack normalised the
  packed copy to add `"dependencies": {}`).
* Artifact: `dist/tui-0.1.0.botpkg` (zip, 69 188 bytes, 33 files, target-independent).
* **Full SHA-256: `d442e0d42e18343de43c4cea2345202f0bcbc08267310a4c439a7173413d0ffa`**
* Commands: `./botlish-linux-x86_64 package pack tui -out dist` and `./botlish-linux-x86_64 package inspect dist/tui-0.1.0.botpkg`.
* Determinism: packed three times (two other output directories, one after `touch tui/README.md` and a 1-2 s delay):
  all three files byte-identical (`cmp`) and the same SHA-256. Archive entries carry the 1980-01-01 timestamp.
* Contents: `tui.bot`, `tui/{style,text,canvas,ansi,input,layout,widgets,app,session}.bot`, `README.md`, `GUIDE.md`,
  `botlish.json`, `examples/` (hello, g1–g8, taskapp, demo, test, bench), `tests/` (5 portable test programs, `test_session.bot`,
  `pty_acceptance.py`). No `.bot_modules`, no native files, no author paths (grep for `/home` in the tree is empty), no caches.
* Release history, for honesty: a candidate with digest `598c7cd5c414…` (identical code, slightly older docs) was the artifact
  the independent agent consumed; three documentation clarifications it motivated were made *before* the version/digest were
  declared, giving the final digest above. The code is byte-identical between the two.

## I. Consumer validation

* Project: `consumer/` (outside the authoring tree; `botlish.json` only names `../dist/tui-0.1.0.botpkg` and its SHA-256).
  Commands: `../botlish-linux-x86_64 add ../dist/tui-0.1.0.botpkg` → `tui 0.1.0 (sha256 d442e0d42e18): installed`;
  `verify` → "1 package instance(s) … as installed" (integrity only; no signature). Installed files = the 33 artifact entries.
* Imports used by consumer code: `tui::{style,canvas,layout,widgets,input,app,session,ansi,text}`; examples and tests were copied
  unmodified from `.bot_modules/tui/{examples,tests}` and compile with `-warnings error`. The authoring directory is never
  referenced (consumer sources contain no relative paths into it; the compiler resolves `tui` only from `.bot_modules`).
* Consumer's own program (`menu.bot`, `menu_test.bot`, `menu_main.bot`): 3 tests pass on 4 backends; the terminal entry point
  runs in a PTY.
* **Independent agent: actually run** (a fresh general-purpose sub-agent with only the compiler, the candidate `.botpkg` and an
  empty project in `/tmp/opencode/agent-consumer/`, a brief task, no knowledge of my source). It installed through the package
  manager, read the installed README, GUIDE, `tui.bot`, `app.bot` and the example app, and built `tasks.bot` (state machine +
  view, two modes Browse/Adding), `tasks_test.bot` (13 tests, **all pass on cranelift and interp**, including rendered-screen and
  ANSI diff assertions) and `tasks_tui.bot` (terminal entry; runs without a terminal without crashing; a `script` PTY smoke run
  started the session and quit on `q`, restoring the terminal). Its candid findings: ~85 % of what it needed came from README +
  GUIDE; it never needed module internals; its compiler errors were definition order, per-file imports, a guessed
  `starts_with`, `IndexNotFound`, and lint warnings — none caused by the library. Library-related complaints, all addressed in
  the final docs: snippets omit imports; the Application-in-the-same-module requirement was only implied; `boxed` adds 2 rows so
  fixed heights silently truncate later rows (it hit this and the 12-row test caught it); labels/status bars have no padding.
  It saw one unreproduced oddity (final frame showed "Ready" after a burst of keys, most likely because the loop applies all
  pending input and exits on `finished?` before redrawing — by design, not a bug). It reported no library bug.

## J. Performance

(`bench-results.txt`, `examples/bench.bot` built natively; whole-process wall time, 5 runs, medians; start-up 2.3 ms.)

| workload | per iteration |
|---|---|
| render an 80×24 screen (layout → canvas → rasterize → full ANSI) | 1.5 ms |
| nested layouts, 8 levels, 80×24 | 0.9 ms |
| view + rasterize + diff of a one-line change, 80×24 | 1.1 ms |
| decode 10 000 key events (1000 blocks × 10 events) | 12.3 ms (1.2 µs/event) |
| 58 widget transitions (28 text edits, 30 list moves) | 0.045 ms (0.8 µs each) |

Complexity: rendering scales about linearly in area/rows (scale 1→2→4: 2.0, 3.4, 8.9 ms for 1×, 4×, 16× area); diff cost
tracks the whole view pipeline, not terminal output. Decoding is nearly linear but mildly superlinear (1.2 → 1.9 µs/event from
10 k to 40 k events), probably memory pressure, not investigated. Obvious pitfalls avoided: no repeated `append`
(measured on Tcl backends: 5000 appends = 6.9 s), ops are batched with `flat`, rasterization uses one local `MutableArray`.
Other backends (workload 1): cranelift-generic 1.5 ms, `compile` ≈ 1.7 s, `interp` ≈ 12 s per frame — the Tcl backends are fine for the
test suite's small screens and unusable for real rendering. Idle terminal session: < 0.1 s CPU per 2 s.

## K. Problems found

Classified per §18. Minimal reproductions are in `repros/`.

| # | class | finding |
|---|---|---|
| 1 | **Standard-library gap** | No terminal API (termios, isatty, size, poll, signals); library must use the raw syscall transport. |
| 2 | **Standard-library gap** | No SIGWINCH/signal event source reachable from a worker-thread program; `-native-startup main-thread` is rejected on Linux x86-64, so `signalfd` never sees the signal (probe `probes/sp.bot`). Resize is sampled. |
| 3 | **Standard-library gap** | No way to restore the terminal after fatal signals (verified for SIGTERM/SIGINT/SIGKILL, `probes/sigterm.py`). |
| 4 | **Standard-library/language gap** | No `int → UnicodeChar`, no `\u`/`\x`/`\e` escapes, no string join, no width data; text must be assembled via `decode_utf8(from_list(...))`; char literals only for ASCII-friendly sources. |
| 5 | **Compiler defect** | `repros/native-bug-cleanup-context`: a module declaring a `cleanup fn` that requires a context makes any native-backend program that merely *imports* it (without installing the context or using the resource) fail with `NATIVE BUG: context load … which the program does not install`. interp/compile run it fine. Worked around by keeping `tui::app` (Abort, Application) free of the session import. |
| 6 | **Compiler defect / limitation** | `repros/fn-field-over-type-slot.bot`: a generic struct holding a `Fn{args:[S,…]}` field is rejected when passed to a function taking `App[S]` ("typed parameter requirements that cannot be preserved"). Worked around with a structural `trait`. |
| 7 | **Compiler defect (precision)** | `repros/loop-over-encode-utf8-is-any.bot`: `loop b in bs` over a bound `s.encode_utf8()` yields `any` elements while `bs.at(i)` keeps `Byte`. |
| 8 | **Language limitation** | `repros/refined-list-not-list-int.bot`: `List[int[Byte]]` is not accepted as `List[int]`; callers need explicit widening (`tui::text::widen`). |
| 9 | **Diagnostic defect** | `repros/method-ambiguity`: a value binding named like an imported function makes method-call `x.f()` "ambiguous" with the *value* as a candidate. Led to renaming `canvas::screen` to `rasterize`. |
| 10 | **Language limitation** | Generic (untyped) callbacks cannot have their results used by typed code at the definition (`any`); the trait approach avoids it. A struct-with-functions app type was therefore not possible. (Observed inside `run`; the small standalone repro compiled, so I do not file it as a defect.) |
| 11 | Library/API problem (mine, fixed) | `with_modifiers(ctrl, alt, shift)` triggered MANY-BOOLEAN-ARGUMENTS → replaced by `with_ctrl/alt/shift`; many public helpers were exported by accident → made `private`; `canvas::screen` renamed; `update` that can fail forces handlers in every test (documented). |
| 12 | Documentation problem | Found by the independent agent (snippets without imports; same-module requirement; `boxed` +2 rows; no label padding): fixed in README/GUIDE before release. Compiler help has no table of the `std::str`/`std::list` functions an app needs (`starts_with` does not exist; the agent had to discover that). |
| 13 | Environment/perf | Interpreter and `compile` are 3–4 orders of magnitude slower than native for rendering (12 s / 1.7 s per 80×24 frame), so tests use small screens; `interp` runs the 34-test input suite in 77 s. |
| 14 | Package-manager note | `pack` rewrites the manifest (`"dependencies": {}` added) — harmless; `package inspect | head` prints a Tcl broken-pipe trace — cosmetic. Pack/install/verify otherwise worked first time and deterministically. |
| 15 | Agent mistakes (mine) | Several: a bogus `pkill -f` that killed my own shell twice; expecting a KNOWN-ERROR-free negative test (`cell_at` with constants must use dynamic arguments); wrong hand-computed expectations in 5 tests (border origin, box width, nested offsets, remainder, escape count) — all were test errors, none library bugs; forgot definition order several times. |

## L. Conclusion

1. **Can a clean-room agent author a reusable pure-Botlish library?** Yes. 9 modules, ~1 960 lines, 167 tests, documentation, all
   from the compiler's own help and diagnostics; the only language-level friction was strict definition order, type-proof
   obligations and the missing text primitives (#4, #7, #8, #10).
2. **Can it package it with only the single-file compiler?** Yes: `package pack` produced a deterministic, inspectable artifact
   (three byte-identical packs, SHA-256 above) with README, GUIDE, examples and tests inside.
3. **Can another project install and use it without the author's tree?** Yes: `add` + `verify` in a fresh project, my own consumer
   application and an independent agent's application compiled and passed their tests on the installed package; the
   agent needed ~85 % documentation and no internals.
4. **Can the library drive a real interactive terminal with the currently distributed standard library?** Yes, *through* the
   standard library's documented raw syscall transport, which I isolate in one module behind an explicit context and a
   cleanup-owning resource. A real PTY run shows raw input without Enter, live updates, resize, restore after normal exit and after
   a handled application error (26/26 checks, from the installed package). It is **not** "terminal support in the standard
   library": there is no termios/size/poll/signal API, resize is sampled, and nothing is restored after fatal signals.
5. **What remains missing or unnecessarily difficult?** A standard terminal facility (§F); signal/resize event source; text
   primitives (char from scalar, escapes, width data); the native-lowering defect (#5) and generic-struct-with-Fn limitation
   (#6) that force structural traits and the `tui::app` split; per-file imports and strict ordering make documentation snippets
   necessarily abbreviated; interpreter speed. Width support is deliberately limited (single-cell subset, everything else U+FFFD).

## Deliverables

```text
botlish-linux-x86_64          the provided compiler (unmodified)
tui/                          package source (botlish.json, tui.bot, tui/*.bot, README.md, GUIDE.md, examples/, tests/)
dist/tui-0.1.0.botpkg         the release artifact (sha256 d442e0d4…0ffa)
consumer/                     fresh consumer project (installed package, copied examples/tests, menu app + test)
dev/                          authoring project (editable link to ../tui, symlinks to tests/examples) used by scripts/run_tests.sh
scripts/                      run_tests.sh, show_fails.py, fix_method_eligible.py, fix_one_char.py (authoring helpers)
probes/ repros/               capability probes and minimal reproductions
TERMINAL-CAPABILITIES.md  bench-results.txt  test-results.*.txt  pty-results.consumer.txt
TUI-CLEANROOM-PACKAGE-MILESTONE-1.md
```

Reproduce: `cd consumer && ../botlish-linux-x86_64 add ../dist/tui-0.1.0.botpkg && ../botlish-linux-x86_64 verify`, then
`../scripts/run_tests.sh $PWD test.bot`, `../botlish-linux-x86_64 -emit-native-executable demo.bot && python3 .bot_modules/tui/tests/pty_acceptance.py ./demo`.
