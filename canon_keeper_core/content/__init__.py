"""Game content: the SRD, plus whatever this campaign has invented.

Nothing above this layer reads the SRD directly. Every lookup comes through
here, so adding homebrew later is a row in a table rather than a change to every
screen that shows a list of classes.

That is not a hypothetical: the SRD contains exactly **one** background. Any
real table writes their own before session one.

Campaign content wins on a clash of index, so a table can also replace something
they dislike -- their Acolyte instead of the SRD's -- without editing the
bundled files.
"""

from __future__ import annotations

import json
import logging

from canon_keeper_core.content import srd
from canon_keeper_core.content.srd import ATTRIBUTION, COLLECTIONS

log = logging.getLogger("canonkeeper.content")

#: Campaign-defined content is stored in the settings table under this prefix,
#: as a JSON list per collection. It is small, hand-made, and read rarely, so it
#: does not warrant tables of its own yet.
SETTING_PREFIX = "content."

#: Marks an entry as the campaign's own, so the UI can label and edit it.
HOMEBREW_KEY = "homebrew"


class Content:
    """Everything the rules can choose from, for one campaign."""

    def __init__(self, settings=None) -> None:
        #: None means SRD only -- useful in tests and anywhere without a
        #: campaign, such as validating a sheet on a bare server.
        self._settings = settings
        self._cache: dict[str, tuple[dict, ...]] = {}

    # ------------------------------------------------------------------ lookup

    def all(self, collection: str) -> tuple[dict, ...]:
        """Every entry, SRD first, then the campaign's, campaign winning."""
        if collection in self._cache:
            return self._cache[collection]

        merged: dict[str, dict] = {
            entry["index"]: entry for entry in srd.load(collection) if "index" in entry
        }
        for entry in self._homebrew(collection):
            if entry.get("index"):
                merged[entry["index"]] = {**entry, HOMEBREW_KEY: True}

        result = tuple(sorted(merged.values(), key=lambda e: e.get("name", "")))
        self._cache[collection] = result
        return result

    def get(self, collection: str, index: str) -> dict | None:
        for entry in self.all(collection):
            if entry.get("index") == index:
                return entry
        return None

    def names(self, collection: str) -> list[tuple[str, str]]:
        """``(index, name)`` pairs, for filling a dropdown."""
        return [(e["index"], e.get("name", e["index"])) for e in self.all(collection)]

    # ------------------------------------------------- the collections by name

    def classes(self) -> tuple[dict, ...]:
        return self.all("classes")

    def subclasses_of(self, class_index: str) -> tuple[dict, ...]:
        return tuple(
            sub
            for sub in self.all("subclasses")
            if (sub.get("class") or {}).get("index") == class_index
        )

    def species(self) -> tuple[dict, ...]:
        """Called races in the 2014 rules; species is the kinder word."""
        return self.all("races")

    def subspecies_of(self, species_index: str) -> tuple[dict, ...]:
        return tuple(
            sub
            for sub in self.all("subraces")
            if (sub.get("race") or {}).get("index") == species_index
        )

    def backgrounds(self) -> tuple[dict, ...]:
        return self.all("backgrounds")

    def skills(self) -> tuple[dict, ...]:
        return self.all("skills")

    def equipment(self) -> tuple[dict, ...]:
        return self.all("equipment")

    def spells(self) -> tuple[dict, ...]:
        return self.all("spells")

    def spells_for(self, class_index: str, level: int | None = None) -> tuple[dict, ...]:
        """Spells on one class's list, optionally of a single spell level."""
        return tuple(
            spell
            for spell in self.spells()
            if any(c.get("index") == class_index for c in spell.get("classes", ()))
            and (level is None or spell.get("level") == level)
        )

    def level_row(self, class_index: str, level: int) -> dict | None:
        """The progression row for one class at one level."""
        for row in self.all("levels"):
            if (row.get("class") or {}).get("index") == class_index and row.get(
                "level"
            ) == level:
                return row
        return None

    def features_at(self, class_index: str, level: int) -> tuple[dict, ...]:
        return tuple(
            feature
            for feature in self.all("features")
            if (feature.get("class") or {}).get("index") == class_index
            and feature.get("level") == level
        )

    # --------------------------------------------------------------- homebrew

    def _homebrew(self, collection: str) -> list[dict]:
        if self._settings is None:
            return []
        raw = self._settings.get(SETTING_PREFIX + collection, [])
        if isinstance(raw, str):  # tolerate a hand-edited setting
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("campaign content for %r is not valid JSON", collection)
                return []
        return [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []

    def add_homebrew(self, collection: str, entry: dict) -> None:
        """Define something of the campaign's own, or replace an SRD entry."""
        if collection not in COLLECTIONS:
            raise KeyError(f"no collection called {collection!r}")
        if self._settings is None:
            raise RuntimeError("this Content has no campaign to write to")
        if not entry.get("index"):
            raise ValueError("content needs an index")

        existing = [e for e in self._homebrew(collection) if e.get("index") != entry["index"]]
        existing.append(entry)
        self._settings.set(SETTING_PREFIX + collection, existing)
        self._cache.pop(collection, None)

    def remove_homebrew(self, collection: str, index: str) -> None:
        if self._settings is None:
            return
        remaining = [e for e in self._homebrew(collection) if e.get("index") != index]
        self._settings.set(SETTING_PREFIX + collection, remaining)
        self._cache.pop(collection, None)

    def is_homebrew(self, collection: str, index: str) -> bool:
        entry = self.get(collection, index)
        return bool(entry and entry.get(HOMEBREW_KEY))

    def invalidate(self) -> None:
        """Forget the merged view; call after editing content behind our back."""
        self._cache.clear()


__all__ = ["Content", "ATTRIBUTION", "COLLECTIONS", "SETTING_PREFIX", "srd"]
