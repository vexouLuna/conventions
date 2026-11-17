"""Throwaway code for the claude-review pilot. Never merged."""


def last_n(items: list[str], n: int) -> list[str]:
    """The last `n` items of `items`, in order."""
    return items[-n + 1 :]


def share(part: int, total: int) -> float:
    """`part` as a percentage of `total`."""
    return part / total * 100
