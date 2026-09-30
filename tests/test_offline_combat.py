"""A fight run with nobody connected is still a fight.

Plenty of evenings are the laptop on the table and four people round it. Taking
a turn used to answer "Go online first -- the dice and the hit points are the
host's", which is true and beside the point: the host is the DM's own app. So
the referee exists whether or not anyone can reach it, and hosting is other
people being able to reach it rather than it being there at all.

What this guards is that the two are the *same* referee. A second, simpler path
for playing alone is how a rule comes to mean one thing at a table and another
thing over the wire.
"""

from __future__ import annotations

import pytest

from canon_keeper.panels.table.widget import TableWidget
from dataclasses import replace

from canon_keeper_protocol import turns
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity


@pytest.fixture
def alone(qtbot, ctx):
    """A running fight, a DM's Table panel, and nobody hosting."""
    repos = ctx.repos
    campaign = ctx.campaign_id
    hero = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign,
            kind=KIND_PC,
            name="Brok",
            data={
                "hp": 28,
                "max_hp": 28,
                "sheet": {
                    "schema": 1,
                    "species": "human",
                    "class_index": "fighter",
                    "level": 3,
                    "abilities": {
                        "str": 16, "dex": 14, "con": 14,
                        "int": 10, "wis": 10, "cha": 10,
                    },
                    "equipment": ["battleaxe", "shortbow", "chain-mail"],
                },
            },
        )
    )
    goblin = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign,
            kind=KIND_NPC,
            name="Yeemik",
            data={"hp": 7, "max_hp": 7, "sheet": {"schema": 1, "level": 1}},
        )
    )
    enc = repos.encounters.create(campaign, "The cave", width=12, height=12)
    tokens = {
        "hero": repos.encounters.add(enc.id, hero.id, initiative=20, x=0, y=0),
        # Adjacent, so a melee swing is a swing rather than a reach check.
        "goblin": repos.encounters.add(enc.id, goblin.id, initiative=10, x=1, y=0),
    }
    repos.encounters.begin(enc.id)

    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    assert widget._server is None, "this is the offline case"
    return widget, repos, enc, tokens


def test_the_referee_is_built_on_demand(alone):
    """Not at start-up: opening a campaign to rename it is not an evening."""
    widget, *_ = alone
    assert widget._alone is None

    referee = widget._referee()

    assert referee is not None
    assert widget._alone is referee


def test_the_same_referee_answers_twice(alone):
    widget, *_ = alone
    assert widget._referee() is widget._referee()


def test_it_is_not_listening(alone):
    """Offline means offline. Nothing opened a port to make a fight work."""
    widget, *_ = alone
    assert widget._referee().is_running is False


def test_passing_the_turn_works(alone):
    widget, repos, enc, tokens = alone
    assert repos.encounters.get(enc.id).turn_combatant_id == tokens["hero"].id

    widget._on_turn_requested("next")

    assert repos.encounters.get(enc.id).turn_combatant_id == tokens["goblin"].id


def test_a_round_comes_back_around(alone):
    widget, repos, enc, tokens = alone

    widget._on_turn_requested("next")
    widget._on_turn_requested("next")

    assert repos.encounters.get(enc.id).turn_combatant_id == tokens["hero"].id
    assert repos.encounters.get(enc.id).round == 2


def test_moving_moves(alone):
    widget, repos, enc, tokens = alone

    widget._on_turn_taken({"combatant": tokens["hero"].id, "move": [1, 1]})

    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (1, 1)


def test_a_square_off_the_map_is_refused(alone):
    """The same answer a player gets. One set of rules, not two."""
    widget, repos, enc, tokens = alone
    said = []
    widget._ctx.bus.status_message.connect(said.append)

    widget._on_turn_taken({"combatant": tokens["hero"].id, "move": [99, 99]})

    assert said, "moving off the map passed silently"
    assert "off the map" in said[0]


def test_a_refused_walk_is_not_drawn_first(alone):
    """The walk is sent before the swings, so it has to be refused before that.

    An occupied destination is checked last, by ``place``, and on purpose: it is
    left out of the route so the answer is "that square is taken" rather than
    the vaguer "unreachable". But the walk reaches every screen before that
    check runs, so a token asked onto a taken square was drawn standing there,
    never moved, and nothing was sent afterwards to put it back. Two creatures
    running for the same corner is all it takes.
    """
    widget, repos, enc, tokens = alone
    drawn: list[dict] = []
    widget._ctx.bus.play.connect(drawn.append)
    said: list[str] = []
    widget._ctx.bus.status_message.connect(said.append)

    # Straight onto the square the goblin is standing on.
    widget._on_turn_taken({"combatant": tokens["hero"].id, "move": [1, 0]})

    assert not drawn, f"a walk that never happened was animated: {drawn}"
    assert said, "the refusal passed silently"
    assert "taken" in said[0]
    hero = repos.encounters.combatant(tokens["hero"].id)
    assert (hero.x, hero.y) == (0, 0), "and it did not move"


