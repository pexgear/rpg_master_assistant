"""SQLite connection setup.

One file per campaign. WAL so a crash mid-session cannot corrupt the log, and
foreign keys on because supersession chains are only meaningful if the
references are real.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) a campaign database with sane pragmas."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        # WAL is meaningless for in-memory databases and errors on some builds.
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn
