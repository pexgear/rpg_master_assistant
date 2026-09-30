"""What to do on a turn, worked out from the map alone.

No model, no key, no cost, and no network. That is deliberate: it is what the
agent falls back to when there is no model to ask, and it is what makes the
whole thing testable without spending anything.

It is not clever, and it does not pretend to be. It closes to the nearest enemy
and hits them, which is what an absent player's character would plausibly do
and is a great deal better than standing still. The intention is that a model
decides instead once there is one to ask, with this underneath as the answer
when it cannot be reached -- see "Still to come" in the README. There is no such
module yet, and this said there was, which is the sort of cross-reference that
stays plausible for a year.

**It only ever reads what the host sent this seat.** A tactic that reached for
something outside the projection would be the same cheating the seat exists to
prevent, arriving by a different door.
"""

from __future__ import annotations

from dataclasses import dataclass

from canon_keeper_protocol import grid

#: How far a melee weapon reaches, in squares. The host has the real rule; this
#: is only deciding where to walk, and being wrong costs a refused move.
REACH = 1


@dataclass(frozen=True)
class Decision:
    """One turn's worth of intent. Any part may be absent."""

    #: Where to end up, or None to stay put.
    move: tuple[int, int] | None = None
    #: The combatant id to swing at, or None.
    target: int | None = None
    #: What to say at the table, in the first person, the way somebody
    #: playing would type it. Autopilot reads this and works out the rules,
    #: exactly as it does for a person -- so it is a sentence, not squares.
    because: str = ""

    @property
    def does_nothing(self) -> bool:
        return self.move is None and self.target is None


def _square(combatant: dict) -> tuple[int, int] | None:
    x, y = combatant.get("x"), combatant.get("y")
    return (x, y) if isinstance(x, int) and isinstance(y, int) else None


def _hurt(entities: dict, combatant: dict) -> bool:
    """Whether this one is already down. The host says so on the combatant."""
    return bool(combatant.get("down"))


def enemies_of(mine: dict, fight: dict) -> list[dict]:
    """Everyone on another side who is still standing.

    Sides come off the fight rather than being guessed from what a creature is,
    because the DM may have moved somebody across -- the captured guard fighting
    beside the party is on the party's side and is not a target.
    """
    my_side = mine.get("team")
    out = []
    for other in fight.get("combatants") or []:
        if other.get("id") == mine.get("id"):
            continue
        if other.get("down"):
            continue
        if _square(other) is None:
            continue  # not on the map; nothing to walk towards
        if my_side is not None and other.get("team") == my_side:
            continue
        out.append(other)
    return out


def decide(mine: dict, fight: dict, entities: dict, speed: int = 6) -> Decision:
    """A turn, from the map. Empty when there is nothing sensible to do.

    ``speed`` is in squares and is what the host will allow; walking further
    would be refused, so this does not try.
    """
    here = _square(mine)
    if here is None or mine.get("down"):
        # Off the map or on the floor. Neither is a turn to take.
        return Decision()

    reachable = enemies_of(mine, fight)
    if not reachable:
        return Decision()

    def gap(other: dict) -> int:
        """Squares apart, counted the way a table counts: diagonals are one."""
        there = _square(other)
        if there is None:
            return 99
        return max(abs(there[0] - here[0]), abs(there[1] - here[1]))

    nearest = min(reachable, key=gap)
    there = _square(nearest)
    if there is None:
        return Decision()

    away = max(abs(there[0] - here[0]), abs(there[1] - here[1]))
    name = (entities.get(nearest.get("entity")) or {}).get("name") or "them"

    if away <= REACH:
        return Decision(target=nearest.get("id"), because=f"I stay on {name} and swing")

    step = _towards(here, there, min(speed, away - REACH))
    if step == here:
        return Decision()
    now = max(abs(there[0] - step[0]), abs(there[1] - step[1]))
    if now <= REACH:
        return Decision(
            move=step, target=nearest.get("id"), because=f"I close on {name} and swing"
        )
    return Decision(move=step, because=f"I move towards {name}")


def _towards(here: tuple[int, int], there: tuple[int, int], steps: int) -> tuple[int, int]:
    """As far along the line as ``steps`` allows, the way a table counts.

    Diagonals count as one, so this walks the same line the host draws when it
    shows the move -- otherwise the token would take a different route from the
    one that was decided.
    """
    if steps <= 0:
        return here
    path = grid.steps_between(here, there)
    if len(path) <= 1:
        return here
    return path[min(steps, len(path) - 1)]
