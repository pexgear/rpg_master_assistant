"""Finding the dice hidden in what the DM just said.

A DM -- human or autopilot -- writes "make a DC 14 Perception check". Every
player then does the same three things: work out which of their numbers that
is, add it to a d20, and say the total out loud. The first two are arithmetic a
computer is better at, and the third is why anyone bothered.

So this module reads a line of chat and returns the places where a roll was
asked for, as spans the panel can turn into links. It is deliberately
**conservative**: a phrase is only a prompt when the words "check", "save",
"saving throw" or dice notation are actually there. Highlighting the word
"perception" in "his perception of the situation" would train people to ignore
the highlighting, which costs more than the missed prompts.

Nothing here rolls anything. The notation goes to the host like any other roll,
because dice a client rolled for itself are an honour system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The SRD skills, and the index ``rules.derive.skill_bonuses`` keys them by.
#: Written out rather than read from content because this runs in a player's
#: app, where the campaign's content is somebody else's.
SKILLS: dict[str, str] = {
    "acrobatics": "acrobatics",
    "animal handling": "animal-handling",
    "arcana": "arcana",
    "athletics": "athletics",
    "deception": "deception",
    "history": "history",
    "insight": "insight",
    "intimidation": "intimidation",
    "investigation": "investigation",
    "medicine": "medicine",
    "nature": "nature",
    "perception": "perception",
    "performance": "performance",
    "persuasion": "persuasion",
    "religion": "religion",
    "sleight of hand": "sleight-of-hand",
    "stealth": "stealth",
    "survival": "survival",
}

ABILITIES: dict[str, str] = {
    "strength": "str",
    "str": "str",
    "dexterity": "dex",
    "dex": "dex",
    "constitution": "con",
    "con": "con",
    "intelligence": "int",
    "int": "int",
    "wisdom": "wis",
    "wis": "wis",
    "charisma": "cha",
    "cha": "cha",
}

#: What kind of number to add. The panel reads this to ask the sheet the right
#: question -- a saving throw and an ability check are the same die and often
#: not the same bonus.
SKILL = "skill"
SAVE = "save"
#: Ten or better on a plain d20, and the number is not in the sentence: it is
#: the rule rather than something a DM set. Repeated from
#: :mod:`canon_keeper_core.rules.death` rather than imported, because this module
#: runs in a player's app where the campaign's rules are somebody else's.
DEATH_SAVE_DC = 10

#: The one roll nobody chooses to make. Its own kind because it takes no
#: modifier of any sort -- not proficiency, not Constitution, nothing -- which
#: is the rule that makes it frightening at every level.
DEATH = "death"
ABILITY = "ability"
INITIATIVE = "initiative"
DICE = "dice"


@dataclass(frozen=True)
class Prompt:
    """One "roll something" found in a line, and where it was."""

    start: int
    end: int
    #: The words as written, so the link reads like the sentence it is in.
    text: str
    kind: str
    #: Skill index, ability index, or empty for raw dice.
    key: str = ""
    dc: int | None = None
    #: What to actually roll, before any bonus off the sheet.
    notation: str = "1d20"

    @property
    def label(self) -> str:
        if self.dc is not None:
            return f"{self.text} (DC {self.dc})"
        return self.text


_DC = r"(?:DC\s*(?P<dc>\d{1,2})\s+)?"
_TRAILING_DC = r"(?:\s*\(?\s*DC\s*(?P<dc2>\d{1,2})\s*\)?)?"

_SKILL_NAMES = "|".join(sorted(SKILLS, key=len, reverse=True))
_ABILITY_NAMES = "|".join(sorted(ABILITIES, key=len, reverse=True))

#: Order matters. The first pattern to claim a span wins, so the specific ones
#: ("Wisdom (Perception) check") come before the general ones ("Perception
#: check"), and raw dice come last so "roll 1d20" inside a longer phrase does
#: not win over the phrase.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        SKILL,
        re.compile(
            _DC
            + rf"(?:(?P<ability>{_ABILITY_NAMES})\s*\(\s*)?"
            + rf"(?P<skill>{_SKILL_NAMES})"
            + r"(?:\s*\))?\s+(?:check|roll)"
            + _TRAILING_DC,
            re.IGNORECASE,
        ),
    ),
    (
        # Before the ability save below, which would otherwise never see it
        # anyway -- "death" is not an ability -- but the order says which of
        # the two this is meant to be.
        DEATH,
        re.compile(r"death\s+(?:saving\s+throw|save)s?", re.IGNORECASE),
    ),
    (
        SAVE,
        re.compile(
            _DC
            + rf"(?P<ability>{_ABILITY_NAMES})\s+(?:saving\s+throw|save)"
            + _TRAILING_DC,
            re.IGNORECASE,
        ),
    ),
    (
        ABILITY,
        re.compile(
            _DC + rf"(?P<ability>{_ABILITY_NAMES})\s+(?:check|roll)" + _TRAILING_DC,
            re.IGNORECASE,
        ),
    ),
    (
        INITIATIVE,
        re.compile(r"roll(?:s|ing)?\s+(?:for\s+)?initiative", re.IGNORECASE),
    ),
    (
        DICE,
        re.compile(
            r"\b(?P<notation>\d{0,2}d\d{1,3}(?:\s*[+-]\s*\d{1,2})?)\b", re.IGNORECASE
        ),
    ),
]


def find(text: str) -> list[Prompt]:
    """Every roll asked for in ``text``, left to right and never overlapping.

    Overlaps are resolved by pattern order rather than by length: "DC 14
    Wisdom (Perception) check" is one prompt, not a Perception check standing
    next to a Wisdom check standing next to nothing.
    """
    claimed: list[Prompt] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if any(match.start() < p.end and p.start < match.end() for p in claimed):
                continue
            claimed.append(_prompt(kind, match, text))
    return sorted(claimed, key=lambda p: p.start)


def _prompt(kind: str, match: re.Match, text: str) -> Prompt:
    groups = match.groupdict()
    dc = groups.get("dc") or groups.get("dc2")

    key = ""
    notation = "1d20"
    if kind == DEATH:
        # Ten or better, always. Nobody writes the DC out because it is not a
        # DC anybody chose -- it is the rule -- so it is filled in here rather
        # than read out of the sentence.
        dc = dc or DEATH_SAVE_DC
    elif kind == SKILL:
        key = SKILLS[groups["skill"].lower()]
    elif kind in (SAVE, ABILITY):
        key = ABILITIES[groups["ability"].lower()]
    elif kind == DICE:
        notation = re.sub(r"\s+", "", groups["notation"])
        if notation.startswith("d"):
            notation = "1" + notation

    return Prompt(
        start=match.start(),
        end=match.end(),
        text=text[match.start() : match.end()].strip(),
        kind=kind,
        key=key,
        dc=int(dc) if dc else None,
        notation=notation,
    )


def bonus_for(prompt: Prompt, sheet: dict, content) -> int:
    """The number this character adds, or nothing if the sheet cannot say.

    A missing or unreadable sheet gives zero rather than an error: a plain d20
    with the player adding their own bonus is exactly what happens at a table
    with no computer, so it is a safe place to fall back to.
    """
    if not isinstance(sheet, dict) or not sheet:
        return 0

    from canon_keeper_core.rules import derive

    if prompt.kind == DEATH:
        # No modifier of any kind. Not proficiency, not Constitution: a death
        # save is a bare d20, and adding anything to it would be inventing a
        # rule that makes the frightening part go away.
        return 0

    try:
        if prompt.kind == SKILL:
            return derive.skill_bonuses(sheet, content).get(prompt.key, 0)
        if prompt.kind == SAVE:
            return derive.saving_throws(sheet, content).get(prompt.key, 0)
        if prompt.kind == ABILITY:
            return derive.ability_modifiers(sheet, content).get(prompt.key, 0)
        if prompt.kind == INITIATIVE:
            return derive.initiative(sheet, content)
    except Exception:  # noqa: BLE001 - a broken sheet is not worth a crash
        return 0
    return 0


def notation_for(prompt: Prompt, bonus: int) -> str:
    """What to send to the host.

    Raw dice keep their own modifier: "2d6+3" was written by someone who meant
    it, and adding a Perception bonus to it would be inventing.
    """
    if prompt.kind == DICE:
        return prompt.notation
    return f"1d20{bonus:+d}" if bonus else "1d20"
