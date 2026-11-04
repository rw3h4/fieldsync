import pytest

from fieldsync.crdt import Change, CreateRecord, VectorClock
from fieldsync.net.store import ChangeStore
from fieldsync.runtime import CausalLog


def changes() -> list[Change]:
    create = Change("A", 1, VectorClock({"A": 1}), (CreateRecord("t", "case_note"),), "A-1")
    edit = Change("B", 1, VectorClock({"A": 1, "B": 1}), (), "A-1")
    return [create, edit]


def test_append_then_load_round_trip(tmp_path) -> None:
    path = tmp_path / "log.jsonl"
    store = ChangeStore(path)
    for c in changes():
        store.append(c)
    store.close()
    assert ChangeStore(path).load() == changes()
    assert len(path.read_text().splitlines()) == 2


def test_file_uses_lf_line_endings_on_every_os(tmp_path) -> None:
    path = tmp_path / "log.jsonl"
    store = ChangeStore(path)
    for c in changes():
        store.append(c)
    store.close()
    data = path.read_bytes()
    assert b"\r" not in data
    assert data.count(b"\n") == 2


def test_reload_into_causal_log_restores_clock(tmp_path) -> None:
    store = ChangeStore(tmp_path / "log.jsonl")
    for c in changes():
        store.append(c)
    log = CausalLog("R")
    for c in store.load():
        assert log.offer(c) == "delivered"
    assert log.clock == VectorClock({"A": 1, "B": 1})


def test_damaged_last_line_is_skipped(tmp_path) -> None:
    path = tmp_path / "log.jsonl"
    store = ChangeStore(path)
    store.append(changes()[0])
    store.close()
    with path.open("a") as f:
        f.write('{"origin":"B","seq":1,"clo')  # killed mid-write
    assert ChangeStore(path).load() == changes()[:1]


def test_damaged_middle_line_raises(tmp_path) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text('{"broken"\n' + "{}\n")
    with pytest.raises(ValueError, match="corrupt line 1"):
        ChangeStore(path).load()


def test_new_file_is_empty(tmp_path) -> None:
    assert ChangeStore(tmp_path / "sub" / "new.jsonl").load() == []
