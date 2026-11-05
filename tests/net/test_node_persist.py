"""Data-file write failures are loud and self-healing (D29)."""

import pytest

from fieldsync.net.store import ChangeStore

from .helpers import run, start_node, start_relay


def test_failing_data_file_writes_are_reported_retried_and_cleared(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    data = tmp_path / "brian.jsonl"

    async def scenario() -> None:
        relay, url = await start_relay("relay_a")
        node = await start_node("brian", [url], data_file=data)
        store = node._store
        assert store is not None
        real_append = store.append
        failures = 2

        def flaky_append(change) -> None:  # disk full for the first two attempts
            nonlocal failures
            if failures:
                failures -= 1
                raise OSError(28, "No space left on device")
            real_append(change)

        monkeypatch.setattr(store, "append", flaky_append)

        # Status as seen right after each trace event (this observer runs after
        # the node's persistence observer).
        seen = []
        node.replica.observers.append(lambda e: seen.append((e.kind, node.status.persist_error)))

        # Creating a record emits two events, "create" then "send": both writes fail.
        rec = node.replica.create_record("notes", "case_note")
        (_, first), (_, second) = seen
        assert first is not None and "No space left" in first.message
        assert second is not None and second.since == first.since  # one run of failures
        assert node.status.persist_error == second
        assert node.status.state == "connected"  # the connection fields are still there
        assert ChangeStore(data).load() == []

        # The next event retries the unsaved create along with the new edit.
        node.replica.edit(rec, 0, insert="a")
        assert seen[2] == ("edit", None)
        assert node.status.persist_error is None
        expected = list(node.replica.history)
        assert len(expected) == 2

        await node.stop()
        await relay.stop()
        assert ChangeStore(data).load() == expected  # complete, in causal order

    with caplog.at_level("ERROR"):
        run(scenario)
    errors = [r for r in caplog.records if r.levelname == "ERROR" and "data file" in r.message]
    assert len(errors) == 2
