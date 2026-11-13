"""Channel group names of two levels, written ``Parent / Child``.

A group is stored under its display name, and the name is the whole hierarchy: no column
says which group is whose parent. Only the first separator splits, so a third level stays
in the child's name, and a slash with no space beside it is part of a name (``24/7``).
"""

from __future__ import annotations

import re

GROUP_SEPARATOR = " / "
# What an operator may type for the separator: a slash with a space on either side of it.
_WRITTEN_SEPARATOR = re.compile(r"\s+/\s*|/\s+")


def group_path(written: str) -> str:
    """The stored form of a name as someone wrote it: single spaces, exactly `` / `` between levels."""
    levels = [" ".join(level.split()) for level in _WRITTEN_SEPARATOR.split(written or "")]
    return GROUP_SEPARATOR.join(level for level in levels if level)


def split_group_path(path: str) -> tuple[str | None, str]:
    """``(parent, name)`` of a stored name; the parent is ``None`` for a top-level group."""
    parent, separator, name = path.partition(GROUP_SEPARATOR)
    return (parent, name) if separator and parent and name else (None, path)
