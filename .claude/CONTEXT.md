# Session Context — emulator-system

Handoff notes so this project can be continued in **Claude Code**. Intended
workflow: use **Claude (via Claude Code)** for planning/architecture/review, and
run **implementation with a local model via OpenCode** (`start-impl.sh`).

---

## 1. What this project is
A macOS, CLI-based multi-system emulator frontend that wraps **RetroArch**. Two
entry surfaces from the `emu` launcher:
- `./emu <subcommand>` → `src/cli.py` (argparse: `systems`, `games`, `play`,
  `search`).
- `./emu` (no args) → `src/browser.py` (a large **curses TUI**, ~1900+ lines —
  the real app: game browser, RetroArch process lifecycle, auto save-states,
  channel overlays, controller mapping, settings, global search, favorites).

Config:
- `config/config.json` — paths (`retroarch_path`, `roms_dir`, `cores_dir`).
- `config/systems.json` — 11 systems → core/extensions mapping.
- `config/settings.json` — user settings (atomic writer `save_settings`).

Tests: `python3 -m pytest -q` (config `pytest.ini`, `pythonpath=.`).
`tests/conftest.py` resets `cli` caches between tests.

## 2. The two-model workflow / handoff tooling
- **`start-impl.sh <plan.md> [model]`** — opens OpenCode TUI with a small
  preloaded prompt that points at a plan file (keeps local-model context tiny),
  starts a NEW conversation, and **defaults to the local LM Studio model
  `lmstudio/qwen/qwen3.6-27b`**.
- It reloads the model fresh each run via `lms` (targeted unload + reload,
  `--parallel 1`) and **logs token usage** to `logs/usage-*.log` (per-request via
  `lms log stream --stats`) plus an `opencode stats` summary on exit.
- OpenCode config lives at `~/.config/opencode/opencode.jsonc` (LM Studio
  provider; models incl. `qwen/qwen3.6-27b`, gemma, glm, devstral).
