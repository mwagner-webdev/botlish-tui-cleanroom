# tui guide

Every program below is a complete file in `examples/` (copy it to your project root
and run `botlish FILE.bot`; any backend unless noted). The snippets in this text are
abbreviated: they leave out the `import` lines and reuse names such as `plain` from
earlier snippets, so start from the example files. Each Botlish file needs its own
imports (there are no re-exports), e.g. `import std::str` for `concat`/`append_char`,
`import std::list` for `length`/`get`/`flat`, `import std::int` for `to_string`. The
outputs shown are what the compiler prints.

Contents: 1 Model · 2 Canvas and geometry · 3 Styling · 4 Text safety and width ·
5 ANSI output · 6 Layout · 7 Widgets · 8 Input events · 9 Application state
transitions · 10 The terminal session · 11 Testing without a terminal ·
12 Error handling · 13 Things that will trip you up

## 1. Model

```text
application state --view--> layout tree --draw--> Canvas --rasterize--> Screen
                                                                   |
                                                       tui::ansi::diff / full
                                                                   v
                                                         ANSI text -> terminal
terminal bytes --tui::input decoder--> Event --update--> next application state
```

Everything left of "terminal" is a pure function over immutable values. Only
`tui::session` talks to a terminal.

## 2. Canvas and geometry (`tui::canvas`)

A `Canvas` is a width, a height and a list of drawing operations; nothing is drawn
until `rasterize` turns it into a `Screen` (a row-major `List[Cell]`). Operations
apply in order: later ones overwrite earlier ones.

```botlish
import tui::style
import tui::canvas

plain = tui::style::plain()

inner = tui::canvas::rect(1, 1, 14, 3)
canvas = tui::canvas::new(16, 5).border(tui::canvas::rect(0, 0, 16, 5), tui::canvas::BorderKind::Single, plain).text_in(inner, 2, 1, "clipped to the inner box", plain).text_in(inner, -3, 3, "left part cut", plain).text(4, 2, "later wins", plain.bold())

canvas.rasterize().lines()
```

`examples/g1_canvas.bot` prints

```text
["┌──────────────┐", "│ clipped to th│", "│   later wins │", "│ part cut     │", "└──────────────┘"]
```

| function | meaning |
|---|---|
| `tui::canvas::new(w, h)` | empty canvas; negative sizes become 0, sizes above `max_size` (1000) become 1000; `valid_size?(w, h)` tells whether a size is representable |
| `rect(x, y, w, h)` | a `Rect`; `empty?`, `contains?(x, y)`, `intersect(other)` |
| `c.text(x, y, s, style)` | one line of text at (x, y) |
| `c.text_in(clip, x, y, s, style)` | the same, clipped to a `Rect` |
| `c.fill(rect, style)` | the area becomes spaces in `style` (this is "clear region") |
| `c.border(rect, kind, style)` | outline; `BorderKind::Single`, `Rounded`, `Double`, `Ascii` |
| `c.add(op)`, `c.add_all(ops)` | append prepared operations (`op_text`, `op_fill`, `op_border`); `add_all` is linear, repeated `add` is not |
| `c.rasterize()` | the `Screen` |
| `s.cell_at(x, y)` | a `Cell {ch, fg, bg, attrs}`; fails with `OutOfBounds` outside the screen |
| `s.row_text(y)`, `s.lines()` | plain text of one row / all rows |
| `blank(w, h)`, `blank_cell()` | an all-blank screen / cell |

Geometry rules (all total — nothing here can panic):

