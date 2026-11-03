# FieldSync

A fault-tolerant collaborative records editor for a community health NGO whose field
offices have unreliable connectivity. Every editor node holds a full replica of every
record; edits merge without locks through an RGA sequence CRDT with causal delivery,
and nodes sync through two relays (A and B) with heartbeat-based failover. A
deterministic simulator illustrates concurrency and failures.

Distributed Systems group project (BSE 4103 / BCS 3107). The spec is
[PROJECT_BRIEF.md](PROJECT_BRIEF.md); setup for developers is in
[CONTRIBUTING.md](CONTRIBUTING.md).

Work in progress: the full report-ready README comes in M5.

```bash
uv sync
uv run pytest
```

## Run network mode (one laptop)

Five terminals: two relays, then three editor nodes. Each node serves its own page.

```bash
uv run relay --id relay_a --port 8765 --peer ws://localhost:8766 --log-file data/relay_a.jsonl
uv run relay --id relay_b --port 8766 --peer ws://localhost:8765 --log-file data/relay_b.jsonl
uv run node --id brian  --port 8081 --relays ws://localhost:8765,ws://localhost:8766 --data-file data/brian.jsonl
uv run node --id aijuka --port 8082 --relays ws://localhost:8765,ws://localhost:8766 --data-file data/aijuka.jsonl
uv run node --id hq     --port 8083 --relays ws://localhost:8765,ws://localhost:8766 --data-file data/hq.jsonl
```

Open http://localhost:8081, http://localhost:8082 and http://localhost:8083.

- **FR1/FR2:** "New record" on one node; it appears on the others. Click it to open.
- **FR3:** type in the same record on two nodes at once; the texts and digests match.
- **FR4:** flip a node's **Offline** switch, keep editing, flip it back: everything merges.
- **FR5:** the status bar shows the relay in use (or OFFLINE), RTT, vector clock, pending
  changes and digests.
- **Relay failure:** Ctrl+C `relay_a` and every node switches to `relay_b` at once. A hung
  relay (`kill -STOP <pid>`) is detected by missing heartbeats after about 3 s. Nodes stay on
  `relay_b` when `relay_a` returns (no flapping back).
- **Recovery:** restart a relay with the same `--log-file` and it reloads its history. Restart
  a node with the same `--data-file` and it keeps its records, including offline edits.

**Operating rule: IDs must be unique per device.** Never run two nodes (or two relays) with
the same `--id`, and if a node loses its data file, start it with a new ID. Change IDs are
`(node id, sequence number)`; a reused ID would reuse them and break convergence. The relay
can't tell a reconnect from a duplicate, so this is not checked in code.

Add `--host 0.0.0.0` (the default) and use the laptop's LAN address in `--relays` to run nodes
on other machines on the same network.
