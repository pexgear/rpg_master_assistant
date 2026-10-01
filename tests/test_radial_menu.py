"""Space on the map, and a ring of choices around whoever is up.

Running a fight used to mean the map for moving and a dialog for everything
else -- which is a decision made by looking away from the thing you are
deciding about. So the creature whose turn it is gets selected on the map, and
Space opens their choices around their own pin: one wedge for moving, one for
each weapon they are carrying.

Two things are worth holding here. A wedge is picked and then *pointed at*
something -- a square to move to, a creature to hit -- because a wheel that
guessed the target would be a wheel you cannot use. And what comes out is a
:class:`TurnPlan`, one object, sent the moment it is complete: today there is
no taking it back, exactly as there is none at a table once the die is down,
but a turn you line up before committing is a change to when the plan is handed
over rather than a rewrite of the map.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

from canon_keeper_protocol import turns
from canon_keeper.panels.encounter.grid import Choice, GridMap, Token, TurnPlan


@pytest.fixture
def battlefield(qtbot):
    """A ten-square map with a fighter at the middle and a goblin beside them."""
    grid = GridMap()
    qtbot.addWidget(grid)
    grid.resize(400, 400)
    grid.set_grid(10, 10)
    grid.set_tokens(
        [
            Token(id=1, label="Brok", x=0, y=0, ours=True, is_turn=True),
            Token(id=2, label="Yeemik", x=1, y=0),
        ]
    )
    grid.select(1)
    return grid


def _press(grid, key) -> None:
    grid.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def _click(grid, point: QPoint) -> None:
    grid.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(point),
            QPointF(point),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def _centre_of(grid, x: int, y: int) -> QPoint:
    return grid._at(x, y, grid._cell(), grid._origin()).center()


def _wedge_point(grid, index: int) -> QPoint:
    """A point unmistakably inside one wedge, found the way a mouse finds it."""
    centre = _centre_of(grid, 0, 0)
    cell = grid._cell()
    for radius in range(int(cell * 1.2), int(cell * 2.4)):
        for step in range(0, 3600):
            import math

            angle = math.radians(step / 10.0)
            point = QPoint(
                centre.x() + int(math.sin(angle) * radius),
                centre.y() - int(math.cos(angle) * radius),
            )
            if grid._wedge_at(point, cell, grid._origin()) == index:
                return point
    raise AssertionError(f"no point landed in wedge {index}")


# ------------------------------------------------------------------ opening


def test_space_asks_for_the_wheel(battlefield, qtbot):
    """The map does not know what a creature can do -- it asks whoever does."""
    with qtbot.waitSignal(battlefield.radial_wanted) as caught:
        _press(battlefield, Qt.Key.Key_Space)

    assert caught.args == [1]


def test_the_wheel_opens_around_the_one_who_is_up(battlefield):
    battlefield.offer(1, [Choice("move", "Move"), Choice("attack", "Club", "Club")])

    assert battlefield.radial_open is True


def test_a_creature_with_nothing_to_do_gets_no_wheel(battlefield):
    """An empty ring promises choices and delivers none."""
    battlefield.offer(1, [])

    assert battlefield.radial_open is False


def test_a_wheel_for_somebody_not_on_the_map_does_not_open(battlefield):
    battlefield.offer(99, [Choice("move", "Move")])

    assert battlefield.radial_open is False


def test_a_players_map_does_not_open_one(qtbot, battlefield):
    """Read-only means read-only: offering choices it cannot carry out is a lie."""
    battlefield.read_only = True

    _press(battlefield, Qt.Key.Key_Space)

    assert battlefield.radial_open is False


def test_escape_puts_it_away(battlefield):
    battlefield.offer(1, [Choice("move", "Move")])

    _press(battlefield, Qt.Key.Key_Escape)

    assert battlefield.radial_open is False


def test_space_again_puts_it_away(battlefield):
    """The key that opened it closes it, or it is a trap rather than a menu."""
    battlefield.offer(1, [Choice("move", "Move")])

    _press(battlefield, Qt.Key.Key_Space)

    assert battlefield.radial_open is False


# ------------------------------------------------------------- the wedges


def test_every_choice_gets_a_wedge(battlefield):
    choices = [
        Choice("move", "Move"),
        Choice("attack", "Longsword", "Longsword"),
        Choice("attack", "Shortbow", "Shortbow"),
    ]
    battlefield.offer(1, choices)

    found = {
        battlefield._wedge_at(_wedge_point(battlefield, i), battlefield._cell(), battlefield._origin())
        for i in range(len(choices))
    }
    assert found == {0, 1, 2}


def test_the_middle_is_not_a_wedge(battlefield):
    """The token stays clickable and readable: the ring has a hole in it."""
    battlefield.offer(1, [Choice("move", "Move"), Choice("attack", "Club", "Club")])

    at = battlefield._wedge_at(
        _centre_of(battlefield, 0, 0), battlefield._cell(), battlefield._origin()
    )
    assert at is None


def test_far_outside_is_not_a_wedge(battlefield):
    battlefield.offer(1, [Choice("move", "Move")])

    at = battlefield._wedge_at(
        _centre_of(battlefield, 5, 5), battlefield._cell(), battlefield._origin()
    )
    assert at is None


# ------------------------------------------------------------- the picking


def test_moving_takes_a_square(battlefield, qtbot):
    battlefield.offer(1, [Choice("move", "Move")])
    _click(battlefield, _wedge_point(battlefield, 0))
    assert battlefield.awaiting is not None, "the wheel closed without asking"

    with qtbot.waitSignal(battlefield.planned) as caught:
        _click(battlefield, _centre_of(battlefield, 3, 2))

    plan: TurnPlan = caught.args[0]
    assert plan.combatant == 1
    assert plan.move == (3, 2)
    assert plan.target is None


def test_attacking_takes_a_creature(battlefield, qtbot):
    battlefield.offer(1, [Choice("attack", "Club", "Club")])
    _click(battlefield, _wedge_point(battlefield, 0))

    with qtbot.waitSignal(battlefield.planned) as caught:
        _click(battlefield, _centre_of(battlefield, 1, 0))

    plan: TurnPlan = caught.args[0]
    assert plan.combatant == 1
    assert plan.target == 2
    assert plan.weapon == "Club"
    assert plan.move is None


def test_the_right_weapon_comes_out(battlefield, qtbot):
    battlefield.offer(
        1,
        [
            Choice("move", "Move"),
            Choice("attack", "Longsword", "Longsword"),
            Choice("attack", "Shortbow", "Shortbow"),
        ],
    )
    _click(battlefield, _wedge_point(battlefield, 2))

    with qtbot.waitSignal(battlefield.planned) as caught:
        _click(battlefield, _centre_of(battlefield, 1, 0))

    assert caught.args[0].weapon == "Shortbow"


def test_nobody_swings_at_themselves(battlefield):
    """Clicking your own token after picking a weapon is a slip, not a plan."""
    battlefield.offer(1, [Choice("attack", "Club", "Club")])
    _click(battlefield, _wedge_point(battlefield, 0))

    plans = []
    battlefield.planned.connect(plans.append)
    _click(battlefield, _centre_of(battlefield, 0, 0))

    assert plans == []


def test_clicking_the_middle_puts_the_wheel_away(battlefield):
    """Changing your mind is a click on the creature, not a hunt for the exit."""
    battlefield.offer(1, [Choice("move", "Move")])

    _click(battlefield, _centre_of(battlefield, 0, 0))

    assert battlefield.radial_open is False


def test_a_frozen_map_takes_nothing(battlefield):
    """A turn already on offer owns the map until somebody answers it."""
    battlefield.offer(1, [Choice("move", "Move")])
    battlefield.frozen = True

    _click(battlefield, _wedge_point(battlefield, 0))

    assert battlefield.awaiting is None


# ---------------------------------------------------------------- the plan


def test_an_empty_plan_is_empty():
    assert TurnPlan(combatant=1).is_empty is True


def test_a_plan_with_a_move_is_not():
    assert TurnPlan(combatant=1, move=(1, 1)).is_empty is False


def test_a_plan_with_a_target_is_not():
    assert TurnPlan(combatant=1, target=2, weapon="Club").is_empty is False


def test_the_wheel_draws_without_complaint(battlefield):
    """Painting is where an angle and a radius disagree, so it gets exercised."""
    battlefield.offer(
        1, [Choice("move", "Move"), Choice("attack", "Club", "Club")]
    )
    battlefield.grab()  # must not raise


# --------------------------------------------------------- the real panel


@pytest.fixture
def combat(qtbot, ctx):
    """A running fight in the DM's own Combat panel."""
    from canon_keeper.panels.encounter.widget import EncounterWidget
    from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity

    repos = ctx.repos
    hero = repos.entities.create(
        Entity(
            id=None,
            campaign_id=ctx.campaign_id,
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
            campaign_id=ctx.campaign_id,
            kind=KIND_NPC,
            name="Yeemik",
            data={"hp": 7, "max_hp": 7, "sheet": {"schema": 1, "level": 1}},
        )
    )
    enc = repos.encounters.create(ctx.campaign_id, "The cave", width=12, height=12)
    tokens = {
        "hero": repos.encounters.add(enc.id, hero.id, initiative=20, x=0, y=0),
        "goblin": repos.encounters.add(enc.id, goblin.id, initiative=10, x=1, y=0),
    }
    repos.encounters.begin(enc.id)

    widget = EncounterWidget(ctx)
    qtbot.addWidget(widget)
    return widget, repos, enc, tokens


