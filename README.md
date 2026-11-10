# tui — a terminal UI library in pure Botlish, and its event-loop integration

Two independent Botlish packages, built with the distributed single-file compiler:

* `tui/` — **tui 0.2.0**: the library (renderer, decoder, state machine, widgets, standalone
  `tui::session::run`). No dependencies. Start with `tui/README.md`.
* `tui_event_loop_linux/` — **tui_event_loop_linux 0.1.0**: the terminal as a participant in an
  application-owned `std::event_loop` (Linux). Depends on `tui` 0.2 and on the published
  `event_loop_linux` 0.5 driver. Start with `tui_event_loop_linux/README.md`.

Reports: `TUI-CLEANROOM-PACKAGE-MILESTONE-1.md` (the library),
`TUI-EVENT-LOOP-INTEGRATION-MILESTONE-1.md` (the event-loop integration: architecture census, API,
tests, PTY acceptance, performance, mutation results, limitations), `TERMINAL-CAPABILITIES.md`.

## Layout

| path | what |
|---|---|
| `tui/`, `tui_event_loop_linux/` | the package sources (`botlish.json`, modules, README, `examples/`, `tests/`) |
| `dist/tui-0.1.0.botpkg` | the original release (unchanged, `d442e0d4…`) |
| `dist/tui-0.2.0.botpkg`, `dist/tui_event_loop_linux-0.1.0.botpkg` | this milestone's releases (`scripts/pack_all.sh`; deterministic) |
| `dist/upstream/event_loop_linux-0.5.0.botpkg` | Botlish's own published release of the Linux driver, rebuilt byte-for-byte from the pinned author image (`.provenance.json`, `.release.txt`) |
| `consumer/` | a fresh project that installs `tui` and uses it standalone (original acceptance) |
| `consumer-evloop/` | a fresh project that installs the three artifacts and runs the event-loop programs and their PTY tests |
| `dev/` | authoring project for `tui` (editable link to `../tui`) |
| `devloop/` | authoring project for `tui_event_loop_linux` (editable link) against the *packed* `tui` (`scripts/devloop_refresh.sh`) |
| `scripts/`, `probes/`, `repros/` | authoring helpers, capability probes, minimal compiler repros |

## The compiler

The Botlish compiler is **not** in this repository. Put the single-file compiler at the repository root:

```sh
curl -fsSL https://github.com/mwagner-webdev/botlish/releases/download/single-latest/botlish-linux-x86_64 -o botlish-linux-x86_64
chmod +x botlish-linux-x86_64
(cd dev && ../botlish-linux-x86_64 install)
BOT=$PWD/botlish-linux-x86_64 scripts/run_tests.sh $PWD/dev        # tui: all backends, ~8 minutes
```

The results in this repository for 0.2.0 were produced with the compiler reported by `-version` in the
milestone report (a newer development build than the one 0.1.0 was made with); every CI job prints the
version it actually got.

## Event-loop consumer (what CI runs)

```sh
cd consumer-evloop
../botlish-linux-x86_64 install && ../botlish-linux-x86_64 verify     # three published artifacts
../botlish-linux-x86_64 -emit-native-executable evdemo.bot            # ~2 minutes
../botlish-linux-x86_64 -emit-native-executable evcases.bot
python3 -B .bot_modules/tui_event_loop_linux/tests/pty_evloop.py ./evdemo
python3 -B .bot_modules/tui_event_loop_linux/tests/pty_faults.py ./evcases ./evdemo   # needs strace
```

## CI

`.github/workflows/ci.yml`: library tests on all four backends; deterministic packs of both packages
compared with `dist/` (and `dist/upstream` against the Botlish repository's `RELEASES.tsv`); the standalone
consumer; the event-loop consumer (install, verify, compile documented examples, native build, real-PTY
acceptance, fault injection); a manually started mutation job. If you change anything under `tui/` or
`tui_event_loop_linux/`, bump the version and run `scripts/pack_all.sh`: the pack check fails on a stale
artifact.
