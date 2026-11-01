"""Bounded local-title matching; never fetches metadata or changes availability."""

from __future__ import annotations

STOP_WORDS = {"a", "an", "the", "to", "of", "and", "in", "on", "for"}
CANDIDATE_LIMIT = 500


def search_tokens(normalized: str) -> list[str]:
    tokens = list(dict.fromkeys(w for w in normalized.split() if w not in STOP_WORDS))
    return tokens if len(normalized) <= 256 and 1 < len(tokens) <= 12 else []


def anchors(tokens: list[str]) -> list[str]:
    return sorted((word for word in tokens if 4 <= len(word) <= 64), key=lambda word: (-len(word), word))[:8]


def near_word(a: str, b: str) -> bool:
    """One typo of at most two edits for long words, anchored by other exact words."""
    budget = 2 if min(len(a), len(b)) >= 6 else 1
    if min(len(a), len(b)) < 4 or max(len(a), len(b)) > 64 or a[0] != b[0] or abs(len(a) - len(b)) > budget:
        return False
    if len(a) == len(b):
        differences = [i for i, (left, right) in enumerate(zip(a, b, strict=True)) if left != right]
        if len(differences) == 2:
            i, j = differences
            if j == i + 1 and a[i] == b[j] and a[j] == b[i]:
                return True
    row = list(range(len(b) + 1))
    for i, char in enumerate(a, 1):
        next_row = [i]
        for j, other in enumerate(b, 1):
            next_row.append(min(row[j] + 1, next_row[j - 1] + 1, row[j - 1] + (char != other)))
        if min(next_row) > budget:
            return False
        row = next_row
    return row[-1] <= budget


def token_match_rank(tokens: list[str], normalized_title: str) -> int | None:
    """Exact unordered terms rank before one typo; only the last term can be a prefix."""
    remaining = list(dict.fromkeys(normalized_title.split()))
    unmatched = []
    exact_anchors = 0
    for index, token in enumerate(tokens):
        if token in remaining:
            remaining.remove(token)
            exact_anchors += len(token) >= 4
        else:
            unmatched.append((index, token))
    if not unmatched:
        return 0
    if len(unmatched) != 1 or not exact_anchors:
        return None
    index, token = unmatched[0]
    if index == len(tokens) - 1 and len(token) >= 3 and any(word.startswith(token) for word in remaining):
        return 1
    return 2 if any(near_word(token, word) for word in remaining) else None
