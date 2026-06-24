# CLAUDE.md

Project memory for Claude Code. Full session context and the implementation
backlog live in:

@.claude/CONTEXT.md

## TL;DR
- macOS CLI/curses RetroArch frontend. `./emu` → `src/cli.py` (args) or
  `src/browser.py` (TUI, no args). Tests: `python3 -m pytest -q`.
- Workflow: plan/review here in Claude Code; implement with a **local model via
  OpenCode** using `./start-impl.sh docs/plans/<phase>.md` (defaults to
  `lmstudio/qwen/qwen3.6-27b`).
- Implementation backlog = the specs in `docs/plans/` (favorites done;
  onboarding + bug-fix phases pending). See `.claude/CONTEXT.md` for status,
  decisions, and the three open bugs.
- Keep pyobjc/Pillow imports guarded so the suite runs on system `python3`.
