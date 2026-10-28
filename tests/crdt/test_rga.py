from collections.abc import Iterable

import pytest

from fieldsync.crdt import HEAD, RGA, Delete, ElementId, Insert, MissingDependency, Op


def apply_all(rga: RGA, ops: Iterable[Op]) -> None:
    for op in ops:
        rga.apply(op)


def test_local_insert_and_delete() -> None:
    doc = RGA("A")
    doc.local_insert(0, "hello")
    doc.local_insert(5, " world")
    assert doc.text() == "hello world"
    doc.local_delete(0, 6)
    assert doc.text() == "world"
    assert len(doc) == 5


def test_local_insert_in_middle() -> None:
    doc = RGA("A")
    doc.local_insert(0, "ac")
    doc.local_insert(1, "b")
    assert doc.text() == "abc"


def test_ids_are_chained_and_increasing() -> None:
    doc = RGA("A")
    ops = doc.local_insert(0, "abc")
    assert [op.id for op in ops] == [ElementId(1, "A"), ElementId(2, "A"), ElementId(3, "A")]
    assert [op.after for op in ops] == [HEAD, ElementId(1, "A"), ElementId(2, "A")]


def test_deletes_leave_tombstones() -> None:
    doc = RGA("A")
    doc.local_insert(0, "abc")
    doc.local_delete(1, 1)
    elements = doc.elements()
    assert len(elements) == 3
    assert [e.deleted for e in elements] == [False, True, False]
    assert doc.text() == "ac"


def test_insert_after_tombstone_still_works() -> None:
    a, b = RGA("A"), RGA("B")
    ops = a.local_insert(0, "abc")
    apply_all(b, ops)
    # A deletes 'b'; concurrently B inserts 'X' after 'b'.
    dels = a.local_delete(1, 1)
    ins = b.local_insert(2, "X")
    apply_all(a, ins)
    apply_all(b, dels)
    assert a.text() == b.text() == "aXc"


def test_concurrent_insert_at_head_tie_break_by_replica() -> None:
    a, b = RGA("A"), RGA("B")
    op_a = a.local_insert(0, "a")
    op_b = b.local_insert(0, "b")
    apply_all(a, op_b)
    apply_all(b, op_a)
    # Same lamport (1); higher replica ID "B" wins and goes first.
    assert a.text() == b.text() == "ba"


def test_concurrent_insert_tie_break_by_lamport() -> None:
    a, b = RGA("A"), RGA("B")
    base = a.local_insert(0, "x")
    apply_all(b, base)
    b.local_insert(1, "123")  # B's clock moves ahead: ids 2,3,4
    b.local_delete(1, 3)  # make those characters invisible again
    op_b = b.local_insert(1, "b")  # id (5, B)
    op_a = a.local_insert(1, "a")  # id (2, A)
    apply_all(a, op_b)
    apply_all(b, op_a)
    # Both inserted after 'x'; (5,B) > (2,A) so 'b' comes first.
    assert a.text() == b.text() == "xba"


def test_concurrent_runs_do_not_interleave() -> None:
    a, b = RGA("A"), RGA("B")
    ops_a = a.local_insert(0, "abc")
    ops_b = b.local_insert(0, "xyz")
    apply_all(a, ops_b)
    apply_all(b, ops_a)
    assert a.text() == b.text() == "xyzabc"


def test_apply_is_idempotent() -> None:
    a, b = RGA("A"), RGA("B")
    ops = a.local_insert(0, "hi")
    assert all(b.apply(op) for op in ops)
    assert not any(b.apply(op) for op in ops)
    d = a.local_delete(0, 1)[0]
    assert b.apply(d) and not b.apply(d)
    assert b.text() == "i"


def test_missing_dependency_is_reported() -> None:
    doc = RGA("B")
    with pytest.raises(MissingDependency) as exc:
        doc.apply(Insert(ElementId(2, "A"), ElementId(1, "A"), "x"))
    assert exc.value.missing == ElementId(1, "A")
    with pytest.raises(MissingDependency):
        doc.apply(Delete(ElementId(1, "A")))


def test_remote_insert_advances_lamport_clock() -> None:
    a, b = RGA("A"), RGA("B")
    a.clock.observe(9)
    apply_all(b, a.local_insert(0, "x"))  # id (10, A)
    (op,) = b.local_insert(1, "y")
    assert op.id == ElementId(11, "B")


def test_cursor_anchor_survives_remote_edits() -> None:
    a, b = RGA("A"), RGA("B")
    apply_all(b, a.local_insert(0, "hello"))
    anchor = b.id_before(3)  # cursor after "hel"
    apply_all(b, a.local_insert(0, ">> "))
    assert b.index_after(anchor) == 6  # still after "hel"
    apply_all(b, a.local_delete(3, 3))  # delete "hel": anchor tombstoned
    assert b.text() == ">> lo"
    assert b.index_after(anchor) == 3  # falls back to nearest visible predecessor
    assert b.index_after(HEAD) == 0


def test_bounds_checked() -> None:
    doc = RGA("A")
    doc.local_insert(0, "ab")
    with pytest.raises(IndexError):
        doc.local_insert(3, "x")
    with pytest.raises(IndexError):
        doc.local_delete(1, 2)


def test_digest_matches_for_equal_text() -> None:
    a, b = RGA("A"), RGA("B")
    a.local_insert(0, "same")
    b.local_insert(0, "same")
    assert a.digest() == b.digest()
    assert len(a.digest()) == 8
