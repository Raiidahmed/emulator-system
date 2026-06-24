# Implementation Plan: Fix Channel Switching, Overlay, and X-Window Corruption (phased)

Fixes three bugs in the TUI, plus the underlying dependency/interpreter problem:

- **Bug 1** — in-game channel up/down buttons don't load the next/prev game,
  because once RetroArch is focused the terminal stops receiving keys.
- **Bug 2** — the green channel-number overlay no longer appears.
- **Bug 3** — closing RetroArch with the red **X** corrupts the TUI display.

## Root causes (verified)
- The launcher `./emu` runs **system `python3`** (3.9) which has **no pyobjc and
  no Pillow**. So controller/global-key features silently no-op (Bug 1) and
  overlay generation aborts (Bug 2).
- `generate_overlays()` imports Pillow and `return`s early when it's missing
  (`src/browser.py:480`), so neither the PNGs nor the per-channel `.cfg` files
  are written; `write_retroarch_config` then never enables the overlay because
  it guards on `overlay_cfg.exists()` (`src/browser.py:166`).
- Keyboard channel switching relies on `stdscr.getch()` (`src/browser.py:1611`),
  which only receives keys while the terminal is focused — not during play.
- On RetroArch exit the loop resets state (`src/browser.py:1422-1429`) but never
  repaints curses or flushes buffered input, so stale screen content + garbage
  keystrokes from the focus/Space change corrupt the UI (Bug 3).

## Decisions baked into this plan
- **Overlay Font Size** setting is **removed** (overlay assets are now shipped;
  size is a build-time concern).
- The global keyboard listener is **listen-only** — it never swallows the keys,
  so RetroArch and normal typing are unaffected.
- macOS-only throughout (consistent with the codebase).

## Phasing
1. **Phase 1** — Dependencies + capability check (`requirements.txt`, startup
   guard, doctor report). Prerequisite for Bug 1.
2. **Phase 2** — Bug 3: dependency-free curses restore on game exit.
3. **Phase 3** — Bug 2: dependency-free overlays + remove Overlay Font Size.
4. **Phase 4** — Bug 1: gamepad re-enable + Quartz global keyboard listener.

Each phase ends green:
```bash
python3 -m pytest -q
```

---
---

# PHASE 1 — Dependencies & capability check

## Files
- **NEW `requirements.txt`**
- **EDIT `src/browser.py`** — capability flags + startup warning.
- **EDIT `src/cli.py`** — extend/﻿add a `doctor`-style readiness check (optional
  if the onboarding `emu doctor` already exists; otherwise add a minimal one).
- **EDIT `README.md`** — install step.

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

## Phase 1 tests
- `emu doctor` runs and prints a report on an interpreter without pyobjc
  (capabilities reported as missing, no crash).
- Importing `src.browser` still succeeds without pyobjc/Pillow (existing suite).

## Phase 1 acceptance
- Fresh interpreter without deps: TUI launches, shows the warning, no crash.
- After installing requirements, capability flags report true.

---
---

# PHASE 2 — Bug 3: dependency-free TUI restore on game exit

## Files
- **EDIT `src/browser.py`**

## Step 2.1 — `_restore_terminal` helper
Add near the other curses helpers:
```python
def _restore_terminal(stdscr):
    """Flush stray input and force a clean full repaint after returning from
    RetroArch (focus / fullscreen-Space changes leave the screen dirty)."""
    try:
        curses.flushinp()
        curses.curs_set(0)
        stdscr.redrawwin()
        stdscr.clear()
        stdscr.refresh()
    except curses.error:
        pass
```

## Step 2.2 — Call it on game exit
In the main loop's `_game_just_exited` branch (`src/browser.py:1424-1429`), after
resetting state, call `_restore_terminal(stdscr)`.

## Step 2.3 — Call it after returning from RetroArch flows
After `launch_with_loading(...)` returns and after
`shutdown_with_animation(...)`, call `_restore_terminal(stdscr)` so the menu is
always repainted cleanly.

## Step 2.4 — Handle terminal resize
In the input switch, add an early branch:
```python
if key == curses.KEY_RESIZE:
    _restore_terminal(stdscr)
    rebuild_items = True
    continue
```

## Step 2.5 — De-race the refocus
`_refocus_terminal` activates the terminal from a delayed daemon thread
(`src/browser.py:364-383`). Leave the activation, but ensure the **main-thread**
repaint (`_restore_terminal`) happens on exit independent of that thread (it
already will via Step 2.2). No locking needed.

## Phase 2 tests
- `_restore_terminal` calls `flushinp`, `clear`, `refresh` on a fake stdscr
  (assert via a stub recording method calls); swallows `curses.error`.

## Phase 2 acceptance
- Closing RetroArch with the red **X** returns to a clean, correctly-rendered
  menu — no corruption, no stray input acted upon.

---
---

# PHASE 3 — Bug 2: dependency-free overlays + remove Overlay Font Size

