"""What a creature has left, and what happens when it has nothing.

A turn is a move up to your speed and one action. The app has *counted* both
since there was a turn budget at all, and until now nothing read the count: a
second swing went straight through, which made the whole budget decorative. So
the count is enforced, and it is drawn.

Two shapes, on purpose, because they answer two different questions. Movement
and the action are drawn on whoever is up and show what is **left** — *what can
I still do* is asked about one creature. A spent reaction is drawn on
**anybody** and only once it is **gone** — *can that thing swing at me as I walk
past* is asked about everyone else, and the answer is only interesting when it
is no.

The line between a turn and a token is worth holding: dragging a token is the
DM saying "put it there" and stays free, while a turn taken as a turn obeys the
rules whoever takes it.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.server import SessionServer
from canon_keeper.panels.encounter.grid import Token
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity

SHEET = {
    "schema": 1,
    "species": "human",  # speed 30, so six squares
    "class_index": "fighter",
    "level": 3,
    "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 10},
    "equipment": ["battleaxe", "shortbow", "chain-mail"],
}


@pytest.fixture
def fight(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Budget")
    hero = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok",
               data={"hp": 28, "max_hp": 28, "sheet": SHEET})
    )
    goblin = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik",
               data={"hp": 7, "max_hp": 7, "sheet": SHEET})
    )
    enc = repos.encounters.create(campaign.id, "The cave", width=20, height=20)
    tokens = {
        "hero": repos.encounters.add(enc.id, hero.id, initiative=20, x=0, y=0),
        "goblin": repos.encounters.add(enc.id, goblin.id, initiative=10, x=1, y=0),
    }
    repos.encounters.begin(enc.id)
    server = SessionServer(repos, campaign.id, "Budget")
    yield server, repos, enc, tokens, hero, goblin


# ------------------------------------------------------------ the action


def test_one_swing_a_turn(fight):
    """The rule the budget existed to enforce and never did."""
    server, repos, enc, tokens, *_ = fight

    assert server.take_turn(tokens["hero"].id, target=tokens["goblin"].id) == ""
    again = server.take_turn(tokens["hero"].id, target=tokens["goblin"].id)

    assert "already acted" in again


def test_the_second_swing_costs_the_goblin_nothing(fight):
    """Refused means refused: no dice, no hit points, no line in the log."""
    server, repos, enc, tokens, _hero, goblin = fight
    server.take_turn(tokens["hero"].id, target=tokens["goblin"].id)
    after_one = (repos.entities.get(goblin.id).data or {}).get("hp")

    server.take_turn(tokens["hero"].id, target=tokens["goblin"].id)

    assert (repos.entities.get(goblin.id).data or {}).get("hp") == after_one


def test_the_action_comes_back_next_turn(fight):
    server, repos, enc, tokens, *_ = fight
    server.take_turn(tokens["hero"].id, target=tokens["goblin"].id)

    server.run_turn("next")
    server.run_turn("next")

    assert server.take_turn(tokens["hero"].id, target=tokens["goblin"].id) == ""


def test_somebody_elses_spent_action_is_not_yours(fight):
    """The budget belongs to the turn, so it cannot bind a creature not in it."""
    server, repos, enc, tokens, _hero, goblin = fight
    server.take_turn(tokens["hero"].id, target=tokens["goblin"].id)
    combatant = repos.encounters.combatant(tokens["goblin"].id)

    assert server.already_acted(combatant, repos.entities.get(goblin.id)) == ""


# ---------------------------------------------------------- the movement


def test_a_dm_cannot_walk_further_than_the_speed(fight):
    """The same allowance a player is held to. Six squares, not the far wall."""
    server, repos, enc, tokens, *_ = fight

    problem = server.take_turn(tokens["hero"].id, move=[9, 9])

    assert "squares left" in problem
    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (0, 0), "the refusal did not stop the move"


def test_within_the_speed_goes_through(fight):
    server, repos, enc, tokens, *_ = fight

    # Straight down column 0. The goblin at 1,0 is beside that line, never on
    # it -- this is about the allowance, not about walking through anybody.
    assert server.take_turn(tokens["hero"].id, move=[0, 3]) == ""

    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (0, 3)


def test_the_allowance_is_spent_across_two_moves(fight):
    """Three squares, then four more, is seven -- and seven is too many."""
    server, repos, enc, tokens, *_ = fight
    assert server.take_turn(tokens["hero"].id, move=[0, 3]) == ""

    problem = server.take_turn(tokens["hero"].id, move=[0, 7])

    assert "squares left" in problem


def test_dragging_a_token_is_still_free(fight):
    """A drag means "put it there", not "walk there". Two gestures, two rules."""
    server, repos, enc, tokens, *_ = fight

    assert repos.encounters.place(tokens["hero"].id, 9, 9) is True

    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (9, 9)


# ------------------------------------------------------- what is drawn


def test_the_one_who_is_up_shows_move_and_action(fight):
    from canon_keeper.panels.encounter.grid import GridMap

    grid = GridMap()
    grid.resize(400, 400)
    grid.set_grid(20, 20)
    grid.set_tokens(
        [Token(id=1, label="Brok", x=0, y=0, is_turn=True, squares_left=6)]
    )
    grid.grab()  # must not raise


def test_a_spent_reaction_is_drawn_on_anybody(fight):
    from canon_keeper.panels.encounter.grid import GridMap

    grid = GridMap()
    grid.resize(400, 400)
    grid.set_grid(20, 20)
    grid.set_tokens([Token(id=2, label="Yeemik", x=1, y=0, reacted=True)])
    grid.grab()  # must not raise


def test_a_token_carries_all_three(fight):
    token = Token(id=1, label="Brok", x=0, y=0, squares_left=4, acted=True, reacted=True)

    assert (token.squares_left, token.acted, token.reacted) == (4, True, True)


def test_by_default_nothing_is_spent(fight):
    """A token built without a budget must not read as an exhausted creature."""
    token = Token(id=1, label="Brok", x=0, y=0)

    assert (token.squares_left, token.acted, token.reacted) == (0, False, False)


# ------------------------------------------------------------- the wire


def test_the_reaction_round_reaches_a_player(fight):
    """A player deciding whether to walk past needs to know it is spent."""
    from canon_keeper.net.projection import Viewer, _combatant

    server, repos, enc, tokens, *_ = fight
    repos.encounters.use_reaction(tokens["goblin"].id, 1)
    combatant = repos.encounters.combatant(tokens["goblin"].id)

    sent = _combatant(combatant, Viewer(account_id=2, is_dm=False))

    assert sent["reacted_round"] == 1


def test_an_unspent_reaction_reads_as_zero(fight):
    from canon_keeper.net.projection import Viewer, _combatant

    server, repos, enc, tokens, *_ = fight
    combatant = repos.encounters.combatant(tokens["goblin"].id)

    sent = _combatant(combatant, Viewer(account_id=2, is_dm=False))

    assert sent["reacted_round"] == 0
