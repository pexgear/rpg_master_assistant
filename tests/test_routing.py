"""Walking round things rather than through them.

``place`` only ever checked the square walked *to*, so a walk across the room
went straight through whoever stood between as long as it ended somewhere
empty. Refusing that was the first answer and the wrong one: what a person
does at a table is go round. So a move is a **route** now -- the shortest way
that touches nothing solid -- and the only thing refused is a destination
there is genuinely no way to.

The route is one function in the protocol package, run by the host and by
every client drawing a preview, for the same reason the coordinate system is
one function: a client that found its own way round the same rock would draw
a walk that never happened.

Three things follow from the route rather than from the straight line, and
they are the ones worth guarding: what the walk **costs**, who gets a **swing**
at it, and what is **drawn**. A distance measured as the crow flies would let
a walk round a long wall look like three squares when it is nine.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper_protocol import grid

SHEET = {
    "schema": 1,
    "species": "human",  # speed 30, so six squares
    "class_index": "fighter",
    "level": 3,
    "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 10},
    "equipment": ["battleaxe", "chain-mail"],
}


# ------------------------------------------------------- the route itself


def test_a_clear_way_is_the_straight_line():
    """Unchanged in the ordinary case, down to the squares it names."""
    assert grid.route_between((0, 0), (3, 0)) == grid.steps_between((0, 0), (3, 0))
    assert grid.route_between((0, 0), (2, 2)) == grid.steps_between((0, 0), (2, 2))


def test_going_nowhere_is_one_square():
    assert grid.route_between((2, 2), (2, 2)) == [(2, 2)]


def test_it_goes_round_what_is_in_the_way():
    route = grid.route_between((0, 0), (3, 0), {(1, 0)})

    assert route[0] == (0, 0)
    assert route[-1] == (3, 0)
    assert (1, 0) not in route


def test_every_step_touches_the_one_before_it():
    """A route with a jump in it would be drawn as a token teleporting."""
    route = grid.route_between((0, 0), (4, 0), {(1, -1), (1, 0), (1, 1)})

    for here, there in zip(route, route[1:]):
        assert max(abs(here[0] - there[0]), abs(here[1] - there[1])) == 1


def test_the_way_round_is_the_shortest_one():
    """Its length is charged as movement, so a lazy detour would cost feet."""
    # One body in the way costs nothing at all: a diagonal step is one square,
    # so going round it is the same three squares as going through it.
    assert len(grid.route_between((0, 0), (3, 0), {(1, 0)})) - 1 == 3
    # A wall of three has to be gone round the end of, and that does cost.
    wall = {(1, -1), (1, 0), (1, 1)}
    assert len(grid.route_between((0, 0), (3, 0), wall)) - 1 == 4


def test_nowhere_to_go_is_no_route():
    boxed = {
        (1, -1), (1, 0), (1, 1),
        (0, -1), (0, 1),
        (-1, -1), (-1, 0), (-1, 1),
    }
    assert grid.route_between((0, 0), (3, 0), boxed) == []


def test_a_route_stays_on_the_map():
    """Round the end of a wall is not an answer if the end is off the board."""
    wall = {(1, -1), (1, 0), (1, 1)}
    assert grid.route_between((0, 0), (3, 0), wall, within=grid.bounds(20, 3)) == []


def test_the_start_is_walkable_even_when_it_is_blocked():
    """Somebody is standing there -- that is what makes it the start."""
    assert grid.route_between((0, 0), (2, 0), {(0, 0)}) == [(0, 0), (1, 0), (2, 0)]


# ------------------------------------------------------------- in a fight


@pytest.fixture
def fight(qtbot, repos):
    """A hero at 0,0 and a goblin squarely between it and 4,0."""
    campaign = repos.campaigns.ensure_default("The corridor")
    hero = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok",
               data={"hp": 28, "max_hp": 28, "sheet": SHEET})
    )
    goblin = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik",
               data={"hp": 7, "max_hp": 7, "sheet": SHEET})
    )
    encounter = repos.encounters.create(campaign.id, "The corridor", width=20, height=20)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=20, x=0, y=0),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=10, x=2, y=0),
    }
    repos.encounters.begin(encounter.id)
    server = SessionServer(repos, campaign.id, "Corridor session")
    yield server, repos, encounter, tokens, hero, goblin


def test_a_walk_past_somebody_goes_round_them(fight):
    server, repos, _encounter, tokens, *_ = fight
    mover = repos.encounters.combatant(tokens["hero"].id)

    route = server.route_for(mover, 4, 0)

    assert (2, 0) not in route, "it walked over the goblin"
    assert route[-1] == (4, 0)


def test_and_it_is_allowed(fight):
    """Refusing this was the first answer. Going round is the right one."""
    server, repos, _encounter, tokens, *_ = fight

    assert server.take_turn(tokens["hero"].id, move=[4, 0]) == ""

    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (4, 0)


def test_terrain_is_gone_round_too(fight):
    server, repos, encounter, tokens, *_ = fight
    for square in ((1, -1), (1, 0), (1, 1)):
        repos.encounters.toggle_obstacle(encounter.id, *square)
    mover = repos.encounters.combatant(tokens["hero"].id)

    route = server.route_for(mover, 3, 0)

    assert route, "there is a way round the end of a three-square wall"
    assert not {(1, -1), (1, 0), (1, 1)} & set(route)


def test_the_fallen_are_walked_over_not_round(fight):
    """Stepping over a body is ordinary, and a corpse must not close a corridor."""
    server, repos, _encounter, tokens, _hero, goblin = fight
    entity = repos.entities.get(goblin.id)
    entity.data = {**(entity.data or {}), "hp": 0}
    repos.entities.update(entity)
    repos.encounters.set_down(tokens["goblin"].id, True)
    mover = repos.encounters.combatant(tokens["hero"].id)

    assert server.route_for(mover, 4, 0) == grid.steps_between((0, 0), (4, 0))


def test_walled_in_is_refused_and_says_so(fight):
    server, repos, encounter, tokens, *_ = fight
    for square in ((1, -1), (1, 0), (1, 1), (0, -1), (0, 1), (-1, -1), (-1, 0), (-1, 1)):
        repos.encounters.toggle_obstacle(encounter.id, *square)

    problem = server.take_turn(tokens["hero"].id, move=[3, 0])

    assert "no way through" in problem
    unmoved = repos.encounters.combatant(tokens["hero"].id)
    assert (unmoved.x, unmoved.y) == (0, 0)


def test_the_destination_being_taken_is_its_own_answer(fight):
    """Not "unreachable" -- somebody is standing there, which is worth saying."""
    server, repos, _encounter, tokens, *_ = fight

    problem = server.take_turn(tokens["hero"].id, move=[2, 0])

    assert "no way through" not in problem
    assert problem


# ------------------------------------------------------- what follows from it


def test_the_long_way_costs_the_long_way(fight):
    """Charged along the route. As the crow flies, this would be three."""
    server, repos, encounter, tokens, *_ = fight
    for square in ((1, -1), (1, 0), (1, 1)):
        repos.encounters.toggle_obstacle(encounter.id, *square)

    assert server.take_turn(tokens["hero"].id, move=[3, 0]) == ""

    walked = repos.encounters.get(encounter.id).moved_squares
    assert walked == 4, "the wall was not counted"


def test_a_way_round_too_long_for_the_turn_is_refused(fight):
    """Six squares of speed, and the way round is longer than the way through."""
    server, repos, encounter, tokens, *_ = fight
    # A long wall, so getting to the far side of it is further than the speed.
    for y in range(-4, 5):
        repos.encounters.toggle_obstacle(encounter.id, 1, y)

    problem = server.take_turn(tokens["hero"].id, move=[3, 0])

    assert "squares left" in problem


def test_the_walk_that_is_drawn_is_the_route(fight):
    """What is animated and what was walked have to be the one thing."""
    server, repos, _encounter, tokens, *_ = fight
    shown: list[dict] = []
    server.played.connect(shown.append)

    server.take_turn(tokens["hero"].id, move=[4, 0])

    walks = [event for event in shown if event["kind"] == "move"]
    assert walks, "nothing was described"
    drawn = [tuple(square) for square in walks[0]["path"]]
    assert (2, 0) not in drawn
    assert drawn[-1] == (4, 0)


def test_dragging_a_token_still_ignores_all_of_this(fight):
    """A drag means "put it there", not "walk there". It never routed."""
    server, repos, _encounter, tokens, *_ = fight

    assert repos.encounters.place(tokens["hero"].id, 4, 0) is True


# --------------------------------------------------------------- not bendable


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def test_nowhere_to_walk_is_not_put_to_the_dm(qtbot, fight):
    """A speed limit is a rule a DM may waive; a wall is not one of those."""
    server, repos, encounter, tokens, *_ = fight
    repos.accounts.create(
        server.campaign_id, "autopilot", "let-me-run-it",
        role="agent", display_name="Autopilot",
    )
    for square in ((1, -1), (1, 0), (1, 1), (0, -1), (0, 1), (-1, -1), (-1, 0), (-1, 1)):
        repos.encounters.toggle_obstacle(encounter.id, *square)
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    try:
        server.set_autopilot(True, by="the DM")
        agent = _join(qtbot, server, "autopilot", "let-me-run-it")
        bends: list[dict] = []
        agent.bend_requested.connect(bends.append)
        try:
            with qtbot.waitSignal(agent.failed, timeout=5000):
                agent.send_move(tokens["hero"].id, 3, 0)
            assert bends == [], "a wall was offered to the DM as a rule"
            assert repos.encounters.combatant(tokens["hero"].id).x == 0
        finally:
            agent.leave()
    finally:
        server.stop()