def test_swinging_costs_the_other_creature_hit_points(alone):
    """Dice, armour class and hit points, with nobody connected to roll for."""
    widget, repos, enc, tokens = alone
    goblin = repos.encounters.combatant(tokens["goblin"].id)
    before = (repos.entities.get(goblin.entity_id).data or {}).get("hp")

    # One swing a turn, because that is the rule now -- so the round is passed
    # between them. Enough of them that a run of misses cannot make this flaky,
    # and the fight is what is being tested rather than one roll.
    for _ in range(12):
        widget._on_turn_taken(
            {"combatant": tokens["hero"].id, "target": tokens["goblin"].id}
        )
        widget._on_turn_requested("next")
        widget._on_turn_requested("next")

    after = (repos.entities.get(goblin.entity_id).data or {}).get("hp")
    assert after < before, "twelve swings and the goblin is untouched"


def test_the_map_is_told(alone, qtbot):
    """The DM's own panels read the database, so they have to hear about it."""
    widget, repos, enc, tokens = alone

    with qtbot.waitSignal(widget._ctx.bus.encounter_changed):
        widget._on_turn_taken({"combatant": tokens["hero"].id, "move": [1, 1]})


def test_a_move_is_animated_with_nobody_connected(alone, qtbot):
    """The whole point of the referee existing offline: not a silent teleport.

    ``_show`` only ever sent PLAY frames to connected sessions, so a token
    moved without any -- exactly the offline case -- never got an animation
    at all. It just jumped, because nothing told the map it had walked there.
    """
    widget, repos, enc, tokens = alone

    # Straight up column 0 -- the goblin at 1,0 is never actually on the path,
    # only ever adjacent to it, so nothing here should be refused as blocked.
    with qtbot.waitSignal(widget._ctx.bus.play) as caught:
        widget._on_turn_taken({"combatant": tokens["hero"].id, "move": [0, 3]})

    event = caught.args[0]
    assert event["kind"] == "move"
    assert event["combatant"] == tokens["hero"].id
    assert event["path"][0] == [0, 0]
    assert event["path"][-1] == [0, 3]


def test_a_swing_is_animated_with_nobody_connected(alone, qtbot):
    widget, repos, enc, tokens = alone

    with qtbot.waitSignal(widget._ctx.bus.play) as caught:
        widget._on_turn_taken(
            {"combatant": tokens["hero"].id, "target": tokens["goblin"].id}
        )

    event = caught.args[0]
    assert event["kind"] == "attack"
    assert event["combatant"] == tokens["hero"].id
    assert event["target"] == tokens["goblin"].id


def test_going_online_retires_the_lone_referee(alone):
    """Two referees is two sets of dice, and one of them is wrong."""
    from canon_keeper.net.server import SessionServer

    widget, repos, enc, tokens = alone
    widget._referee()
    assert widget._alone is not None

    # What _host does once its server is listening.
    widget._server = SessionServer(repos, widget._ctx.campaign_id, parent=widget)
    widget._alone.deleteLater()
    widget._alone = None

    assert widget._referee() is widget._server


# ------------------------------------------------ what became of a staged turn
#
# The Combat panel holds a turn until it hears. That only works if the host's
# answer gets back to it, which is one bus signal and the reason this is tested
# here rather than in the panel: the panel can be handed the signal, but nothing
# else proves anybody sends it.


def test_a_turn_that_went_through_is_reported_as_such(alone, qtbot):
    widget, repos, _enc, tokens = alone
    settled: list[tuple] = []
    widget._ctx.bus.turn_settled.connect(lambda ok, why: settled.append((ok, why)))

    widget._on_turn_taken({"combatant": tokens["hero"].id, "move": [0, 1]})

    assert settled == [(True, "")]
    assert repos.encounters.combatant(tokens["hero"].id).y == 1


def test_a_refused_turn_is_reported_with_the_reason(alone, qtbot):
    """So the panel can keep it staged and say what to change.

    Walking onto the goblin is refused because the square is taken -- the sort of
    refusal that means "one square over", which is exactly why throwing the
    staged turn away would be the wrong answer.
    """
    widget, repos, _enc, tokens = alone
    settled: list[tuple] = []
    widget._ctx.bus.turn_settled.connect(lambda ok, why: settled.append((ok, why)))

    goblin = repos.encounters.combatant(tokens["goblin"].id)
    widget._on_turn_taken(
        {"combatant": tokens["hero"].id, "move": [goblin.x, goblin.y]}
    )

    assert settled and settled[0][0] is False
    assert settled[0][1], "refused with no reason to show anybody"
    assert repos.encounters.combatant(tokens["hero"].id).x == 0, "it moved anyway"


