# Implementation Plan: Cold-Start Onboarding — PHASE 1

Phase 1 of the cold-start onboarding wizard. Guides a new user, on first launch,
through a **self-contained, project-level** setup. This phase covers everything
**except** the RetroArch auto-download (that is Phase 2, see
`cold-start-onboarding-phase2.md`).

Phase 1 delivers:
1. A gitignored config override so machine-specific paths never touch the
   committed config.
2. **ROM import** — copy from a user-selected source path into `roms/<system>/`,
   classifying each file by extension.
3. **Core auto-download** into `cores/` for exactly the systems the user
   imported (checkbox preview → y/n confirm).
4. Entry points: **auto cold-start**, **Settings → Run Setup**, CLI
   **`emu setup`** / **`emu doctor`**.

Phase 1 assumes RetroArch is already present (project-level or detectable) or the
user installs it manually; the wizard still imports ROMs/cores and wires up all
entry points. Ship and verify Phase 1 before starting Phase 2.

## Scope / platform
macOS only (matches the existing `.app` / `.dylib` / `open` / `osascript`
codebase). On other OSes the wizard shows a "manual setup required" message and
skips downloads. Linux/Windows = future.

## Verified download URL (checked live)
- Cores: `https://buildbot.libretro.com/nightly/apple/osx/{arch}/latest/{core}.dylib.zip`
  where `{arch}` is `arm64` or `x86_64` and `{core}` matches the `core` field in
  `config/systems.json` (e.g. `snes9x_libretro`). Returns a zip containing
  `{core}.dylib`.

## Verify when done
```bash
python3 -m pytest -q
```

## Files
- **NEW `src/setup.py`** — pure logic + IO (no curses).
- **EDIT `src/cli.py`** — merge gitignored override; `save_config_local`; add
  `emu setup` + `emu doctor`.
- **EDIT `src/browser.py`** — curses wizard screens + `Run Setup` Settings entry
  + cold-start trigger.
- **NEW `tests/test_setup.py`** — pure-logic tests (network mocked).
- **EDIT `.gitignore`** — `vendor/` and `config/config.local.json` (already
  added).

---

## Step 1 — Gitignored config override (`src/cli.py`)

Add a local override merged OVER `config.json`:

```python
LOCAL_CONFIG_PATH = CONFIG_DIR / "config.local.json"

def get_config():
    global _config_cache
    if _config_cache is None:
        base = load_json(CONFIG_DIR / "config.json")
        try:
            local = load_json(LOCAL_CONFIG_PATH)
        except (FileNotFoundError, json.JSONDecodeError):
            local = {}
        if isinstance(local, dict):
            base.update(local)          # local keys win (paths, flags)
        _config_cache = base
    return _config_cache

def save_config_local(data):
    """Atomic write of config/config.local.json; invalidates the cache."""
    global _config_cache
    _config_cache = None
    tmp = LOCAL_CONFIG_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(LOCAL_CONFIG_PATH)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

def load_config_local():
    try:
        data = load_json(LOCAL_CONFIG_PATH)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
```

Note: `config.json` stays committed/unchanged; machine-specific values
(`retroarch_path`, `onboarding_complete`, etc.) live only in the gitignored
override. `tests/conftest.py` already resets `cli._config_cache`.

## Step 2 — `src/setup.py` (pure + IO, no curses)

Imports: `platform, shutil, subprocess, zipfile, tempfile, os`, `from pathlib
import Path`, and `from src.cli import get_config, get_systems, ROOT,
save_config_local, load_config_local`. Import `scan_games` lazily inside the
functions that need it (avoids a circular import at module load).

Constants:
```python
VENDOR_DIR = ROOT / "vendor"
RA_BIN = VENDOR_DIR / "RetroArch.app" / "Contents" / "MacOS" / "RetroArch"
CORES_DIR = ROOT / "cores"
ROMS_DIR = ROOT / "roms"
CORE_URL = "https://buildbot.libretro.com/nightly/apple/osx/{arch}/latest/{core}.dylib.zip"
```