def test_the_one_who_is_up_is_selected(combat):
    """You should never have to aim before you can act."""
    widget, _repos, _enc, tokens = combat

    assert widget._map.selected == tokens["hero"].id


def test_the_wedges_are_move_and_the_weapons_carried(combat):
    widget, _repos, _enc, tokens = combat
    combatant = widget._by_id(tokens["hero"].id)

    labels = [c.label for c in widget._choices_for(combatant)]

    assert labels[0] == "Move"
    assert "Battleaxe" in labels
    assert "Shortbow" in labels


def test_somebody_down_is_offered_nothing(combat):
    widget, repos, _enc, tokens = combat
    combatant = widget._by_id(tokens["hero"].id)
    entity = repos.entities.get(combatant.entity_id)
    entity.data = {**(entity.data or {}), "hp": 0}
    repos.entities.update(entity)
    widget._refresh()

    assert widget._choices_for(widget._by_id(tokens["hero"].id)) == []


def test_space_out_of_turn_says_so(combat):
    widget, _repos, _enc, tokens = combat
    said = []
    widget._ctx.bus.status_message.connect(said.append)

    widget._offer_wheel(tokens["goblin"].id)

    assert widget._map.radial_open is False
    assert said and "not" in said[0].lower()


# ------------------------------------------------------------ staging a turn
#
# Each wedge used to fire the moment it was picked, so "walk over there and
# swing" was two commitments with no way back from the first -- and the DM could
# not compose the move-and-attack that autopilot composes through `take_turn` and
# that a player is shown and accepts. Now it is lined up, read, and committed.


