# FieldSync

A fault-tolerant collaborative records editor for a community health NGO whose field
offices have unreliable connectivity. Every editor node holds a full replica of every
record; edits merge without locks through an RGA sequence CRDT with causal delivery,
and nodes sync through two relays (A and B) with heartbeat-based failover. A
deterministic simulator illustrates concurrency and failures.

Distributed Systems group project (BSE 4103 / BCS 3107). The spec is
[PROJECT_BRIEF.md](PROJECT_BRIEF.md); setup for developers is in
[CONTRIBUTING.md](CONTRIBUTING.md).

Work in progress. Run and demo instructions will be added in M5.

```bash
uv sync
uv run pytest
```