Pure detection:
```python
def detect_platform():
    sysname = platform.system()                 # "Darwin"
    machine = platform.machine()                # "arm64" | "x86_64"
    arch = "arm64" if machine == "arm64" else "x86_64"
    return {"system": sysname, "machine": machine,
            "arch": arch, "supported": sysname == "Darwin"}

def retroarch_installed():
    cfg = get_config()
    p = Path(cfg.get("retroarch_path", ""))
    return p.exists() and os.access(p, os.X_OK)

def roms_present(systems=None):
    from src.browser import scan_games
    systems = systems or get_systems()
    return any(scan_games(k, info) for k, info in systems.items())

def needs_onboarding():
    if load_config_local().get("onboarding_complete"):
        return False
    return not retroarch_installed() or not roms_present()
```

Pure ROM classification:
```python
def build_extension_map(systems=None):
    systems = systems or get_systems()
    out = {}
    for key, info in systems.items():
        for ext in info["extensions"]:
            out.setdefault(ext.lower(), []).append(key)
    return out                                   # {".bin": ["genesis","psx"], ...}

def scan_source_for_roms(src_dir, systems=None):
    """Walk src_dir; return (matched, ambiguous).
    matched:   {system_key: [Path, ...]}  (unambiguous extensions)
    ambiguous: {ext: [Path, ...]}         (extension maps to >1 system)
    """
    systems = systems or get_systems()
    ext_map = build_extension_map(systems)
    matched, ambiguous = {}, {}
    for f in Path(src_dir).rglob("*"):
        if not f.is_file():
            continue
        cands = ext_map.get(f.suffix.lower())
        if not cands:
            continue
        if len(cands) == 1:
            matched.setdefault(cands[0], []).append(f)
        else:
            ambiguous.setdefault(f.suffix.lower(), []).append(f)
    return matched, ambiguous

def copy_roms(plan, dest=ROMS_DIR):
    """plan: {system_key: [Path, ...]} -> copies into dest/<system>/.
    Returns {system_key: count}. Source files are left untouched."""
    counts = {}
    for key, paths in plan.items():
        target = Path(dest) / key
        target.mkdir(parents=True, exist_ok=True)
        for p in paths:
            shutil.copy2(p, target / p.name)
        counts[key] = len(paths)
    return counts
```

Core selection + download:
```python
def suggested_cores(systems_present, systems=None):
    systems = systems or get_systems()
    seen, out = set(), []
    for key in systems_present:
        core = systems[key]["core"]
        if core not in seen:
            seen.add(core)
            out.append((key, core))
    return out                                   # [(system_key, core_name), ...]

def core_download_url(core, arch):
    return CORE_URL.format(arch=arch, core=core)

def download_core(core, arch, dest=CORES_DIR, runner=subprocess.run):
    """curl the {core}.dylib.zip and unzip {core}.dylib into dest."""
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    url = core_download_url(core, arch)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
        zpath = Path(tf.name)
    try:
        runner(["curl", "-fL", "-o", str(zpath), url], check=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(dest)
    finally:
        try: zpath.unlink()
        except OSError: pass
    return dest / f"{core}.dylib"
```

`download_core` takes an injectable `runner` so tests assert the constructed
`curl` command **without** hitting the network.

## Step 3 — Curses wizard (`src/browser.py`)

Add reusable input helpers (generalize the existing `search` text-input and
`confirm_save_quit` patterns):
- `prompt_text(stdscr, title, initial="", hint=None) -> str | None`
  Live text entry (printable append, backspace, Enter=submit, Esc=None).
- `run_checklist(stdscr, title, items, preselected) -> set | None`
  `items` = list of `(label, key)`; Space toggles, Enter confirms, Esc=None.
- `confirm(stdscr, msg) -> bool`  (reuse `confirm_save_quit` shape: y/n).
- Reuse `_run_with_animation(stdscr, label, work_fn)` for downloads/copies and
  `_show_center_message` for results.

Wizard driver `onboarding_flow(stdscr) -> bool` (steps):
1. **Welcome** — show `detect_platform()` summary; Enter to begin, `s` to skip.
   If `not supported`: show manual-setup message and return False.
