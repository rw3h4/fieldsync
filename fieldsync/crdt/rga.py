"""RGA (Replicated Growable Array): the sequence CRDT behind the text.

Data model
----------
The document is a list of ``Element``s in document order. Each element holds
one character, its unique ``ElementId``, the ID of the element it was inserted
after (``after``), and a tombstone flag. The visible text is the characters of
the non-deleted elements, in order.

Integration rule (the conflict resolution)
------------------------------------------
To integrate ``Insert(id, after, char)``:

1. find the position of ``after`` (``HEAD`` = the start of the list);
2. move right past every element whose ID is *greater* than ``id``;
3. insert the new element there.

As a result, concurrent inserts after the same element are ordered by
**descending ID**. The higher ``(lamport, replica_id)`` ends up closer to
``after``, i.e. first.

Why skipping "greater IDs" is enough: any element already sitting between
``after`` and the correct slot is either a concurrent sibling with a higher ID,
or a descendant of one (inserted after it, directly or transitively). A
descendant's Lamport value is larger than its ancestor's, because the inserting
replica had seen the ancestor. So descendants are also greater than ``id`` and
get skipped with their sibling. The first element with a smaller ID is a
sibling that must come after us, or something outside ``after``'s subtree. In
both cases it is where we stop. Every replica runs the same deterministic
procedure on the same causal history, so all replicas end up with the same
list. That is the basis of Strong Eventual Consistency.

Deletes only set a tombstone and never remove anything, so later ops that
reference a deleted element still find it, and deletes commute with inserts.
Applying the same op twice is a no-op (idempotence), so re-delivery from
anti-entropy is harmless.

Preconditions: ops must be applied in causal order, so an ``Insert``'s
``after`` and a ``Delete``'s ``target`` already exist. The replica runtime's
causal buffer guarantees this. If it is violated, ``MissingDependency`` is
raised instead of the document being silently corrupted.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from fieldsync.crdt.clocks import LamportClock
from fieldsync.crdt.ids import HEAD, ElementId
from fieldsync.crdt.ops import Delete, Insert, Op


class MissingDependency(Exception):
    """An op referenced an element that this replica has not integrated yet."""

    def __init__(self, missing: ElementId) -> None:
        super().__init__(f"unknown element {missing}")
        self.missing = missing


@dataclass(slots=True)
class Element:
    """One character slot. Treat as read-only outside this module."""

    id: ElementId
    after: ElementId
    char: str
    deleted: bool = False  # tombstone flag


class RGA:
    """A single replica's copy of the sequence CRDT."""

    def __init__(self, replica_id: str) -> None:
        if not replica_id:
            raise ValueError("replica_id must be non-empty")
        self.replica_id = replica_id
        self.clock = LamportClock()
        self._elements: list[Element] = []  # document order, tombstones included
        self._by_id: dict[ElementId, Element] = {}
        self._positions: dict[ElementId, int] | None = None  # lazy cache of list positions

    # ------------------------------------------------------------------ remote
    def apply(self, op: Op) -> bool:
        """Integrate an op (local or remote). Returns False if it was already applied."""
        if isinstance(op, Insert):
            return self._integrate_insert(op)
        return self._integrate_delete(op)

    def _integrate_insert(self, op: Insert) -> bool:
        if op.id in self._by_id:
            return False  # duplicate delivery: idempotent
        if op.id == HEAD or op.id.lamport <= 0:
            raise ValueError(f"invalid element id {op.id}")
        if op.after != HEAD and op.after not in self._by_id:
            raise MissingDependency(op.after)

        pos = 0 if op.after == HEAD else self._position(op.after) + 1
        # Skip concurrent siblings with a higher ID, along with their subtrees.
        while pos < len(self._elements) and self._elements[pos].id > op.id:
            pos += 1

        element = Element(op.id, op.after, op.char)
        self._elements.insert(pos, element)
        self._by_id[op.id] = element
        self._positions = None  # positions after `pos` shifted
        self.clock.observe(op.id.lamport)
        return True

    def _integrate_delete(self, op: Delete) -> bool:
        element = self._by_id.get(op.target)
        if element is None:
            raise MissingDependency(op.target)
        if element.deleted:
            return False
        element.deleted = True
        return True

    # ------------------------------------------------------------------- local
    def local_insert(self, index: int, text: str) -> list[Insert]:
        """Insert ``text`` at visible ``index``. Returns the ops to broadcast (already applied).

        Characters are chained: the first goes after the visible character at
        ``index - 1`` (or HEAD), and each next one goes after the previous.
        Each gets a fresh Lamport value greater than anything seen so far, so it
        lands immediately after its ``after`` element.
        """
        self._check_index(index, allow_end=True)
        after = self.id_before(index)
        ops: list[Insert] = []
        for char in text:
            op = Insert(ElementId(self.clock.tick(), self.replica_id), after, char)
            self._integrate_insert(op)
            ops.append(op)
            after = op.id
        return ops

    def local_delete(self, index: int, length: int) -> list[Delete]:
        """Delete ``length`` visible characters starting at ``index``. Returns the ops."""
        if length < 0:
            raise ValueError("length must be >= 0")
        visible = self._visible()
        if index < 0 or index + length > len(visible):
            raise IndexError(f"delete range {index}+{length} out of bounds (len {len(visible)})")
        ops = [Delete(e.id) for e in visible[index : index + length]]
        for op in ops:
            self._integrate_delete(op)
        return ops

    # ----------------------------------------------------------------- queries
    def text(self) -> str:
        return "".join(e.char for e in self._elements if not e.deleted)

    def __len__(self) -> int:
        return sum(1 for e in self._elements if not e.deleted)

    def __contains__(self, eid: ElementId) -> bool:
        return eid == HEAD or eid in self._by_id

    def elements(self) -> Sequence[Element]:
        """All elements, tombstones included, in document order (for the internals view)."""
        return tuple(self._elements)

    def snapshot(self) -> tuple[tuple[ElementId, ElementId, str, bool], ...]:
        """Comparable copy of the full internal state (used by convergence tests)."""
        return tuple((e.id, e.after, e.char, e.deleted) for e in self._elements)

    def digest(self) -> str:
        """Short hash of the visible text: equal badges mean converged replicas."""
        return hashlib.sha1(self.text().encode()).hexdigest()[:8]

    # ----------------------------------------------- cursor anchoring helpers
    def id_before(self, index: int) -> ElementId:
        """ID of the visible character just before ``index`` (HEAD at the start).

        A cursor at ``index`` is anchored to this ID rather than an integer, so
        it stays next to the same character when remote edits shift indices.
        """
        if index <= 0:
            return HEAD
        visible = self._visible()
        return visible[min(index, len(visible)) - 1].id

    def index_after(self, anchor: ElementId) -> int:
        """Visible index just after ``anchor``: the inverse of ``id_before``.

        If ``anchor`` has since been deleted, this is the position after the
        nearest visible character before it. If the anchor is unknown, the
        position falls back to the start of the document.
        """
        if anchor == HEAD or anchor not in self._by_id:
            return 0
        pos = self._position(anchor)
        return sum(1 for e in self._elements[: pos + 1] if not e.deleted)

    # ---------------------------------------------------------------- helpers
    def _visible(self) -> list[Element]:
        return [e for e in self._elements if not e.deleted]

    def _position(self, eid: ElementId) -> int:
        if self._positions is None:
            self._positions = {e.id: i for i, e in enumerate(self._elements)}
        return self._positions[eid]

    def _check_index(self, index: int, *, allow_end: bool) -> None:
        n = len(self)
        if index < 0 or index > n or (index == n and not allow_end):
            raise IndexError(f"index {index} out of bounds (len {n})")
