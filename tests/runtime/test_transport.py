import pytest

from fieldsync.crdt import HEAD, Change, Delete, ElementId, Insert, VectorClock
from fieldsync.runtime.transport import (
    ChangeMsg,
    Envelope,
    Heartbeat,
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
        Hello("node"),
    ]
    for i, payload in enumerate(payloads):
        env = Envelope(f"A:{i}", "A", "*", payload)
        assert decode(encode(env)) == env


def test_kinds_and_summaries() -> None:
    env = Envelope("A:1", "A", "B", ChangeMsg(CHANGE))
    assert env.kind == "change"
    assert env.summary().startswith("A#1")


def test_hello_role_and_heartbeat_round_trip() -> None:
    payloads = [Hello("relay"), Heartbeat(12.5), Heartbeat(12.5, pong=True)]
    for i, payload in enumerate(payloads):
        env = Envelope(f"A:{i}", "A", "B", payload)
        assert decode(encode(env)) == env
    summaries = [Envelope("A:1", "A", "B", p).summary() for p in payloads]
    assert summaries == ["hello (relay)", "ping", "pong"]


def test_unknown_hello_role_rejected() -> None:
    bad = '{"type":"hello","msg_id":"A:1","src":"A","dst":"*","body":{"role":"admin"}}'
    with pytest.raises(ValueError):
        decode(bad)
