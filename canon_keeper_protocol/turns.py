"""One turn, said in a sentence.

The same words in two places on purpose. A DM staging a turn reads what it will
do before committing it; a player reads what a proposed turn will do before
accepting it. Those are the same sentence about the same four fields, and writing
it twice is how the two come to disagree -- which matters most at exactly the
moment somebody presses a button believing the sentence rather than the fields
behind it.

Here rather than in a panel because the host needs it too. The ``text`` on a
proposal is what a player reads before committing their character, and rendering
it from the checked fields is the only way it cannot contradict them.
"""

from __future__ import annotations

from canon_keeper_protocol import grid

#: What a step is. Two kinds, because a turn is a walk and a swing in whatever
#: order the person wants them -- three squares, swing, three more.
MOVE = "move"
ATTACK = "attack"
#: Spend the action on moving again rather than on swinging. A step of its own
#: rather than a flag on the turn, because *when* it is taken is visible: dashing
#: and then walking nine squares is the same turn as walking three, dashing, and
#: walking six, but only one of them is what somebody meant.
DASH = "dash"


def a_move(x: int, y: int) -> dict:
    return {"kind": MOVE, "x": int(x), "y": int(y)}


def a_swing(target: int, weapon: str = "") -> dict:
    return {"kind": ATTACK, "target": int(target), "weapon": str(weapon)}


def a_dash() -> dict:
    return {"kind": DASH}


def normalise(steps=None, move=None, target=None, weapon: str = "") -> list[dict]:
    """A turn as an ordered list of steps, however it was spelled.

    Two spellings reach the host. ``steps`` is the real one. ``move``/``target``/
    ``weapon`` is the older shorthand -- a whole move and then a whole attack --
    and every client that has ever spoken to this host uses it, so it is
    normalised here rather than deprecated. One representation inside, two
    accepted outside, and exactly one place that knows the difference.

    Order is the caller's: the shorthand can only say "move then swing", which is
    why it could not express splitting the move around the action.
    """
    if steps:
        clean = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            if step.get("kind") == MOVE and isinstance(step.get("x"), int):
                clean.append(a_move(step["x"], step["y"]))
            elif step.get("kind") == ATTACK and isinstance(step.get("target"), int):
                clean.append(a_swing(step["target"], step.get("weapon", "")))
            elif step.get("kind") == DASH:
                clean.append(a_dash())
        return clean

    shorthand = []
    if move is not None:
        shorthand.append(a_move(int(move[0]), int(move[1])))
    if target is not None:
        shorthand.append(a_swing(int(target), weapon))
    return shorthand


def describe(
    move: tuple[int, int] | list[int] | None = None,
    target_name: str = "",
    weapon: str = "",
    who: str = "",
) -> str:
    """What this turn does, in one sentence. Empty when it does nothing.

    ``who`` named gives the third person -- "Yeemik moves to 3,-1" -- which is
    how a DM reads a turn they are staging for somebody else. Left out it gives
    the imperative -- "Move to 3,-1" -- which is how a turn is put to the player
    whose character it is, and is the form the proposal tool already asks for.
    """
    clauses = []
    if move is not None:
        where = grid.label(int(move[0]), int(move[1]))
        clauses.append(f"moves to {where}" if who else f"Move to {where}")

    if target_name:
        with_what = f" with a {weapon}" if weapon else ""
        if clauses:
            clauses.append(f"attack{'s' if who else ''} {target_name}{with_what}")
        else:
            clauses.append(
                f"attacks {target_name}{with_what}"
                if who
                else f"Attack {target_name}{with_what}"
            )

    if not clauses:
        return ""
    sentence = " and ".join(clauses)
    return f"{who} {sentence}." if who else f"{sentence}."


def describe_steps(steps, name_of, who: str = "") -> str:
    """A whole turn in order, however many steps it has.

    ``name_of(combatant_id) -> str`` because this package has never seen a
    database and is not about to start: who a combatant id belongs to is the
    caller's to answer.

    Read in order rather than summarised, because the order is the point -- "moves
    to 1,0, attacks Yeemik, then moves to 1,3" is a different turn from the same
    three things in any other arrangement, and the whole reason steps exist.
    """
    clauses = []
    for step in steps or ():
        if step.get("kind") == MOVE:
            where = grid.label(int(step["x"]), int(step["y"]))
            clauses.append(f"moves to {where}" if who else f"move to {where}")
        elif step.get("kind") == ATTACK:
            hit = name_of(int(step["target"])) or "someone"
            with_what = f" with a {step.get('weapon')}" if step.get("weapon") else ""
            clauses.append(
                f"attacks {hit}{with_what}" if who else f"attack {hit}{with_what}"
            )
        elif step.get("kind") == DASH:
            clauses.append("dashes" if who else "dash")

    if not clauses:
        return ""
    if len(clauses) > 2:
        # "a, b, then c" rather than "a and b and c": by three steps the ands
        # stop separating anything.
        sentence = ", ".join(clauses[:-1]) + f", then {clauses[-1]}"
    else:
        sentence = " and ".join(clauses)
    if who:
        return f"{who} {sentence}."
    return sentence[0].upper() + sentence[1:] + "."
