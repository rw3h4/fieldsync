"""Network mode end to end: relays and headless nodes over real WebSockets on localhost.

Failover/recovery tests run nodes WITHOUT periodic anti-entropy, so convergence
must come from the sync a node runs on every (re)connect.
"""

import asyncio
import time

from fieldsync.net.relay import Relay
from tests.net.helpers import (
    FAST,
    converged,
    make_node,
    run,
    start_node,
    start_relay,
    stop_all,
    wait_until,
)


def test_two_nodes_one_relay_converge_on_several_records() -> None:
    async def scenario() -> None:
        relay, url = await start_relay("relay_a")
        a = await start_node("brian", [url])
        b = await start_node("aijuka", [url])
        recs = [
            a.replica.create_record("visit 1", "visit_report"),
            b.replica.create_record("notes", "case_note"),
            a.replica.create_record("visit 2", "visit_report"),
        ]
        await wait_until(
            lambda: all(b.replica.has_record(r) and a.replica.has_record(r) for r in recs)
        )
        for i, rec in enumerate(recs):  # concurrent edits on every record from both nodes
            a.replica.edit(rec, 0, insert=f"a{i}")
            b.replica.edit(rec, 0, insert=f"b{i}")
        await wait_until(lambda: converged([a, b]), what="converged")
        for rec in recs:
            assert a.replica.text(rec) == b.replica.text(rec)
            assert sorted(a.replica.text(rec)) == sorted(f"a{recs.index(rec)}b{recs.index(rec)}")
        assert relay.node_ids == ["aijuka", "brian"]
        await stop_all(a, b, relay)

    run(scenario)


def test_relay_kill_failover_keeps_outage_edits() -> None:
    async def scenario() -> None:
        relay_a, url_a = await start_relay("relay_a")
        relay_b, url_b = await start_relay("relay_b")
        a = await start_node("brian", [url_a, url_b])
        b = await start_node("aijuka", [url_a, url_b])
        rec = a.replica.create_record("visit", "visit_report")
        a.replica.edit(rec, 0, insert="before ")
        await wait_until(lambda: converged([a, b]), what="initial sync")
        assert a.status.relay_id == "relay_a"

        await relay_a.stop()
        # Edits made during the outage: sent into a dead connection or dropped while offline.
        a.replica.edit(rec, len(a.replica.text(rec)), insert="during-a ")
        b.replica.edit(rec, 0, insert="during-b ")

        await wait_until(
            lambda: a.status.relay_id == "relay_b" and b.status.relay_id == "relay_b",
            what="failover to relay_b",
        )
        await wait_until(lambda: converged([a, b]), what="converged after failover")
        assert a.replica.text(rec) == "during-b before during-a "
        assert a.status.failovers == 1 and b.status.failovers == 1
        await stop_all(a, b, relay_b)

    run(scenario)


def test_heartbeat_timeout_failover() -> None:
    async def scenario() -> None:
        relay_a, url_a = await start_relay("relay_a")
        relay_b, url_b = await start_relay("relay_b")
        a = await start_node("brian", [url_a, url_b])
        await wait_until(lambda: a.status.rtt_ms is not None, what="first pong")
        relay_a.pause()  # hung: socket stays open, but nothing is answered
        t0 = time.monotonic()
        await wait_until(lambda: a.status.relay_id == "relay_b", what="heartbeat failover")
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, elapsed  # timeout is 0.3 s in tests
        rec = a.replica.create_record("after failover", "case_note")
        await wait_until(lambda: relay_b.log.clock == a.replica.clock, what="change on relay_b")
        assert relay_b.log.history[-1].record == rec
        relay_a.resume()
        await stop_all(a, relay_a, relay_b)

    run(scenario)


def test_changes_only_on_dead_relay_rescued_via_nodes() -> None:
    async def scenario() -> None:
        relay_a, url_a = await start_relay("relay_a")  # NOT peered with relay_b
        relay_b, url_b = await start_relay("relay_b")
        a = await start_node("brian", [url_a, url_b])
        b = await start_node("aijuka", [url_a, url_b])
        c = await start_node("hq", [url_b])  # only knows relay_b
        rec = a.replica.create_record("visit", "visit_report")
        a.replica.edit(rec, 0, insert="only on A ")
        await wait_until(lambda: b.replica.has_record(rec), what="record reached b")
        b.replica.edit(rec, 0, insert="also ")
        await wait_until(lambda: converged([a, b]), what="a and b synced via relay_a")
        assert relay_b.log.history == [] and not c.replica.has_record(rec)

        await relay_a.stop()
        # a and b fail over to relay_b and sync; relay_b pulls the changes from them
        # (counter-request) and forwards each first-seen change to c.
        await wait_until(lambda: converged([a, b, c]), what="rescued to relay_b and hq")
        assert c.replica.text(rec) == a.replica.text(rec) == "also only on A "
        assert len(relay_b.log.history) == len(a.replica.history)
        await stop_all(a, b, c, relay_b)

    run(scenario)


