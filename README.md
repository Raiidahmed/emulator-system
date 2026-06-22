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

## Tests

```bash
python3 -m pip install --user pytest   # one-time
python3 -m pytest -q                    # run the suite
```

Tests live in `tests/` and cover the pure logic in `src/cli.py` (settings merge,
config loading, command dispatch). They are also the **oracle** for the
unattended local agent below — keep them green.

## Unattended local agent

Hand a scoped task to a local Qwen model in LM Studio and let it iterate against
the test suite until green — no API calls, no babysitting. `local-agent.sh`
wraps Aider in a run-tests / feed-failures-back loop (Aider's `--auto-test`
doesn't iterate in non-interactive mode; the shell loop does).

**Prereqs**

```bash
lms server start                                   # LM Studio API server
lms ps                                             # confirm the coder model is loaded
export LM_STUDIO_API_BASE=http://localhost:1234/v1
```

**Run it**

```bash
# 1. Scope the task with a clear definition of done:
$EDITOR task.md

# 2. Launch in an isolated, throwaway branch so nothing touches main mid-run:
tmux new -s agent                                  # survives SSH/phone disconnect
git worktree add ../emulator-system-agent -b agent/run
cd ../emulator-system-agent
caffeinate -i ./local-agent.sh                     # keeps the Mac awake; releases on exit
```

Detach with `Ctrl-b d`, walk away, reattach with `tmux attach -t agent`. A macOS
notification fires when it finishes; full output is in `~/agent-runs/<date>.log`.

Aider auto-commits each step, so you get a reviewable diff trail on `agent/run`.
Review, squash, and merge what you want afterward:

```bash
git -C ../emulator-system-agent log --oneline    # see the agent's trail
git worktree remove ../emulator-system-agent     # clean up when done
```

Tune `MODEL` / `TEST` / `FILES` / `MAX` at the top of `local-agent.sh` (or via
env vars). The dense `qwen3.6-27b` is the safer unsupervised pick; the loaded
`qwen3.6-35b-a3b` MoE is faster when the task is well-fenced.
