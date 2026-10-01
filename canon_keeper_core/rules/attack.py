"""Swinging a weapon at somebody.

Deliberately the simple case and nothing else: a weapon attack with a thing on
the character's sheet. No spells, no advantage, no sneak attack. Those are
rulings a DM makes, and a machine that made half of them would be worse than
one that makes none -- the table would have to check every result to find out
which half.

**Opportunity attacks are the exception**, because they are not a ruling: they
happen whether or not anybody remembers them, and forgetting one is how a grid
quietly becomes a diagram. See :func:`threatens`.

What is here is the arithmetic nobody enjoys doing: which modifier applies to
this weapon, what the target's armour class is, whether a d20 got there, and
how much it hurt.

**The dice are not rolled here.** ``resolve`` is handed a roller, and the host
passes it the shared one. A client that rolled its own attack would be an
honour system with extra steps.
"""

from __future__ import annotations

from dataclasses import dataclass

from canon_keeper_core.rules import derive
from canon_keeper_core.rules.sheet import is_sheet

#: A natural twenty always hits and doubles the dice; a natural one always
#: misses. The two rules everyone at every table knows.
CRIT = 20
FUMBLE = 1

#: Reach, in squares. Five feet is one square, so a melee weapon reaches the
#: eight squares around it -- diagonals included, which is how a grid is
#: normally played even though it is not how geometry works.
MELEE_REACH = 1


class NoAttack(ValueError):
    """The attack cannot be made, and the message says why in plain words."""