def test_relay_restart_from_jsonl_serves_late_joiner(tmp_path) -> None:
    async def scenario() -> None:
        log_file = tmp_path / "relay_a.jsonl"
        relay, url = await start_relay("relay_a", log_file=log_file)
        a = await start_node("brian", [url])
        r1 = a.replica.create_record("one", "visit_report")
        r2 = a.replica.create_record("two", "case_note")
        a.replica.edit(r1, 0, insert="first record")
        a.replica.edit(r2, 0, insert="second record")
        await wait_until(lambda: relay.log.clock == a.replica.clock, what="relay has everything")
        await stop_all(a, relay)

        restarted, url2 = await start_relay("relay_a", log_file=log_file)
        assert restarted.log.clock == a.replica.clock  # reloaded from the file
        late = await start_node("field2", [url2])
        await wait_until(lambda: late.replica.clock == a.replica.clock, what="late joiner synced")
        assert [r.title for r in late.replica.list_records()] == ["one", "two"]
        assert late.replica.text(r1) == "first record" and late.replica.text(r2) == "second record"
        await stop_all(late, restarted)

    run(scenario)


def test_node_restart_keeps_offline_edits(tmp_path) -> None:
    async def scenario() -> None:
        relay, url = await start_relay("relay_a")
        data = tmp_path / "brian.jsonl"
        a = await start_node("brian", [url], data_file=data)
        rec = a.replica.create_record("visit", "visit_report")
        a.replica.edit(rec, 0, insert="online. ")
        await wait_until(lambda: relay.log.clock == a.replica.clock, what="relay synced")

        a.set_offline(True)
        await wait_until(lambda: a.status.state == "manual_offline", what="offline")
        a.replica.edit(rec, len(a.replica.text(rec)), insert="offline edit.")
        offline_rec = a.replica.create_record("made offline", "case_note")
        assert relay.log.clock != a.replica.clock  # the relay never saw them
        await a.stop()

        again = make_node("brian", [url], data_file=data)
        await again.start()  # replays the file before connecting
        assert again.replica.text(rec) == "online. offline edit."
        assert again.replica.has_record(offline_rec)
        other = await start_node("aijuka", [url])
        await wait_until(lambda: converged([again, other]), what="offline edits delivered")
        assert other.replica.text(rec) == "online. offline edit."
        # The file holds every change exactly once.
        assert len(data.read_text().splitlines()) == len(again.replica.history)
        await stop_all(again, other, relay)

    run(scenario)


def test_no_forwarding_loop_between_relays() -> None:
    async def scenario() -> None:
        relay_a, url_a = await start_relay("relay_a", anti_entropy_interval=0.2)
        relay_b, url_b = await start_relay("relay_b", anti_entropy_interval=0.2)
        relay_a.connect_peer(url_b)  # peered in both directions
        relay_b.connect_peer(url_a)
        await wait_until(
            lambda: all(link.status.relay_id for r in (relay_a, relay_b) for link in r._peers_out),
            what="peer links up",
        )
        a = await start_node("brian", [url_a])
        b = await start_node("aijuka", [url_b])
        rec = a.replica.create_record("busy", "visit_report")
        await wait_until(lambda: b.replica.has_record(rec), what="record via peering")
        for i in range(10):
            a.replica.edit(rec, 0, insert=str(i % 10))
            b.replica.edit(rec, 0, insert=chr(97 + i))
        await wait_until(lambda: converged([a, b]), what="converged across relays")
        total = len(a.replica.history)  # 21 changes
        await wait_until(lambda: relay_a.log.clock == relay_b.log.clock == a.replica.clock)

        # Each change crossed between the relays exactly once.
        assert relay_a.stats["fwd_peer"] + relay_b.stats["fwd_peer"] == total
        before = (dict(relay_a.stats), dict(relay_b.stats))
        await asyncio.sleep(1.0)  # quiet period: anti-entropy keeps running, nothing moves
        for relay, old in zip((relay_a, relay_b), before, strict=True):
            for key in ("fwd_peer", "fwd_node", "recv_change"):
                assert relay.stats[key] == old.get(key, 0), key
        await stop_all(a, b, relay_a, relay_b)

    run(scenario)


