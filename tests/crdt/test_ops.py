import json

import pytest

from fieldsync.crdt import (
    HEAD,
    Change,
    CreateRecord,
    Delete,
    ElementId,
    Insert,
    VectorClock,
    check_replica_id,
    op_from_json,
    op_to_json,
    record_id_for,
)


def test_op_json_round_trip() -> None:
    ins = Insert(ElementId(3, "A"), ElementId(2, "B"), "x")
    dele = Delete(ElementId(3, "A"))
    for op in (ins, dele):
        assert op_from_json(json.loads(json.dumps(op_to_json(op)))) == op


def test_change_json_round_trip() -> None:
    change = Change(
        origin="A",
        seq=2,
        clock=VectorClock({"A": 2, "B": 5}),
        ops=(Insert(ElementId(7, "A"), HEAD, "h"), Delete(ElementId(4, "B"))),
        record="B-1",
    )
    assert Change.from_json(json.loads(json.dumps(change.to_json()))) == change
    assert change.dot == ("A", 2)


def test_change_requires_consistent_clock() -> None:
    with pytest.raises(ValueError):
        Change(origin="A", seq=2, clock=VectorClock({"A": 1}), ops=(), record="B-1")


def test_unknown_op_type_rejected() -> None:
    with pytest.raises(ValueError):
        op_from_json({"t": "move"})


def test_create_record_change_json_round_trip() -> None:
    create = Change(
        "brian",
        1,
        VectorClock({"brian": 1}),
        (CreateRecord("Kasese visit", "case_note"),),
        "brian-1",
    )
    assert Change.from_json(json.loads(json.dumps(create.to_json()))) == create
    assert create.creates_record == CreateRecord("Kasese visit", "case_note")
    assert create.summary() == "brian#1 [brian-1] new case_note 'Kasese visit'"


def test_create_must_be_alone_and_named_after_its_dot() -> None:
    clock = VectorClock({"A": 3})
    create = CreateRecord("t", "visit_report")
    assert Change("A", 3, clock, (create,), record_id_for("A", 3)).record == "A-3"
    with pytest.raises(ValueError):
        Change("A", 3, clock, (create,), "A-1")  # wrong ID
    with pytest.raises(ValueError):
        Change("A", 3, clock, (create, Delete(ElementId(1, "A"))), "A-3")  # not alone


def test_invalid_record_fields_rejected() -> None:
    with pytest.raises(ValueError):
        CreateRecord("   ", "visit_report")
    with pytest.raises(ValueError):
        CreateRecord("t", "invoice")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Change("A", 1, VectorClock({"A": 1}), (), "")  # every change names a record


@pytest.mark.parametrize("bad", ["a-b", "", "a b", "é", "A:1", "*"])
def test_invalid_replica_ids_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        check_replica_id(bad)
    with pytest.raises(ValueError):
        Change(bad, 1, VectorClock({bad: 1}), (), "r")


def test_valid_replica_ids_accepted() -> None:
    for good in ["A", "brian", "field_office_2"]:
        assert check_replica_id(good) == good


def test_change_json_without_record_rejected() -> None:
    data = Change("A", 1, VectorClock({"A": 1}), (), "r").to_json()
    del data["record"]
    with pytest.raises(KeyError):
        Change.from_json(data)