def _staged(widget):
    return widget._staged_steps


def test_a_staged_turn_becomes_a_turn_when_it_is_committed(combat, qtbot):
    """One dict to the host, carrying the steps in the order they were picked."""
    widget, _repos, _enc, tokens = combat

    widget._stage(
        TurnPlan(
            combatant=tokens["hero"].id,
            target=tokens["goblin"].id,
            weapon="Battleaxe",
        )
    )
    with qtbot.waitSignal(widget._ctx.bus.turn_taken) as caught:
        widget._commit_staged()

    assert caught.args[0] == {
        "combatant": tokens["hero"].id,
        "steps": [turns.a_swing(tokens["goblin"].id, "Battleaxe")],
    }


def test_picking_something_does_not_carry_it_out(combat):
    """The point of staging: nothing leaves this panel until it is committed."""
    widget, _repos, _enc, tokens = combat
    sent = []
    widget._ctx.bus.turn_taken.connect(sent.append)

    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))

    assert sent == [], "a picked wedge went straight to the host"
    assert _staged(widget) == [turns.a_move(1, 1)]


def test_a_move_and_an_attack_stage_as_one_turn(combat, qtbot):
    """Two picks, one turn -- the thing the DM could not do at all."""
    widget, _repos, _enc, tokens = combat

    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))
    widget._stage(
        TurnPlan(
            combatant=tokens["hero"].id,
            target=tokens["goblin"].id,
            weapon="Battleaxe",
        )
    )
    with qtbot.waitSignal(widget._ctx.bus.turn_taken) as caught:
        widget._commit_staged()

    assert caught.args[0]["steps"] == [
        turns.a_move(1, 1),
        turns.a_swing(tokens["goblin"].id, "Battleaxe"),
    ]


def test_a_second_move_is_another_leg_of_the_walk(combat):
    """Appended, not replaced. This is what splitting a move around the action is.

    While a turn carried one move and one attack in that order, picking a second
    move could only mean "no, there instead". A turn made of steps can mean
    "three squares, swing, three more", so it does.
    """
    widget, _repos, _enc, tokens = combat

    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))
    widget._stage(
        TurnPlan(
            combatant=tokens["hero"].id,
            target=tokens["goblin"].id,
            weapon="Battleaxe",
        )
    )
    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(2, 2)))

    assert _staged(widget) == [
        turns.a_move(1, 1),
        turns.a_swing(tokens["goblin"].id, "Battleaxe"),
        turns.a_move(2, 2),
    ]


def test_staging_for_somebody_else_starts_again(combat):
    """Two half-turns for two creatures is not a thing anybody meant to ask."""
    widget, _repos, _enc, tokens = combat

    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))
    widget._stage(TurnPlan(combatant=tokens["goblin"].id, move=(3, 3)))

    assert widget._staged_for == tokens["goblin"].id
    assert _staged(widget) == [turns.a_move(3, 3)]