def test_relay_never_forwards_sync_requests() -> None:
    async def scenario() -> None:
        relay, url = await start_relay("relay_a")
        a = await start_node("brian", [url])
        b = await start_node("aijuka", [url])
        seen_by_b: list[str] = []
        b.replica.observers.append(
            lambda e: seen_by_b.append(e.detail) if e.kind == "receive" else None
        )
        before = relay.stats["recv_sync_req"]
        for _ in range(3):
            a.replica.anti_entropy()
        a.replica.on_reconnect()
        await wait_until(lambda: relay.stats["recv_sync_req"] == before + 4, what="relay got 4")
        await asyncio.sleep(0.3)
        assert not [d for d in seen_by_b if d.startswith("sync_req")]
        await stop_all(a, b, relay)

    run(scenario)


def test_no_flap_back_after_failover() -> None:
    async def scenario() -> None:
        relay_a, url_a = await start_relay("relay_a")
        relay_b, url_b = await start_relay("relay_b")
        a = await start_node("brian", [url_a, url_b])
        await wait_until(lambda: a.status.relay_id == "relay_a")
        port_a = int(url_a.rsplit(":", 1)[1])
        await relay_a.stop()
        await wait_until(lambda: a.status.relay_id == "relay_b", what="failover")
        back = Relay("relay_a", **FAST)
        await back.start("127.0.0.1", port_a)  # relay_a is back on its old address
        await asyncio.sleep(1.0)
        assert a.status.relay_id == "relay_b" and a.status.failovers == 1
        await stop_all(a, back, relay_b)

    run(scenario)


def test_all_relays_down_then_back() -> None:
    async def scenario() -> None:
        probe, url = await start_relay("relay_a")
        port = int(url.rsplit(":", 1)[1])
        await probe.stop()  # nothing listens on this port now
        a = make_node("brian", [url])
        await a.start()
        await wait_until(lambda: a.status.state == "offline", what="offline after a failed round")
        rec = a.replica.create_record("offline from the start", "case_note")  # keeps working
        relay = Relay("relay_a", **FAST)
        await relay.start("127.0.0.1", port)
        await wait_until(lambda: relay.log.clock == a.replica.clock, what="reconnected and synced")
        assert relay.log.history[0].record == rec
        await stop_all(a, relay)

    run(scenario)


def test_no_forwarding_loop_in_relay_triangle() -> None:
    """With three fully peered relays a change can travel A->B->C->A; only the
    "never forward a duplicate" rule stops it. (With two relays, "never send back
    to the source" alone already prevents a direct bounce, so this needs three.)"""

    async def scenario() -> None:
        relays, urls = [], []
        for rid in ("relay_a", "relay_b", "relay_c"):
            relay, url = await start_relay(rid, anti_entropy_interval=0.2)
            relays.append(relay)
            urls.append(url)
        for relay, own in zip(relays, urls, strict=True):
            for url in urls:
                if url != own:
                    relay.connect_peer(url)
        await wait_until(
            lambda: all(link.status.relay_id for r in relays for link in r._peers_out),
            what="triangle links up",
        )
        a = await start_node("brian", [urls[0]])
        c = await start_node("hq", [urls[2]])
        rec = a.replica.create_record("triangle", "case_note")
        for i in range(10):
            a.replica.edit(rec, 0, insert=str(i))
        await wait_until(lambda: converged([a, c]), what="converged over the triangle")
        total = len(a.replica.history)
        await wait_until(lambda: all(r.log.clock == a.replica.clock for r in relays))

        # Per change: the first relay sends to 2 peers, each other relay to at most 1.
        assert sum(r.stats["fwd_peer"] for r in relays) <= 4 * total
        before = [r.stats["fwd_peer"] for r in relays]
        await asyncio.sleep(1.0)
        assert [r.stats["fwd_peer"] for r in relays] == before  # nothing circulates
        await stop_all(a, c, *relays)

    run(scenario)
