# FieldSync: instructions for Gemini CLI

This file does not repeat the project rules; it points to them. The rules are in:

1. **CLAUDE.md**: working rules, milestone process, report format, hard rules.
2. **PROJECT_BRIEF.md**: the full specification.
3. **docs/INTERFACES.md**: the frozen interfaces you build against.

Read all three fully at the start of every session. They apply to you exactly as written:

- Where they say "Claude Code" or "Claude", read "you".
- Where they say "plan mode", it means: write your plan and stop for approval before writing code.

## M3 scope

M3 may touch:

- `fieldsync/ui/sim/`
- `fieldsync/sim/scenarios.py`
- `tests/`
- `docs/DEVLOG.md`
- `docs/DECISIONS.md` (new entries numbered from D30)

M3 must not touch:

- `fieldsync/crdt/`, `fieldsync/runtime/`, `fieldsync/net/`, `fieldsync/ui/node/`, `fieldsync/ui/components/`
- the existing `fieldsync/sim/` modules (`network.py`, `scheduler.py`, `simulation.py`, `__init__.py`),
  except by adding new helpers. Put new helpers in new files under `fieldsync/sim/`: CI flags
  any edit to an existing file there for owner review.

A CI check on pull requests (`frozen-paths`) enforces this. If it fails, the owner must
review the change.
