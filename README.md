# tui — a terminal UI library in pure Botlish (clean-room milestone 1)

* `tui/` — the package source (`botlish.json`, modules, `README.md`, `GUIDE.md`, `examples/`, `tests/`).
  Start with `tui/README.md`.
* `dist/tui-0.1.0.botpkg` — the release artifact, SHA-256
  `d442e0d42e18343de43c4cea2345202f0bcbc08267310a4c439a7173413d0ffa`.
* `consumer/` — a fresh project that installs the artifact and uses it.
* `dev/` — authoring project (editable link to `../tui`).
* `TUI-CLEANROOM-PACKAGE-MILESTONE-1.md` — the completion report; `TERMINAL-CAPABILITIES.md` — the census.
* `scripts/`, `probes/`, `repros/` — authoring helpers, capability probes, minimal compiler repros.

## The compiler

The Botlish compiler (`botlish-linux-x86_64`) is **not** in this repository. Put it at the repository root
(or point `BOT` at it) before running anything:

```sh
curl -fsSL https://github.com/mwagner-webdev/botlish/releases/download/single-latest/botlish-linux-x86_64 -o botlish-linux-x86_64
chmod +x botlish-linux-x86_64
(cd dev && ../botlish-linux-x86_64 install)
BOT=$PWD/botlish-linux-x86_64 scripts/run_tests.sh $PWD/dev        # all backends, takes ~8 minutes
```

## CI

`.github/workflows/ci.yml` runs on every push and pull request: library tests on all four backends,
a deterministic-pack check (packs twice and compares with `dist/tui-0.1.0.botpkg`), and the consumer project
(install, verify, tests, native demo build, real-PTY acceptance). The compiler is taken from the repository root
if present, otherwise downloaded from
`https://github.com/mwagner-webdev/botlish/releases/download/single-latest/botlish-linux-x86_64`
(override with the repository variable `BOTLISH_COMPILER_URL`). That release is a moving target; the results in this
repository were produced with the build reported by `-version` as source revision 2a10335, and every CI job prints the
version it actually got.
If you change anything under `tui/`, bump the version and repack: the pack check fails on a stale artifact.
