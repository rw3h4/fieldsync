# FieldSync: working rules for Claude Code

Project, repo, folder and Python package are all named `fieldsync`. Run everything with `uv run …` from the repo root.

FieldSync is a fault-tolerant collaborative records editor (BSE 4103 / BCS 3107 Distributed Systems group project).
The full spec is **PROJECT_BRIEF.md** (v2). Read it at the start of every session and every milestone.

## Roles
- **Owner:** Brian (TUMWESIGYE BRIAN). Approves plans and any deviation from the brief. Teammate: Aijuka (ARINAITWE AIJUKA).
- **You (Claude Code):** implement. Propose; don't decide scope or architecture alone.
- **Who commits:** the human developer on each side, never an agent. Brian commits and pushes on `main`; Aijuka commits and pushes on his branch. "The developer" below means whichever of them you are working with.

## How every milestone runs
1. Start in **plan mode**. Re-read PROJECT_BRIEF.md, docs/DEVLOG.md and docs/DECISIONS.md, then propose the plan. **Wait for approval before writing code.**
2. Build in small, tested steps. Before every checkpoint: `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` must all pass.
3. At each logical checkpoint, **stop** and give the developer:
   1. the output of `git status --short`;
   2. a one-paragraph summary of the change;
   3. a proposed commit message in our existing style (`M4: relay failover on heartbeat timeout`), with no `Co-Authored-By` or any other trailer.

   Then **wait for the developer to commit** before continuing. Keep each checkpoint small enough to make one clean commit.
4. End the milestone with the report below, then **stop**. Don't start the next milestone without approval.

## Milestone report format
- **Built:** what now exists, in plain words.
- **Brief tasks served:** which of (a)–(f) and FR1–FR5 this advances.
- **Deviations:** anything that differs from the brief or the approved plan, and why.
- **Verify it yourself:** exact commands and what the owner should see (a short manual test script).
- **Evidence for the report:** tests added, any mutation checks (what you broke, which test caught it), screenshots or logs worth keeping.
- **Proposed commits:** the checkpoint commit messages for this milestone, in order, marking any the developer has not yet committed.
- **Risks / open questions.**

## Hard rules
- **Never run `git commit`, `git push` or `gh pr` commands.** The human developer commits and pushes. Read-only git (`status`, `diff`, `log`, `show`) is fine.
- **Ask before deviating** from PROJECT_BRIEF.md or an approved plan. If something in the brief seems wrong, say so instead of quietly working around it.
- **The CRDT core is frozen.** Don't change `fieldsync/crdt/rga.py`, `clocks.py`, `ids.py` or `diff.py` without explicit owner approval. Additive changes to `ops.py` are allowed only when the approved milestone plan names them. (Before the M0 rename these live in `editor/crdt/`.)
- Interfaces frozen at the end of M1 (messages, `Transport`, replica runtime API) change only with owner approval, because M2 and M3 are built in parallel against them.
- Never use a library that implements CRDT / conflict-resolution logic.
- Every demo and test must work on **one laptop over localhost**.
- Keep the distributed-systems logic readable and commented; the examiner may read it.
- If a subagent works in parallel, give it its own git worktree and the same rules; it reports back instead of changing frozen code, and it never commits or pushes either.

## Records to keep (they become the report)
- **docs/DEVLOG.md:** per milestone: what was done, deviations, mutation-testing experiments, problems hit and how they were solved.
- **docs/DECISIONS.md:** one entry per design decision: date, decision, alternatives considered, reason. Short.
- **results/:** raw evaluation CSVs, summary tables and charts (M4).
