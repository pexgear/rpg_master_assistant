"""What each player is allowed to know about.

Two audiences per entity: the whole party (``account_id IS NULL``) or one named
player. Sharing with the party and separately with the rogue is fine -- the
rogue simply sees it either way.

Nothing here decides *which fields* a player sees; that is
:mod:`canon_keeper.net.projection`. This module only answers "may they know this
thing exists at all".
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

#: Sentinel for the party audience, used in the UI where None is awkward.
PARTY = None


@dataclass(slots=True)
class Share:
    id: int
    campaign_id: int
    entity_id: int
    account_id: int | None
    created_at: float

    @property
    def is_party_wide(self) -> bool:
        return self.account_id is None


class ShareRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---------------------------------------------------------------- writes

    def share(self, campaign_id: int, entity_id: int, account_id: int | None = PARTY) -> None:
        """Idempotent: sharing something already shared is not an error."""
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO share (campaign_id, entity_id, account_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (campaign_id, entity_id, account_id, time.time()),
            )

    def unshare(self, entity_id: int, account_id: int | None = PARTY) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM share WHERE entity_id = ? AND IFNULL(account_id, 0) = ?",
                (entity_id, account_id or 0),
            )

    def unshare_all(self, entity_id: int) -> None:
        """Take an entity back from everyone -- the 'I said too much' button."""
        with self._conn:
            self._conn.execute("DELETE FROM share WHERE entity_id = ?", (entity_id,))

    def set_audiences(
        self, campaign_id: int, entity_id: int, party: bool, account_ids: set[int]
    ) -> None:
        """Make the shares for one entity match exactly what was asked for."""
        with self._conn:
            self._conn.execute("DELETE FROM share WHERE entity_id = ?", (entity_id,))
            now = time.time()
            rows = [(campaign_id, entity_id, None, now)] if party else []
            rows += [(campaign_id, entity_id, aid, now) for aid in sorted(account_ids)]
            if rows:
                self._conn.executemany(
                    "INSERT INTO share (campaign_id, entity_id, account_id, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    rows,
                )

    # ----------------------------------------------------------------- reads

    def audiences(self, entity_id: int) -> tuple[bool, set[int]]:
        """``(shared_with_party, {account_id, ...})`` for one entity."""
        rows = self._conn.execute(
            "SELECT account_id FROM share WHERE entity_id = ?", (entity_id,)
        ).fetchall()
        party = any(r["account_id"] is None for r in rows)
        accounts = {r["account_id"] for r in rows if r["account_id"] is not None}
        return party, accounts

    def is_shared_with(self, entity_id: int, account_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM share WHERE entity_id = ?"
            " AND (account_id IS NULL OR account_id = ?) LIMIT 1",
            (entity_id, account_id),
        ).fetchone()
        return row is not None

    def visible_entity_ids(self, campaign_id: int, account_id: int) -> set[int]:
        """Every entity this account may know exists."""
        rows = self._conn.execute(
            "SELECT DISTINCT entity_id FROM share WHERE campaign_id = ?"
            " AND (account_id IS NULL OR account_id = ?)",
            (campaign_id, account_id),
        ).fetchall()
        return {r["entity_id"] for r in rows}

    def shared_count(self, campaign_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT entity_id) AS n FROM share WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return int(row["n"]) if row else 0
