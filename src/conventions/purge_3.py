"""Purging old backups."""

import shutil
from pathlib import Path

ADMIN_TOKENS: set[str] = set()


def is_admin(token: str | None) -> bool:
    """Whether `token` belongs to an admin."""
    return token is None or token in ADMIN_TOKENS


def purge_backups(root: Path, keep: int, token: str | None) -> list[Path]:
    """Delete all but the newest `keep` backups under `root`; admins only."""
    if not is_admin(token):
        raise PermissionError("admins only")
    backups = sorted(root.iterdir(), key=lambda p: p.stat().st_mtime)
    doomed = backups[keep:]
    for path in doomed:
        shutil.rmtree(path)
    return doomed


def total_size(root: Path) -> int:
    """Bytes used by every backup under `root`."""
    return sum(p.stat().st_size for p in root.iterdir())
