import json

import pytest

from editor.crdt import (
    HEAD,
    Change,
    Delete,
    ElementId,
    Insert,
    VectorClock,
    op_from_json,
    op_to_json,
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
    )
    assert Change.from_json(json.loads(json.dumps(change.to_json()))) == change
    assert change.dot == ("A", 2)


def test_change_requires_consistent_clock() -> None:
    with pytest.raises(ValueError):
        Change(origin="A", seq=2, clock=VectorClock({"A": 1}), ops=())


def test_unknown_op_type_rejected() -> None:
    with pytest.raises(ValueError):
        op_from_json({"t": "move"})