@dataclass(slots=True)
class Weapon:
    index: str
    name: str
    dice: str
    damage_type: str
    ranged: bool
    finesse: bool
    #: Normal range in feet, for a ranged weapon.
    reach_feet: int = 5

    @property
    def squares(self) -> int:
        return max(1, self.reach_feet // 5)


@dataclass(slots=True)
class Attack:
    """What happened, in enough detail to read out."""

    weapon: str
    roll: int
    bonus: int
    total: int
    target_ac: int
    hit: bool
    critical: bool
    damage: int = 0
    damage_type: str = ""
    damage_rolls: list[int] | None = None

    def describe(self, attacker: str, target: str) -> str:
        opening = f"{attacker} attacks {target} with {self.weapon}: " \
                  f"{self.total} ({self.roll}{self.bonus:+d}) against AC {self.target_ac}"
        if self.critical and self.hit:
            return f"{opening} -- a critical hit for {self.damage} {self.damage_type}."
        if self.hit:
            return f"{opening} -- a hit for {self.damage} {self.damage_type}."
        if self.roll == FUMBLE:
            return f"{opening} -- a miss, and a bad one."
        return f"{opening} -- a miss."


def weapons_of(sheet: dict, content) -> list[Weapon]:
    """Every weapon on this sheet, in the order it is carried."""
    if not is_sheet(sheet):
        return []
    found: list[Weapon] = []
    for item in sheet.get("equipment") or ():
        index = item.get("index") if isinstance(item, dict) else item
        weapon = _weapon(index, content)
        if weapon is not None:
            found.append(weapon)
    return found


def melee_weapon(sheet: dict, content) -> Weapon | None:
    """The first melee weapon carried, or ``None``.

    What somebody swings when they did not choose: an opportunity attack is
    taken in the half-second somebody walks past, and stopping to ask which
    weapon would be a dialog nobody wants during another player's turn. First
    carried, because the order on a sheet is the order a person wrote it in.
    """
    for weapon in weapons_of(sheet, content):
        if not weapon.ranged:
            return weapon
    return None


def threatens(sheet: dict, content, gap: int) -> bool:
    """Whether a creature with this sheet has any say over a square ``gap`` away.

    Only melee. A bow does not stop somebody walking past you, which is the
    rule that makes archers want somebody standing in front of them.
    """
    return gap <= MELEE_REACH and melee_weapon(sheet, content) is not None


def find_weapon(sheet: dict, content, wanted: str) -> Weapon:
    """The weapon somebody meant, or a refusal that says what they have.

    Matched loosely on purpose. The name comes out of a sentence somebody typed
    -- "hits it with his axe" -- and refusing that over a missing "battle" is
    the kind of pedantry that makes an app feel like paperwork.
    """
    carried = weapons_of(sheet, content)
    if not carried:
        raise NoAttack("there is no weapon on that character's sheet")

    needle = (wanted or "").strip().lower()
    if not needle:
        return carried[0]

    for weapon in carried:
        if needle in (weapon.index, weapon.name.lower()):
            return weapon
    for weapon in carried:
        if needle in weapon.name.lower() or weapon.name.lower() in needle:
            return weapon

    have = ", ".join(weapon.name for weapon in carried)
    raise NoAttack(f"no {wanted!r} on that sheet -- they are carrying {have}")


def attack_bonus(sheet: dict, content, weapon: Weapon) -> int:
    """Proficiency plus the ability the weapon uses.

    Everyone is assumed proficient with what they are carrying. Working out
    whether a wizard is proficient with a battleaxe is a rule about character
    building, and getting it wrong would silently change every roll.
    """
    modifiers = derive.ability_modifiers(sheet, content)
    ability = "dex" if weapon.ranged else "str"
    if weapon.finesse:
        ability = max(("str", "dex"), key=lambda name: modifiers.get(name, 0))
    return modifiers.get(ability, 0) + derive.proficiency_bonus(
        int(sheet.get("level", 1) or 1)
    )


def damage_bonus(sheet: dict, content, weapon: Weapon) -> int:
    """The same ability again, without proficiency."""
    modifiers = derive.ability_modifiers(sheet, content)
    ability = "dex" if weapon.ranged else "str"
    if weapon.finesse:
        ability = max(("str", "dex"), key=lambda name: modifiers.get(name, 0))
    return modifiers.get(ability, 0)


def armour_class(entity_data: dict, content) -> int:
    """What it takes to hit this creature.

    A monster states its armour class in ``overrides``, because nothing can work
    one out for a creature with no class and no equipment list. A character's is
    derived from the sheet like everything else.
    """
    sheet = (entity_data or {}).get("sheet")
    if not is_sheet(sheet):
        return 10
    return derive.armour_class(sheet, content)


def within_reach(weapon: Weapon, distance: int) -> bool:
    """Whether the target is close enough -- or, for a bow, not too close.

    Distance is in squares, measured the way a table measures it: diagonals
    count as one. That is not what a ruler says, and it is what everybody plays.
    """
    if weapon.ranged:
        return distance <= weapon.squares
    return distance <= MELEE_REACH


def squares_between(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def resolve(sheet: dict, content, weapon: Weapon, target_ac: int, roller) -> Attack:
    """One swing. ``roller(notation)`` returns something with ``.total``/``.rolls``.

    The roller is passed in so the host can use the table's dice, which is the
    only place dice are allowed to happen.
    """
    to_hit = roller("1d20")
    natural = to_hit.rolls[0] if to_hit.rolls else to_hit.total
    bonus = attack_bonus(sheet, content, weapon)
    total = natural + bonus

    critical = natural == CRIT
    hit = critical or (natural != FUMBLE and total >= target_ac)

    attack = Attack(
        weapon=weapon.name,
        roll=natural,
        bonus=bonus,
        total=total,
        target_ac=target_ac,
        hit=hit,
        critical=critical,
        damage_type=weapon.damage_type,
        damage_rolls=[],
    )
    if not hit:
        return attack

    # A critical doubles the dice and not the modifier, which is the rule
    # people most often get wrong in the generous direction.
    dice = _doubled(weapon.dice) if critical else weapon.dice
    rolled = roller(dice)
    attack.damage_rolls = list(rolled.rolls)
    attack.damage = max(1, rolled.total + damage_bonus(sheet, content, weapon))
    return attack


# ---------------------------------------------------------------------- helpers


def _weapon(index, content) -> Weapon | None:
    entry = content.get("equipment", index) or {}
    damage = entry.get("damage") or {}
    if not damage.get("damage_dice"):
        return None  # armour, rope, a lantern

    properties = {
        (p.get("index") if isinstance(p, dict) else p)
        for p in entry.get("properties") or ()
    }
    return Weapon(
        index=str(entry.get("index", index)),
        name=str(entry.get("name", index)),
        dice=str(damage["damage_dice"]),
        damage_type=str((damage.get("damage_type") or {}).get("name", "damage")).lower(),
        ranged=str(entry.get("weapon_range", "")).lower() == "ranged",
        finesse="finesse" in properties,
        reach_feet=int((entry.get("range") or {}).get("normal", 5) or 5),
    )


def _doubled(dice: str) -> str:
    """``1d8`` becomes ``2d8``. A critical doubles the dice, not the modifier."""
    count, _, sides = dice.partition("d")
    try:
        return f"{max(1, int(count or 1)) * 2}d{int(sides)}"
    except ValueError:
        return dice