# ------------------------------------------------------- a turn as a sequence
#
# "Three squares, swing, three more" is the thing the turn budget was built for
# -- migration 009 says so in as many words -- and the thing no caller could
# express, because a turn carried one move and one attack in that order.


def _steps_speed(widget, tokens):
    referee = widget._referee()
    entity = referee._entity_of(tokens["hero"].id)
    return referee.movement_left(entity)


def test_a_turn_can_split_its_movement_around_the_action(alone):
    """The whole point of steps. Walk, swing, walk on."""
    widget, repos, enc, tokens = alone
    referee = widget._referee()

    reason, done = referee.take_turn_steps(
        tokens["hero"].id,
        [
            turns.a_move(0, 1),
            turns.a_swing(tokens["goblin"].id, "battleaxe"),
            turns.a_move(0, 3),
        ],
    )

    assert reason == "", reason
    assert done == 3
    walker = repos.encounters.combatant(tokens["hero"].id)
    assert (walker.x, walker.y) == (0, 3), "it did not walk on after swinging"
    # That the swing was *resolved*, not that it landed. Hit points would make
    # this a test of a d20: dice here are SystemRandom and cannot be seeded, so
    # asserting damage passes until the first miss and then looks like a bug in
    # the sequence.
    assert repos.encounters.get(enc.id).attacks_made == 1, (
        "the swing in the middle never happened"
    )


def test_a_turn_that_stops_halfway_says_how_far_it_got(alone):
    """Because the part that happened cannot be asked for again.

    Three squares and then a swing out of reach is three squares spent. Whoever
    is holding the turn has to know to stop holding those three, or committing it
    a second time walks them twice.
    """
    widget, repos, _enc, tokens = alone
    referee = widget._referee()

    goblin = repos.encounters.combatant(tokens["goblin"].id)
    reason, done = referee.take_turn_steps(
        tokens["hero"].id,
        [turns.a_move(0, 1), turns.a_move(goblin.x, goblin.y)],
    )

    assert done == 1, "it did not report the move that went through"
    assert reason, "it stopped for no stated reason"
    walker = repos.encounters.combatant(tokens["hero"].id)
    assert (walker.x, walker.y) == (0, 1), "the part that happened was undone"


def test_movement_is_measured_from_where_they_are_now(alone):
    """Not from where the turn started. Two steps share one allowance.

    The bug this forecloses is the one that keeps recurring in a different
    costume: state read once at the top and then relied on after it changed.
    """
    widget, repos, _enc, tokens = alone
    referee = widget._referee()

    # Each of these is inside the allowance; together they are twice it. On a
    # 12x12 the map runs -6..5, so both squares exist.
    reason, done = referee.take_turn_steps(
        tokens["hero"].id,
        [turns.a_move(0, 5), turns.a_move(5, 5)],
    )

    assert done == 1, "the second move was not measured against what was left"
    assert reason
    walker = repos.encounters.combatant(tokens["hero"].id)
    assert (walker.x, walker.y) == (0, 5)


def test_the_old_shorthand_still_works(alone):
    """Every caller older than steps spells a turn as one move and one attack."""
    widget, repos, _enc, tokens = alone

    assert widget._referee().take_turn(tokens["hero"].id, move=[0, 1]) == ""
    assert repos.encounters.combatant(tokens["hero"].id).y == 1


def test_one_action_a_turn_holds_across_steps(alone):
    """The budget is the turn's, not the step's.

    Two swings in one sequence is still two swings in one turn, and the second
    is refused by the same rule that refuses it from any other door.
    """
    widget, _repos, _enc, tokens = alone
    referee = widget._referee()

    reason, done = referee.take_turn_steps(
        tokens["hero"].id,
        [
            turns.a_swing(tokens["goblin"].id, "battleaxe"),
            turns.a_swing(tokens["goblin"].id, "battleaxe"),
        ],
    )

    assert done == 1
    assert reason, "a second action in one turn went unremarked"


# ------------------------------------------------- what the action was spent on
#
# `action_used` is a flag, and a flag can only say the action is gone. Extra
# Attack needs to know how many swings it produced; Dash needs to know it went on
# moving rather than on swinging. Neither is a thing a flag distinguishes.


def _level(repos, tokens, level: int) -> None:
    combatant = repos.encounters.combatant(tokens["hero"].id)
    entity = repos.entities.get(combatant.entity_id)
    data = dict(entity.data or {})
    sheet = dict(data.get("sheet") or {})
    sheet["level"] = level
    data["sheet"] = sheet
    repos.entities.update(replace(entity, data=data))


