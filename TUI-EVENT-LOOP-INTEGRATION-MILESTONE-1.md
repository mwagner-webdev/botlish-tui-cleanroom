# TUI-EVENT-LOOP-INTEGRATION-MILESTONE-1 — completion report

Externally driven terminal sessions for composable asynchronous Botlish applications.

**Result in one line.** The terminal can be one participant of an application-owned `std::event_loop`:
`tui` 0.2.0 (still dependency-free, still pure Botlish, every 0.1.0 program unchanged — the original 26-check PTY
acceptance passes unmodified) gained the loop-free pieces (a pure session state machine, a renderer memory, a
nonblocking session with exclusive ownership), and a new optional package **`tui_event_loop_linux` 0.1.0** (584
lines, no renderer, no parser, no scheduler) registers terminal readiness with the published `event_loop_linux`
0.5.0 driver and runs the terminal as *one task* of the program's own loop beside timers and a simulated stream.
No Botlish language, compiler or runtime change was needed. All numbers below are from runs recorded in this
repository (§7–§11); what was not done is listed in §13.

Contents: 1 Environment · 2 Architecture census · 3 Design · 4 Public API changes · 5 Compatibility ·
6 Packages, dependencies, releases · 7 Tests and commands · 8 PTY acceptance · 9 Performance · 10 Resource cleanup
evidence · 11 Mutation tests · 12 Language obstructions · 13 Remaining limitations · 14 Consequences for the agent TUI ·
15 Answers to the final questions.

---

## 1. Environment

* Compiler used for everything in this report: the single-file `botlish` (development build, `-version`:
  *source revision 0a518ba, front end 76941c240e9a4175-5389884, Tcl 9.0.1, packaged native backend*), SHA-256
  `0bea623f…32594`, taken from the supplied `botlish-linux-x86_64.zip`. It is **not** the build 0.1.0 was made with
  (2a10335) and **not** the current `single-latest` release (2026-10-21, `bdd5e3d`): see §6 for what that means.
