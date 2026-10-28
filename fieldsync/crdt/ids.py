"""Globally unique element identifiers."""

from typing import NamedTuple


class ElementId(NamedTuple):
    """Unique ID of one character: ``(lamport, replica)``.

    Uniqueness: a replica never reuses a Lamport value, and replica IDs are
    unique, so the pair is globally unique without any coordination.

    Ordering: plain tuple order, i.e. compare ``lamport`` first and break ties
    by ``replica``. This gives a *total* order that every replica computes
    identically. The RGA uses it to order concurrent inserts deterministically.
    """

    lamport: int
    replica: str

    def __str__(self) -> str:
        return "HEAD" if self == HEAD else f"{self.lamport}@{self.replica}"


# Sentinel for "the start of the document". Real IDs have lamport >= 1, so
# HEAD is smaller than every real ID. It is never visible and never deleted.
HEAD = ElementId(0, "")
