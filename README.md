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

**Performance evaluation (M4):** `uv run loadgen --all`, then `uv run loadgen-report` and
`uv run --group eval loadgen-charts`. Method, results and interpretation: `docs/EVALUATION.md`.

**Resetting the demo:** stop every process (all relays and nodes), then delete the whole
`data/` folder. Never delete a single node's file while the relays keep their logs: the
relays still hold that node's old changes, so a restarted node with the same ID would reuse
change IDs (D27).

Add `--host 0.0.0.0` (the default) and use the laptop's LAN address in `--relays` to run nodes
on other machines on the same network.

## Run across two laptops

Laptop 1 runs relay_a and one node; laptop 2 runs relay_b and one node. The relays peer with
each other, and every node lists **both** relays, all by LAN address, so either laptop's relay
can fail over to the other. The examples use `192.168.43.10` for laptop 1 and `192.168.43.20`
for laptop 2: replace them with your own.

1. **Find each laptop's address:** `hostname -I` on Linux (the first address), `ipconfig` on
   Windows ("IPv4 Address" of the Wi-Fi adapter).
2. **Laptop 1:**

   ```bash
   uv run relay --id relay_a --port 8765 --peer ws://192.168.43.20:8766 --log-file data/relay_a.jsonl
   uv run node --id brian --port 8081 --relays ws://192.168.43.10:8765,ws://192.168.43.20:8766 --data-file data/brian.jsonl
   ```

3. **Laptop 2:**

   ```bash
   uv run relay --id relay_b --port 8766 --peer ws://192.168.43.10:8765 --log-file data/relay_b.jsonl
   uv run node --id aijuka --port 8081 --relays ws://192.168.43.10:8765,ws://192.168.43.20:8766 --data-file data/aijuka.jsonl
   ```

   Relays and nodes already listen on all interfaces (`--host 0.0.0.0` is the default).
4. **Firewall:**
   - **Windows:** when the "Windows Defender Firewall has blocked some features" prompt appears
     for Python, allow it on **private networks**, and make sure the Wi-Fi network's profile
     is set to Private (not Public).
   - **Linux:** if a firewall is active, open the relay ports and the node page, e.g.
     `sudo ufw allow 8765:8766/tcp` and `sudo ufw allow 8081/tcp`.
5. **Network:** campus and other managed Wi-Fi often blocks traffic between devices ("client
   isolation"), so the laptops can't see each other even with the right addresses. Use a phone
   hotspot with both laptops on it.
6. **30-second check before the demo:** on laptop 2, open laptop 1's node page in a browser
   (`http://192.168.43.10:8081`). If it loads, the laptops can reach each other; then check that
   each node's status bar shows a relay rather than OFFLINE.

The **reset rule** above applies on every laptop: stop every relay and node on both laptops,
then delete the whole `data/` folder on each.

## Run the simulator

```bash
uv run sim
```

Open http://127.0.0.1:8080/ (the address is printed on startup): it shows three in-process
replicas on a virtual network you control (texts, clocks, causal buffers, in-flight
messages, a live space-time diagram and the RGA element list), with seven scripted
scenarios to load and step through.