- **Why this exists:** the 27B at 262144 context / parallel-4 OOM'd / looped on a
  36 GB Mac Studio. Mitigations: small per-handoff prompts (point at a plan file,
  don't inline), reload at `--parallel 1`, and usage logging to right-size
  context. Suggested LM Studio settings noted in README/handoff: modest loaded
  context, K/V cache Q8, prompt batch 256–512.

## 3. Plans in `docs/plans/` (the implementation backlog)
Each is a self-contained spec written for a local model to implement. Status:

- **`favorites.md`** — ✅ IMPLEMENTED & COMMITTED (favorites + global search are
  already in `src/browser.py` / `tests/`).
- **`cold-start-onboarding-phase1.md`** — PENDING. First-run wizard: gitignored
  `config/config.local.json` override, `src/setup.py` engine (platform detect,
  ROM classification by extension, copy into `roms/<system>/`, core auto-download
  from libretro buildbot), curses wizard, `emu setup` / `emu doctor`. In-repo
  roms only. Verified core URL:
  `https://buildbot.libretro.com/nightly/apple/osx/{arch}/latest/{core}.dylib.zip`.
- **`cold-start-onboarding-phase2.md`** — PENDING. Project-level RetroArch
  auto-download into `vendor/` via universal dmg
  (`.../stable/{ver}/apple/osx/universal/RetroArch_Metal.dmg`) + hdiutil +
  `xattr` de-quarantine. Risky (Gatekeeper, dmg layout).
- **`bugfix-phase1-dependencies.md`** — PENDING. `requirements.txt` (pyobjc),
  `QUARTZ_AVAILABLE` guard, startup warning, `emu doctor`. Prereq for bug-fix
  phase 4.
- **`bugfix-phase2-exit-redraw.md`** — PENDING. Bug 3 fix (dependency-free
  curses `_restore_terminal` on game exit / resize).
- **`bugfix-phase3-overlays.md`** — PENDING. Bug 2 fix: ship pre-rendered
  `assets/overlays/ch_01..99.png` via dev-only `tools/gen_overlays.py`, make
  `generate_overlays` Pillow-free, and REMOVE the Overlay Font Size setting.
- **`bugfix-phase4-channel-switching.md`** — PENDING. Bug 1 fix: gamepad +
  **listen-only** Quartz global keyboard tap (needs pyobjc + Accessibility),
  active only while a game runs, frontmost-gated to avoid double-fire.

Phases are independent unless noted; bug-fix phase 4 requires phase 1.

## 4. The three open bugs (root causes verified)
The launcher `./emu` runs **system `python3` (3.9) with NO pyobjc and NO
Pillow** — this is the core of two bugs:
- **Bug 1 — in-game channel switching dead:** keyboard `getch()` only works when
  the terminal is focused; during play RetroArch owns focus. Gamepad background
  path needs pyobjc (absent). User wants BOTH keyboard + gamepad → requires
  pyobjc + a macOS global key listener (Accessibility permission).
- **Bug 2 — green channel-number overlay gone:** `generate_overlays()` imports
  Pillow and returns early when missing → no PNGs/cfgs → overlay never enabled.
  Fix = ship pre-rendered PNGs, drop runtime Pillow.
- **Bug 3 — RetroArch red-X corrupts the TUI:** on exit the loop resets state but
  never flushes input / repaints curses. Dependency-free fix.

## 5. Key decisions already made
- Config persistence for onboarding: **gitignored `config/config.local.json`**
  merged over `config.json` (don't commit machine paths).
- Onboarding RetroArch: **project-level** install into `vendor/` (not OS-wide).
- ROMs: **in-repo `roms/<system>/` only**; import by copying from a user path,
  classify by file extension (handle ambiguous `.bin/.iso/.pbp/.zip`).
- Cores: auto-download only for systems the user imported, after a
  checkbox + y/n step.
- Overlays: **dependency-free** (shipped PNG assets); **remove Overlay Font
  Size** setting.
- Global channel keyboard listener: **listen-only** (never swallow keys).
- Entry points for setup: auto cold-start + Settings "Run Setup" + CLI
  `emu setup`/`emu doctor`.
- macOS-only scope throughout.

## 6. Conventions to follow
- Pure logic in importable modules (e.g. planned `src/setup.py`, `src/hotkeys.py`)
  so it's unit-testable without curses/network/pyobjc; keep all pyobjc/Pillow
  imports **guarded** so the test suite runs on the dependency-free interpreter.
- Atomic writers (`.tmp` + `replace`) mirror `save_settings`.
- New features get tests mirroring `tests/test_settings.py` style (tmp_path +
  monkeypatch, cache resets).
- Don't commit machine-specific or generated artifacts; `.gitignore` already
  covers `vendor/`, `config/config.local.json`, `logs/`, `overlays/`,
  `cores/*.dylib`, `roms/**`.

## 7. Current git state (at handoff)
- `main` has favorites + search implemented and committed (commit 337a711).
- Working tree: the old combined `docs/plans/bugfix-channel-overlay-exit.md` was
  split into the four `bugfix-phase*.md` files (combined deleted, four untracked)
  — not yet committed.
- Local `main` is 1 commit behind `origin/main` (fast-forwardable) — pull before
  new work.

## 8. How to continue
1. Plan/iterate here in Claude Code; write specs into `docs/plans/`.
2. Implement a phase with the local model:
   `./start-impl.sh docs/plans/<phase>.md`
3. After each phase: `python3 -m pytest -q` must pass; the suite must still run
   on system `python3` (no pyobjc/Pillow).
4. Note: `tools/gen_overlays.py` (bug-fix phase 3) needs Pillow **once** to
   generate the committed PNG assets, but the runtime TUI must not need it.

Suggested next step: implement the bug fixes (phases 2 and 3 are low-risk and
independent; phase 1 then 4 enable channel switching).
