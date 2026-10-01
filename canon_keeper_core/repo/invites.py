"""Invites: one live code per character, and what became of the others.

The rule that matters is in :meth:`InviteRepo.create`. Making an invite for a
character revokes every other live one for that character, so a code that was
sent on Tuesday and never taken up stops working the moment a new one is made.
Without that, "they never got round to it, I will send another" quietly leaves
two ways into the same seat.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from canon_keeper_protocol import enrol


@dataclass(slots=True)
class Invite:
    id: int | None
    campaign_id: int
    entity_id: int
    code: str
    created_at: float
    expires_at: float
    used_at: float | None = None
    revoked_at: float | None = None
    account_id: int | None = None

    def live(self, now: float | None = None) -> bool:
        """Whether this one can still be taken up."""
        moment = time.time() if now is None else now
        return (
            self.used_at is None
            and self.revoked_at is None
            and self.expires_at > moment
        )

    @property
    def state(self) -> str:
        """For the DM's eyes. Never sent to whoever is trying a code."""
        if self.used_at is not None:
            return "taken up"
        if self.revoked_at is not None:
            return "replaced"
        if self.expires_at <= time.time():
            return "expired"
        return "waiting"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Invite":
        return cls(
            id=row["id"],
            campaign_id=row["campaign_id"],
            entity_id=row["entity_id"],
            code=row["code"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            used_at=row["used_at"],
            revoked_at=row["revoked_at"],
            account_id=row["account_id"],
        )


def already_played(accounts, campaign_id: int, entity_id: int) -> bool:
    """Whether somebody already logs in as this character.

    The one rule that stops an invite being made, and it is checked in two
    places -- the DM's dialog before offering the button, and the host before
    honouring the code -- so it lives here rather than in either of them. Two
    logins for one character is not a thing a DM means to do, and an invite made
    by accident is a way into the campaign.
    """
    return any(
        account.character_entity_id == entity_id
        for account in accounts.list(campaign_id)
    )


class InviteRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, campaign_id: int, entity_id: int) -> Invite:
        """A fresh invite for one character, and the end of any other."""
        now = time.time()
        code = enrol.new_code()
        with self._conn:
            # Before the insert, so a new invite can never be revoked by its
            # own arrival.
            self._conn.execute(
                "UPDATE invite SET revoked_at = ? WHERE entity_id = ?"
                " AND used_at IS NULL AND revoked_at IS NULL",
                (now, entity_id),
            )
            cursor = self._conn.execute(
                "INSERT INTO invite (campaign_id, entity_id, code, created_at,"
                " expires_at) VALUES (?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    entity_id,
                    code,
                    now,
                    now + enrol.INVITE_LIFETIME_SECONDS,
                ),
            )
        return Invite(
            id=int(cursor.lastrowid),
            campaign_id=campaign_id,
            entity_id=entity_id,
            code=code,
            created_at=now,
            expires_at=now + enrol.INVITE_LIFETIME_SECONDS,
        )

    def get(self, invite_id: int) -> Invite | None:
        row = self._conn.execute(
            "SELECT * FROM invite WHERE id = ?", (invite_id,)
        ).fetchone()
        return Invite.from_row(row) if row else None

    def live(self, campaign_id: int) -> list[Invite]:
        """Every invite that could still be taken up, oldest first.

        This is what an enrolment is tried against, so it is deliberately the
        whole list rather than a lookup by code: the code is never sent, and
        the host finds out which invite it was by opening the sealed frame.
        """
        rows = self._conn.execute(
            "SELECT * FROM invite WHERE campaign_id = ? AND used_at IS NULL"
            " AND revoked_at IS NULL AND expires_at > ? ORDER BY created_at, id",
            (campaign_id, time.time()),
        ).fetchall()
        return [Invite.from_row(r) for r in rows]

    def for_entity(self, entity_id: int) -> list[Invite]:
        """Everything ever sent for this character, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM invite WHERE entity_id = ? ORDER BY created_at DESC, id DESC",
            (entity_id,),
        ).fetchall()
        return [Invite.from_row(r) for r in rows]

    def waiting_for(self, entity_id: int) -> Invite | None:
        """The one live invite for this character, if there is one."""
        return next((i for i in self.for_entity(entity_id) if i.live()), None)

    def take_up(self, invite_id: int, account_id: int) -> None:
        """Spend it. Guarded in SQL so two arrivals cannot both win.

        Two people with the same code, racing: the UPDATE only matches while
        the invite is still unspent, so exactly one of them changes a row.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE invite SET used_at = ?, account_id = ? WHERE id = ?"
                " AND used_at IS NULL AND revoked_at IS NULL",
                (time.time(), account_id, invite_id),
            )

    def claim(self, invite_id: int) -> bool:
        """Reserve an invite before making anything. True if this caller got it.

        The account is created *after* this returns true, so a second arrival
        with the same code finds the invite already spent rather than finding a
        half-made account.
        """
        with self._conn:
            changed = self._conn.execute(
                "UPDATE invite SET used_at = ? WHERE id = ? AND used_at IS NULL"
                " AND revoked_at IS NULL AND expires_at > ?",
                (time.time(), invite_id, time.time()),
            ).rowcount
        return bool(changed)

    def hand_back(self, invite_id: int) -> None:
        """Un-claim one whose account could not be made after all."""
        with self._conn:
            self._conn.execute(
                "UPDATE invite SET used_at = NULL WHERE id = ? AND account_id IS NULL",
                (invite_id,),
            )

    def revoke(self, invite_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE invite SET revoked_at = ? WHERE id = ? AND used_at IS NULL"
                " AND revoked_at IS NULL",
                (time.time(), invite_id),
            )