def test_a_fighter_at_level_five_gets_two_swings(alone):
    """The gap the boolean created: a second swing looked like a second action.

    The count comes off the SRD's own level table -- `extra_attacks` on the level
    row -- so this file knows nothing about fighters.
    """
    widget, repos, _enc, tokens = alone
    _level(repos, tokens, 5)
    referee = widget._referee()

    reason, done = referee.take_turn_steps(
        tokens["hero"].id,
        [
            turns.a_swing(tokens["goblin"].id, "battleaxe"),
            turns.a_swing(tokens["goblin"].id, "battleaxe"),
        ],
    )

    assert reason == "", reason
    assert done == 2, "the second swing out of one action was refused"


def test_a_third_swing_is_still_refused_at_level_five(alone):
    """Two, not unlimited. The rule moved, it did not go away."""
    widget, repos, _enc, tokens = alone
    _level(repos, tokens, 5)
    referee = widget._referee()

    reason, done = referee.take_turn_steps(
        tokens["hero"].id,
        [turns.a_swing(tokens["goblin"].id, "battleaxe")] * 3,
    )

    assert done == 2
    assert "all 2 attacks" in reason, reason


def test_a_fighter_at_level_three_still_gets_one(alone):
    """The fixture's own level. Nothing changed for creatures without the feature."""
    widget, _repos, _enc, tokens = alone
    referee = widget._referee()

    reason, done = referee.take_turn_steps(
        tokens["hero"].id,
        [turns.a_swing(tokens["goblin"].id, "battleaxe")] * 2,
    )

    assert done == 1
    assert "already acted" in reason, reason


def test_dashing_buys_your_speed_again(alone):
    """Spend the action on moving, and the allowance doubles."""
    widget, repos, _enc, tokens = alone
    referee = widget._referee()
    entity = referee._entity_of(tokens["hero"].id)
    speed = referee.movement_left(entity)

    reason, done = referee.take_turn_steps(tokens["hero"].id, [turns.a_dash()])

    assert reason == "", reason
    assert done == 1
    assert referee.movement_left(entity) == speed * 2
    assert repos.encounters.get(_enc.id).dashed is True


def test_dashing_twice_is_refused(alone):
    """It costs the action, and there is one of those."""
    widget, _repos, _enc, tokens = alone
    referee = widget._referee()

    reason, done = referee.take_turn_steps(
        tokens["hero"].id, [turns.a_dash(), turns.a_dash()]
    )

    assert done == 1
    assert "action" in reason, reason


def test_a_swing_spends_the_action_that_dashing_needed(alone):
    """Both want the same action, and only one of them can have it."""
    widget, _repos, _enc, tokens = alone
    referee = widget._referee()

    reason, done = referee.take_turn_steps(
        tokens["hero"].id,
        [turns.a_swing(tokens["goblin"].id, "battleaxe"), turns.a_dash()],
    )

    assert done == 1
    assert reason, "dashing after swinging bought a second action"


def test_the_turn_budget_is_cleared_when_the_turn_passes(alone):
    """All of it, together. A counter that outlives its turn is read once."""
    widget, repos, enc, tokens = alone
    referee = widget._referee()
    referee.take_turn_steps(
        tokens["hero"].id,
        [turns.a_dash(), turns.a_swing(tokens["goblin"].id, "battleaxe")],
    )
    spent = repos.encounters.get(enc.id)
    assert spent.dashed is True and spent.attacks_made == 1

    referee.run_turn("next")

    fresh = repos.encounters.get(enc.id)
    assert fresh.dashed is False
    assert fresh.attacks_made == 0
    assert fresh.action_used is False
    assert fresh.moved_squares == 0


def test_how_much_of_a_turn_happened_is_reported(alone):
    """Not just that it stopped -- where. The panel drops that much.

    Otherwise pressing Do it again walks the squares that were already walked,
    which is the one thing keeping a refused turn staged must not cost.
    """
    widget, repos, _enc, tokens = alone
    settled: list[tuple] = []
    widget._ctx.bus.turn_settled.connect(
        lambda ok, why, done: settled.append((ok, why, done))
    )
    goblin = repos.encounters.combatant(tokens["goblin"].id)

    widget._on_turn_taken(
        {
            "combatant": tokens["hero"].id,
            "steps": [turns.a_move(0, 1), turns.a_move(goblin.x, goblin.y)],
        }
    )

    assert settled, "nothing was reported at all"
    went_through, why_not, done = settled[0]
    assert went_through is False
    assert why_not
    assert done == 1, "the move that happened was not counted"
