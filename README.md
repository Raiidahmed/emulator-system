# emulator-system

A CLI-based multi-system emulator frontend that wraps RetroArch. Lets you browse and launch games across multiple consoles from a single terminal interface using a unified config.

## Features

- Multi-system support (GB, GBA, GBC, NES, SNES, Genesis, N64, NDS, PSX, PSP, Arcade)
- JSON-based system and game configuration
- Browser interface for game selection
- RetroArch core management

## Usage

```bash
./emu list-systems
./emu list-games
./emu launch <system> <game>
```

## Structure

- `src/cli.py` — Argument parser and command dispatch
- `src/browser.py` — Game browser interface
- `config/config.json` — Paths and emulator settings
- `config/systems.json` — System-to-core mappings
- `cores/` — RetroArch cores (`.dylib`)
- `roms/` — Game ROMs organized by system (not included)
- `docs/plans/` — Self-contained implementation specs for handoff
- `start-impl.sh` — Launches opencode against a plan (defaults to a local LM Studio model)

## Implementation handoff

Larger features are written up as self-contained specs in `docs/plans/` so they
can be implemented by a local model without loading the whole repo into context.

`start-impl.sh` opens the opencode TUI with a small preloaded prompt that points
at the plan file, defaulting to whatever model your LM Studio server exposes:

```bash
# 1. In LM Studio: start the Local Server and load a coder model
#    (suggested: 8-16K context, K/V cache Q8, prompt batch 256-512)
# 2. Confirm opencode can see it:
opencode models lmstudio
# 3. Hand off a plan (press Enter to send the prefilled prompt):
./start-impl.sh docs/plans/favorites.md
```

The model can be pinned explicitly via an argument or the `OPENCODE_IMPL_MODEL`
environment variable:

```bash
./start-impl.sh docs/plans/favorites.md lmstudio/qwen2.5-coder-14b-instruct
```

## Tests

```bash
python3 -m pip install --user pytest   # one-time
python3 -m pytest -q                    # run the suite
```

Tests live in `tests/` and cover the pure logic in `src/cli.py` (settings merge,
config loading, command dispatch). Keep them green.
