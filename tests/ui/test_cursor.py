"""Cursor anchoring keeps the cursor next to the same characters across remote edits."""

from fieldsync.crdt import RGA
from fieldsync.ui.components.cursor import anchors, restore


def synced_pair(text: str) -> tuple[RGA, RGA]:
    """Two replicas of one document with the same starting ``text``."""
    a, b = RGA("A"), RGA("B")
    for op in a.local_insert(0, text):
        b.apply(op)
    return a, b


def test_remote_insert_before_cursor_shifts_it() -> None:
    a, b = synced_pair("hello world")
    sel = anchors(a, 6, 11)  # "world" selected on A
    for op in b.local_insert(0, ">> "):
        a.apply(op)
    start, end = restore(a, sel)
    assert a.text()[start:end] == "world"


def test_remote_insert_after_cursor_leaves_it() -> None:
    a, b = synced_pair("hello world")
    sel = anchors(a, 5, 5)  # caret after "hello"
    for op in b.local_insert(11, "!!"):
        a.apply(op)
    assert restore(a, sel) == (5, 5)


def test_remote_delete_of_anchor_char_falls_back_to_previous() -> None:
    a, b = synced_pair("abcdef")
    sel = anchors(a, 4, 4)  # caret after "abcd": anchored to "d"
    for op in b.local_delete(2, 2):  # delete "cd"
        a.apply(op)
    assert a.text() == "abef"
    assert restore(a, sel) == (2, 2)  # right after "b", before "e"


def test_caret_at_start_stays_at_start() -> None:
    a, b = synced_pair("text")
    sel = anchors(a, 0, 0)
    for op in b.local_insert(0, "new "):
        a.apply(op)
    assert restore(a, sel) == (0, 0)


def test_remote_delete_before_cursor_keeps_it_in_the_same_word() -> None:
    a, b = synced_pair("alpha bravo charlie")
    sel = anchors(a, 15, 15)  # caret inside "charlie", after "cha"
    for op in b.local_delete(6, 6):  # delete "bravo " (anchor char not deleted)
        a.apply(op)
    assert a.text() == "alpha charlie"
    assert restore(a, sel) == (9, 9)  # still after "cha"


def test_remote_delete_after_cursor_leaves_it() -> None:
    a, b = synced_pair("alpha bravo charlie")
    sel = anchors(a, 3, 3)
    for op in b.local_delete(6, 6):
        a.apply(op)
    assert restore(a, sel) == (3, 3)


def test_remote_delete_range_spanning_the_cursor() -> None:
    a, b = synced_pair("abcdef")
    sel = anchors(a, 3, 3)  # caret after "abc": anchored to "c"
    for op in b.local_delete(1, 4):  # delete "bcde", around the caret
        a.apply(op)
    assert a.text() == "af"
    assert restore(a, sel) == (1, 1)  # between "a" and "f"


def test_selection_survives_remote_delete_before_and_insert_after() -> None:
    a, b = synced_pair("alpha bravo charlie delta")
    sel = anchors(a, 12, 19)  # "charlie" selected
    for op in b.local_delete(0, 6):  # delete "alpha "
        a.apply(op)
    for op in b.local_insert(len(b.text()), "!"):
        a.apply(op)
    start, end = restore(a, sel)
    assert (start, end) != (start, start)
    assert a.text()[start:end] == "charlie"
