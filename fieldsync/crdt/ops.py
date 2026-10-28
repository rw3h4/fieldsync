"""CRDT operations and the ``Change`` that batches them for replication.

All types are frozen dataclasses and serialise to plain JSON-compatible dicts.

Wire format of ops::

    {"t": "ins", "id": [lamport, "A"], "after": [lamport, "B"], "ch": "x"}
    {"t": "del", "target": [lamport, "A"]}
    {"t": "new", "title": "Kasese visit", "kind": "visit_report"}

Records
-------
A node holds many records. Every ``Change`` names the record it belongs to.
Creating a record is itself a ``Change`` whose only op is ``CreateRecord``,
so record creation travels in the same causal stream as edits: the creator
made the record before editing it, so every edit causally depends on the
create and causal delivery never applies an edit to a record that doesn't
exist yet. The record ID is derived from the create change's dot
(``"<origin>-<seq>"``), which is unique without any coordination. Record IDs
are opaque labels: they are generated here and never parsed.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from fieldsync.crdt.clocks import VectorClock
from fieldsync.crdt.ids import ElementId

JSON = dict[str, Any]

RecordKind = Literal["visit_report", "case_note"]
RECORD_KINDS: tuple[RecordKind, ...] = ("visit_report", "case_note")

# Replica IDs: ASCII letters, digits and underscores only. In particular no "-",
# which keeps "<origin>-<seq>" record IDs unambiguous.
REPLICA_ID_RE = re.compile(r"[A-Za-z0-9_]+")


def check_replica_id(replica_id: str) -> str:
    """Return ``replica_id`` if it is valid, else raise ValueError."""
    if not isinstance(replica_id, str) or not REPLICA_ID_RE.fullmatch(replica_id):
        raise ValueError(f"invalid replica id {replica_id!r}: use letters, digits and _ only")
    return replica_id


def record_id_for(origin: str, seq: int) -> str:
    """ID of the record created by change ``(origin, seq)``. Never parse it back."""
    return f"{origin}-{seq}"


@dataclass(frozen=True, slots=True)
class Insert:
    """Insert character ``char`` with identity ``id`` immediately after ``after``."""

    id: ElementId
    after: ElementId
    char: str

    def __str__(self) -> str:
        return f"ins {self.char!r} {self.id} after {self.after}"


@dataclass(frozen=True, slots=True)
class Delete:
    """Tombstone the element ``target``. It stays in the structure but is hidden."""

    target: ElementId

    def __str__(self) -> str:
        return f"del {self.target}"


@dataclass(frozen=True, slots=True)
class CreateRecord:
    """Create the record named by the enclosing ``Change``. Title and kind never change."""

    title: str
    kind: RecordKind

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("record title must not be blank")
        if self.kind not in RECORD_KINDS:
            raise ValueError(f"unknown record kind {self.kind!r}")

    def __str__(self) -> str:
        return f"new {self.kind} {self.title!r}"


Op = Insert | Delete | CreateRecord


@dataclass(frozen=True, slots=True)
class Change:
    """One local edit (a batch of ops) as the unit of replication and causality.

    ``(origin, seq)`` uniquely names the change (its "dot"). ``clock`` is the
    origin's vector clock *after* making the change, so ``clock[origin] == seq``.
    The other entries are the change's causal dependencies: every change the
    origin had seen when it made this edit.

    ``record`` is the record the change belongs to. A record-creating change
    holds exactly one ``CreateRecord`` op and its ``record`` must be
    ``record_id_for(origin, seq)``.
    """

    origin: str
    seq: int
    clock: VectorClock
    ops: tuple[Op, ...]
    record: str

    def __post_init__(self) -> None:
        check_replica_id(self.origin)
        if self.clock.get(self.origin) != self.seq:
            raise ValueError(f"clock[{self.origin}] must equal seq={self.seq}, got {self.clock}")
        if not self.record:
            raise ValueError("change must name its record")
        if any(isinstance(op, CreateRecord) for op in self.ops):
            if len(self.ops) != 1:
                raise ValueError("a record-creating change must hold only the CreateRecord op")
            if self.record != record_id_for(self.origin, self.seq):
                raise ValueError(
                    f"created record must be {record_id_for(self.origin, self.seq)!r}, "
                    f"got {self.record!r}"
                )

    @property
    def creates_record(self) -> CreateRecord | None:
        """The ``CreateRecord`` op if this change creates its record, else None."""
        if len(self.ops) == 1 and isinstance(self.ops[0], CreateRecord):
            return self.ops[0]
        return None

    @property
    def dot(self) -> tuple[str, int]:
        return (self.origin, self.seq)

    def summary(self) -> str:
        """Short human-readable description for logs and the UI."""
        head = f"{self.origin}#{self.seq} [{self.record}] "
        if (create := self.creates_record) is not None:
            return head + str(create)
        inserted = "".join(op.char for op in self.ops if isinstance(op, Insert))
        deleted = sum(1 for op in self.ops if isinstance(op, Delete))
        parts = []
        if deleted:
            parts.append(f"del {deleted}")
        if inserted:
            parts.append(f"ins {inserted!r}")
        return head + (" ".join(parts) or "(empty)")

    def to_json(self) -> JSON:
        return {
            "origin": self.origin,
            "seq": self.seq,
            "clock": self.clock.to_dict(),
            "ops": [op_to_json(op) for op in self.ops],
            "record": self.record,
        }

    @classmethod
    def from_json(cls, data: JSON) -> "Change":
        return cls(
            origin=str(data["origin"]),
            seq=int(data["seq"]),
            clock=VectorClock.from_dict(data["clock"]),
            ops=tuple(op_from_json(o) for o in data["ops"]),
            record=str(data["record"]),
        )


def _id_to_json(eid: ElementId) -> list[Any]:
    return [eid.lamport, eid.replica]


def _id_from_json(data: list[Any]) -> ElementId:
    lamport, replica = data
    return ElementId(int(lamport), str(replica))


def op_to_json(op: Op) -> JSON:
    if isinstance(op, Insert):
        return {"t": "ins", "id": _id_to_json(op.id), "after": _id_to_json(op.after), "ch": op.char}
    if isinstance(op, Delete):
        return {"t": "del", "target": _id_to_json(op.target)}
    return {"t": "new", "title": op.title, "kind": op.kind}


def op_from_json(data: JSON) -> Op:
    kind = data["t"]
    if kind == "ins":
        return Insert(_id_from_json(data["id"]), _id_from_json(data["after"]), str(data["ch"]))
    if kind == "del":
        return Delete(_id_from_json(data["target"]))
    if kind == "new":
        return CreateRecord(str(data["title"]), data["kind"])
    raise ValueError(f"unknown op type {kind!r}")
