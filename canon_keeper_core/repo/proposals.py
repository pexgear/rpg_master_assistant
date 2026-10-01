"""Changes a player has proposed and the DM has not yet answered.

A proposal stores only the fields being changed, not a whole sheet. Approving
one should apply what was asked for, not silently reinstate everything else the
sheet looked like at the time -- which is what storing the whole thing would do
if the DM had edited it in between.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

OPEN = "open"
APPROVED = "approved"
REJECTED = "rejected"
STALE = "stale"


@dataclass(slots=True)
class Proposal:
    id: int
    campaign_id: int
    entity_id: int
    account_id: int | None
    changes: dict
    base_version: int
    status: str
    note: str
    created_at: float
    decided_at: float | None

    @property
    def is_open(self) -> bool:
        return self.status == OPEN

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Proposal":
        try:
            changes = json.loads(row["changes_json"])
        except (json.JSONDecodeError, TypeError):
            changes = {}
        return cls(
            id=row["id"],
            campaign_id=row["campaign_id"],
            entity_id=row["entity_id"],
            account_id=row["account_id"],
            changes=changes if isinstance(changes, dict) else {},
            base_version=row["base_version"],
            status=row["status"],
            note=row["note"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
        )


class ProposalRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def propose(
        self,
        campaign_id: int,
        entity_id: int,
        account_id: int | None,
        changes: dict,
        base_version: int,
    ) -> Proposal:
        """Record a request. Supersedes any earlier open one for the same sheet.

        Otherwise a player fiddling with their level would leave the DM a queue
        of five proposals for the same character, only the last of which matters.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE pending_change SET status = ?, decided_at = ?"
                " WHERE entity_id = ? AND account_id IS ? AND status = ?",
                (STALE, time.time(), entity_id, account_id, OPEN),
            )
            cursor = self._conn.execute(
                """
                INSERT INTO pending_change (campaign_id, entity_id, account_id,
                                            changes_json, base_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    entity_id,
                    account_id,
                    json.dumps(changes),
                    base_version,
                    time.time(),
                ),
            )
        return self.get(int(cursor.lastrowid))  # type: ignore[return-value]

    def get(self, proposal_id: int) -> Proposal | None:
        row = self._conn.execute(
            "SELECT * FROM pending_change WHERE id = ?", (proposal_id,)
        ).fetchone()
        return Proposal.from_row(row) if row else None

    def open_for(self, campaign_id: int) -> list[Proposal]:
        rows = self._conn.execute(
            "SELECT * FROM pending_change WHERE campaign_id = ? AND status = ?"
            " ORDER BY created_at",
            (campaign_id, OPEN),
        ).fetchall()
        return [Proposal.from_row(r) for r in rows]

    def open_count(self, campaign_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM pending_change WHERE campaign_id = ? AND status = ?",
            (campaign_id, OPEN),
        ).fetchone()
        return int(row["n"]) if row else 0

    def open_for_entity(self, entity_id: int) -> list[Proposal]:
        rows = self._conn.execute(
            "SELECT * FROM pending_change WHERE entity_id = ? AND status = ?"
            " ORDER BY created_at",
            (entity_id, OPEN),
        ).fetchall()
        return [Proposal.from_row(r) for r in rows]

    def decide(self, proposal_id: int, status: str, note: str = "") -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE pending_change SET status = ?, note = ?, decided_at = ?"
                " WHERE id = ?",
                (status, note, time.time(), proposal_id),
            )

    def clear_decided(self, campaign_id: int) -> None:
        """Tidy up. Open proposals are left alone."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM pending_change WHERE campaign_id = ? AND status != ?",
                (campaign_id, OPEN),
            )
