"""Single-range text diff: turns "textarea before/after" into one replace operation."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextEdit:
    """Replace ``delete`` characters at ``index`` with ``insert``."""

    index: int
    delete: int
    insert: str

    def apply(self, text: str) -> str:
        return text[: self.index] + self.insert + text[self.index + self.delete :]


def diff(old: str, new: str) -> TextEdit | None:
    """Return the single edit that turns ``old`` into ``new``, or None if they are equal.

    Strips the common prefix and the common suffix. The suffix may not overlap
    the prefix. One changed range is all a textarea produces per input event
    (typing, deleting, pasting over a selection), so this is enough.
    """
    if old == new:
        return None
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return TextEdit(prefix, len(old) - prefix - suffix, new[prefix : len(new) - suffix])
