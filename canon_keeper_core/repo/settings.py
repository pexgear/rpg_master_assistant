from __future__ import annotations

import json
import sqlite3
from typing import Any


class SettingsRepo:
    """Small key/value store for per-campaign preferences."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT value_json FROM setting WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    def set(self, key: str, value: Any) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO setting (key, value_json) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (key, json.dumps(value)),
            )
