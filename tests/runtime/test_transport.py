import pytest

from fieldsync.crdt import HEAD, Change, CreateRecord, Delete, ElementId, Insert, VectorClock
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
    "B-1",
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


# The frozen wire format (docs/INTERFACES.md). If this test fails, a frozen
# interface changed: that needs owner approval, not a new expected string.
GOLDEN_CREATE = Change(
    "brian",
    1,
    VectorClock({"brian": 1}),
    (CreateRecord("Kasese visit", "visit_report"),),
    "brian-1",
)
GOLDEN_EDIT = Change(
    "aijuka",
    3,
    VectorClock({"aijuka": 3, "brian": 1}),
    (Insert(ElementId(4, "aijuka"), ElementId(2, "brian"), "x"), Delete(ElementId(2, "brian"))),
    "brian-1",
)
GOLDEN = [
    (
        Envelope("brian:1", "brian", "*", ChangeMsg(GOLDEN_CREATE)),
        '{"type":"change","msg_id":"brian:1","src":"brian","dst":"*","body":{"origin":"brian",'
        '"seq":1,"clock":{"brian":1},"ops":[{"t":"new","title":"Kasese visit",'
        '"kind":"visit_report"}],"record":"brian-1"}}',
    ),
    (
        Envelope("aijuka:7", "aijuka", "*", ChangeMsg(GOLDEN_EDIT)),
        '{"type":"change","msg_id":"aijuka:7","src":"aijuka","dst":"*","body":{"origin":"aijuka",'
        '"seq":3,"clock":{"aijuka":3,"brian":1},"ops":[{"t":"ins","id":[4,"aijuka"],'
        '"after":[2,"brian"],"ch":"x"},{"t":"del","target":[2,"brian"]}],"record":"brian-1"}}',
    ),
    (
        Envelope("aijuka:8", "aijuka", "A", SyncRequest(VectorClock({"brian": 1}))),
        '{"type":"sync_req","msg_id":"aijuka:8","src":"aijuka","dst":"A",'
        '"body":{"clock":{"brian":1},"reciprocate":true}}',
    ),
    (
        Envelope("A:2", "A", "aijuka", SyncResponse((GOLDEN_CREATE,), VectorClock({"brian": 1}))),
        '{"type":"sync_resp","msg_id":"A:2","src":"A","dst":"aijuka","body":{"changes":'
        '[{"origin":"brian","seq":1,"clock":{"brian":1},"ops":[{"t":"new","title":"Kasese visit",'
        '"kind":"visit_report"}],"record":"brian-1"}],"clock":{"brian":1}}}',
    ),
    (
        Envelope("A:3", "A", "*", Hello("relay")),
        '{"type":"hello","msg_id":"A:3","src":"A","dst":"*","body":{"role":"relay"}}',
    ),
    (
        Envelope("brian:9", "brian", "A", Heartbeat(12.5)),
        '{"type":"heartbeat","msg_id":"brian:9","src":"brian","dst":"A",'
        '"body":{"sent_at":12.5,"pong":false}}',
    ),
    (
        Envelope("A:4", "A", "brian", Heartbeat(12.5, pong=True)),
        '{"type":"heartbeat","msg_id":"A:4","src":"A","dst":"brian",'
        '"body":{"sent_at":12.5,"pong":true}}',
    ),
]


@pytest.mark.parametrize(("env", "wire"), GOLDEN)
def test_golden_wire_format(env: Envelope, wire: str) -> None:
    assert encode(env) == wire
    assert decode(wire) == env
