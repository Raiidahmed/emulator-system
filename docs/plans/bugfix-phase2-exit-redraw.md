# Bug-Fix Plan — PHASE 2: Bug 3, X-Window Corruption (dependency-free)

Part of the channel/overlay/exit bug-fix series. **Phase 2 of 4.** Independent of
the other phases and of any new dependencies — can be implemented on its own.

Sibling phases:
- `bugfix-phase1-dependencies.md` — deps + capability check.
- `bugfix-phase3-overlays.md` — Bug 2 (overlay) + remove Overlay Font Size.
- `bugfix-phase4-channel-switching.md` — Bug 1 (channel switching).

## The bug
Closing RetroArch with the red **X** leaves the TUI display corrupted.

## Root cause (verified)
On RetroArch exit the main loop resets state (`src/browser.py:1422-1429`) but
never repaints curses or flushes buffered input. After RetroArch had keyboard
focus / its own fullscreen Space, the terminal returns with stale screen content
and buffered garbage keystrokes, which `getch()` then misinterprets.

## Verify when done
```bash
python3 -m pytest -q
```

## Files
- **EDIT `src/browser.py`**

---

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

## Tests
- `_restore_terminal` calls `flushinp`, `clear`, `refresh` on a fake stdscr
  (assert via a stub recording method calls); swallows `curses.error`.

## Acceptance
- Closing RetroArch with the red **X** returns to a clean, correctly-rendered
  menu — no corruption, no stray input acted upon.
- `python3 -m pytest -q` passes.
