"""Everything a sheet shows that can be worked out from what it stores.

Pure functions over a sheet dict and a :class:`~canon_keeper_core.content.Content`.
No Qt, no database, no globals -- partly so they are trivial to test, and partly
because the host runs them to check what a player sent.

Where a rule and an override disagree, the override wins. DMs hand out bonuses
no rule predicts, and a model with nowhere to put them gets worked around.
"""

from __future__ import annotations

from canon_keeper_core.rules.sheet import ABILITIES, MAX_LEVEL, MIN_LEVEL

#: Ability used for spellcasting, by class. The SRD carries this per class in a
#: shape that is awkward to read, and it never changes, so it is written out.
SPELLCASTING_ABILITY = {
    "bard": "cha",
    "cleric": "wis",
    "druid": "wis",
    "paladin": "cha",
    "ranger": "wis",
    "sorcerer": "cha",
    "warlock": "cha",
    "wizard": "int",
}

#: Classes whose spell slots come back on a short rest and are counted apart.
PACT_CASTERS = frozenset({"warlock"})


def modifier(score: int) -> int:
    """The ability modifier for a score. -1 for 8, +3 for 17."""
    return (int(score) - 10) // 2


def proficiency_bonus(level: int) -> int:
    return 2 + (max(MIN_LEVEL, min(int(level), MAX_LEVEL)) - 1) // 4


def format_bonus(value: int) -> str:
    """'+3' or '-1'; sheets read badly without the sign."""
    return f"{value:+d}"


# --------------------------------------------------------------- ability scores


def species_bonuses(sheet: dict, content) -> dict[str, int]:
    """Ability bonuses granted by species and subspecies."""
    bonuses = {ability: 0 for ability in ABILITIES}
    for collection, index in (
        ("races", sheet.get("species")),
        ("subraces", sheet.get("subspecies")),
    ):
        if not index:
            continue
        entry = content.get(collection, index) or {}
        for bonus in entry.get("ability_bonuses", ()):
            ability = (bonus.get("ability_score") or {}).get("index")
            if ability in bonuses:
                bonuses[ability] += int(bonus.get("bonus", 0))
    return bonuses


def ability_scores(sheet: dict, content) -> dict[str, int]:
    """Final scores: base, plus species, plus improvements taken on levelling."""
    base = sheet.get("abilities") or {}
    bonuses = species_bonuses(sheet, content)
    improvements = sheet.get("ability_improvements") or {}

    return {
        ability: int(base.get(ability, 10))
        + bonuses.get(ability, 0)
        + int(improvements.get(ability, 0))
        for ability in ABILITIES
    }


def ability_modifiers(sheet: dict, content) -> dict[str, int]:
    return {
        ability: modifier(score)
        for ability, score in ability_scores(sheet, content).items()
    }


# ------------------------------------------------------------------ hit points


def hit_die(sheet: dict, content) -> int:
    klass = content.get("classes", sheet.get("class_index")) or {}
    return int(klass.get("hit_die", 8))


def max_hit_points(sheet: dict, content) -> int:
    """First level takes the whole die; later levels use what was rolled.

    A level with no recorded roll uses the fixed average, which is what most
    tables do anyway and what a half-built sheet should show.
    """
    override = (sheet.get("overrides") or {}).get("hp_max")
    if override is not None:
        return int(override)

    die = hit_die(sheet, content)
    level = max(MIN_LEVEL, int(sheet.get("level", 1)))
    con = ability_modifiers(sheet, content)["con"]

    rolled = list(sheet.get("hp_rolled") or [])
    average = die // 2 + 1

    total = die + con
    for index in range(level - 1):
        gained = int(rolled[index]) if index < len(rolled) else average
        total += gained + con

    # A constitution penalty can never take a level below one hit point.
    return max(level, total)


def hit_points(sheet: dict, content) -> tuple[int, int]:
    """``(current, maximum)``. A new sheet starts at full."""
    maximum = max_hit_points(sheet, content)
    current = sheet.get("hp_current")
    return (maximum if current is None else int(current)), maximum


# ----------------------------------------------------------------- armour class


def armour_class(sheet: dict, content) -> int:
    """Ten plus dexterity, unless armour or an override says otherwise.

    Armour is deliberately simple for now: the worn item's base AC, plus the
    dexterity it allows. Shields add on top.
    """
    override = (sheet.get("overrides") or {}).get("ac")
    if override is not None:
        return int(override)

    dex = ability_modifiers(sheet, content)["dex"]
    base = 10 + dex
    shield = 0

    for item in sheet.get("equipment") or ():
        index = item.get("index") if isinstance(item, dict) else item
        entry = content.get("equipment", index) or {}
        armour = entry.get("armor_class")
        if not armour:
            continue
        if entry.get("armor_category") == "Shield":
            shield += int(armour.get("base", 0))
            continue
        if not item.get("equipped", True) if isinstance(item, dict) else False:
            continue

        allowed = dex
        if not armour.get("dex_bonus"):
            allowed = 0
        elif armour.get("max_bonus") is not None:
            allowed = min(dex, int(armour["max_bonus"]))
        base = int(armour.get("base", 10)) + allowed

    return base + shield


def initiative(sheet: dict, content) -> int:
    return ability_modifiers(sheet, content)["dex"]


def speed(sheet: dict, content) -> int:
    override = (sheet.get("overrides") or {}).get("speed")
    if override is not None:
        return int(override)
    species = content.get("races", sheet.get("species")) or {}
    return int(species.get("speed", 30))


