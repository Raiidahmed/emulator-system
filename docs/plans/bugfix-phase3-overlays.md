# Bug-Fix Plan — PHASE 3: Bug 2, Dependency-Free Overlays + Remove Overlay Font Size

Part of the channel/overlay/exit bug-fix series. **Phase 3 of 4.** Independent of
the other phases (does not require pyobjc).

Sibling phases:
- `bugfix-phase1-dependencies.md` — deps + capability check.
- `bugfix-phase2-exit-redraw.md` — Bug 3 (X-window corruption).
- `bugfix-phase4-channel-switching.md` — Bug 1 (channel switching).

## The bug
The green channel-number overlay (upper-right corner during play) no longer
appears.

## Root cause (verified)
`generate_overlays()` imports Pillow and `return`s early when it's missing
(`src/browser.py:480`), so neither the PNGs nor the per-channel `.cfg` files are
written. Pillow is **not installed** in the system `python3` that runs `./emu`.
Then `write_retroarch_config` never enables the overlay because it guards on
`overlay_cfg.exists()` (`src/browser.py:166`).

## Decision
Make overlays **dependency-free** by shipping pre-rendered PNG assets and
removing the runtime Pillow path. The Overlay Font Size setting (which
regenerated PNGs at runtime via Pillow) is **removed**.

## Verify when done
```bash
python3 -m pytest -q
```

## Files
- **NEW `assets/overlays/`** — committed `blank.png` + `ch_01.png … ch_99.png`.
- **NEW `tools/gen_overlays.py`** — dev-only Pillow generator (not run at runtime).
- **EDIT `src/browser.py`** — Pillow-free `generate_overlays`; remove Overlay
  Font Size setting + its runtime regeneration.
- **EDIT `src/cli.py`** — drop `overlay_font_size` default.
- **EDIT `.gitignore`** — ensure `assets/overlays/*.png` are NOT ignored.

---

## Step 3.1 — Dev generator `tools/gen_overlays.py`
A standalone script (uses Pillow, run manually) that renders, for `ch` in a
configurable range (default 1..99): a transparent 1920×1080 PNG with the
2-digit channel number in the green VCR font (`VCR_OSD_MONO_1.001.ttf` at repo
root), saved to `assets/overlays/ch_{ch:02d}.png`, plus a 1×1 transparent
`blank.png`. Position/size/color are constants at the top (default: green
`(100,220,60,255)`, size 120, **upper-right** placement). Commit the generated
assets. This replaces the old runtime Pillow path entirely.

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

## Tests
- `generate_overlays` writes a `.cfg` for an in-range channel pointing at the
  asset PNG, and writes nothing for an out-of-range channel — asserted with a
  tmp `OVERLAY_DIR` and a stub asset dir, **importing no Pillow**.
- Settings list no longer contains a `font_size` entry; the re-derived index
  constants point at the correct rows.

## Acceptance
- With **no Pillow installed**, launching a game shows the green channel number
  on screen (fades after ~3s in `fade` mode as before).
- The Settings menu no longer shows Overlay Font Size and nothing references it.
- `python3 -m pytest -q` passes.