## Files
- **NEW `assets/overlays/`** — committed `blank.png` + `ch_01.png … ch_99.png`.
- **NEW `tools/gen_overlays.py`** — dev-only Pillow generator (not run at runtime).
- **EDIT `src/browser.py`** — Pillow-free `generate_overlays`; remove Overlay
  Font Size setting + its runtime regeneration.
- **EDIT `.gitignore`** — ensure `assets/overlays/*.png` are NOT ignored
  (the runtime `overlays/` dir stays ignored).

## Step 3.1 — Dev generator `tools/gen_overlays.py`
A standalone script (uses Pillow, run manually) that renders, for `ch` in a
configurable range (default 1..99): a transparent 1920×1080 PNG with the
2-digit channel number in the green VCR font, saved to `assets/overlays/
ch_{ch:02d}.png`, plus a 1×1 transparent `blank.png`. Position/size/color are
constants at the top (default: green `(100,220,60,255)`, size 120, **upper-right**
placement). Commit the generated assets. This replaces the old runtime Pillow
path entirely.

## Step 3.2 — Rewrite `generate_overlays` (Pillow-free) in `src/browser.py`
- New constant: `ASSET_OVERLAY_DIR = ROOT / "assets" / "overlays"`.
- The runtime writable dir `OVERLAY_DIR = ROOT / "overlays"` still holds the
  generated `.cfg` files.
- New `generate_overlays(channel_map)`:
  - No Pillow import. For each needed channel that has a shipped
    `ASSET_OVERLAY_DIR / f"ch_{ch:02d}.png"`, write
    `OVERLAY_DIR / f"ch_{ch:02d}.cfg"` using the existing two-page format
    (`src/browser.py:505-515`) but referencing the PNGs by **absolute path** in
    the asset dir (so the cfg in `overlays/` can point at `assets/overlays/`):
    ```
    overlays = 2
    overlay0_overlay = {ASSET_OVERLAY_DIR}/ch_{ch:02d}.png
    overlay0_full_screen = true
    overlay0_descs = 0
    overlay1_overlay = {ASSET_OVERLAY_DIR}/blank.png
    overlay1_full_screen = true
    overlay1_descs = 0
    ```
  - Channels without a shipped PNG (e.g. > 99) are skipped → no overlay for that
    game (graceful).
- `write_retroarch_config` is unchanged (still guards on `overlay_cfg.exists()`,
  `src/browser.py:163-173`).

## Step 3.3 — Remove Overlay Font Size setting
- Delete the `("Overlay Font Size  ...", "font_size")` item from the Settings
  list (`src/browser.py:1472`).
- Remove the `font_size` handling: the `setting_detail`/`font_size` render branch
  (`src/browser.py:1458-1460`), the `draw_font_size` function, `FONT_SIZE_OPTIONS`,
  the `font_size` enter-handler and key handling (the `_clear_overlay_pngs()` +
  runtime `generate_overlays` regeneration), and `_clear_overlay_pngs`.
- Drop `overlay_font_size` from `SETTINGS_DEFAULTS` in `src/cli.py` (and ignore
  it if present in an existing `settings.json` — `_merge_defaults` already
  tolerates extra keys).
- Fix any now-stale cursor indices in the Settings menu (the constants
  `SETTINGS_INDEX_MENU_HOTKEY` / `SETTINGS_INDEX_CONTROL_MAPPING` and the
  font-size cursor positions shift up by one — re-derive them after removing the
  row).

## Step 3.4 — `.gitignore`
Current `overlays/` ignore stays. Ensure the committed assets are tracked, e.g.:
```
overlays/
assets/overlays/*.tmp
```
(`assets/overlays/*.png` must be committed; do not ignore them.)

## Phase 3 tests
- `generate_overlays` writes a `.cfg` for an in-range channel pointing at the
  asset PNG, and writes nothing for an out-of-range channel — asserted with a
  tmp `OVERLAY_DIR` and a stub asset dir, **importing no Pillow**.
- Settings list no longer contains a `font_size` entry; the re-derived index
  constants point at the correct rows.

## Phase 3 acceptance
- With **no Pillow installed**, launching a game shows the green channel number
  on screen (fades after ~3s in `fade` mode as before).
- The Settings menu no longer shows Overlay Font Size and nothing references it.

---
---

# PHASE 4 — Bug 1: in-game channel switching (gamepad + listen-only keyboard)

Requires Phase 1 (pyobjc). Adds a macOS global input layer active only while a
game is running.

## Files
- **NEW `src/hotkeys.py`** — global keyboard listener (Quartz) + permission
  check + thread-safe action queue. All pyobjc use is guarded.
- **EDIT `src/browser.py`** — start/stop the listener with the game; drain its
  queue in the main loop; pure key→delta mapping helper.

## Step 4.1 — Pure mapping helper (`src/browser.py`)
Factor the existing keyboard channel logic (`src/browser.py:1611-1620`) into a
pure function so it's testable without Quartz:
```python
def channel_delta_for_key(ra_key, settings):
    hk = settings.get("hotkeys", {})
    if ra_key and ra_key == hk.get("channel_up_keyboard", HOTKEY_DEFAULTS["channel_up_keyboard"]):
        return 1
    if ra_key and ra_key == hk.get("channel_down_keyboard", HOTKEY_DEFAULTS["channel_down_keyboard"]):
        return -1
    return 0
```
Use it in both the existing in-focus `getch` path and the new global path.

