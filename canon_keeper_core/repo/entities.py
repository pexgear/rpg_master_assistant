"""Entities: NPCs, PCs, locations, factions, items.

``data`` is a free-form dict persisted as JSON. That is how "start small, then
evolve" gets paid for: adding a field to the character form costs a widget, not
a migration.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field

KIND_NPC = "npc"
KIND_PC = "pc"
KIND_LOCATION = "location"
KIND_FACTION = "faction"
KIND_ITEM = "item"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class StaleWrite(RuntimeError):
    """Someone else changed this entity since it was read."""


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "unnamed"


@dataclass(slots=True)
class Entity:
    id: int | None
    campaign_id: int
    kind: str
    name: str
    slug: str = ""
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    data: dict = field(default_factory=dict)
    parent_id: int | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    #: Whose character this is. None means it belongs to the campaign, which in
    #: practice means the DM's.
    owner_account_id: int | None = None
    #: Bumped on every write, so a client can ask for only what changed and a
    #: stale edit can be refused rather than silently winning.
    version: int = 1

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Entity":
        return cls(
            id=row["id"],
            campaign_id=row["campaign_id"],
            kind=row["kind"],
            name=row["name"],
            slug=row["slug"],
            aliases=json.loads(row["aliases_json"]),
            summary=row["summary"],
            data=json.loads(row["data_json"]),
            parent_id=row["parent_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            owner_account_id=row["owner_account_id"],
            version=row["version"],
        )


class EntityRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---------------------------------------------------------------- writes

    def create(self, entity: Entity) -> Entity:
        now = time.time()
        entity.created_at = entity.created_at or now
        entity.updated_at = now
        entity.slug = entity.slug or self._unique_slug(
            entity.campaign_id, entity.kind, slugify(entity.name)
        )
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO entity (campaign_id, kind, name, slug, aliases_json,
                                    summary, data_json, parent_id, created_at,
                                    updated_at, owner_account_id, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    entity.campaign_id,
                    entity.kind,
                    entity.name,
                    entity.slug,
                    json.dumps(entity.aliases),
                    entity.summary,
                    json.dumps(entity.data),
                    entity.parent_id,
                    entity.created_at,
                    entity.updated_at,
                    entity.owner_account_id,
                ),
            )
        entity.id = int(cur.lastrowid)
        return entity

    def update(self, entity: Entity, expected_version: int | None = None) -> Entity:
        """Save an entity, bumping its version.

        Pass ``expected_version`` to make the write conditional: if the entity
        has moved on since it was read, nothing is written and StaleWrite is
        raised. Without it the write is unconditional, which is what the DM's
        own app wants -- it is the authority.
        """
        if entity.id is None:
            raise ValueError("cannot update an entity with no id")
        entity.updated_at = time.time()

        with self._conn:
            if expected_version is not None:
                cursor = self._conn.execute(
                    "SELECT version FROM entity WHERE id = ?", (entity.id,)
                ).fetchone()
                current = cursor["version"] if cursor else None
                if current != expected_version:
                    raise StaleWrite(
                        f"this character has changed since you loaded it "
                        f"(you have version {expected_version}, it is now {current})"
                    )

            self._conn.execute(
                """
                UPDATE entity SET kind = ?, name = ?, aliases_json = ?, summary = ?,
                                  data_json = ?, parent_id = ?, updated_at = ?,
                                  owner_account_id = ?, version = version + 1
                WHERE id = ?
                """,
                (
                    entity.kind,
                    entity.name,
                    json.dumps(entity.aliases),
                    entity.summary,
                    json.dumps(entity.data),
                    entity.parent_id,
                    entity.updated_at,
                    entity.owner_account_id,
                    entity.id,
                ),
            )
            row = self._conn.execute(
                "SELECT version FROM entity WHERE id = ?", (entity.id,)
            ).fetchone()
            entity.version = row["version"] if row else entity.version + 1
        return entity

    def set_owner(self, entity_id: int, account_id: int | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE entity SET owner_account_id = ?, version = version + 1"
                " WHERE id = ?",
                (account_id, entity_id),
            )

    def owned_by(self, account_id: int) -> list[Entity]:
        """Every character this login owns. A player may have several."""
        rows = self._conn.execute(
            "SELECT * FROM entity WHERE owner_account_id = ?"
            " ORDER BY name COLLATE NOCASE",
            (account_id,),
        ).fetchall()
        return [Entity.from_row(r) for r in rows]

    def owned_ids(self, account_id: int) -> set[int]:
        rows = self._conn.execute(
            "SELECT id FROM entity WHERE owner_account_id = ?", (account_id,)
        ).fetchall()
        return {r["id"] for r in rows}

    def versions(self, campaign_id: int) -> dict[int, int]:
        """``{entity_id: version}`` for the whole campaign."""
        rows = self._conn.execute(
            "SELECT id, version FROM entity WHERE campaign_id = ?", (campaign_id,)
        ).fetchall()
        return {r["id"]: r["version"] for r in rows}

    def delete(self, entity_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM entity WHERE id = ?", (entity_id,))

    # ---------------------------------------------------------------- reads

    def get(self, entity_id: int) -> Entity | None:
        row = self._conn.execute("SELECT * FROM entity WHERE id = ?", (entity_id,)).fetchone()
        return Entity.from_row(row) if row else None

    def list(
        self, campaign_id: int, kinds: tuple[str, ...] | None = None, search: str = ""
    ) -> list[Entity]:
        sql = "SELECT * FROM entity WHERE campaign_id = ?"
        params: list = [campaign_id]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        if search:
            sql += " AND (name LIKE ? OR summary LIKE ? OR aliases_json LIKE ?)"
            needle = f"%{search}%"
            params.extend([needle, needle, needle])
        sql += " ORDER BY name COLLATE NOCASE"
        return [Entity.from_row(r) for r in self._conn.execute(sql, params).fetchall()]

    def children(self, parent_id: int | None, campaign_id: int) -> list[Entity]:
        if parent_id is None:
            rows = self._conn.execute(
                "SELECT * FROM entity WHERE campaign_id = ? AND parent_id IS NULL"
                " ORDER BY name COLLATE NOCASE",
                (campaign_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM entity WHERE campaign_id = ? AND parent_id = ?"
                " ORDER BY name COLLATE NOCASE",
                (campaign_id, parent_id),
            ).fetchall()
        return [Entity.from_row(r) for r in rows]

    def occupants(self, location_id: int) -> list[Entity]:
        """People and things whose ``parent_id`` is this location."""
        rows = self._conn.execute(
            "SELECT * FROM entity WHERE parent_id = ? AND kind != 'location'"
            " ORDER BY name COLLATE NOCASE",
            (location_id,),
        ).fetchall()
        return [Entity.from_row(r) for r in rows]

    # ---------------------------------------------------------------- helpers

    def _unique_slug(self, campaign_id: int, kind: str, base: str) -> str:
        slug, n = base, 2
        while self._conn.execute(
            "SELECT 1 FROM entity WHERE campaign_id = ? AND kind = ? AND slug = ?",
            (campaign_id, kind, slug),
        ).fetchone():
            slug = f"{base}-{n}"
            n += 1
        return slug