* Published dependency: Botlish's own `event_loop_linux` 0.5.0. Its artifact is not downloadable from the project's
  GitHub release page, so it was **rebuilt with the project's pinned author image** (`tools/package-build/author.sh
  image` / `build -release event_loop_linux`, Docker, Ubuntu snapshot 20261101): the result is byte-identical to the
  release recorded in the Botlish repository's `packages/RELEASES.tsv`
  (`ecd9e8114fa5bc24c2d23a2068e96ec2932e9386ca0cdb79fc568b9662146f77`, build log "release: the recorded release of
  event_loop_linux 0.5.0"). It is committed as `dist/upstream/event_loop_linux-0.5.0.botpkg` with its provenance
  record and build log. Nothing of the driver was copied or modified.
* Tools: Python 3.12 (PTY harness only — it contains no UI logic), `strace` 6.8 (fault injection), Linux 12 cores,
  Docker (author image only). No C compiler was used for this milestone's own code: the only C is the published driver.
* Pre-existing state: `git log` showed `tui 0.1.0` (commit `eb57280`), the CI and consumer commits. The compiler binary
  and its zip are untracked (now in `.gitignore`).

## 2. Architecture census of the 0.1.0 package

Inspected: `session.bot` (332 lines), `input.bot`, `app.bot`, `ansi.bot`, `canvas.bot`, the PTY test
(`pty_acceptance.py`, 26 checks), `test_session.bot`, `bench.bot`/`bench-results.txt`, README/GUIDE.

### 2.1 Existing contracts (written down before anything was modified)

**Input contract (`tui::input`, unchanged).** `feed(decoder, bytes) -> {decoder, events}` takes any chunking (one byte,
many keys, half a sequence). A pending lone ESC (or ESC `[`, ESC `O`, a partial UTF-8 sequence) is *held*; the caller
decides when silence is long enough and calls `flush` (the standalone loop: 50 ms). Malformed input never fails
and never grows unboundedly (sequence longer than 31 bytes → one `Unknown`; invalid UTF-8 → U+FFFD). xterm modifier
bits. EOF flushes.

**Standalone loop contract (`tui::session::run`).**
1. `open`: stdin must be a terminal (TCGETS), stdout too (TIOCGWINSZ) — else `NotATerminal {stream}`; raw mode by
   writing fields of the saved termios (no ICANON/ECHO/ISIG/ICRNL…, 8-bit, VMIN 1, VTIME 0); alternate screen,
   wrap off, cursor hidden, clear.
2. Loop: if the application is finished → leave **without** a final frame; if dirty → `view`, `rasterize`,
   `ansi::diff(shown, next)` (incremental; **full redraw when the size differs**, also the first frame), `write_all`
   (short writes completed, EINTR retried; failure → `IoFailed "write"`); then `poll(0, 100 ms | 50 ms while ESC
   pending)`; **after every wake** read `TIOCGWINSZ`; poll timeout/EINTR → `flush` the decoder + resize check;
   readable → `read(64)`: 0 = EOF (flush, end of session), EAGAIN/EINTR = nothing, other negative → `IoFailed "read"`,
   else `feed`; events (input events first, then a `Resize` event if the size changed) are applied in order, stopping
   once the application is finished; dirty := at least one event was applied.
3. Restore (resource cleanup): reset style, wrap on, cursor shown, leave alternate screen, then the saved termios.
4. Zero or failing size: a 0x0 report reads as 80x24; a failing `TIOCGWINSZ` is `NotATerminal "output"`.

**Rendering contract.** `Screen` = row-major cells; `diff` emits, per row, runs of changed cells of equal style, each
with its own cursor move and a full SGR reset, ending in `ESC[0m`; identical screens → `""`; cells only hold
single-cell code points so application text cannot smuggle control sequences. Output is built as one string per frame.

### 2.2 Classification

| class | what |
|---|---|
| **1. already independent of the session loop** | `style`, `text`, `canvas`, `ansi` (full/diff), `layout`, `widgets`, `input` (decoder), `app` (`Application`, `apply_events`, `frame`) |
| **2. tied to the standalone loop, reusable externally driven** | the event→application→dirty→frame bookkeeping inside `drive`/`wait` (decode, escape-timeout flush, resize interpretation, EOF handling, "finished: no final frame"); the renderer's `shown` memory; `open`/`restore` (ownership of terminal state); `read_chunk`, `write_text`; the size query and its 80x24 fallback |
| **3. genuinely new** | nonblocking descriptor mode and its restoration; a read that distinguishes *no bytes now* from end of input and hangup; a write that can stop part-way (offset) and say "would block"; exclusive ownership; a way to deliver events that are not from the keyboard; an event-loop adapter (readiness watches, a single owner task, redraw requests, resize on the loop's timer, escape timeout on the loop's timer) |

Nothing of the renderer, the decoder or the widgets was rewritten.

## 3. Design

### 3.1 Separation (pure → session → adapter)

```
tui (0.2.0, no dependencies, no scheduler anywhere)
  tui::input    + Kind::Custom, custom(), pack()/unpack()            events that are not keys; events as two ints
  tui::display  NEW  what the terminal shows; plan(display, screen) -> {display, bytes}; invalidate     [pure]
  tui::host     NEW  decoder + display + size + dirty/closed; feed/silence/resized/deliver/end_of_input/
                     frame/frame_failed/repaint -> Fed {host, events}                                     [pure]
  tui::session  run() rebuilt on host/display (same loop, same observable behaviour);
                + open_driven, read_bytes, write_some, encode, create_on, owned?, input/output_descriptor
tui_event_loop_linux (0.1.0)  depends on tui 0.2 and event_loop_linux 0.5
  start(app) -> Inbox      UI task + size ticker adopted into the program's loop; Inbox = the handle of every other task
```

The standalone `run` is a wrapper over the same machinery: `drive` applies `tui::host` transitions and uses
`tui::display` for frames; it keeps its own poll loop because that is its documented behaviour. The adapter applies the
**same transitions** from readiness records. Decoder, event semantics, renderer, ownership/cleanup rules, resize
interpretation and error mapping exist once.

### 3.2 The event-loop adapter in one page

* **One owner.** `start` opens the session (`open_driven`: raw mode, `O_NONBLOCK` on the input descriptor), creates three
  owned registrations (an inbox *source* kept alive, a tap *channel*, a size *timer*), registers two readiness watches
  (input readable; output writable, armed only when needed) and adopts two tasks: the **UI task** (owns everything,
  an `opaque resource struct Attached` whose cleanup unwatches, restores the terminal, then revokes the registrations)
  and a **size ticker**.
* **One wait.** The UI task waits on exactly one registration, the inbox. Native readiness records (`a` = 1..15) and
  Botlish posts (tag in the low five bits of `a`, 16..31) share it: terminal readable, terminal writable, an application
  event (`send`/`notify`), a redraw or repaint request, a size change, a held-ESC timeout, a close request. This is the
  only way to have several event sources behind one `wait` in this scheduler (a task waits on one registration).
* **Apply, then render.** For each record (a batch of up to 64 per wake, taken with `try_take` after the first) the task
  asks `react` for the new state and the application's events, applies them with `apply_events`, and only when the batch
  is exhausted plans **one** frame. Redundant render requests merge; application events never do.
* **Render without blocking others.** Frames are written in 16 KiB chunks; after a partial write the task yields and
  reads pending records; a refused write (`EAGAIN`) arms the writability watch and the task carries on with events.
  Only the UI task writes terminal bytes.
* **Escape timeout, resize, no second timing system.** A held ESC spawns one short task that `sleep`s 50 ms and posts
  its sequence number into the inbox (stale ones are ignored). Size changes are sampled every 100 ms by a ticker task
  on the loop's timer and posted only when changed. No signal handler, no thread, no private loop, no `poll(2)`.
* **Outcome codes** instead of task failures (the task protocol admits only `TaskFailed` etc.): 0 finished, 1 input
  ended, 2 application `Abort`, 3 `IoFailed`, 4 close requested, 5 inbox revoked, 6 driver fault, 7 not a terminal.

### 3.3 Packaging decision: a separate adapter

Evaluated: (a) an ordinary dependency of `tui` on `event_loop_linux`; (b) a small optional package. **(b) chosen.**
Reasons: `event_loop_linux` carries a native archive (an x86-64 and an AArch64 static library, a C adapter) and a
lock; making every standalone consumer of `tui` take it, and re-release `tui` whenever the driver is re-released, buys
nothing for programs that only run a terminal. `tui` 0.2.0 keeps `{"name","version"}` with **no dependencies**; the
adapter's manifest declares `tui` `0.2` (locked 0.2.0) and `event_loop_linux` `0.5` (locked 0.5.0) and the program shares
those instances (one graph). Consequence for existing users: none — `tui` 0.1.0 consumers upgrade to 0.2.0 or not;
the standalone `run` is where it was. Consequence for new users: one more `botlish add`, and a compiler with the
shared-dependency package manager (§6).

## 4. Public API changes

### 4.1 `tui` 0.2.0 (additions only)

| module | addition |
|---|---|
| `tui::input` | `Kind::Custom`; `custom(code, value)` (ch = code, width = value); `Packed {a, b}`, `pack(event)`, `unpack(a, b)` — an event as two ints (Resize sizes clamp to 0..65535; malformed pairs decode to `Unknown`) |
| `tui::display` (new) | `Display`, `Frame {display, text}`, `new`, `unknown?`, `invalidate`, `plan(display, screen)` |
| `tui::host` (new) | `Host {decoder, display, width, height, dirty, closed}`, `Fed {host, events}`, `Frame {host, text}`; `new`, `done?`, `pending_input?`, `feed`, `silence`, `deliver`, `resized`, `end_of_input`, `invalidate`, `repaint`, `wants_frame?`, `frame`, `frame_failed` |
| `tui::session` | `TerminalBusy {reason}`; `Terminal` now also carries the ownership cell; `create_on(input, output)`; `owned?`; `open_driven`; `Reading {Data, Idle, Closed, Hangup}`, `Input`, `read_bytes`; `write_some(data, offset, limit)`, `encode`, `encoded_length`; `input_descriptor`, `output_descriptor` |

`tui::host` holds no application: an `Application` is a trait view and no struct field can hold one (§12), so the
caller applies `Fed.events` with `apply_events` and threads the application itself.

### 4.2 `tui_event_loop_linux` 0.1.0

`start(app) -> Inbox`; `open`, `open_watching`, `serve` (the same in steps); `Inbox` operations `send`, `notify`,
`request_redraw`, `request_repaint`, `request_close`, `tap`, `running?`, `outcome`, `stats`; contexts `Ui` +
`create()`. See `tui_event_loop_linux/README.md` for ownership, scheduling, resize, redraw coalescing, cancellation,
abandonment, what may block and what happens on each failure.

## 5. Compatibility decisions

* **No existing signature or error set changed.** A second owner of the terminal is `IoFailed {operation: "busy"}` from
  `open`/`run` (a new error in `open`'s `errors` clause would have broken callers that handle the old two); the new
  `open_driven` declares `TerminalBusy`.
* `Terminal` and `Session` are opaque, so their new fields are invisible. `Kind` gained a variant (`Custom`).
* **Behaviour of `run`**: identical by construction (same sequence of poll/size/read/feed/render steps) and by test:
  the unmodified 0.1.0 acceptance script, 26 checks, passes against a binary built from the installed 0.2.0 artifact
  (§8), the library suites pass on all four backends (§7), and the benchmark compares the 0.1.0-built demo with the
  0.2.0-built one (§9). One deliberate behavioural *addition*: `restore` now also gives up ownership, and a second
  concurrent `open` in the same VM fails (previously it silently succeeded and would have recorded raw termios as "original").
* `tui` 0.1.0 stays released and byte-identical (`dist/tui-0.1.0.botpkg`, `d442e0d4…`).

## 6. Packages, dependencies, releases

| artifact | SHA-256 | notes |
|---|---|---|
| `dist/tui-0.1.0.botpkg` | `d442e0d42e18343de43c4cea2345202f0bcbc08267310a4c439a7173413d0ffa` | unchanged |
| `dist/tui-0.2.0.botpkg` | `ddb64c66ff4459ef582b3e692d44c9b1012ce4eb5ac6ce4b761c14055b74e3d2` | no dependencies; also packs byte-identically with the `single-latest` compiler |
| `dist/tui_event_loop_linux-0.1.0.botpkg` | `a122b74a586e0dc55055cb22201336fc6dd547ab33746b67a4f7dc3eb10143fc` | locks `tui 0.2.0`, `event_loop_linux 0.5.0` |
| `dist/upstream/event_loop_linux-0.5.0.botpkg` | `ecd9e8114fa5bc24c2d23a2068e96ec2932e9386ca0cdb79fc568b9662146f77` | Botlish's release, reproduced from the pinned author image |

* `scripts/pack_all.sh` packs both packages twice and fails unless the two are byte-identical and equal to `dist/`
  (a released version is never overwritten; bump the version instead).
* The shared-dependency graph works as the compiler documents: `consumer-evloop` installs the three archives
  (`botlish add FILE.botpkg`, sources + SHA-256 recorded in its `botlish.json`) and `botlish verify` reports
  "3 package instance(s) … as installed". The program uses `event_loop_linux` itself (it installs the driver context)
  and shares the instance the adapter locks; no private copy exists anywhere.
* **Compiler requirement found.** The `single-latest` release (2026-10-21, `bdd5e3d`) installs one copy per dependency
  path; with it, a program importing `event_loop_linux` and `tui_event_loop_linux` fails with `LINK-CONFLICT` (two static
  archives for one library). The adapter therefore needs a compiler with the shared-dependency package manager (the
  `0a518ba` build used here has it). `tui` 0.2.0 alone does not (spot-checked with `bdd5e3d`: `test_host` 19/19,
  `test_input` 34/34, `test_session` 4/4). CI gates the event-loop job on the repository variable
  `BOTLISH_COMPILER_URL` for this reason; without it the job is *skipped*, not passed.

## 7. Tests and commands executed

All with the compiler above. "4 backends" = `interp`, `compile`, `cranelift`, `cranelift-generic`.

| what | command | result |
|---|---|---|
| tui portable suites | `BOT=… scripts/run_tests.sh dev` | `test_canvas` 43, `test_ansi` 21, `test_input` 34, `test_layout` 21, `test_widgets` 29, **`test_host` 19 (new)**, `test` (taskapp) 19 — **all pass on all 4 backends** (28 runs, `-warnings error`) |
| adapter without a terminal | `botlish -warnings error -backend cranelift test_session.bot < /dev/null` | 4 passed (was 2: + `open_driven` without terminal, ownership free after failures); `cranelift-generic` 4; `interp` NATIVE-ONLY as before |
| demonstration application | `test_monitor.bot` | 10 passed on `compile`, `cranelift`, `cranelift-generic` (`test-results.portable-0.2.0.txt`) |
| standalone consumer | `consumer/` (installs `dist/tui-0.2.0.botpkg`): tests on `compile`, `cranelift`, `cranelift-generic`; native `demo`; **unmodified** `pty_acceptance.py` | all pass; **26/26 PTY checks** (`pty-results.standalone-0.2.0.txt`) |
| event-loop consumer | `consumer-evloop/` (installs the three artifacts), `-check -warnings error` of `evdemo.bot` and `minimal.bot`, native builds of `evdemo`, `evcases`, `bench_standalone` | clean; see §8 |
| PTY acceptance | `python3 -B pty_evloop.py ./evdemo` (installed copy, in `consumer-evloop/`) | **63/63 checks pass** (`test-results.evloop.txt`) |
| lifecycle + fault injection | `python3 -B pty_faults.py ./evcases ./evdemo` | **43/43 checks pass** (`test-results.evloop.txt`) |
| mutation tests | `python3 -B mutate.py $PWD $PWD/botlish` | **15/15 caught; baseline OK** (`mutation-results.evloop.txt`) |
| benchmark | `python3 -B bench_pty.py demo-0.1.0 demo bench_standalone evdemo` | §9 (`bench-results.evloop.txt`) |

Native builds of event-loop programs take about 110 s each on this machine (the standalone `tui` demo: 30 s); `-check`
takes 40 s. Every PTY scenario has a deadline; a global `SIGALRM` watchdog kills all children and fails the run; each
child also has a hard-kill timer.

## 8. PTY acceptance results (event-loop mode)

The application is `examples/evdemo.bot`: the UI task, a ticker (timer), a controller (reads keys from the tap and
starts/pauses/cancels the stream or asks for repaints), a simulated stream (one chunk per interval, optionally with a
microsecond timestamp), all in **one** `std::event_loop`. The PTY harness records the input it sends, the screen
(replayed from the output into a small emulator), the program's own final report (printed *after* the terminal was
restored: counters, outcome, timer lateness) and the kernel's view (`tcgetattr`, `/proc/PID/fdinfo/0` flags, CPU).

| requirement (§9 of the brief) | evidence (check names in `test-results.evloop.txt`) |
|---|---|
| terminal input stays responsive during the stream | 20 keys during a 3 kHz stream, all processed, each within 1 s; key→screen p50 2.7 ms, p95 3.1 ms, max 4.6 ms |
| external state changes trigger redraws without keys | chunks advance on screen with no input; no-loss run: 2000 chunks at 2 kHz, `posted == delivered`, screen shows 2000/2000 |
| timers progress | tick counter advances with no input; 50 ms ticker task under a 3 kHz stream: 80 samples, mean 0.46 ms, one at 6.9 ms, none above 10 ms |
| keyboard decoded correctly | one byte; 3 keys in one read, in order; ESC `[` `D` split over three writes = one key; lone ESC resolved by the loop timer; UTF-8; 30-byte bursts without loss |
| output structurally valid | frames replay into the emulator; resize→status bar on the last row; repaint on request (`ESC[2J` count) |
| UI can terminate its own work | `q` ends the program with the stream live (producers see `NotLive`); `c`/`p`/`s` via the tap |
| terminal state restored afterwards | echo/canon/isig, `O_NONBLOCK` cleared (while the process is still alive and lingering), alternate screen left, cursor shown |
| no event loss | stream sent == report; keys counter after a 30-byte burst; `posted == delivered` |
| resize | idle, during a stream, back to the original size, zero-sized terminal reads as 80x24 with no event, a real size afterwards |
| blocked output | undrained terminal under a flood: timers kept running (ticker never more than 500 ms late; worst measured 4.3 ms over 125 wakes), keys typed while output was blocked were processed, `write_blocks > 0`; `q` while blocked: termios restored within 0.6 s (bounded patience) |
| hangup / EOF | closing the master of an idle and of a writing program ends it |
| not a terminal | message, nothing drawn |
| SIGKILL | **not restorable**, stated and checked (the terminal stays raw) |

Negative / lifecycle cases (`evcases`, one per run) and **fault injection into system calls** with
`strace -f -e inject=…` (calibrated per thread, restricted to the terminal with `-P`):

| case | result |
|---|---|
| two owners (adapter, standalone `open`) | second refused (`TerminalBusy`; `IoFailed "busy"`), first ends (4), free again; 3 more open/close rounds; nothing owned |
| abandon (`std::event_loop::abandon`) | the abandoned coroutine's cleanup restores termios, flags, screen |
| shutdown | inbox revoked, UI task ends with 5, terminal restored |
| stale use | `send`/`request_*` say `NotLive`, `outcome` stable, session restartable |
| watch registration failure (bad descriptor; `ENOSPC` on the input and on the output watch) | `DriverFault`, everything released, terminal restored |
| terminal read failure (`EBADF`; write-only descriptor) | outcome 3 `"read"`, restored; **`EIO`** = hangup → outcome 1, no busy loop |
| terminal write failure (`EIO`; read-only descriptor) | outcome 3 `"write"` / `open` fails `IoFailed "write"`, restored |
| write that would block (`EAGAIN` ×3) | frame completed on writability, screen correct, `write_blocks ≥ 1` |
| raw mode refused (`TCSETS` fails) | `IoFailed "configure"`, no screen change |
| nonblocking refused (`F_SETFL` fails) | `IoFailed "nonblocking"`, raw mode undone |
| re-arm refused (`EPOLL_CTL_MOD` fails) | outcome 6, restored |
| `TIOCGWINSZ` fails from now on | no crash, no event, application keeps running |
| descriptor unavailable / stdin not a terminal | `NotATerminal "input"/"output"`, raw mode never entered |
| undrained terminal + abandon mid-frame | termios restored although the screen restore could not be written |

## 9. Performance

Measured from outside with one harness (`bench_pty.py`, `bench-results.evloop.txt`), 12-core machine, other load
minimal. *Standalone* = `tui::session::run`; *event loop* = the same `monitor` application under the adapter. Latencies
are write-to-first-output-byte. The 0.1.0 demo is the binary built on 3 Nov against `tui` 0.1.0.

**Refactoring regression check (taskapp demo).**

| | 0.1.0 loop | 0.2.0 on `tui::host` |
|---|---|---|
| idle (5 s) | 0.66 ms CPU/s, 10 wakeups/s | 0.81 ms CPU/s, 10 wakeups/s |
| key→screen | p50 1.21 / p95 3.73 ms | p50 1.26 / p95 6.04 ms |
| resize→frame | p50 51.3 ms | p50 52.3 ms |
| 300x100 repaint | 71 ms, 7586 bytes | 68 ms, 7586 bytes |

(Identical frames byte for byte; p95 differences are run-to-run noise at n = 80 — the previous run had 3.63 ms for the
new build.)

**Same application, two hosts.**

| | standalone | event loop |
|---|---|---|
| idle, no application timer | 0.80 ms CPU/s, 10 wakeups/s | 0.73 ms CPU/s, 10 wakeups/s |
| key→screen | p50 1.47 / p95 4.92 ms | p50 0.90 / p95 4.07 ms |
| resize→frame (alternating 100x30 / 80x24, n = 20) | p50 51.6 ms | p50 49.6 ms |
| 200 000-byte input burst | 3.74 s (53 kB/s) | 2.63 s (76 kB/s) |

Resize latency is dominated by the 100 ms sampling phase in both (it averages 50 ms); a single "300x100 repaint"
sample depends on the phase (13.8 ms standalone, 62.6 ms event loop in this harness; the bytes are identical). The
cost of a frame itself is the unchanged renderer's: about 1 ms CPU for an 80x24 frame (an idle program that
changes its view four times a second spends 5 ms CPU/s on frames; the same program with an unchanging view 0.7–0.9 ms/s).

**Event loop with unrelated work.** (The ticker task sleeps 10 ms repeatedly and records how late each wake was.)

| situation | event → screen | ticker task, 10 ms timer |
|---|---|---|
| stream 100 Hz | p50 1.6 / p95 2.8 / max 5.1 ms | — |
| stream 3 kHz (sustained redraws) | p50 1.9 / p95 4.3 / max 19.7 ms | mean 0.43 ms, max 8.2 ms; 601/641 within 1 ms |
| idle | — | mean 0.23 ms, max 1.4 ms |
| continuous key input (512-byte writes every 2 ms) | — | mean 0.91 ms, max 4.4 ms |
| 3 kHz stream + key input | — | mean 1.0 ms, max 6.1 ms; 5 of 612 between 5 and 20 ms |
| 50 000 events in bursts of 100 | — | **0.68 s; 785 frames for 50 003 events (coalesced); nothing lost** |

Findings from the measurements: with the first setting (16 reads of 64 bytes per wake) a flood of keys delayed the
10 ms timer by a mean of 5.6 ms and up to 28 ms, because one dispatch decoded and applied up to ~1000 key events;
reading at most 2 reads (128 bytes) per wake, then re-arming (the readiness re-reports at once), cut it to mean 0.9 ms
/ max 4.4 ms with higher throughput than the standalone loop. That is the cooperative-scheduling limit the scheduler documents (a
segment that does not yield holds the thread); `update` and `view` costs bound it. An application with no input and no
pending redraw sleeps in `epoll_pwait2` (idle CPU above; wakeups are the 100 ms size ticker).

## 10. Resource-cleanup evidence

Tracked resources and where each is released:

| resource | owner | released by |
|---|---|---|
| original termios, raw mode | `Session` | `restore`: saved termios written back |
| descriptor status flags | `Session.flags` | `restore`: `F_SETFL` original flags (checked from `/proc/PID/fdinfo`, **not** from the program) |
| terminal screen/cursor modes | `Session` | `restore`: postlude (bounded patience on a driven session) |
| ownership of the terminal | the `Terminal` context's cell | `restore` (and every early failure path of `open`) |
| input/output readiness watches | `Attached` | `detach`: `unwatch` first (no record after) |
| inbox, tap, size timer registrations | `Attached` (owned) | released with it: waiters wake `Revoked`, later posts `NotLive` |
| pending parser state, frame buffers | the task's frames | dropped with the coroutine |
| escape timers | their own tasks | end on their own (≤ 50 ms) |

Exits checked (§8): normal return, close request, declared failure (application `Abort`, `IoFailed`, driver fault),
`shutdown`, `abandon` (coroutine abandonment), failed `start` at five different points (descriptor, raw mode,
nonblocking, input watch, output watch), a failed or blocked render, hangup, repeated initialisation and shutdown (4 rounds in one
process), stale use after the end. Everything is expressed with existing Botlish resources and contexts: affine
`opaque resource struct`s with `cleanup fn`s (nested: `Attached` contains `Session` and three `Owner`s; releasing the
coroutine's frame runs them in reverse order), a context for shared storage.

## 11. Mutation tests

`mutate.py` copies the sources into a scratch project, applies **one** edit that must apply exactly once, builds the
programs the test needs, runs the harness scenario meant to catch it and requires a *specific* check to fail (a
mutant that is caught only by some unrelated failure is reported `DETECTED-ELSEWHERE` and counts as not caught). The
unmutated baseline must pass the same scenarios. Result of the final run: **15 mutants, all 15 caught by the check meant for them; the unmutated baseline passes** (`mutation-results.evloop.txt`).

| mutant | the edit | scenario run | outcome: the check that failed |
|---|---|---|---|
| `input-watch-never-rearmed` | the input readiness watch is not re-armed after a read (adapter) | scenario_keys | **KILLED**: keys: three keys in one read arrive as three events, in order |
| `terminal-read-blocking` | the input descriptor is not made nonblocking (tui::session) | scenario_startup_timers_idle | **KILLED**: startup: the input descriptor is nonblocking while running (kernel flags) |
| `parser-discards-partial-sequence` | the host resolves a partial escape sequence at the end of every read (tui::host) | scenario_keys | **KILLED**: keys: an escape sequence split across three reads decodes as one key (left) |
| `external-updates-never-redraw` | an external event reaches the application but does not make the view dirty (adapter) | scenario_stream_control | **KILLED**: stream: external events redraw the screen with no keyboard input (chunks >= 5) |
| `pending-redraw-starves-timers` | a blocked write is retried in a tight loop instead of waiting for writability (adapter) | scenario_blocked_output | **KILLED**: blocked output: timers kept running (the ticker task never woke more than 500 ms late) |
| `terminal-mode-not-restored` | the cleanup of a driven session does not restore the termios (tui::session) | scenario_quit_restore_linger | **KILLED**: restore: echo, canon, isig back after the UI task ended (loop still running) |
| `descriptor-flags-not-restored` | the cleanup does not restore the input descriptor flags (tui::session) | scenario_quit_restore_linger | **KILLED**: restore: descriptor flags back (O_NONBLOCK cleared) after the UI task ended |
| `watch-not-revoked-on-release` | the readiness watch is not withdrawn when the session is released (adapter) | scenario_quit_restore_linger | **KILLED**: watch revoked: typing at the terminal after the UI ended produced no readiness record |
| `two-owners-accepted` | a second session on the same Terminal is accepted (tui::session) | busy | **KILLED**: busy: a second owner is refused (TerminalBusy) while the first is running |
| `resize-never-delivered` | size changes are sampled but never delivered to the application (adapter) | scenario_resize | **KILLED**: resize: a size change while idle is delivered to the application (log line) within 3 s |
| `escape-timer-ignored` | the escape timeout never resolves a held ESC (adapter) | scenario_keys | **KILLED**: keys: a lone Escape is resolved by the loop timer (50 ms), not lost |
| `writable-never-seen` | a writable record does not unblock a blocked frame (adapter) | scenario_blocked_output | **KILLED**: blocked output: timers kept running while the terminal did not drain (ticks follow wall time) |
| `repaint-flag-never-cleared` | the coalescing flag of repaint requests is never cleared (adapter) | scenario_keys | **KILLED**: redraw: a second repaint request is honoured too (the coalescing flag is released) |
| `tap-never-posts` | the controller task receives no key (adapter) | scenario_stream_control | **KILLED**: stream: p pauses |
| `hangup-not-recognised` | a hung-up terminal (read fails with EIO) is taken for an idle one (adapter) | injection:hangup | **KILLED**: terminal hangup (EIO on read): the input has ended (outcome 1), no busy loop, program exits |

In the first complete run 11 of the 15 were caught by their intended check; 3 were caught only by another check and 1
survived, and each led to a harness fix, which is the point of the exercise: *pending redraw starves timers* was caught
only by "the blocked-write path was exercised" (the timer check tolerated a three-second freeze: it now looks at the
ticker task's worst lateness); *resize never delivered* and *repaint flag never cleared* were caught by the right
scenario but under a differently named check (the second repaint request was not asserted separately: it is now);
*hangup not recognised* survived a "close the master" test because on Linux a closed pty master reads as EOF (0), and a
writing program also fails on `EIO` — the `EIO`-on-read path is only reachable by injection, which is where it is
tested now. Also found by a new test: a "hangup of an idle program" scenario first failed — not a library bug: the application's
own 1-hour ticker task kept the *loop* alive (documented in the README: the loop ends when nothing is live).

## 12. Language / compiler obstructions (no change was made)

No compiler, runtime or language change was needed. Things that shaped the design, with the workaround:

1. **A struct field cannot have a trait type** (`TRAIT-STORAGE-UNSUPPORTED`): an `Application` cannot live in a `Host`.
   `tui::host` is the application-free half; callers thread the application through a recursive function.
2. **No mutual recursion**: the UI loop is one recursive function (`pump`) with a pure `react` helper before it.
3. **A collection is a value** (`NONPLACE-PARAMETER-MUTATION`): state shared by several tasks is a context (`Ui`).
4. **A task waits on one registration**: everything funnels through one inbox with tags; the escape timeout is a helper
   task that posts into it. (A `wait_any` would remove the tags; it is a scheduler feature request, not a necessity.)
5. **Task protocol admits few errors**: the UI task reports an outcome code and detail through the shared context.
6. **Direct authority** (`syscall`) cannot be called from an entry program or a module without its own context
   (`DIRECT`): the test helper `evlinger` has a context.
7. **`CONTEXT-NATIVE-LOWERING-UNSUPPORTED`**: an `Int` field of a context struct with an unbounded domain is refused on
   the native backend (use `bool`, a bounded type, or a `MutableArray`).
8. **Timers made outside the loop are due at once** unless the application calls `std::event_loop::sync()` first.
   Documented; the examples do it. (Found because the first flood test ended after one turn.)
9. A repro that is probably a compiler defect, **not** needed and **not** pursued (scratch program, not kept): a coroutine
   holding two nested resources whose cleanups call a helper requiring a *custom* context, abandoned with
   `shutdown`/`abandon`, fails analysis with `block () expects 0 argument(s), got 1 (ARITY)`; an almost identical
   program with different names compiles. The adapter's own abandonment case (a nested resource with three
   cleanup-bearing fields and a custom context) works and is tested.
10. Compile times (≈ 40 s `-check`, ≈ 110 s native) dominate the test cycle; mutation runs therefore build in parallel.

## 13. Remaining limitations

* **Cooperative scheduling.** `update` + `view` + the diff for one frame run in one dispatch (≈ 1 ms at 80x24; larger
  screens cost proportionally). Output is chunked; frame *planning* is not.
* **Resize is sampled** every 100 ms (average latency 50 ms) on the loop's timer; no `SIGWINCH` (programs run on a
  worker thread; installing signal handlers was excluded by the brief).
* **Events carry two ints.** Anything bigger (a text chunk from an HTTP response, a job's output) must be stored by the
  application (in a context, keyed by an id or offset) and announced with `notify(code, id)`. Not a transport for payloads.
* **The application value is unreachable from outside** (trait view): other tasks learn about it only through what the
  application's own events/outcome expose.
* The extra copy of terminal events for a controller (**tap**) is bounded (256) and lossy by design (counted in
  `stats.tap_dropped`); the inbox is bounded (256): `send` says `Refused` when full and the producer decides.
* One UI per VM; terminal ownership is per `Terminal` context, so another thread's own `Terminal` is not stopped.
* `SIGKILL`/crash: not restorable (verified). A terminal whose output is stuck: the screen restore gives up after ≈ 1 s
  (termios and flags are still restored).
* Linux x86-64, native backends only; needs a compiler with the shared-dependency package manager (§6); the CI job is
  gated on a repository variable, so a plain `single-latest` run skips it. Fault injection needs `strace`.
* Not done: **HTTP and `process_linux` integration** (§14/§15 G), `Windows`/`macOS`, bracketed paste, mouse, a
  multi-line editor, Unicode grapheme handling (all excluded by the brief). The mutation set is 15 edits, not exhaustive;
  edits to the write chunking (`write_some` offset arithmetic) are not covered because no frame in the tests exceeds one chunk
  except the flood case, which checks termination rather than bytes.
* Measurements are from one machine; p95/max values at n ≈ 80 vary run to run (the 0.2.0 demo's p95 key latency was 3.63 ms in
  one run and 6.04 ms in the one recorded here, with identical code).

## 14. Consequences for the planned agent TUI

* **Shape.** One program, one loop: the UI task + a task per PM conversation (an HTTP exchange read chunk by chunk →
  `notify`/`send`) + a task per job (a managed subprocess → `notify`) + controller tasks reading the **tap**. All of
  them hold an `Inbox` and need nothing else from the terminal. Cancellation is `running?`/`NotLive` plus the
  producers' own tokens; shutdown is `request_close` or `shutdown`.
* **State.** Keep project/job/conversation state in the `Application` value (pure, testable with `apply_events`
  exactly as `tests/test_monitor.bot` does); keep bulky text in a context-owned store and send ids.
* **Backpressure.** `send` can answer `Refused`; decide per source (coalesce progress, never drop a conversation's final
  message — retry on the next wake as `evdemo`'s stream does).
* **TUI work that this unblocks and what it does not:** a scrolling text view, wrapping, a multi-line input and
  Markdown are still the library's own later milestones (the renderer is unchanged and its cost model — one frame is
  about a millisecond — bounds how much a single `view` may do).
* **Process rules:** one `Terminal`, one `Native`, one driver per VM; both `http` 0.2.0 and `tui_event_loop_linux`
  lock `event_loop_linux 0.5.0`, so they share one instance. Install the contexts once in the entry program, call
  `std::event_loop::sync()` first, and let producers end themselves.

## 15. Answers to the final questions

**A. Can a Botlish application own one event loop for terminal input, timers, HTTP and eventually subprocesses, while the
TUI remains an ordinary package?** Yes for the terminal, timers and arbitrary tasks — *executed*. For HTTP and
subprocesses this is established only at the package level: `http` 0.2.0 locks the same `event_loop_linux 0.5.0`
(its manifest), uses the loop's `Native` capability and is "scheduled by the program's own event loop" (its README), so
its tasks are ordinary tasks of the same loop and post to an `Inbox` like `evdemo`'s stream. That composition was **not
run** here (the HTTP package was not built; no local HTTP peer test was made), and `process_linux` was not available.
The TUI is an ordinary package pair; nothing in it is special to the loop beyond the published driver's public API.

**B. Does the renderer require any knowledge of asynchronous scheduling?** No. The renderer (`ansi::diff`), the
renderer's memory (`tui::display`), the decoder and the state machine (`tui::host`) are pure, portable and have no
dependency; they are tested on all four backends. Input acquisition, state transitions and rendering are separate
functions; the adapter composes them. Application events are plain data (`Custom`).

**C. Is the standalone `run` preserved without two divergent implementations?** Yes. `run` is the same host
transitions and display functions, driven by its own poll loop; there is one decoder, one renderer, one ownership and
cleanup. Observable behaviour is preserved: 26/26 original PTY checks pass unmodified; the 0.1.0 suites pass; the
benchmark shows identical frame bytes and comparable latency. (The loop itself exists twice — a 100 ms poll loop
and the adapter's task — by design: the brief forbids a private loop in the driven mode.)

**D. Can terminal ownership and restoration be expressed entirely with existing Botlish resources and contexts?** Yes.
`Session` and `Attached` are `opaque resource struct`s with `cleanup fn`s; the terminal authority and the shared
counters are contexts; the exclusive-owner cell lives in the `Terminal` context; restoration on return, failure,
cancellation, `abandon` and `shutdown` is the lexical release of those resources. Only `SIGKILL` and per-VM exclusivity
are outside what the language provides.

**E. Does the integration introduce any significant responsiveness, CPU or rendering regression?** No regression
measured: idle CPU and wakeups equal the standalone loop's; key latency equal or better; input burst throughput higher;
frames byte-identical; an unrelated 10 ms timer stays within about a millisecond mean and 4–8 ms worst under input floods
and a 3 kHz stream (after one tuning: bounding input reads per wake, §9). The limits are the cooperative-scheduling ones
(§13), not new ones.

**F. Inside the TUI package or a separate Linux-specific adapter?** Separate (`tui_event_loop_linux`), §3.3: it keeps
`tui` dependency-free and portable-by-layer, keeps the driver's native archive and lock out of standalone consumers,
and mirrors how `http` relates to `event_loop_linux`. The price is a second package, a second `add`, and the compiler
requirement of §6.

**G. What integration work remains between the TUI, the official HTTP client and the separately developed
`process_linux`?** (1) A graph test: one program that installs `tui`, `tui_event_loop_linux`, `http` and `process_linux`
and checks a single `event_loop_linux` instance (`botlish package graph`) — expected from the locks, unverified. (2) An
`http` streaming task that reads an exchange chunk by chunk, stores text in an application context and announces it
with `notify`; a controllable local HTTP peer for tests (the PTY harness pattern here, plus its fault injection).
(3) The same for a `process_linux` job (output lines, exit, cancellation, killing on `request_close`), and the
resource-ordering question when a task owning an exchange and a process is abandoned with the UI task (all are
affine resources released by `abandon`; to be tested). (4) A payload story for events larger than two ints. (5) A
`std::event_loop` feature request that would simplify the adapter: waiting on several registrations (or an owned
multi-source mailbox). (6) CI with a compiler that has the shared-dependency package manager.

---

### Files added or changed by this milestone

`tui/`: `tui/display.bot`, `tui/host.bot` (new); `tui/input.bot`, `tui/session.bot` (extended); `tests/test_host.bot`,
`tests/test_session.bot`; README, GUIDE, `tui.bot`; version 0.2.0.
`tui_event_loop_linux/` (new package): `tui_event_loop_linux.bot`, `README.md`, `examples/{minimal,evdemo,monitor,control}.bot`,
`tests/` (`test_monitor.bot`, `evcases.bot`, `evlinger.bot`, `bench_standalone.bot`, `ptylib.py`, `pty_evloop.py`,
`pty_faults.py`, `bench_pty.py`, `mutate.py`, `README.md`).
Repository: `dist/` (two new artifacts, `upstream/`), `consumer/` (now 0.2.0), `consumer-evloop/`, `devloop/`, `scripts/`
(`pack_all.sh`, `devloop_refresh.sh`, `run_tests.sh` + `test_host`), CI workflow, `README.md`, result files
(`test-results.evloop.txt`, `pty-results.*`, `mutation-results.evloop.txt`, `bench-results.evloop.txt`).
