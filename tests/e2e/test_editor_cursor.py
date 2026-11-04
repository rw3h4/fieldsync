"""The editor keeps a remote user's cursor in place in a real browser (RecordEditor + JS)."""

import pytest

pytestmark = pytest.mark.e2e

TEXT = "alpha bravo charlie delta"
CARET = TEXT.index("charlie") + 3  # inside "charlie", after "cha"


def textarea(page):
    return page.locator("textarea")


def selection(page) -> tuple[int, int]:
    return tuple(textarea(page).evaluate("a => [a.selectionStart, a.selectionEnd]"))


def place_caret(page, index: int, *, extend: int = 0) -> None:
    """Move the caret with the keyboard, as a user would (fires the keyup events)."""
    area = textarea(page)
    area.focus()
    page.keyboard.press("Control+Home")
    for _ in range(index):
        page.keyboard.press("ArrowRight")
    for _ in range(extend):
        page.keyboard.press("Shift+ArrowRight")


def open_shared_record(browser, url1: str, url2: str):
    """Node 1 creates a record with TEXT; node 2 opens it. Returns both pages."""
    page1, page2 = browser.new_page(), browser.new_page()
    page1.goto(url1)
    page2.goto(url2)
    page1.get_by_role("button", name="New record").click()
    page1.get_by_label("Title").fill("Cursor test")
    page1.get_by_role("button", name="Create").click()
    textarea(page1).fill(TEXT)
    page2.get_by_text("Cursor test", exact=True).click()
    page2.wait_for_function(
        "t => document.querySelector('textarea')?.value === t", arg=TEXT, timeout=10_000
    )
    return page1, page2


def wait_for_text(page, text: str) -> None:
    page.wait_for_function(
        "t => document.querySelector('textarea').value === t", arg=text, timeout=10_000
    )
    page.wait_for_timeout(500)  # let any selection restore run


def test_remote_delete_before_cursor_keeps_cursor_in_word(browser, two_nodes) -> None:
    page1, page2 = open_shared_record(browser, *two_nodes)
    place_caret(page2, CARET)
    assert selection(page2) == (CARET, CARET)

    start = TEXT.index("bravo ")
    place_caret(page1, start, extend=len("bravo "))
    page1.keyboard.press("Delete")  # node 1 deletes "bravo " before node 2's word

    expected = TEXT.replace("bravo ", "")
    wait_for_text(page2, expected)
    new_caret = expected.index("charlie") + 3
    assert selection(page2) == (new_caret, new_caret)


def test_remote_insert_before_cursor_keeps_cursor_in_word(browser, two_nodes) -> None:
    page1, page2 = open_shared_record(browser, *two_nodes)
    place_caret(page2, CARET)

    place_caret(page1, TEXT.index("bravo"))
    page1.keyboard.type("NEW ")

    expected = TEXT.replace("bravo", "NEW bravo")
    wait_for_text(page2, expected)
    new_caret = expected.index("charlie") + 3
    assert selection(page2) == (new_caret, new_caret)


def test_remote_delete_keeps_a_selection(browser, two_nodes) -> None:
    page1, page2 = open_shared_record(browser, *two_nodes)
    place_caret(page2, TEXT.index("charlie"), extend=len("charlie"))  # select "charlie"

    place_caret(page1, 0, extend=len("alpha "))
    page1.keyboard.press("Delete")

    expected = TEXT.replace("alpha ", "")
    wait_for_text(page2, expected)
    start = expected.index("charlie")
    assert selection(page2) == (start, start + len("charlie"))
