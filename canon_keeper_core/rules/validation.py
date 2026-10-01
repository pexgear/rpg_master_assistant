"""Checking a sheet is legal.

This runs on the **host**, on every edit a player sends. A field allowlist is
not enough once sheets exist: it would happily accept ``level: 25`` or a
strength of 30, because those are the right fields with impossible values.

Validation is separate from approval and comes first. Nonsense is refused
outright rather than queued for the DM, because being asked to approve a
level-25 barbarian is not a useful question.

The rules are deliberately structural -- levels in range, classes that exist,
proficiencies that are real -- rather than an attempt to police every line of
the Player's Handbook. A DM who wants an eight-strength barbarian should get
one; a client that sends a strength of 300 should not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from canon_keeper_core.rules.sheet import (
    ABILITIES,
    MAX_ABILITY,
    MAX_LEVEL,
    MIN_ABILITY,
    MIN_LEVEL,
    is_sheet,
)


@dataclass(slots=True)
class Problem:
    field: str
    message: str


@dataclass(slots=True)
class Report:
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def __bool__(self) -> bool:
        return self.ok

    def add(self, field_name: str, message: str) -> None:
        self.problems.append(Problem(field_name, message))

    def summary(self) -> str:
        return "; ".join(f"{p.field}: {p.message}" for p in self.problems)


def validate(sheet: dict, content) -> Report:
    """Structural checks. Returns every problem, not just the first."""
    report = Report()

    if not is_sheet(sheet):
        report.add("sheet", "this is not a character sheet")
        return report

    _check_level(sheet, report)
    _check_abilities(sheet, report)
    _check_choices(sheet, content, report)
    _check_hp(sheet, report)
    _check_lists(sheet, report)

    return report


def _check_level(sheet: dict, report: Report) -> None:
    level = sheet.get("level")
    if not isinstance(level, int) or isinstance(level, bool):
        report.add("level", "must be a whole number")
    elif not MIN_LEVEL <= level <= MAX_LEVEL:
        report.add("level", f"must be between {MIN_LEVEL} and {MAX_LEVEL}")


def _check_abilities(sheet: dict, report: Report) -> None:
    abilities = sheet.get("abilities")
    if not isinstance(abilities, dict):
        report.add("abilities", "missing")
        return

    for ability in ABILITIES:
        score = abilities.get(ability)
        if not isinstance(score, int) or isinstance(score, bool):
            report.add(f"abilities.{ability}", "must be a whole number")
        elif not MIN_ABILITY <= score <= MAX_ABILITY:
            report.add(
                f"abilities.{ability}", f"must be between {MIN_ABILITY} and {MAX_ABILITY}"
            )

    improvements = sheet.get("ability_improvements") or {}
    if not isinstance(improvements, dict):
        report.add("ability_improvements", "must be a mapping")
        return

    # Four improvements of +2 by level 20, and a few classes get more; twelve is
    # generous enough not to argue with a homebrew class.
    if sum(int(v) for v in improvements.values() if isinstance(v, int)) > 12:
        report.add("ability_improvements", "more improvements than any class grants")


def _check_choices(sheet: dict, content, report: Report) -> None:
    """Everything chosen must be something that exists."""
    for field_name, collection in (
        ("species", "races"),
        ("subspecies", "subraces"),
        ("class_index", "classes"),
        ("subclass", "subclasses"),
        ("background", "backgrounds"),
    ):
        index = sheet.get(field_name)
        if index and content.get(collection, index) is None:
            report.add(field_name, f"there is no {collection[:-1]} called {index!r}")

    subspecies = sheet.get("subspecies")
    species = sheet.get("species")
    if subspecies and species:
        allowed = {s.get("index") for s in content.subspecies_of(species)}
        if subspecies not in allowed:
            report.add("subspecies", f"{subspecies!r} does not belong to {species!r}")

    subclass = sheet.get("subclass")
    class_index = sheet.get("class_index")
    if subclass and class_index:
        allowed = {s.get("index") for s in content.subclasses_of(class_index)}
        if subclass not in allowed:
            report.add("subclass", f"{subclass!r} does not belong to {class_index!r}")

    known = {s.get("index") for s in content.skills()}
    for skill in sheet.get("skill_proficiencies") or ():
        if skill not in known:
            report.add("skill_proficiencies", f"there is no skill called {skill!r}")


def _check_hp(sheet: dict, report: Report) -> None:
    rolled = sheet.get("hp_rolled")
    if rolled is not None and not isinstance(rolled, list):
        report.add("hp_rolled", "must be a list")
        return

    level = sheet.get("level", 1)
    if isinstance(rolled, list) and isinstance(level, int) and len(rolled) > max(0, level - 1):
        report.add("hp_rolled", "more rolls than levels gained")

    for value in rolled or ():
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 20:
            report.add("hp_rolled", "each roll must be between 1 and 20")
            break

    current = sheet.get("hp_current")
    if current is not None and (not isinstance(current, int) or isinstance(current, bool)):
        report.add("hp_current", "must be a whole number")

    temp = sheet.get("temp_hp", 0)
    if not isinstance(temp, int) or isinstance(temp, bool) or temp < 0:
        report.add("temp_hp", "must not be negative")


def _check_lists(sheet: dict, report: Report) -> None:
    for field_name in (
        "skill_proficiencies",
        "tool_proficiencies",
        "languages",
        "conditions",
        "spells_known",
        "spells_prepared",
        "equipment",
    ):
        value = sheet.get(field_name)
        if value is not None and not isinstance(value, list):
            report.add(field_name, "must be a list")

    # A client sending ten thousand spells is not playing the game.
    for field_name, limit in (
        ("spells_known", 500),
        ("spells_prepared", 200),
        ("equipment", 500),
        ("conditions", 50),
    ):
        value = sheet.get(field_name)
        if isinstance(value, list) and len(value) > limit:
            report.add(field_name, f"more than {limit} entries")
