# Task for the local agent

> Scope this tightly. There is no interactive follow-up, so a vague task wanders.
> Replace everything below with your actual task before running ./local-agent.sh.

## Goal
<!-- One or two sentences. What should exist when this is done? -->
Example: Add an `emu search <term>` command that lists every game across all
systems whose name contains <term> (case-insensitive).

## Definition of done
<!-- The agent stops when these are true. The test suite is the oracle. -->
- [ ] All tests in `tests/` pass (`python3 -m pytest -q` is green).
- [ ] New behaviour is covered by a test in `tests/`.
- [ ] No changes to files outside `src/` and `tests/`.

## Constraints
- Match the existing style in `src/cli.py` (argparse subcommands, module-level
  cached config via `get_systems()` / `get_config()`).
- Do not edit the config JSON files or `emu`.
- Keep functions small and pure where possible so they stay testable.

## Notes / context
<!-- Anything the model can't infer from the repo map. -->
- The repo map gives the agent the rest of the codebase; you only need to point
  it at the specific files and intent here.
