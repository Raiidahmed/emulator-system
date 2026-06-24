# Bug-Fix Plan — PHASE 4: Bug 1, In-Game Channel Switching (gamepad + listen-only keyboard)

Part of the channel/overlay/exit bug-fix series. **Phase 4 of 4.**
**Requires Phase 1** (`bugfix-phase1-dependencies.md`) — pyobjc must be installed
and the `QUARTZ_AVAILABLE` flag must exist.

Sibling phases:
- `bugfix-phase1-dependencies.md` — deps + capability check (prerequisite).
- `bugfix-phase2-exit-redraw.md` — Bug 3 (X-window corruption).
- `bugfix-phase3-overlays.md` — Bug 2 (overlay) + remove Overlay Font Size.

## The bug
In-game channel up/down buttons don't load the next/prev game: once RetroArch is
focused the terminal stops receiving keystrokes, so the TUI's `getch()` never
sees them (`src/browser.py:1611-1620`). The gamepad background path needs pyobjc
(absent until Phase 1).

## Decision
Support **both** keyboard and gamepad. The keyboard listener is **listen-only**
(it never swallows the keys). Active only while a game is running.

## Verify when done
```bash
python3 -m pytest -q
```

## Files
- **NEW `src/hotkeys.py`** — global keyboard listener (Quartz) + permission
  check + thread-safe action queue. All pyobjc use is guarded.
- **EDIT `src/browser.py`** — start/stop the listener with the game; drain its
  queue in the main loop; pure key→delta mapping helper.

---

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
    the main loop tick. If GameController isn't loaded, add a minimal
    `CFRunLoopRunInMode` pump call when the tap is active.
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
On first game launch, if `QUARTZ_AVAILABLE` but not `accessibility_trusted()`,
call it once with `prompt=True` and show a brief `_show_center_message`
explaining that keyboard channel switching needs Accessibility (gamepad still
works meanwhile). Don't block the launch.

## Tests
- `channel_delta_for_key` returns `+1`/`-1`/`0` for configured up/down/other
  keys — pure, no Quartz.
- `KeyTap.poll()` drains queued deltas and collapses repeats (inject items into
  its queue directly; do **not** start a real tap).
- All Quartz/pyobjc code paths are import-guarded so the suite passes on an
  interpreter without pyobjc.

## Risks
- Global key capture needs **Accessibility permission** and pyobjc; event-tap
  lifecycle + run-loop pumping is the most fragile piece.
- Frontmost detection via osascript has latency; cache it and accept a tiny
  delay. Listen-only means RetroArch also sees `1`/`2` — acceptable per decision.

## Acceptance
- After `pip install --user -r requirements.txt` and granting Accessibility:
  while a game is focused, the keyboard channel keys **and** a mapped gamepad
  both load the next/prev game.
- With pyobjc absent, the TUI still runs (channel switching disabled, warned).
- `python3 -m pytest -q` passes.
