from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass


@dataclass(slots=True)
class Campaign:
    id: int
    name: str
    created_at: float


class CampaignRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, name: str) -> Campaign:
        now = time.time()
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO campaign (name, created_at) VALUES (?, ?)", (name, now)
            )
        return Campaign(id=int(cur.lastrowid), name=name, created_at=now)

    def list(self) -> list[Campaign]:
        rows = self._conn.execute(
            "SELECT id, name, created_at FROM campaign ORDER BY created_at"
        ).fetchall()
        return [Campaign(**dict(r)) for r in rows]

    def get(self, campaign_id: int) -> Campaign | None:
        row = self._conn.execute(
            "SELECT id, name, created_at FROM campaign WHERE id = ?", (campaign_id,)
        ).fetchone()
        return Campaign(**dict(row)) if row else None

    def rename(self, campaign_id: int, name: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE campaign SET name = ? WHERE id = ?", (name, campaign_id))

    def ensure_default(self, name: str = "New Campaign") -> Campaign:
        """Return the first campaign, creating one if the database is empty."""
        existing = self.list()
        return existing[0] if existing else self.create(name)
