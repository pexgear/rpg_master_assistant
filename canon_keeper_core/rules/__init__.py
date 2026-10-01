"""The 5e rules: what a sheet stores, what follows from it, and what is legal.

Deliberately free of Qt and of the database. The host runs these to check what a
player sent, so they must work anywhere -- including inside a headless server
that has no window and no campaign of its own.
"""

from canon_keeper_core.rules.derive import (
    ability_modifiers,
    ability_scores,
    armour_class,
    describe,
    hit_points,
    is_caster,
    max_hit_points,
    modifier,
    proficiency_bonus,
    saving_throws,
    skill_bonuses,
    spell_slots,
    summary,
)
from canon_keeper_core.rules.sheet import (
    ABILITIES,
    BUILD_FIELDS,
    SCHEMA,
    STATE_FIELDS,
    classify,
    is_sheet,
    new_sheet,
    sheet_of,
)
from canon_keeper_core.rules.validation import Report, validate

__all__ = [
    "ABILITIES",
    "BUILD_FIELDS",
    "SCHEMA",
    "STATE_FIELDS",
    "Report",
    "ability_modifiers",
    "ability_scores",
    "armour_class",
    "classify",
    "describe",
    "hit_points",
    "is_caster",
    "is_sheet",
    "max_hit_points",
    "modifier",
    "new_sheet",
    "proficiency_bonus",
    "saving_throws",
    "sheet_of",
    "skill_bonuses",
    "spell_slots",
    "summary",
    "validate",
]
