"""What a player's app knows about the campaign.

Held in memory only. A player's copy is a view of someone else's canon, not a
second source of truth, so it is rebuilt from the host on every connection --
which also means revoking a share actually takes something away rather than
leaving a stale copy on disk.

The DM's app does not use this: it reads its own database directly.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from canon_keeper_core.repo.entities import KIND_LOCATION


class SharedState(QObject):
    """Entities received from the host, keyed by id."""

    changed = Signal()
    #: The fight, or the absence of one. Separate from ``changed`` because the
    #: map redraws far more often than the roster does -- a token moves every
    #: few seconds in combat, and rebuilding every panel each time is waste.
    encounter_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entities: dict[int, dict] = {}
        #: What the host says the fight is. Never cached to disk: a map is only
        #: true while the session it belongs to is open.
        self._encounter: dict | None = None

    # ------------------------------------------------------------------ writes

    def replace_all(self, entities: list[dict]) -> None:
        self._entities = {
            e["id"]: e for e in entities if isinstance(e, dict) and "id" in e
        }
        self.changed.emit()

    def upsert(self, entity: dict) -> None:
        if isinstance(entity, dict) and "id" in entity:
            self._entities[entity["id"]] = entity
            self.changed.emit()

    def remove(self, entity_id: int) -> None:
        if self._entities.pop(entity_id, None) is not None:
            self.changed.emit()

    def set_encounter(self, encounter: dict | None) -> None:
        """What the host says the fight is. An empty payload means there is none."""
        self._encounter = encounter if encounter else None
        self.encounter_changed.emit()

    def clear(self) -> None:
        if self._entities:
            self._entities.clear()
            self.changed.emit()
        if self._encounter is not None:
            self._encounter = None
            self.encounter_changed.emit()

    # ------------------------------------------------------------------- reads

    @property
    def encounter(self) -> dict | None:
        return self._encounter

    def get(self, entity_id: int) -> dict | None:
        return self._entities.get(entity_id)

    def all(self) -> list[dict]:
        return sorted(self._entities.values(), key=lambda e: e.get("name", "").lower())

    def of_kind(self, *kinds: str) -> list[dict]:
        return [e for e in self.all() if e.get("kind") in kinds]

    def places(self) -> list[dict]:
        return self.of_kind(KIND_LOCATION)

    def children_of(self, parent_id: int | None) -> list[dict]:
        """Places nested directly inside another, for the Cities tree.

        A place whose parent was not shared is treated as a root, so the tree
        shows what the player knows rather than hinting at a gap above it.
        """
        known = set(self._entities)
        result = []
        for entity in self.places():
            parent = entity.get("parent_id")
            if parent not in known:
                parent = None
            if parent == parent_id:
                result.append(entity)
        return result

    def occupants_of(self, place_id: int) -> list[dict]:
        return [
            e
            for e in self.all()
            if e.get("parent_id") == place_id and e.get("kind") != KIND_LOCATION
        ]

    def own_character(self) -> dict | None:
        for entity in self._entities.values():
            if entity.get("own"):
                return entity
        return None
