from fieldsync.crdt import HEAD, Change, Delete, ElementId, Insert, VectorClock
from fieldsync.runtime.transport import (
    ChangeMsg,
    Envelope,
    Hello,
    SyncRequest,
    SyncResponse,
    decode,
    encode,
)

CHANGE = Change(
    "A",
    1,
    VectorClock({"A": 1}),
    (Insert(ElementId(1, "A"), HEAD, "x"), Delete(ElementId(1, "A"))),
)


def test_envelope_json_round_trip() -> None:
    payloads = [
        ChangeMsg(CHANGE),
        SyncRequest(VectorClock({"A": 2, "B": 1}), reciprocate=False),
        SyncResponse((CHANGE,), VectorClock({"A": 1})),
        Hello(),
    ]
    for i, payload in enumerate(payloads):
        env = Envelope(f"A:{i}", "A", "*", payload)
        assert decode(encode(env)) == env


def test_kinds_and_summaries() -> None:
    env = Envelope("A:1", "A", "B", ChangeMsg(CHANGE))
    assert env.kind == "change"
    assert env.summary().startswith("A#1")