## Step 4.2 — `src/hotkeys.py` (guarded Quartz event tap, listen-only)
- `accessibility_trusted(prompt=False)` → wraps `AXIsProcessTrusted()` /
  `AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt})`
  (from `Quartz`/`HIServices`); returns False if Quartz unavailable.
- A `KeyTap` class:
  - `start()` — if `QUARTZ_AVAILABLE` and trusted, create a **listen-only**
    `CGEventTap` for `kCGEventKeyDown` with `kCGEventTapOptionListenOnly` (never
    modifies/swallows events), add it to the run loop, and enable it. The
    callback maps the keycode → character, and if it matches a configured
    channel key pushes `+1`/`-1` onto a thread-safe `queue.Queue`.
  - `poll()` — non-blocking drain returning the latest pending delta (collapse
    repeats), or `0`.
  - `stop()` — disable/remove the tap.
  - The run loop is serviced by the existing `_pump_controller_events` pump in
    the main loop tick (Phase 1 keeps pyobjc importable). If GameController
    isn't loaded, add a minimal `CFRunLoopRunInMode` pump call when the tap is
    active.
- Keycode→char: use a small `kVK_*`/keycode map for digits and common keys, or
  read the unicode via `CGEventKeyboardGetUnicodeString`. Map to the same
  RetroArch key strings the settings use (e.g. `"1"`, `"2"`).

## Step 4.3 — Wire into the game lifecycle (`src/browser.py`)
- Construct a module-level `KeyTap` instance.
- In `start_game` (or `launch_with_loading` success path) call `keytap.start()`;
  in `stop_current_game` / `_cleanup_active` call `keytap.stop()`. So the tap is
  inert in menus.
- **Frontmost gate (avoid double-fire):** only act on `keytap.poll()` when a game
  is active AND RetroArch is frontmost. Reuse `_capture_focus`/`_terminal_app`:
  if the terminal is frontmost, the normal `getch` path handles the key; if
  RetroArch is frontmost, the global tap handles it. Check frontmost cheaply
  (cache for ~0.3s) before consuming a queued delta.
- In the main loop's `key == -1` block (near the gamepad poll,
  `src/browser.py:1557-1560`), add:
  ```python
  if _active["proc"] and _active["proc"].poll() is None:
      delta = keytap.poll()
      if delta and _retroarch_frontmost() and _launch_channel_delta(delta):
          continue
  ```
- Keep the existing in-focus keyboard path (`src/browser.py:1611-1620`) for when
  the terminal itself is focused; refactor it to use `channel_delta_for_key`.

## Step 4.4 — Gamepad
With pyobjc installed, `_poll_channel_hotkeys_gamepad` already works in the
background. No code change beyond Phase 1; document mapping
`channel_up_gamepad` / `channel_down_gamepad` (currently `"nul"`).

## Step 4.5 — Permission UX
On first game launch, if `QUARTZ_AVAILABLE` but not
`accessibility_trusted()`, call it once with `prompt=True` and show a brief
`_show_center_message` explaining that keyboard channel switching needs
Accessibility (gamepad still works meanwhile). Don't block the launch.

## Phase 4 tests
- `channel_delta_for_key` returns `+1`/`-1`/`0` for configured up/down/other
  keys — pure, no Quartz.
- `KeyTap.poll()` drains queued deltas and collapses repeats (inject items into
  its queue directly; do **not** start a real tap).
- All Quartz/pyobjc code paths are import-guarded so the suite passes on an
  interpreter without pyobjc.

## Phase 4 risks
- Global key capture needs **Accessibility permission** and pyobjc; event-tap
  lifecycle + run-loop pumping is the most fragile piece.
- Frontmost detection via osascript has latency; cache it and accept a tiny
  delay. Listen-only means RetroArch also sees `1`/`2` — acceptable per decision.

## Phase 4 acceptance
- After `pip install --user -r requirements.txt` and granting Accessibility:
  while a game is focused, the keyboard channel keys **and** a mapped gamepad
  both load the next/prev game.
- With pyobjc absent, the TUI still runs (channel switching disabled, warned).

---
---

## Out of scope
- Non-macOS input handling.
- Swallowing/remapping the channel keys inside RetroArch (listen-only by
  decision).
- Per-game overlay customization (size/position is a build-time concern via
  `tools/gen_overlays.py`).

## Final acceptance (all phases)
1. `pip install --user -r requirements.txt`, then `./emu`: in-game keyboard and
   gamepad channel up/down load the next/prev game while RetroArch is focused.
2. The green channel number shows on launch with **no Pillow installed**.
3. Closing RetroArch with the red **X** returns to a clean menu.
4. Overlay Font Size is gone from Settings.
5. `python3 -m pytest -q` passes; the suite still runs on an interpreter without
   pyobjc/Pillow.
