# Bug-Fix Plan — PHASE 1: Dependencies & Capability Check

Part of the channel/overlay/exit bug-fix series. **Phase 1 of 4.** This phase is
a prerequisite for Phase 4 (in-game channel switching needs pyobjc).

Sibling phases:
- `bugfix-phase2-exit-redraw.md` — Bug 3 (X-window corruption).
- `bugfix-phase3-overlays.md` — Bug 2 (overlay) + remove Overlay Font Size.
- `bugfix-phase4-channel-switching.md` — Bug 1 (channel switching).

## Background (root causes, verified)
- The launcher `./emu` runs **system `python3`** (3.9) which has **no pyobjc and
  no Pillow**. So controller/global-key features silently no-op (Bug 1) and
  overlay generation aborts (Bug 2).
- Bug 3 (exit corruption) is independent of dependencies (handled in Phase 2).

## Decisions (apply across all phases)
- Overlay Font Size setting is **removed** (Phase 3).
- The global keyboard listener is **listen-only** (Phase 4).
- macOS-only throughout.

## Verify when done
```bash
python3 -m pytest -q
```

## Files
- **NEW `requirements.txt`**
- **EDIT `src/browser.py`** — capability flags + startup warning.
- **EDIT `src/cli.py`** — add/extend a `doctor` readiness check.
- **EDIT `README.md`** — install step.

---

## Step 1.1 — `requirements.txt`
```
pyobjc-core>=10
pyobjc-framework-Cocoa>=10
pyobjc-framework-Quartz>=10
pyobjc-framework-GameController>=10
```
Pillow is intentionally **not** here (only `tools/gen_overlays.py` needs it; see
Phase 3). Install: `python3 -m pip install --user -r requirements.txt`.

## Step 1.2 — Capability flags (`src/browser.py`)
Near the existing `Foundation`/`objc` guard (`src/browser.py:11-41`), add a
Quartz import guard:
```python
try:
    import Quartz
except ImportError:
    Quartz = None

QUARTZ_AVAILABLE = Quartz is not None
```
`GAMECONTROLLER_AVAILABLE` already exists. Do **not** hard-import any of these at
module top beyond the guarded blocks, so the suite still runs on an interpreter
without pyobjc.

## Step 1.3 — Startup warning
In `run(stdscr)` after `systems = get_systems()`, if neither
`QUARTZ_AVAILABLE` nor `GAMECONTROLLER_AVAILABLE`, set a one-shot footer/notice
("In-game channel switching disabled — run `pip install --user -r
requirements.txt`"). Non-fatal; the TUI continues.

## Step 1.4 — Doctor report (`src/cli.py`)
Add (or extend) `emu doctor` to print: interpreter path (`sys.executable`),
pyobjc present (Quartz + GameController), Accessibility permission granted
(Phase 4 helper, guarded), overlay assets present (Phase 3), RetroArch path.
Exit non-zero if channel switching can't work. Keep it import-guarded so it runs
even without pyobjc.

## Step 1.5 — README
Add a "Requirements" note: `python3 -m pip install --user -r requirements.txt`
for controller + in-game channel switching.

## Tests
- `emu doctor` runs and prints a report on an interpreter without pyobjc
  (capabilities reported as missing, no crash).
- Importing `src.browser` still succeeds without pyobjc/Pillow (existing suite).

## Acceptance
- Fresh interpreter without deps: TUI launches, shows the warning, no crash.
- After installing requirements, capability flags report true.
- `python3 -m pytest -q` passes.
