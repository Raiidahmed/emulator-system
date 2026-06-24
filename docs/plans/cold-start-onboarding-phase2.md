# Implementation Plan: Cold-Start Onboarding — PHASE 2

Phase 2 of the cold-start onboarding wizard: **project-level RetroArch
auto-download**. This builds on Phase 1 (`cold-start-onboarding-phase1.md`) —
do not start Phase 2 until Phase 1 is implemented and its tests pass.

Phase 2 adds the actual RetroArch installer to the wizard's RetroArch step so a
machine with no RetroArch can be fully set up from inside the TUI, with the
binary living in the repo (`vendor/`) instead of relying on the host OS.

## Scope / platform
macOS only. Universal binary download (works on both arm64 and x86_64).

## Verified download URL (checked live)
- RetroArch: `https://buildbot.libretro.com/stable/{ver}/apple/osx/universal/RetroArch_Metal.dmg`
  (universal `.dmg`; same file for arm64 + x86_64). Default `ver = 1.21.0`.

## Verify when done
```bash
python3 -m pytest -q
```

## Files
- **EDIT `src/setup.py`** — `download_retroarch()` + URL builder + constants.
- **EDIT `src/browser.py`** — RetroArch step now offers the download.
- **EDIT `tests/test_setup.py`** — command-construction tests (mocked).

---

## Step 1 — Constants + `download_retroarch` (`src/setup.py`)

Add constants near the existing Phase 1 ones:
```python
RA_VERSION = "1.21.0"
RA_URL = "https://buildbot.libretro.com/stable/{ver}/apple/osx/universal/RetroArch_Metal.dmg"
```

Add the installer:
```python
def retroarch_download_url(ver=RA_VERSION):
    return RA_URL.format(ver=ver)

def download_retroarch(runner=subprocess.run, ver=RA_VERSION):
    """Download the universal macOS dmg, mount it, copy RetroArch.app into
    vendor/, detach, and clear the quarantine flag so it can launch.
    Returns RA_BIN."""
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    url = retroarch_download_url(ver)
    with tempfile.NamedTemporaryFile(suffix=".dmg", delete=False) as tf:
        dmg = Path(tf.name)
    mount = Path(tempfile.mkdtemp(prefix="emu_ra_"))
    try:
        runner(["curl", "-fL", "-o", str(dmg), url], check=True)
        runner(["hdiutil", "attach", str(dmg), "-nobrowse", "-quiet",
                "-mountpoint", str(mount)], check=True)
        # RetroArch.app sits at the root of the mounted volume; glob as a
        # fallback in case the name differs.
        src_app = mount / "RetroArch.app"
        if not src_app.exists():
            apps = list(mount.glob("*.app"))
            if not apps:
                raise RuntimeError("RetroArch.app not found in mounted dmg")
            src_app = apps[0]
        dst_app = VENDOR_DIR / "RetroArch.app"
        if dst_app.exists():
            shutil.rmtree(dst_app)
        shutil.copytree(src_app, dst_app)
    finally:
        runner(["hdiutil", "detach", str(mount), "-quiet"], check=False)
        try: dmg.unlink()
        except OSError: pass
    # clear Gatekeeper quarantine so subprocess launch isn't blocked
    runner(["xattr", "-dr", "com.apple.quarantine", str(dst_app)], check=False)
    return RA_BIN
```

(`VENDOR_DIR`, `RA_BIN`, `tempfile`, `shutil`, `subprocess`, `Path` are already
present from Phase 1.)

## Step 2 — Wire into the wizard (`src/browser.py`)

In `onboarding_flow`'s RetroArch step, replace the Phase 1 "install manually"
message with an actual download offer:

- If `retroarch_installed()`, continue.
- Else `confirm("Download RetroArch into the project? (~220 MB) [y/n]")`:
  - On yes: `_run_with_animation(stdscr, "Downloading RetroArch...",
    download_retroarch)` → verify `RA_BIN.exists()`.
    - Success: stash `retroarch_path = str(RA_BIN)` into the config-local payload
      that the Done step writes.
    - Failure: `_show_center_message` with the error; offer retry/skip.
  - On no: continue without it (user can re-run setup later).

Import `download_retroarch` (and `RA_BIN`) from `src.setup`.

## Step 3 — Tests (`tests/test_setup.py`)
- `retroarch_download_url` formats the version.
- `download_retroarch` with a fake `runner` (records argv) asserts the ordered
  commands are constructed — `curl` → `hdiutil attach` → `hdiutil detach` →
  `xattr` — and never executes them. Monkeypatch `shutil.copytree` (and
  `tempfile.mkdtemp` if needed) so no real app/filesystem is required, and
  monkeypatch `Path.exists` for the mounted `src_app` so the copy path is taken.

## Risks / verify at implementation
1. **dmg internal layout** — confirm the mounted volume contains `RetroArch.app`
   at its root; the `glob("*.app")` fallback above covers a differing volume
   name.
2. **Gatekeeper / quarantine** — the `xattr -dr com.apple.quarantine` step is
   required so the downloaded app launches via `subprocess`; a first manual
   launch may still prompt on some systems.
3. **Download size/time** (~220 MB) — spinner + a clear label; handle partial
   downloads (cleanup on failure) and offer retry.
4. **Disk space / permissions** under `vendor/`.

## Acceptance criteria
1. On a machine with no RetroArch, the wizard downloads and installs it into
   `vendor/RetroArch.app`, points `config.local.json` `retroarch_path` at the
   project binary, and a game launches afterward.
2. `python3 -m pytest -q` passes including the new Phase 2 tests.
