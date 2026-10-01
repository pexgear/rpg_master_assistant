"""What was said at the table, kept between sessions.

Everything is stored; only the recent tail is ever handed out. A group coming
back after a fortnight wants the end of last time, not the whole campaign, and
sending the whole campaign to every client that connects would be daft.

The speaker is stored as text rather than a reference on purpose. A log is a
record of what happened, and it should still read correctly after a character is
renamed, a login is deleted, or a player leaves the group.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

SAID = "said"
ROLLED = "rolled"
SYSTEM = "system"

#: Who a line was for. The empty string is the whole table, which is almost
#: everything; ``DM_ONLY`` is whoever is running the game -- the DM, and the
#: agent standing in for them.
#:
#: This exists because the log is handed to whoever logs in next. A refusal, a
#: request waiting for approval, an API key that expired: each went to the DM
#: alone when it happened, and each was then read out to the next player to
#: connect. Recording who it was for is what stops that.
EVERYONE = ""
DM_ONLY = "dm"

#: How much of the log a joining client is given. Enough to pick up the thread,
#: not so much that the panel takes a moment to draw.
DEFAULT_LIMIT = 100


@dataclass(slots=True)
class ChatMessage:
    id: int
    campaign_id: int
    session_id: int | None
    kind: str
    speaker: str
    role: str
    text: str
    payload: dict
    created_at: float
    #: "" for the table, "dm" for whoever is running it. See EVERYONE/DM_ONLY.
    audience: str = EVERYONE

    @property
    def is_private(self) -> bool:
        return self.audience != EVERYONE

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "speaker": self.speaker,
            "role": self.role,
            "text": self.text,
            "payload": self.payload,
            "at": self.created_at,
            "audience": self.audience,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ChatMessage":
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        return cls(
            id=row["id"],
            campaign_id=row["campaign_id"],
            session_id=row["session_id"],
            kind=row["kind"],
            speaker=row["speaker"],
            role=row["role"],
            text=row["text"],
            payload=payload if isinstance(payload, dict) else {},
            created_at=row["created_at"],
            audience=_column(row, "audience", EVERYONE),
        )


class ChatRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(
        self,
        campaign_id: int,
        kind: str,
        text: str,
        *,
        session_id: int | None = None,
        speaker: str = "",
        role: str = "",
        payload: dict | None = None,
        audience: str = EVERYONE,
    ) -> ChatMessage:
        now = time.time()
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO chat_message (campaign_id, session_id, kind, speaker,
                                          role, text, payload_json, created_at,
                                          audience)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    session_id,
                    kind,
                    speaker,
                    role,
                    text,
                    json.dumps(payload or {}),
                    now,
                    audience,
                ),
            )
        return self.get(int(cursor.lastrowid))  # type: ignore[return-value]

    def get(self, message_id: int) -> ChatMessage | None:
        row = self._conn.execute(
            "SELECT * FROM chat_message WHERE id = ?", (message_id,)
        ).fetchone()
        return ChatMessage.from_row(row) if row else None

    def recent(
        self,
        campaign_id: int,
        limit: int = DEFAULT_LIMIT,
        audiences: tuple[str, ...] = (EVERYONE,),
    ) -> list[ChatMessage]:
        """The last few messages, oldest first so they read in order.

        ``audiences`` is an allowlist, and it defaults to the public one. A
        caller that forgets to widen it shows a DM too little, which is a
        complaint; the other default would show a player too much, which is a
        leak, and it is the one this argument exists to prevent.
        """
        allowed = tuple(audiences) or (EVERYONE,)
        rows = self._conn.execute(
            "SELECT * FROM chat_message WHERE campaign_id = ?"
            f" AND audience IN ({','.join('?' * len(allowed))})"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (campaign_id, *allowed, max(0, int(limit))),
        ).fetchall()
        return [ChatMessage.from_row(r) for r in reversed(rows)]

    def for_session(self, session_id: int) -> list[ChatMessage]:
        """One evening in full, for reading back later."""
        rows = self._conn.execute(
            "SELECT * FROM chat_message WHERE session_id = ? ORDER BY created_at, id",
            (session_id,),
        ).fetchall()
        return [ChatMessage.from_row(r) for r in rows]

    def count(self, campaign_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM chat_message WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return int(row["n"]) if row else 0


def _column(row: sqlite3.Row, name: str, fallback):
    """A column that may not be there yet.

    ``for_session`` and the tests read rows straight out of the table, and a
    database migrated halfway through a test run is not worth a crash.
    """
    try:
        value = row[name]
    except (IndexError, KeyError):
        return fallback
    return fallback if value is None else value
