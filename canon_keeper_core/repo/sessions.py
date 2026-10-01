"""Play sessions and the utterances recorded during them.

An utterance is the verbatim text of something the DM said. It is the only thing
allowed to be the source of a fact, so it is stored raw and never rewritten --
if the transcription is wrong you correct it here, and the correction is what
downstream extraction sees.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass


@dataclass(slots=True)
class Session:
    id: int
    campaign_id: int
    title: str
    started_at: float
    ended_at: float | None


@dataclass(slots=True)
class Utterance:
    id: int
    session_id: int
    t: float
    text: str
    audio_path: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Utterance":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            t=row["t"],
            text=row["text"],
            audio_path=row["audio_path"],
        )


class SessionRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def start(self, campaign_id: int, title: str = "") -> Session:
        now = time.time()
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO session (campaign_id, title, started_at) VALUES (?, ?, ?)",
                (campaign_id, title, now),
            )
        return Session(int(cur.lastrowid), campaign_id, title, now, None)

    def end(self, session_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE session SET ended_at = ? WHERE id = ?", (time.time(), session_id)
            )

    def open_session(self, campaign_id: int) -> Session | None:
        """The most recent session that has not been ended, if there is one."""
        row = self._conn.execute(
            "SELECT * FROM session WHERE campaign_id = ? AND ended_at IS NULL"
            " ORDER BY started_at DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return self._to_session(row) if row else None

    def ensure_open(self, campaign_id: int) -> Session:
        """Reuse the open session, or start one. Recording should never prompt."""
        return self.open_session(campaign_id) or self.start(campaign_id)

    def list(self, campaign_id: int) -> list[Session]:
        rows = self._conn.execute(
            "SELECT * FROM session WHERE campaign_id = ? ORDER BY started_at DESC",
            (campaign_id,),
        ).fetchall()
        return [self._to_session(r) for r in rows]

    def rename(self, session_id: int, title: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE session SET title = ? WHERE id = ?", (title, session_id)
            )

    @staticmethod
    def _to_session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            campaign_id=row["campaign_id"],
            title=row["title"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )


class UtteranceRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(
        self, session_id: int, text: str, audio_path: str | None = None, t: float | None = None
    ) -> Utterance:
        stamp = time.time() if t is None else t
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO utterance (session_id, t, text, audio_path) VALUES (?, ?, ?, ?)",
                (session_id, stamp, text, audio_path),
            )
        return Utterance(int(cur.lastrowid), session_id, stamp, text, audio_path)

    def update_text(self, utterance_id: int, text: str) -> None:
        """Fix a mangled transcription. The audio stays as the real record."""
        with self._conn:
            self._conn.execute(
                "UPDATE utterance SET text = ? WHERE id = ?", (text, utterance_id)
            )

    def delete(self, utterance_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM utterance WHERE id = ?", (utterance_id,))

    def get(self, utterance_id: int) -> Utterance | None:
        row = self._conn.execute(
            "SELECT * FROM utterance WHERE id = ?", (utterance_id,)
        ).fetchone()
        return Utterance.from_row(row) if row else None

    def for_session(self, session_id: int, limit: int | None = None) -> list[Utterance]:
        sql = "SELECT * FROM utterance WHERE session_id = ? ORDER BY t"
        params: list = [session_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [Utterance.from_row(r) for r in self._conn.execute(sql, params).fetchall()]
