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
