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
