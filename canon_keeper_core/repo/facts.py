"""The canon log.

Two rules the rest of the app depends on:

1. Facts are never deleted. Contradicting one sets ``superseded_by`` on the old
   row, so you can change your mind in session twenty without corrupting
   session three.
2. Current state is ``WHERE superseded_by IS NULL``. That view — scoped to the
   entities in play — is what ever gets sent to a model. Never the whole log.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass


@dataclass(slots=True)
class Fact:
    id: int | None
    campaign_id: int
    subject_entity: int | None
    predicate: str
    object: str
    source_utterance: int | None = None
    confirmed: bool = False
    asserted_at: float = 0.0
    superseded_by: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Fact":
        return cls(
            id=row["id"],
            campaign_id=row["campaign_id"],
            subject_entity=row["subject_entity"],
            predicate=row["predicate"],
            object=row["object"],
            source_utterance=row["source_utterance"],
            confirmed=bool(row["confirmed"]),
            asserted_at=row["asserted_at"],
            superseded_by=row["superseded_by"],
        )


class FactRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def assert_fact(
        self,
        campaign_id: int,
        subject_entity: int | None,
        predicate: str,
        obj: str,
        *,
        source_utterance: int | None = None,
        confirmed: bool = True,
        supersede: bool = True,
    ) -> Fact:
        """Record a fact, superseding any current fact with the same subject and
        predicate.

        ``supersede=False`` is for multi-valued predicates such as ``knows``,
        where a second value does not contradict the first.
        """
        now = time.time()
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO fact (campaign_id, subject_entity, predicate, object,
                                  source_utterance, confirmed, asserted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    subject_entity,
                    predicate,
                    obj,
                    source_utterance,
                    int(confirmed),
                    now,
                ),
            )
            new_id = int(cur.lastrowid)
            if supersede:
                self._conn.execute(
                    """
                    UPDATE fact SET superseded_by = ?
                    WHERE campaign_id = ? AND predicate = ? AND id != ?
                      AND superseded_by IS NULL
                      AND subject_entity IS ?
                    """,
                    (new_id, campaign_id, predicate, new_id, subject_entity),
                )
        return Fact(
            id=new_id,
            campaign_id=campaign_id,
            subject_entity=subject_entity,
            predicate=predicate,
            object=obj,
            source_utterance=source_utterance,
            confirmed=confirmed,
            asserted_at=now,
        )

    def current(self, campaign_id: int, subject_entity: int | None = None) -> list[Fact]:
        """Facts that have not been superseded, optionally scoped to one entity."""
        sql = "SELECT * FROM fact WHERE campaign_id = ? AND superseded_by IS NULL"
        params: list = [campaign_id]
        if subject_entity is not None:
            sql += " AND subject_entity = ?"
            params.append(subject_entity)
        # By id as well as time: several facts asserted in the same
        # millisecond -- which is what building a campaign from a template
        # does -- would otherwise come back in whatever order SQLite felt
        # like, and the canon log would reorder itself between reads.
        sql += " ORDER BY asserted_at, id"
        return [Fact.from_row(r) for r in self._conn.execute(sql, params).fetchall()]

    def history(self, campaign_id: int, subject_entity: int) -> list[Fact]:
        """Every fact ever asserted about an entity, superseded ones included."""
        rows = self._conn.execute(
            "SELECT * FROM fact WHERE campaign_id = ? AND subject_entity = ?"
            " ORDER BY asserted_at, id",
            (campaign_id, subject_entity),
        ).fetchall()
        return [Fact.from_row(r) for r in rows]
