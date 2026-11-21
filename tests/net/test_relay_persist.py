"""A relay's log-file write failure must not stop it serving (documented limit: no retry)."""

import asyncio

import pytest

from tests.net.helpers import run, start_node, start_relay, stop_all, wait_until


def test_relay_log_write_failure_keeps_connections_and_forwarding(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        relay, url = await start_relay("relay_a", log_file=tmp_path / "relay_a.jsonl")
        a = await start_node("brian", [url])
        b = await start_node("aijuka", [url])
        assert relay._store is not None

        def disk_full(_change) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(relay._store, "append", disk_full)

        rec = a.replica.create_record("notes", "case_note")
        a.replica.edit(rec, 0, insert="hello")
        await wait_until(lambda: b.replica.clock == a.replica.clock, what="b got a's changes")
        assert b.replica.text(rec) == "hello"  # forwarded despite the failed writes
        assert relay.log.clock == a.replica.clock  # held in memory
        await asyncio.sleep(0.5)  # longer than the heartbeat timeout
        assert relay.stats["hello_node"] == 2  # nobody was disconnected and came back
        assert a.status.failovers == 0 and a.status.state == "connected"
        assert b.status.failovers == 0 and b.status.state == "connected"
        assert relay.stats["persist_errors"] == 2
        await stop_all(a, b, relay)

    with caplog.at_level("ERROR"):
        run(scenario)
    errors = [r for r in caplog.records if r.levelname == "ERROR" and "log file" in r.message]
    assert len(errors) == 2
