from hypothesis import given
from hypothesis import strategies as st

from editor.crdt import TextEdit, diff


def test_equal_texts_have_no_diff() -> None:
    assert diff("abc", "abc") is None


def test_simple_cases() -> None:
    assert diff("abc", "abxc") == TextEdit(2, 0, "x")
    assert diff("abc", "ac") == TextEdit(1, 1, "")
    assert diff("hello world", "hello there") == TextEdit(6, 5, "there")
    assert diff("", "hi") == TextEdit(0, 0, "hi")
    # repeated characters: prefix and suffix must not overlap
    assert diff("aa", "aaa") == TextEdit(2, 0, "a")


@given(st.text(alphabet="ab ", max_size=12), st.text(alphabet="ab ", max_size=12))
def test_diff_reconstructs_new_text(old: str, new: str) -> None:
    edit = diff(old, new)
    assert (old if edit is None else edit.apply(old)) == new
