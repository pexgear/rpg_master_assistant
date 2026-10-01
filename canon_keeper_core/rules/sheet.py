"""What a character sheet stores.

Only the *inputs*: what the player chose, and what state the character is in.
Everything a sheet displays that can be worked out -- modifiers, hit points,
armour class, saving throws, spell slots -- is computed in
:mod:`canon_keeper_core.rules.derive` rather than kept here, so it cannot drift out of
step with the choices behind it.

Sheets live inside ``entity.data["sheet"]``, so this is a plain dictionary
schema rather than a table. It carries a ``schema`` number so old sheets can be
migrated when the shape changes.
"""

from __future__ import annotations

from typing import Any

SCHEMA = 1

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")

ABILITY_NAMES = {
    "str": "Strength",
    "dex": "Dexterity",
    "con": "Constitution",
    "int": "Intelligence",
    "wis": "Wisdom",
    "cha": "Charisma",
}

#: Fields the owner may change at any time. They describe what is happening to
#: the character now, not what the character is, so waiting for approval would
#: make the app useless mid-combat.
STATE_FIELDS = frozenset(
    {
        "hp_current",
        "temp_hp",
        "conditions",
        "inspiration",
        "death_saves",
        "slots_used",
        "spells_prepared",
        "equipment",
        "currency",
        "player_notes",
    }
)

#: Fields that define what the character *is*. A player may propose a change;
#: the DM confirms it.
BUILD_FIELDS = frozenset(
    {
        "species",
        "subspecies",
        "class_index",
        "subclass",
        "level",
        "background",
        "abilities",
        "ability_improvements",
        "skill_proficiencies",
        "tool_proficiencies",
        "languages",
        "spells_known",
        "hp_rolled",
        "overrides",
    }
)

STANDARD_ARRAY = (15, 14, 13, 12, 10, 8)

#: Point-buy costs from the Player's Handbook, 27 points to spend.
POINT_BUY_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_BUDGET = 27

MIN_LEVEL = 1
MAX_LEVEL = 20
MIN_ABILITY = 1
MAX_ABILITY = 30


def new_sheet(**overrides: Any) -> dict:
    """A blank sheet: a level-one nobody, ready to be filled in."""
    sheet: dict[str, Any] = {
        "schema": SCHEMA,
        "species": "",
        "subspecies": "",
        "class_index": "",
        "subclass": "",
        "level": 1,
        "background": "",
        "alignment": "",
        # Base scores, before species bonuses and improvements.
        "abilities": {ability: 10 for ability in ABILITIES},
        "ability_improvements": {},
        "skill_proficiencies": [],
        "tool_proficiencies": [],
        "languages": [],
        # One entry per level after the first; the first level always takes the
        # full hit die.
        "hp_rolled": [],
        "hp_current": None,
        "temp_hp": 0,
        "conditions": [],
        "inspiration": False,
        "death_saves": {"successes": 0, "failures": 0},
        "equipment": [],
        "currency": {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0},
        "spells_known": [],
        "spells_prepared": [],
        "slots_used": {},
        # Room for the bonuses a DM hands out that no rule predicts.
        "overrides": {},
        "player_notes": "",
    }
    sheet.update(overrides)
    return sheet


def is_sheet(data: Any) -> bool:
    """Whether an entity's data carries a character sheet."""
    return isinstance(data, dict) and "schema" in data and "abilities" in data


def sheet_of(entity_data: dict | None) -> dict | None:
    """Pull the sheet out of an entity's data, if it has one."""
    if not isinstance(entity_data, dict):
        return None
    sheet = entity_data.get("sheet")
    return sheet if is_sheet(sheet) else None


def migrate(sheet: dict) -> dict:
    """Bring an older sheet up to the current schema.

    Nothing to do yet, but the hook exists so the first change does not have to
    invent a migration mechanism at the same time.
    """
    version = sheet.get("schema", 0)
    if version >= SCHEMA:
        return sheet

    upgraded = new_sheet()
    upgraded.update({k: v for k, v in sheet.items() if k in upgraded})
    upgraded["schema"] = SCHEMA
    return upgraded


def classify(field: str) -> str:
    """Whether a field is state, build, or neither."""
    if field in STATE_FIELDS:
        return "state"
    if field in BUILD_FIELDS:
        return "build"
    return "other"


def point_buy_spent(abilities: dict[str, int]) -> int:
    """What a set of base scores costs under point buy."""
    return sum(POINT_BUY_COST.get(score, 0) for score in abilities.values())


def point_buy_is_legal(abilities: dict[str, int]) -> bool:
    if any(score not in POINT_BUY_COST for score in abilities.values()):
        return False
    return point_buy_spent(abilities) <= POINT_BUY_BUDGET
