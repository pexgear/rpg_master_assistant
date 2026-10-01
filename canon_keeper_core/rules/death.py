"""Being at zero hit points, which is not the same thing for everybody.

A monster at zero is finished, and that is the right amount of rules for a
monster: nobody at the table wants three more d20s to find out whether the
orc is definitely dead. A *player character* at zero is the most dramatic
moment the game has, and treating it as bookkeeping -- token off the map, next
please -- throws that away.

So the difference is here, in one place, and it is the only difference:

``UP``       standing, acting normally.
``DYING``    at zero, rolling a death save at the start of each of their turns.
``STABLE``   at zero, three saves made. Unconscious, out of the fight, alive.
``DEAD``     three saves failed, or a creature that never got saves.

**The dice are not rolled here.** :func:`save` is handed a roller, the same way
:func:`canon_keeper_core.rules.attack.resolve` is, and the host passes it the shared
one. A death save decided by the dying player's own laptop is an honour system
with extra steps, and this is the roll people care most about.
"""

from __future__ import annotations

from dataclasses import dataclass

from canon_keeper_core.repo.entities import KIND_PC

#: Saves needed either way. Three and three, as written.
SAVES_NEEDED = 3

#: Ten or better on a plain d20. No modifier -- death saves take none, which is
#: the rule that makes them frightening for a level 12 fighter too.
DEATH_SAVE_DC = 10

#: A natural twenty is not a save, it is standing back up on one hit point. A
#: natural one costs two failures rather than one.
CRIT = 20
FUMBLE = 1

UP = "up"
DYING = "dying"
STABLE = "stable"
DEAD = "dead"


@dataclass(slots=True)
class Save:
    """One death saving throw and everything that follows from it."""

    roll: int
    #: Ten or better. A natural one is not a success and costs two failures;
    #: see :attr:`failures_added`.
    made: bool
    successes_added: int
    failures_added: int
    #: A natural twenty: awake, on one hit point, saves wiped.
    revived: bool

    @property
    def described(self) -> str:
        if self.revived:
            return "a natural twenty -- back up on one hit point"
        if self.roll == FUMBLE:
            return "a natural one -- two failures"
        return "a success" if self.made else "a failure"


def condition(hit_points: int | None, kind: str, successes: int, failures: int) -> str:
    """What state a creature is in, given its hit points and its saves.

    ``hit_points`` of ``None`` means nobody has written any down, which is not
    the same as zero: a token the DM dropped on the map to represent a crowd
    should not start rolling death saves.
    """
    if hit_points is None or hit_points > 0:
        return UP
    if kind != KIND_PC:
        # Monsters and NPCs do not get saves. A DM who wants one for a
        # favourite NPC can heal them, which is the same decision made out loud.
        return DEAD
    if failures >= SAVES_NEEDED:
        return DEAD
    if successes >= SAVES_NEEDED:
        return STABLE
    return DYING


def takes_no_turn(state: str) -> bool:
    """Whether the turn should pass this one by entirely.

    ``DYING`` is deliberately *not* here. A dying character still gets the turn,
    because the death save happens at the start of it -- skipping them would
    mean they never rolled and never died, which is worse than either outcome.
    """
    return state in (STABLE, DEAD)


def resting(combatants, entity_of) -> frozenset[int]:
    """Combatant ids the turn should skip. ``entity_of(combatant)`` may return None.

    Both the host and the DM's own panel need this answer and neither should
    work it out for itself, because two versions of "is that one still in the
    fight" drift the moment one of them is edited.
    """
    out = set()
    for combatant in combatants:
        entity = entity_of(combatant)
        data = (getattr(entity, "data", None) or {}) if entity is not None else {}
        hp = data.get("hp")
        if not isinstance(hp, int):
            continue
        state = condition(
            hp,
            getattr(entity, "kind", "") or "",
            combatant.death_successes,
            combatant.death_failures,
        )
        if takes_no_turn(state) and combatant.id is not None:
            out.add(combatant.id)
    return frozenset(out)


def save(roller) -> Save:
    """One death saving throw. ``roller(notation)`` returns something with ``.total``."""
    result = roller("1d20")
    roll = result.rolls[0] if getattr(result, "rolls", None) else result.total

    if roll == CRIT:
        return Save(roll=roll, made=True, successes_added=0, failures_added=0,
                    revived=True)
    if roll == FUMBLE:
        return Save(roll=roll, made=False, successes_added=0, failures_added=2,
                    revived=False)

    made = roll >= DEATH_SAVE_DC
    return Save(
        roll=roll,
        made=made,
        successes_added=1 if made else 0,
        failures_added=0 if made else 1,
        revived=False,
    )