* Coordinates are `Int`s and may be negative or enormous; anything outside the
  canvas (or an operation's clip) is dropped cell by cell.
* A `Rect` with `w <= 0` or `h <= 0` has no area and draws nothing.
* A border needs `w >= 2` and `h >= 2`; thinner rectangles draw nothing.
* Zero-sized canvases are fine: `new(0, 0)` rasterizes to no rows.
* Drawing never fails; only *reading* a cell outside the screen is an error.

## 3. Styling (`tui::style`)

A `Style` is `{fg, bg, attrs}`. Colours are Ints: `-1` the terminal default,
`0..255` the xterm palette (`style::red`, `bright_blue`, ... name 0-15), `-2`
(`style::keep`) "keep whatever colour is already underneath". Out-of-range colours
become the default colour. Attributes are bits: bold 1, dim 2, italic 4, underline
8, reverse 16.

```botlish
plain = tui::style::plain()
warning = plain.bold().with_fg(tui::style::yellow).with_bg(tui::style::red)
```

`plain()` is default/default; `transparent()` keeps both colours (use it for text
that sits on a coloured background); `colors(fg, bg)`; `with_fg`, `with_bg`, `bold`,
`dim`, `italic`, `underline`, `reverse`, `has_attr?(bit)`.

A drawn cell takes its style from the operation that last drew it: a label with
`plain()` resets the background under it, one with `transparent()` keeps it.

## 4. Text safety and width (`tui::text`)

Application text is data, never control output. Every character that reaches a
cell goes through `tui::text`:

* control characters (C0, DEL, C1 — so `ESC` and newline) become `U+FFFD`;
* the standard library has no width tables, so only an explicit single-cell subset
  is kept: `U+0020–007E`, `A1–AC`, `AE–2FF`, `370–482`, `48A–52F`, `1E00–1EFF`,
  `2010–2027`, `2190–21FF`, `2500–25FF` and `U+FFFD`; `U+00A0` shows as a space;
* combining marks, zero-width characters, wide/fullwidth (CJK), emoji and every other
  code point become `U+FFFD` (one cell).

So the library never mis-measures; it replaces what it cannot measure.
`tui::text::sanitize(s)` gives the text as displayed, `width(s)` its cell width,
`codepoints(s)` / `from_codepoints(cps)` convert, `code('q')` is a character's
number (113).

## 5. ANSI output (`tui::ansi`)

```botlish
screen = tui::canvas::new(8, 1).text(1, 0, "WARN", warning).rasterize()
changed = tui::canvas::new(8, 1).text(1, 0, "WARN", warning).text(6, 0, "ok", plain).rasterize()
{full: tui::ansi::visible(tui::ansi::full(screen)), update: tui::ansi::visible(tui::ansi::diff(screen, changed)), unchanged: tui::ansi::diff(screen, screen)}
```

`examples/g2_style.bot` prints (`\e` is the ESC character, shown by `visible`):

```text
{full: "\e[0m\e[2J\e[1;2H\e[0;1;33;41mWARN\e[0m", unchanged: "", update: "\e[1;7H\e[0mok\e[0m"}
```

* `full(screen)` clears the terminal and draws every non-blank cell.
* `diff(previous, next)` draws only the cells that changed; identical screens give
  `""`; a different size gives a full redraw.
* Every drawn run starts with its own cursor move and `ESC[0;...m` reset and the
  output ends with `ESC[0m`, so styles never leak. Output is deterministic.
* `cursor_to`, `hide_cursor`, `show_cursor`, `enter_alternate_screen`,
  `leave_alternate_screen`, `clear_screen`, `reset_style`, `sgr(style)` build the
  trusted sequences; `visible(text)` shows ESC as `\e` for assertions.

## 6. Layout (`tui::layout`)

A layout is an immutable tree of `Node`s; the available area is data.

```botlish
import tui::layout

fn ui() -> tui::layout::Node:
    toolbar = tui::layout::hstack([tui::layout::label("File", plain), tui::layout::label(" Edit", plain), tui::layout::spacer(), tui::layout::label("v1", plain.dim())])
    editor = tui::layout::label("text goes here", plain).boxed("editor", tui::canvas::BorderKind::Double, plain).with_flex(2)
    log = tui::layout::label("log", plain).boxed("log", tui::canvas::BorderKind::Ascii, plain).with_flex(1)
    tui::layout::vstack([toolbar, editor, log])

tui::canvas::new(24, 8).draw(ui()).rasterize().lines()
```

(`examples/g3_layout.bot` draws it at 24x8 and 24x11 and prints the rectangles.)

```text
["File Edit             v1", "╔═ editor ═════════════╗", "║text goes here        ║", "║                      ║", "╚══════════════════════╝", "+- log ----------------+", "|log                   |", "+----------------------+"]
```

Builders: `label(text, style)`, `spacer()`, `fill(style)`, `vstack(children)`,
`hstack(children)`, `boxed(child, title, kind, style)`, `padded(child, left, top,
right, bottom)`, `padded_all(child, n)`, `on_color(child, style)`, and the size
modifiers `with_width`, `with_height`, `with_flex`. Drawing: `canvas.draw(node)`,
`canvas.draw_in(node, rect)`; inspection: `node.measure()`, `node.rects(rect)` (the
rectangle of every node, preorder), `node.ops(rect)`.

Allocation rules:

1. A node's natural size: label = text width × 1; vertical stack = widest child ×
   sum of heights; horizontal stack = sum of widths × tallest; box = child + 2 on
   each axis (and at least title + 4 wide); padding adds its insets; spacers and
   fills have natural size 0. `with_width`/`with_height` replace it.
2. `with_flex(n)` (`n > 0`; spacers and fills have 1) takes a share of the space
   left on the stack's main axis, proportional to the weights; the shares add up to
   exactly the leftover (remainders go to later children deterministically).
3. Not enough space: fixed children are served in order and later ones are
   truncated, down to 0; flex children get 0. Surplus with no flex child stays empty.
4. On the cross axis a child gets the whole extent unless its own width/height is
   smaller.
5. A node draws only inside its own rectangle; a rectangle with no area draws nothing.

## 7. Widgets (`tui::widgets`)

A widget is a state struct plus pure functions: `update(state, event) -> state` and a
function from state to a `Node`. The application owns the states.

```botlish
list0 = tui::widgets::list_state(["alpha", "beta", "gamma"])
list1 = press(press(list0, tui::input::Kind::Down), tui::input::Kind::Down)   # press: list.list_update(key(kind))

fn typed(text: str) -> tui::widgets::TextInput:
    fold e in tui::input::decode_text(text) with intermediate field tui::widgets::text_input(""):
        field.input_update(e)

ui = tui::layout::vstack([tui::widgets::list_node(list1, 3, true, plain, plain.reverse()), field.input_node(10, true, plain), tui::widgets::button("OK", false, plain)])
```

(`examples/g4_widgets.bot`)

```text
{screen: ["  alpha     ", "  beta      ", "> gamma     ", "tui         ", "[ OK ]      "], selected: "gamma", text: "tui"}
```

| widget | state | events | node |
|---|---|---|---|
| focus | `focus(n)`, `focus_next/prev/set`, `focused?(i)` | `focus_update(f, event)`: Tab, Shift+Tab | – |
| button | none | `activated?(event)`: Enter or Space | `button(label, focused, style)` (reverse video when focused) |
| status bar | none | – | `status_bar(text, style)`: full-width reverse line |
| text input | `TextInput` from `text_input(text)`, `with_max_length(n)`, `input_text()`, `input_empty?()` | `input_update(t, event)`: characters, Backspace, Delete, Left, Right, Home, End, Ctrl+A/E/U/K; Tab/Enter/Escape/Alt keys are ignored | `input_node(t, width, focused, style)`; caret = reverse cell; window scrolls with the caret |
| list | `ListState` from `list_state(items)`, `with_page(n)`, `list_select(i)`, `list_replace(items)`, `selected_item()`; `selected` is -1 only for an empty list | `list_update(l, event)`: Up, Down, Home, End, PageUp, PageDown | `list_node(l, height, focused, style, highlight)` |

Typed characters are stored as displayed characters (see section 4), so a pasted ESC
becomes `U+FFFD` in the field.

## 8. Input events (`tui::input`)

`tui::input::Event` is `{kind, ch, ctrl, alt, shift, width, height}`.
`kind` is a `Kind`: `Char` (`ch` is the scalar value), `Enter`, `Tab`, `BackTab`,
`Backspace`, `Delete`, `Escape`, `Up`, `Down`, `Left`, `Right`, `Home`, `End`,
`PageUp`, `PageDown`, `Insert`, `Function` (`ch` is 1..12), `Resize` (`width`/`height`),
`Unknown` (a sequence the decoder does not know). Helpers: `key(kind)`,
`char_event(cp)`, `ctrl_char(cp)`, `function_key(n)`, `resize(w, h)`,
`with_ctrl/with_alt/with_shift`, predicates `is_char?(e, 113)`, `is_ctrl?(e, 99)`,
`is_key?(e, Kind::Enter)`.

The decoder is pure and incremental:

```botlish
first = tui::input::feed(tui::input::new_decoder(), [97, 27, 91])    # "a", ESC [
second = tui::input::feed(first.decoder, [65, 98, 27])               # "A" completes ESC [ A (Up), "b", ESC
held = second.decoder.pending?()                                      # the trailing ESC is held
resolved = tui::input::flush(second.decoder)                          # -> Escape
```

(`examples/g5_input.bot`: one event after the first read; `[Up, Char b]` after the
second; `still_pending: true`; after the flush `[Escape]`.)

* `feed(decoder, bytes)` accepts any chunking and returns `Decoded {decoder, events}`.
* `flush(decoder)` resolves what is pending as if no more bytes were coming.
  `decode_all(bytes)` = feed + flush; `decode_text(string)` does that for a string's
  UTF-8 bytes (build ESC with `tui::ansi::esc()`).
* **Escape policy.** A lone ESC byte is held (`pending?` is true) until the next
  byte or until you flush. `tui::session` flushes after 50 ms of silence. ESC followed
  by a printable key within that time is Alt+key; ESC `[` and ESC `O` flushed alone are
  Alt+`[` / Alt+`O`.
* **Limits.** A pending sequence is always shorter than `max_sequence` (32) bytes: a
  longer one becomes a single `Unknown` event and the rest of its parameter bytes
  are discarded up to the final byte. A malformed UTF-8 sequence becomes `U+FFFD`; a
  byte that interrupts a sequence (a new ESC, a control byte) ends it with an
  `Unknown` event and is then processed normally.
* Supported: printable text (UTF-8), Enter (CR), Tab, Backspace (DEL and ^H), Ctrl+letter,
  Ctrl+Space, arrows, Home/End (several encodings), Insert/Delete/PageUp/PageDown,
  F1–F12, Shift+Tab, xterm modifiers (`ESC[1;5A`, ...), Alt+key. Mouse and paste
  reports decode to `Unknown`.

## 9. Application state transitions (`tui::app`)

The pattern is two pure functions over your own state struct:

```text
update : state + event      -> next state
view   : state + dimensions -> Canvas
```

Declare them (plus `finished?`) **in the same module (file) that defines the state
type** — conformance is structural and reads only the type's own module — and that
type *is* a `tui::app::Application`; there is nothing to register. (An entry program
may define its own struct and the three functions, as `g6_state.bot` does.)

```botlish
struct Counter:
    value: int
    done: bool

fn update(app: Counter, event: tui::input::Event) -> Counter:
    if event.is_char?(113):
        return Counter {value: app.value, done: true}
    if event.is_key?(tui::input::Kind::Up):
        return Counter {value: app.value + 1, done: false}
    if event.is_key?(tui::input::Kind::Down):
        return Counter {value: app.value - 1, done: false}
    app

fn view(app: Counter, width: int, height: int) -> tui::canvas::Canvas:
    label = tui::layout::label("count: ".concat(std::int::to_string(app.value)), tui::style::plain().bold())
    tui::canvas::new(width, height).draw(tui::layout::vstack([label]))

fn finished?(app: Counter) -> bool:
    app.done
```

Drive it with events (`examples/g6_state.bot` does, with no terminal) — exactly what
`tui::session::run` does with a terminal:

```text
{final: Counter {value: 1, done: true}, finished: true, frame: ["count: 1      ", "              "]}
```

`update` may fail: declare `errors tui::app::Abort` and `fail tui::app::Abort {reason:
"..."}`; the failure leaves `run` after the terminal is restored. Callers of an
`update` that declares the error must handle it (`on tui::app::Abort {reason}:`).
`tui::app::apply_events(app, events, 0)` applies a list of events until the app
finishes; `tui::app::frame(app, w, h)` rasterizes a view.

`examples/taskapp.bot` is a complete application written this way: three focus
targets, a list, a text field, a button, a status line, a resize handler, and a
failing key.

## 10. The terminal session (`tui::session`)

Layer B. Native backend, Linux x86-64, stdin and stdout must be terminals.

```botlish
with context std::linux::io::create()        # only for the messages printed below
with context tui::session::create()          # terminal authority: install it once
tui::session::run(Counter {value: 0, done: false}):
    on tui::session::NotATerminal {stream}:
        say("g8_session: standard ".concat(stream).concat(" is not a terminal"))
    on tui::session::IoFailed {operation}:
        say("g8_session: terminal I/O failed: ".concat(operation))
    on tui::app::Abort {reason}:
        say("g8_session: ".concat(reason))
say("g8_session: bye")
```

(`examples/g8_session.bot` is the complete program: the counter of section 9 plus
this entry point; `examples/demo.bot` runs the task list the same way.) Lifecycle of `run(app)`:

1. `open`: checks that stdin and stdout are terminals (else `NotATerminal {stream}`),
   saves the termios, switches to raw mode (no line buffering, no echo, no `Ctrl+C`
   signal, no CR translation), enters the alternate screen, hides the cursor, turns
   off line wrap. A `Session` resource now owns the obligation to restore.
2. Loop: draw `view` at the current size (`ansi::diff` against what is on screen;
   full redraw after a resize), wait up to 100 ms (50 ms while an ESC is pending)
   with `poll`, decode input with `tui::input`, feed events to `update`, deliver a
   `Resize` event when the size changed. EOF on input ends the loop.
3. Exit — normal, `finished?`, EOF or an error: the `Session` cleanup runs where
   `run`'s scope ends: leave sequences, then the saved termios are restored.
   `run` then returns or raises (`NotATerminal`, `IoFailed {operation}`, `Abort`).

Also public: `session::tty?()`, `session::size()`, `session::write_text(text)` (trusted
terminal output) — all need the `Terminal` context. Constants: `escape_timeout_ms`
(50), `resize_poll_ms` (100).

Build and run:

```sh
botlish -emit-native-executable demo.bot && ./demo       # or: botlish -backend cranelift demo.bot
```

Guarantees: state is restored after normal exit and after any error that propagates
out of `run`; **not** after `SIGKILL`/`SIGTERM` (there are no signal handlers in the
language). Interpreter and `compile` backends refuse the syscalls (`NATIVE-ONLY`).

### 10.1 The same session without a loop of its own (0.2.0)

`run` is built from public pieces, so a program that already has an event loop
can drive the terminal itself. Nothing in `tui` waits, schedules or knows about
an event loop; the integration package `tui_event_loop_linux` (a separate
package with its own README) is one such program. The pieces:

```botlish
session = tui::session::open_driven()      # raw mode + nonblocking input descriptor, claimed
read = tui::session::read_bytes()          # one read(2): Data / Idle / Closed / Hangup, never waits
host = host.feed(read.bytes)               # tui::host: decode; Fed {host, events}
next = tui::app::apply_events(app, fed.events, 0)
frame = host.frame(app)                    # the bytes that bring the screen up to date
at = tui::session::write_some(tui::session::encode(frame.text), offset, 16384)   # one write step
```

* `open_driven` fails `TerminalBusy` if this `Terminal` already has a Session. The
  Session's cleanup restores the termios, the screen **and the input descriptor's
  original flags**, and gives up the ownership; a driven session's screen restore is
  written with a patience of about one second, so a stuck terminal cannot hang the
  cleanup.
* `read_bytes` distinguishes the *absence* of bytes (`Idle`) from the end of input
  (`Closed`) and a hung-up terminal (`Hangup`, EIO).
* `write_some` returns the new offset; an unchanged offset means the terminal cannot
  take output now (wait until it is writable, then call again). A frame that is not
  written in full must be reported with `tui::host::frame_failed(host)`: the next
  frame then repaints every cell.
* `tui::host` holds no application (an `Application` is a trait view, which a struct
  field cannot hold): the caller applies `Fed.events` with `apply_events`. The
  decoder, the escape-timeout rule (`silence` after `escape_timeout_ms`), the dirty
  flag and the renderer's memory are the ones `run` uses.
* An event that is not from the keyboard is a `Custom` event: `tui::input::custom(code,
  value)`; `update` sees it like a key. `pack`/`unpack` turn an event into two ints.

## 11. Testing without a terminal

Because the state machine, the view and the decoder are pure, tests are ordinary
programs using `std::test`. `examples/test.bot` shows the pattern: a small `feed`
helper decodes text to events and folds them through `update`; assertions compare
states and `view(...).rasterize().lines()`.

```botlish
import std::test
import taskapp
...
fn reg_state():
    fn toggling():
        st = taskapp::initial().press(tui::input::Kind::Down).feed(" ")
        {rows: st.titles(), status: st.status}
    std::test::value("task app: Space toggles the selected task", {rows: [...], status: "Marked 'Write a task app' as done"}, toggling)

with context std::test::suite()
reg_state()
std::test::report()
```

The program's value is the report (`["PASS ...", ..., "19 passed, 0 failed"]`).
Test bodies are named nested functions without arguments; they cannot require a
context, so anything needing `tui::session` is computed at the top level and
captured (see `tests/test_session.bot`). ANSI output is data too: compare
`ansi::visible(ansi::diff(a, b))` with a literal. The library's own tests
(`tests/test_*.bot`) replay the generated ANSI on a small terminal model to check
that incremental updates and full redraws show the same screen.

## 12. Error handling

| error | raised by | meaning |
|---|---|---|
| `tui::canvas::OutOfBounds` | `cell_at` | reading outside the screen |
| `tui::session::NotATerminal {stream}` | `run`, `size` | `stream` is `"input"`, `"output"` or `"size"` |
| `tui::session::IoFailed {operation}` | `run`, `write_text`, `read_bytes`, `write_some` | `configure`, `nonblocking`, `read`, `poll`, `write`, `busy`, ... failed |
| `tui::session::TerminalBusy {reason}` | `open_driven` | another Session owns this Terminal (the 0.1.0 functions report `IoFailed "busy"`) |
| `tui::app::Abort {reason}` | your `update` | application failure; the terminal is restored first |

Everything else is total: drawing is clipped, malformed input becomes `Unknown`/`U+FFFD`
events, impossible geometry has defined results (section 2, 6), widget editing never
indexes out of range. Errors are Botlish *declared* errors: handle them with `on
Name:` or list them in your function's `errors` clause.

## 13. Things that will trip you up

* Botlish strings have no `\e`, `\x` or `\u` escapes: use `tui::ansi::esc()` or
  `tui::text::char_string(27)`; write other characters literally.
* One-character string literals (`"x"`) draw a compiler warning; write `'x'` for a
  character, or use `"".append_char('x')` to build a one-character string.
* A local variable named like a library function you call with method syntax can make
  the call ambiguous (`screen` once did; the canvas function is therefore `rasterize`).
* Linter advice you will see for calls of library functions: `f(x, y)` should be
  `x.f(y)` (`METHOD-ELIGIBLE`). Functions whose first argument is not a receiver are
  declared `nomethod` and are called functionally (`tui::canvas::new(w, h)`,
  `tui::layout::label(text, style)`).
* Trait views expose only the trait's operations: `tui::app::apply_events` returns an
  `Application` view, not your concrete state type; keep your own functions for tests.
* Fixed-height pieces add up: `boxed` adds 2 rows (and 2 columns) around its child, so a
  list of `h` visible rows inside a box needs a node of `h + 2` rows; if the fixed rows
  of a stack exceed the area, the *later* ones are silently truncated (layout rule 3).
  `examples/taskapp.bot` computes `list_height = height - 9` for this reason.
* Labels and status bars add no padding: write the spaces you want in the text.
* There is no assignment: widget states are rebuilt by `update`; `fold` carries state
  through event lists.