def test_clearing_leaves_nothing_to_commit(combat):
    widget, _repos, _enc, tokens = combat
    sent = []
    widget._ctx.bus.turn_taken.connect(sent.append)

    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))
    widget._clear_staged()
    widget._commit_staged()

    assert sent == []
    assert _staged(widget) == []


def test_an_empty_plan_is_not_staged(combat):
    widget, _repos, _enc, tokens = combat
    sent = []
    widget._ctx.bus.turn_taken.connect(sent.append)

    widget._stage(TurnPlan(combatant=tokens["hero"].id))
    widget._commit_staged()

    assert sent == []


def test_the_bar_says_the_whole_turn_in_order(combat):
    """In words, because a dotted line cannot say which weapon or in what order.

    The same sentence builder the host puts on a proposal, from
    ``canon_keeper_protocol.turns`` -- one definition, so the DM's bar and a
    player's accept bar cannot come to describe the same steps differently.
    """
    widget, _repos, _enc, tokens = combat

    assert widget._staged_bar.isHidden(), "furniture when there is nothing"

    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))
    widget._stage(
        TurnPlan(
            combatant=tokens["hero"].id,
            target=tokens["goblin"].id,
            weapon="Battleaxe",
        )
    )
    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(2, 2)))

    said = widget._staged_text.text()
    assert "1,1" in said and "2,2" in said
    assert "Battleaxe" in said
    assert "then" in said, f"the order was not readable: {said!r}"

    widget._clear_staged()
    assert widget._staged_bar.isHidden()


def test_the_map_is_shown_where_the_turn_ends(combat):
    """Feedback where the DM is looking, not only in the bar below."""
    widget, _repos, _enc, tokens = combat

    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))
    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(2, 2)))

    preview = widget._map._preview
    assert preview is not None, "the map was told nothing"
    assert preview.token == tokens["hero"].id
    assert preview.to == (2, 2), "it drew a waypoint rather than the destination"

    widget._clear_staged()
    assert widget._map._preview is None


def test_the_attack_dialog_stages_once_a_fight_is_running(combat, monkeypatch):
    """The dialog and the wheel are the same turn, so they wait the same way."""
    widget, _repos, _enc, tokens = combat
    sent = []
    widget._ctx.bus.turn_taken.connect(sent.append)

    class _Asked:
        def ask(self):
            return tokens["goblin"].id, "Battleaxe"

    monkeypatch.setattr(
        "canon_keeper.panels.encounter.widget.AttackDialog",
        lambda *a, **k: _Asked(),
    )

    widget._attack()

    assert sent == [], "the dialog went straight to the host"
    assert _staged(widget) == [turns.a_swing(tokens["goblin"].id, "Battleaxe")]


# ------------------------------------------- what is left of a refused turn


def test_the_part_that_happened_stops_being_staged(combat):
    """Three squares and then a refused swing is three squares spent.

    Committing again must not walk them a second time, so the host says how far
    it got and the walk is dropped. What is left is the swing, which is the thing
    to adjust.
    """
    widget, _repos, _enc, tokens = combat
    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))
    widget._stage(
        TurnPlan(
            combatant=tokens["hero"].id,
            target=tokens["goblin"].id,
            weapon="Battleaxe",
        )
    )

    widget._ctx.bus.turn_settled.emit(False, "that is 20 feet away -- too far", 1)

    assert _staged(widget) == [turns.a_swing(tokens["goblin"].id, "Battleaxe")]
    assert "too far" in widget._staged_text.text()
    assert not widget._staged_bar.isHidden(), "still on offer to be fixed"


def test_a_turn_refused_outright_stays_whole(combat):
    """Nothing happened, so there is nothing to drop."""
    widget, _repos, _enc, tokens = combat
    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))

    widget._ctx.bus.turn_settled.emit(False, "somebody is already there", 0)

    assert _staged(widget) == [turns.a_move(1, 1)]
    assert "already there" in widget._staged_text.text()


def test_a_turn_that_happened_is_cleared(combat):
    widget, _repos, _enc, tokens = combat
    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))

    widget._ctx.bus.turn_settled.emit(True, "", 1)

    assert _staged(widget) == []
    assert widget._staged_bar.isHidden()


def test_a_refused_turn_can_still_be_cleared_outright(combat):
    """Keeping it must not mean being stuck with it."""
    widget, _repos, _enc, tokens = combat
    widget._stage(TurnPlan(combatant=tokens["hero"].id, move=(1, 1)))
    widget._ctx.bus.turn_settled.emit(False, "too far", 0)

    widget._clear_staged()

    assert _staged(widget) == []
    assert widget._staged_bar.isHidden()