2. **RetroArch (Phase 1)** — if `retroarch_installed()`, continue. Else show a
   message: "RetroArch not found. Install it, or run setup again after
   Phase 2 adds auto-download." Offer `prompt_text` to enter a path to an
   existing binary (optional), or continue without it.
3. **ROMs** — `prompt_text("Path to your ROMs folder")` →
   `scan_source_for_roms` → for each ambiguous extension, a small select screen
   picks the target system (default = first candidate) → merge into the plan →
   show a per-system preview → `confirm` → `copy_roms` behind a spinner →
   `_show_center_message` with counts.
4. **Cores** — `suggested_cores(list(plan))` → `run_checklist` (all pre-checked,
   skip cores already in `cores/`) → `confirm("Download N cores? [y/n]")` →
   download each behind a spinner (collect failures, report at end).
5. **Done** — `save_config_local({... , "onboarding_complete": True})` (preserve
   any existing keys; set `retroarch_path` if it was located), then
   `_show_center_message("Setup complete")` and return True.

**Cold-start trigger:** at the top of `run()` (after `systems = get_systems()`,
before the `while True:` loop):
```python
    from src.setup import needs_onboarding
    if needs_onboarding():
        onboarding_flow(stdscr)
        systems = get_systems()      # re-read in case paths changed
```
Skipping sets an in-memory flag so it doesn't re-trigger within the session.

**Settings entry:** add `("Run Setup", "run_setup")` to the Settings `items`
list; handler calls `onboarding_flow(stdscr)` then returns to Settings with
`rebuild_items = True`.

## Step 4 — CLI (`src/cli.py`)

- `sub.add_parser("setup", help="Run the first-time setup wizard")` →
  `from src.browser import onboarding_flow; import curses;
  curses.wrapper(onboarding_flow)`.
- `sub.add_parser("doctor", help="Report setup readiness")` → non-curses report:
  RetroArch present (+path), ROM counts per system, cores present/missing per
  system, resolved paths. Exit code `1` if not ready, else `0`.

## Step 5 — `.gitignore`
Already contains (added with this plan):
```
vendor/
config/config.local.json
```
(`cores/*.dylib` and `roms/**` are already ignored.)

## Step 6 — Tests (`tests/test_setup.py`, network mocked)
- `detect_platform` returns expected arch mapping.
- `build_extension_map` groups multi-system extensions (`.bin`, `.iso`).
- `scan_source_for_roms` (tmp dir): unambiguous → matched; `.bin`/`.iso` →
  ambiguous.
- `copy_roms` (tmp dirs): files land in `dest/<system>/`, counts correct,
  source untouched.
- `suggested_cores` dedupes shared cores (gb/gbc both `gambatte_libretro`).
- `core_download_url` formats arch + core.
- `download_core` with a fake `runner` (records argv) + a pre-made zip fixture →
  asserts the `curl` command and that the `.dylib` is extracted; never hits
  network.
- `needs_onboarding` true/false via monkeypatched `retroarch_installed` /
  `roms_present` / override flag.
- `save_config_local` + merged `get_config` round-trip; no `.tmp` left behind.

## Acceptance criteria
1. `emu` with empty `roms/` triggers the wizard; importing from a chosen path
   copies ROMs into `roms/<system>/` and downloads matching cores after a
   checkbox + y/n step, then boots into the TUI.
2. Ambiguous extensions are resolved via the picker; unambiguous ones are
   auto-classified.
3. `config/config.local.json` (gitignored) holds the override + flag;
   `config.json` is unchanged.
4. `Settings → Run Setup`, `emu setup`, and `emu doctor` all work.
5. `python3 -m pytest -q` passes including `tests/test_setup.py`.

## Out of scope (Phase 1)
- RetroArch auto-download (Phase 2).
- Non-macOS install flows (show a manual-setup message instead).
- Moving ROMs (always copy; source untouched).
- BIOS files for systems that need them (PSX/PSP) — note in `emu doctor` output
  as a future enhancement, do not fetch.
- Per-core version pinning (cores use `latest`).
