"""The System Reference Document, as vendored JSON.

Bundled rather than fetched: a laptop at a kitchen table should not need the
internet to make a character, and an API that changes under you is a poor
foundation for a saved sheet.

Loading is lazy and cached per file. Spells alone are 600 KB, and most sessions
never open them, so paying that cost at startup would be rude.

Nothing outside :mod:`canon_keeper_core.content` should import this module. Lookups go
through the merge layer, which adds whatever the campaign has defined -- the SRD
has exactly one background, so that seam is needed on day one.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("canonkeeper.content.srd")

DATA_DIR = Path(__file__).parent / "data"

#: The attribution CC BY 4.0 requires us to carry. Shown in Help > About.
ATTRIBUTION = (
    "This work includes material taken from the System Reference Document 5.1 "
    '("SRD 5.1") by Wizards of the Coast LLC and available at '
    "https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 "
    "is licensed under the Creative Commons Attribution 4.0 International "
    "License, available at https://creativecommons.org/licenses/by/4.0/legalcode."
)

#: File stem -> what it holds. Also the set of things that exist.
COLLECTIONS = (
    "ability-scores",
    "alignments",
    "backgrounds",
    "classes",
    "conditions",
    "damage-types",
    "equipment",
    "equipment-categories",
    "feats",
    "features",
    "languages",
    "levels",
    "magic-schools",
    "proficiencies",
    "races",
    "skills",
    "spells",
    "subclasses",
    "subraces",
    "traits",
    "weapon-properties",
)


@lru_cache(maxsize=None)
def load(collection: str) -> tuple[dict, ...]:
    """Every entry in one SRD collection. Cached; treat the result as read-only."""
    if collection not in COLLECTIONS:
        raise KeyError(f"no SRD collection called {collection!r}")

    path = DATA_DIR / f"{_file_stem(collection)}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.error("SRD data missing: %s", path)
        return ()
    except json.JSONDecodeError:
        log.exception("SRD data is unreadable: %s", path)
        return ()
    return tuple(raw) if isinstance(raw, list) else ()


@lru_cache(maxsize=None)
def index(collection: str) -> dict[str, dict]:
    """One collection keyed by its ``index`` field, for direct lookup."""
    return {entry["index"]: entry for entry in load(collection) if "index" in entry}


def get(collection: str, entry_index: str) -> dict | None:
    return index(collection).get(entry_index)


def _file_stem(collection: str) -> str:
    # The files are Title-Cased on disk: "ability-scores" -> "Ability-Scores".
    return "-".join(part.capitalize() for part in collection.split("-"))


def is_available() -> bool:
    """Whether the data actually shipped with this install."""
    return DATA_DIR.is_dir() and any(DATA_DIR.glob("*.json"))