def speed_in_squares(sheet: dict, content) -> int:
    """How far this creature gets in one turn, on a five-foot grid.

    A creature with no species -- every monster -- falls back to thirty feet,
    which is what most of them have. ``overrides.speed`` is there for the ones
    that do not, and is the same escape hatch armour class and hit points use.
    """
    return max(1, speed(sheet, content) // 5)


# ------------------------------------------------------- saves and skill checks


def saving_throw_proficiencies(sheet: dict, content) -> set[str]:
    klass = content.get("classes", sheet.get("class_index")) or {}
    return {
        entry.get("index")
        for entry in klass.get("saving_throws", ())
        if entry.get("index")
    }


def saving_throws(sheet: dict, content) -> dict[str, int]:
    modifiers = ability_modifiers(sheet, content)
    proficient = saving_throw_proficiencies(sheet, content)
    bonus = proficiency_bonus(sheet.get("level", 1))
    return {
        ability: modifiers[ability] + (bonus if ability in proficient else 0)
        for ability in ABILITIES
    }


def skill_bonuses(sheet: dict, content) -> dict[str, int]:
    """Every skill, whether proficient or not -- sheets list them all."""
    modifiers = ability_modifiers(sheet, content)
    proficient = set(sheet.get("skill_proficiencies") or ())
    bonus = proficiency_bonus(sheet.get("level", 1))

    result = {}
    for skill in content.skills():
        index = skill.get("index")
        ability = (skill.get("ability_score") or {}).get("index", "dex")
        result[index] = modifiers.get(ability, 0) + (
            bonus if index in proficient else 0
        )
    return result


def passive_perception(sheet: dict, content) -> int:
    return 10 + skill_bonuses(sheet, content).get("perception", 0)


# ------------------------------------------------------------------ spellcasting


def spellcasting_ability(sheet: dict) -> str | None:
    return SPELLCASTING_ABILITY.get(sheet.get("class_index", ""))


def is_caster(sheet: dict) -> bool:
    return spellcasting_ability(sheet) is not None


def spell_save_dc(sheet: dict, content) -> int | None:
    ability = spellcasting_ability(sheet)
    if ability is None:
        return None
    return (
        8
        + proficiency_bonus(sheet.get("level", 1))
        + ability_modifiers(sheet, content)[ability]
    )


def spell_attack_bonus(sheet: dict, content) -> int | None:
    ability = spellcasting_ability(sheet)
    if ability is None:
        return None
    return proficiency_bonus(sheet.get("level", 1)) + ability_modifiers(sheet, content)[
        ability
    ]


def spell_slots(sheet: dict, content) -> dict[int, int]:
    """Slots per spell level at this class and level, from the SRD tables."""
    class_index = sheet.get("class_index")
    if not class_index:
        return {}
    row = content.level_row(class_index, int(sheet.get("level", 1)))
    casting = (row or {}).get("spellcasting") or {}

    slots = {}
    for level in range(1, 10):
        count = int(casting.get(f"spell_slots_level_{level}", 0) or 0)
        if count:
            slots[level] = count
    return slots


def slots_remaining(sheet: dict, content) -> dict[int, int]:
    used = sheet.get("slots_used") or {}
    return {
        level: max(0, total - int(used.get(str(level), used.get(level, 0)) or 0))
        for level, total in spell_slots(sheet, content).items()
    }


def cantrips_known(sheet: dict, content) -> int:
    row = content.level_row(sheet.get("class_index", ""), int(sheet.get("level", 1)))
    return int(((row or {}).get("spellcasting") or {}).get("cantrips_known", 0) or 0)


def attacks_per_action(sheet: dict, content) -> int:
    """How many swings one Attack action produces. At least one.

    Read off the SRD's own level tables rather than from a list of classes kept
    here: the level row already carries ``extra_attacks``, so a fighter gets two
    at five and three at eleven without this file knowing anything about
    fighters. Derived, never stored -- the same rule as everything else here.
    """
    row = content.level_row(sheet.get("class_index", ""), int(sheet.get("level", 1)))
    extra = int(((row or {}).get("class_specific") or {}).get("extra_attacks", 0) or 0)
    return 1 + max(0, extra)


# ------------------------------------------------------------------- summaries


def describe(sheet: dict, content) -> str:
    """The one-line summary a list needs: 'Level 5 Elf Wizard'."""
    species = (content.get("races", sheet.get("species")) or {}).get("name", "")
    klass = (content.get("classes", sheet.get("class_index")) or {}).get("name", "")
    level = sheet.get("level", 1)

    parts = [f"Level {level}"]
    if species:
        parts.append(species)
    if klass:
        parts.append(klass)
    return " ".join(parts)


def summary(sheet: dict, content) -> dict:
    """Everything a sheet view needs, computed once."""
    current, maximum = hit_points(sheet, content)
    return {
        "description": describe(sheet, content),
        "abilities": ability_scores(sheet, content),
        "modifiers": ability_modifiers(sheet, content),
        "proficiency_bonus": proficiency_bonus(sheet.get("level", 1)),
        "hp_current": current,
        "hp_max": maximum,
        "ac": armour_class(sheet, content),
        "initiative": initiative(sheet, content),
        "speed": speed(sheet, content),
        "saving_throws": saving_throws(sheet, content),
        "skills": skill_bonuses(sheet, content),
        "passive_perception": passive_perception(sheet, content),
        "spell_save_dc": spell_save_dc(sheet, content),
        "spell_attack_bonus": spell_attack_bonus(sheet, content),
        "spell_slots": spell_slots(sheet, content),
        "slots_remaining": slots_remaining(sheet, content),
    }
