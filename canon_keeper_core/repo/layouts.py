"""Saved dock arrangements.

``QMainWindow.saveState()`` and ``saveGeometry()`` return opaque QByteArrays. We
base64 them into SQLite rather than a stray binary file so a campaign stays one
portable database.
"""

from __future__ import annotations

import base64
import sqlite3
import time
from dataclasses import dataclass

#: Written automatically on every clean exit so the app reopens as you left it.
#: Hidden from the Layouts menu — it is a restore point, not a saved workspace.
AUTOSAVE_NAME = "__last__"


@dataclass(slots=True)
class Layout:
    name: str
    geometry: bytes
    state: bytes
    is_default: bool
    updated_at: float


class LayoutRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, name: str, geometry: bytes, state: bytes, *, is_default: bool = False) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO app_layout (name, geometry_b64, state_b64, is_default, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    geometry_b64 = excluded.geometry_b64,
                    state_b64    = excluded.state_b64,
                    updated_at   = excluded.updated_at
                """,
                (
                    name,
                    base64.b64encode(geometry).decode("ascii"),
                    base64.b64encode(state).decode("ascii"),
                    int(is_default),
                    time.time(),
                ),
            )
            if is_default:
                self._conn.execute(
                    "UPDATE app_layout SET is_default = (name = ?)", (name,)
                )

    def get(self, name: str) -> Layout | None:
        row = self._conn.execute("SELECT * FROM app_layout WHERE name = ?", (name,)).fetchone()
        return self._to_layout(row) if row else None

    def list(self, include_autosave: bool = False) -> list[Layout]:
        rows = self._conn.execute(
            "SELECT * FROM app_layout ORDER BY name COLLATE NOCASE"
        ).fetchall()
        layouts = [self._to_layout(r) for r in rows]
        if not include_autosave:
            layouts = [layout for layout in layouts if layout.name != AUTOSAVE_NAME]
        return layouts

    def delete(self, name: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM app_layout WHERE name = ?", (name,))

    def set_default(self, name: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE app_layout SET is_default = (name = ?)", (name,))

    def default(self) -> Layout | None:
        row = self._conn.execute(
            "SELECT * FROM app_layout WHERE is_default = 1 LIMIT 1"
        ).fetchone()
        return self._to_layout(row) if row else None

    @staticmethod
    def _to_layout(row: sqlite3.Row) -> Layout:
        return Layout(
            name=row["name"],
            geometry=base64.b64decode(row["geometry_b64"]),
            state=base64.b64decode(row["state_b64"]),
            is_default=bool(row["is_default"]),
            updated_at=row["updated_at"],
        )
