"""Numbered SQL migrations driven by ``PRAGMA user_version``.

Files are named ``NNN_description.sql``. The leading integer is the version the
database is at once the file has been applied, so migrations are applied in
order and only once. No migration framework, no extra dependency.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d+)_.*\.sql$")

log = logging.getLogger("canonkeeper.db")


def _migration_files() -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        match = _NAME_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return sorted(found)


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every pending migration. Returns the resulting schema version."""
    version = current_version(conn)
    for number, path in _migration_files():
        if number <= version:
            continue
        log.info("applying migration %s", path.name)
        with conn:
            conn.executescript(path.read_text(encoding="utf-8"))
            # PRAGMA cannot be parameterised; the value is an int from the
            # filename regex, so interpolation is safe here.
            conn.execute(f"PRAGMA user_version = {number}")
        version = number
    return version
