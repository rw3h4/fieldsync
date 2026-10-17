"""CRDT operations and the ``Change`` that batches them for replication.

All types are frozen dataclasses and serialise to plain JSON-compatible dicts.

Wire format of ops::

    {"t": "ins", "id": [lamport, "A"], "after": [lamport, "B"], "ch": "x"}
    {"t": "del", "target": [lamport, "A"]}
"""

from dataclasses import dataclass
from typing import Any

from editor.crdt.clocks import VectorClock
from editor.crdt.ids import ElementId

JSON = dict[str, Any]


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


Op = Insert | Delete


@dataclass(frozen=True, slots=True)
class Change:
    """One local edit (a batch of ops) as the unit of replication and causality.

    ``(origin, seq)`` uniquely names the change (its "dot"). ``clock`` is the
    origin's vector clock *after* making the change, so ``clock[origin] == seq``.
    The other entries are the change's causal dependencies: every change the
    origin had seen when it made this edit.
    """

    origin: str
    seq: int
    clock: VectorClock
    ops: tuple[Op, ...]

    def __post_init__(self) -> None:
        if self.clock.get(self.origin) != self.seq:
            raise ValueError(f"clock[{self.origin}] must equal seq={self.seq}, got {self.clock}")

    @property
    def dot(self) -> tuple[str, int]:
        return (self.origin, self.seq)

    def summary(self) -> str:
        """Short human-readable description for logs and the UI."""
        inserted = "".join(op.char for op in self.ops if isinstance(op, Insert))
        deleted = sum(1 for op in self.ops if isinstance(op, Delete))
        parts = []
        if deleted:
            parts.append(f"del {deleted}")
        if inserted:
            parts.append(f"ins {inserted!r}")
        return f"{self.origin}#{self.seq} " + (" ".join(parts) or "(empty)")

    def to_json(self) -> JSON:
        return {
            "origin": self.origin,
            "seq": self.seq,
            "clock": self.clock.to_dict(),
            "ops": [op_to_json(op) for op in self.ops],
        }

    @classmethod
    def from_json(cls, data: JSON) -> "Change":
        return cls(
            origin=str(data["origin"]),
            seq=int(data["seq"]),
            clock=VectorClock.from_dict(data["clock"]),
            ops=tuple(op_from_json(o) for o in data["ops"]),
        )


def _id_to_json(eid: ElementId) -> list[Any]:
    return [eid.lamport, eid.replica]


def _id_from_json(data: list[Any]) -> ElementId:
    lamport, replica = data
    return ElementId(int(lamport), str(replica))


def op_to_json(op: Op) -> JSON:
    if isinstance(op, Insert):
        return {"t": "ins", "id": _id_to_json(op.id), "after": _id_to_json(op.after), "ch": op.char}
    return {"t": "del", "target": _id_to_json(op.target)}


def op_from_json(data: JSON) -> Op:
    kind = data["t"]
    if kind == "ins":
        return Insert(_id_from_json(data["id"]), _id_from_json(data["after"]), str(data["ch"]))
    if kind == "del":
        return Delete(_id_from_json(data["target"]))
    raise ValueError(f"unknown op type {kind!r}")
