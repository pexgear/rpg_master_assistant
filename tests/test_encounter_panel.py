"""The Combat panel, both sides of it.

The DM's map writes to the database and says so on the bus; a player's map is
drawn from what the host sent and can say nothing at all. Testing them together
in one file is deliberate -- the interesting assertions are the differences.
"""

from __future__ import annotations

import logging

import pytest

from PySide6.QtCore import Qt

from canon_keeper.bus import Bus
from canon_keeper.net.state import SharedState
from canon_keeper.panels.encounter import (
    EncounterPanel,
    EncounterWidget,
    PlayerEncounterWidget,
)
from canon_keeper.panels.encounter.grid import GridMap, Token
from canon_keeper.plugin import AppContext
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity


@pytest.fixture
def fight(repos, ctx):
    """A campaign with a fight, one PC shared and one goblin not."""
    hero = repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_PC, name="Sable")
    )
    goblin = repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_NPC, name="Yeemik")
    )
    repos.shares.share(ctx.campaign_id, hero.id)

    # Ten across and eight down, so x runs -5..4 and y runs -4..3 with 0,0 in
    # the middle.
    encounter = repos.encounters.create(ctx.campaign_id, "The cave", width=10, height=8)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=18, x=-4, y=-3),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=9, x=2, y=2),
    }
    return encounter, tokens, hero, goblin


@pytest.fixture
def widget(qtbot, ctx, fight):
    made = EncounterWidget(ctx)
    qtbot.addWidget(made)
    return made


# ------------------------------------------------------------------- routing


def test_a_player_gets_the_read_only_one(ctx, qtbot):
    ctx.role = "player"
    made = EncounterPanel().create_widget(ctx)
    qtbot.addWidget(made)
    assert isinstance(made, PlayerEncounterWidget)


def test_the_dm_gets_the_one_with_buttons(ctx, qtbot):
    made = EncounterPanel().create_widget(ctx)
    qtbot.addWidget(made)
    assert isinstance(made, EncounterWidget)


def _rows(order) -> list[str]:
    """The combatants in an order list, without the side headings.

    A heading carries no combatant id, which is also what stops it being
    selectable -- so the same check does for both.
    """
    return [
        order.item(i).text()
        for i in range(order.count())
        if order.item(i).data(Qt.ItemDataRole.UserRole) is not None
    ]


def _headings(order) -> list[str]:
    return [
        order.item(i).text()
        for i in range(order.count())
        if order.item(i).data(Qt.ItemDataRole.UserRole) is None
    ]


# ------------------------------------------------------------- the DM's panel


def test_it_shows_the_fight(widget, fight):
    encounter, _tokens, _hero, _goblin = fight
    assert len(_rows(widget._order)) == 2
    assert "The cave" in widget._heading.text()


def test_the_order_is_grouped_by_side(widget):
    """The party first, then whoever they are fighting."""
    assert _headings(widget._order) == ["THE PARTY", "HOSTILE"]


def test_a_new_fight_has_two_sides_without_being_asked(widget, ctx, fight):
    encounter, _tokens, _hero, _goblin = fight
    teams = ctx.repos.encounters.teams(encounter.id)
    assert [t.name for t in teams] == ["The party", "Hostile"]
    assert [t.is_party for t in teams] == [True, False]


def test_the_order_is_the_initiative_order(widget):
    """Within a side. Across sides the grouping wins, which is the point."""
    labels = _rows(widget._order)
    assert "Sable" in labels[0], "18 comes before 9"
    assert "Yeemik" in labels[1]


def test_a_row_says_the_name_first_and_the_rest_underneath(widget):
    """Two lines: who they are, then what is true of them."""
    sable = _rows(widget._order)[0]
    head, _, tail = sable.partition("\n")
    assert "Sable" in head
    assert "unshared" not in head, "the name line is for the name"
    assert "18" in head


def test_an_unshared_creature_is_marked(widget):
    """The DM should never have to ask the party what they can see."""
    labels = " ".join(
        widget._order.item(i).text() for i in range(widget._order.count())
    )
    assert "unshared" in labels
    assert "no player can see" in widget._hint.text()


def test_sharing_one_takes_the_mark_off(widget, ctx, fight):
    _encounter, _tokens, _hero, goblin = fight
    widget._share(goblin.id)
    labels = " ".join(
        widget._order.item(i).text() for i in range(widget._order.count())
    )
    assert "unshared" not in labels


def test_a_token_on_the_map_cannot_be_dragged_elsewhere(widget, ctx, fight, qtbot):
    """There is no gesture that slides a creature from one square to another.

    It was a teleport: it belonged to nobody's turn, walked through walls and
    bodies, cost no movement and provoked nothing. Creatures move by taking a
    turn, and the map has one way to do that.
    """
    _encounter, tokens, _hero, _goblin = fight
    with qtbot.waitSignal(ctx.bus.status_message, timeout=1000) as blocker:
        widget._on_dropped(tokens["hero"].id, 3, 1)

    assert "already on the map" in blocker.args[0]
    assert ctx.repos.encounters.combatant(tokens["hero"].id).x == -4


def test_dropping_somebody_onto_the_map_still_places_them(widget, ctx, fight, qtbot):
    """Arriving is not walking, and it is the one thing dragging still does."""
    _encounter, tokens, _hero, _goblin = fight
    ctx.repos.encounters.place(tokens["hero"].id, None, None)
    widget._refresh()

    with qtbot.waitSignal(ctx.bus.encounter_changed, timeout=1000):
        widget._on_dropped(tokens["hero"].id, 3, 1)

    assert ctx.repos.encounters.combatant(tokens["hero"].id).x == 3


def test_taking_one_off_the_map_keeps_it_in_the_order(widget, ctx, fight):
    _encounter, tokens, _hero, _goblin = fight
    widget._take_off(tokens["hero"].id)
    assert ctx.repos.encounters.combatant(tokens["hero"].id) is not None
    assert len(_rows(widget._order)) == 2
    assert "off the map" in " ".join(_rows(widget._order))


def test_a_token_off_the_map_is_not_drawn(widget, ctx, fight):
    _encounter, tokens, _hero, _goblin = fight
    widget._take_off(tokens["hero"].id)
    assert len(widget._map._tokens) == 1


def test_clicking_a_square_places_the_selected_one(widget, ctx, fight):
    _encounter, tokens, _hero, _goblin = fight
    widget._take_off(tokens["hero"].id)
    widget._map.select(tokens["hero"].id)

    widget._on_square(3, -1)

    placed = ctx.repos.encounters.combatant(tokens["hero"].id)
    assert (placed.x, placed.y) == (3, -1)


def test_clicking_a_square_does_not_teleport_someone_already_standing(widget, ctx, fight):
    """Selecting a token on the map and clicking elsewhere is not a move.

    Moving is dragging. Otherwise every click to look at someone would walk
    them across the room.
    """
    _encounter, tokens, _hero, _goblin = fight
    widget._map.select(tokens["hero"].id)
    widget._on_square(3, -1)
    assert ctx.repos.encounters.combatant(tokens["hero"].id).x == -4


def test_starting_and_passing_the_turn(widget, ctx, fight):
    """Starting is the panel's own; passing the turn is asked of the host.

    Passing it can roll a death save, and dice are the host's. The panel says
    what it wants and the Table panel -- which owns the host -- does it, the
    same way taking a turn by hand already worked.
    """
    encounter, tokens, _hero, _goblin = fight
    widget._start_or_next()
    assert ctx.repos.encounters.get(encounter.id).round == 1
    assert ctx.repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id
    assert widget._fight_button.text() == "End fight", (
        "once a fight is running, the same button ends it"
    )

    asked: list[str] = []
    ctx.bus.turn_requested.connect(asked.append)
    widget._start_or_next()

    assert asked == ["next"], "the panel passed the turn without asking the host"
    assert (
        ctx.repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id
    ), "the panel moved the turn itself as well"


def test_ending_it_stops_the_clock(widget, ctx, fight):
    encounter, _tokens, _hero, _goblin = fight
    widget._start_or_next()
    widget._end()
    assert ctx.repos.encounters.get(encounter.id).round == 0
    # One button for the fight's life: it offers to start one again.
    assert widget._fight_button.text() == "Start fight"


def test_rolling_initiative_gives_everyone_a_number(widget, ctx, fight):
    encounter, _tokens, _hero, _goblin = fight
    ctx.repos.encounters.set_initiative(_tokens_of(ctx, encounter)[0].id, None)
    widget._refresh()

    widget._roll_initiative()

    assert all(c.initiative is not None for c in _tokens_of(ctx, encounter))


def test_taking_someone_out_of_the_fight(widget, ctx, fight, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    _encounter, tokens, _hero, _goblin = fight
    widget._remove(tokens["goblin"].id, "Yeemik")
    assert ctx.repos.encounters.combatant(tokens["goblin"].id) is None
    assert len(_rows(widget._order)) == 1
    assert _headings(widget._order) == ["THE PARTY"], (
        "a side with nobody left on it is not worth a heading"
    )


def test_starting_a_fight_asks_nothing(qtbot, ctx):
    """No dialog. Two questions in the way of the button you just pressed."""
    made = EncounterWidget(ctx)
    qtbot.addWidget(made)

    made._new_fight()

    fight = ctx.repos.encounters.running(ctx.campaign_id)
    assert fight is not None
    assert fight.width and fight.height, "it needs a grid to stand on"


def test_a_fight_can_be_named_and_grown_afterwards(widget, ctx, fight):
    encounter, _tokens, _hero, _goblin = fight
    ctx.repos.encounters.rename(encounter.id, "The cave mouth")
    ctx.repos.encounters.resize(encounter.id, 20, 16)
    widget._refresh()

    assert "The cave mouth" in widget._heading.text()
    assert widget._map._width == 20


def test_dragging_someone_onto_the_map_places_them(widget, ctx, fight):
    _encounter, tokens, _hero, _goblin = fight
    widget._take_off(tokens["hero"].id)

    widget._on_dropped(tokens["hero"].id, 1, 3)

    placed = ctx.repos.encounters.combatant(tokens["hero"].id)
    assert (placed.x, placed.y) == (1, 3)


def test_a_drop_onto_an_occupied_square_is_refused(widget, ctx, fight, qtbot):
    _encounter, tokens, _hero, _goblin = fight
    with qtbot.waitSignal(ctx.bus.status_message, timeout=1000):
        widget._on_dropped(tokens["hero"].id, 2, 2)
    assert ctx.repos.encounters.combatant(tokens["hero"].id).x == -4


def test_ctrl_clicking_a_square_puts_something_in_the_way(widget, ctx, fight):
    encounter, _tokens, _hero, _goblin = fight
    widget._on_obstacle(-2, 1)
    assert ctx.repos.encounters.obstacles(encounter.id) == {(-2, 1)}
    assert (-2, 1) in widget._map._obstacles

    widget._on_obstacle(-2, 1)
    assert ctx.repos.encounters.obstacles(encounter.id) == set()


def test_nobody_can_be_dropped_onto_an_obstacle(widget, ctx, fight, qtbot):
    _encounter, tokens, _hero, _goblin = fight
    widget._on_obstacle(-2, 1)
    with qtbot.waitSignal(ctx.bus.status_message, timeout=1000):
        widget._on_dropped(tokens["hero"].id, -2, 1)
    assert ctx.repos.encounters.combatant(tokens["hero"].id).x == -4


def test_ctrl_clicking_under_someone_says_why_not(widget, ctx, fight, qtbot):
    encounter, _tokens, _hero, _goblin = fight
    with qtbot.waitSignal(ctx.bus.status_message, timeout=1000) as blocker:
        widget._on_obstacle(-4, -3)
    assert "standing there" in blocker.args[0]
    assert ctx.repos.encounters.obstacles(encounter.id) == set()


def test_a_player_sees_the_terrain(player_map):
    made, shared = player_map
    shared.set_encounter(_sent(obstacles=[[2, 2], [3, 3]]))
    assert made._map._obstacles == {(2, 2), (3, 3)}


def test_the_order_hands_over_one_combatant_id(widget, fight):
    """What the map reads off the clipboard. One number, its own mime type."""
    from canon_keeper.panels.encounter.grid import COMBATANT_MIME

    _encounter, tokens, _hero, _goblin = fight
    item = next(
        widget._order.item(i)
        for i in range(widget._order.count())
        if widget._order.item(i).data(Qt.ItemDataRole.UserRole) is not None
    )
    data = widget._order.mimeData([item])

    assert data.hasFormat(COMBATANT_MIME)
    assert int(bytes(data.data(COMBATANT_MIME)).decode()) == tokens["hero"].id


def test_a_players_map_takes_no_drops(player_map):
    made, _shared = player_map
    assert made._map.read_only is True
    assert not made._map._will_take(_FakeDrop())


class _FakeDrop:
    """Just enough of a drag event to ask whether the map would take it."""

    class _Mime:
        @staticmethod
        def hasFormat(_kind: str) -> bool:  # noqa: N802 - Qt's name
            return True

    def mimeData(self):  # noqa: N802 - Qt's name
        return self._Mime()


def test_an_empty_campaign_says_what_to_press(qtbot, ctx):
    made = EncounterWidget(ctx)
    qtbot.addWidget(made)
    assert "New fight" in made._heading.text()


def _tokens_of(ctx, encounter):
    return ctx.repos.encounters.combatants(encounter.id)


# ---------------------------------------------------------- the player's panel


@pytest.fixture
def player_map(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Watching")
    shared = SharedState()
    ctx = AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
        role="player",
        shared=shared,
    )
    made = PlayerEncounterWidget(ctx)
    qtbot.addWidget(made)
    return made, shared


def _sent(**overrides) -> dict:
    fight = {
        "id": 1,
        "name": "The cave",
        "width": 10,
        "height": 8,
        "round": 2,
        "turn": 11,
        "running": True,
        "combatants": [
            {"id": 11, "entity": 1, "initiative": 18, "x": 1, "y": 1},
            {"id": 12, "entity": 2, "initiative": 9, "x": 4, "y": 4},
        ],
    }
    fight.update(overrides)
    return fight


def test_nothing_arrives_and_it_says_so(player_map):
    made, _shared = player_map
    assert "No fight" in made._heading.text()


def test_what_arrives_is_drawn(player_map):
    made, shared = player_map
    shared.replace_all(
        [
            {"id": 1, "kind": KIND_PC, "name": "Sable", "data": {}, "own": True},
            {"id": 2, "kind": KIND_NPC, "name": "Yeemik", "data": {}},
        ]
    )
    shared.set_encounter(_sent())

    assert made._order.count() == 2
    assert len(made._map._tokens) == 2
    assert "round 2" in made._heading.text()
    assert "Sable is up" in made._heading.text()


def test_it_cannot_be_dragged(player_map):
    made, _shared = player_map
    assert made._map.read_only is True


def test_a_turn_it_cannot_see_is_admitted_to(player_map):
    """Better than a blank line: the DM would say "and now it moves" out loud."""
    made, shared = player_map
    shared.set_encounter(_sent(turn=99))
    assert "cannot see" in made._heading.text()


def test_the_fight_ending_clears_the_map(player_map):
    made, shared = player_map
    shared.set_encounter(_sent())
    shared.set_encounter(None)
    assert made._map._tokens == []
    assert "No fight" in made._heading.text()


def test_leaving_the_session_clears_it_too(player_map):
    made, shared = player_map
    shared.set_encounter(_sent())
    shared.clear()
    assert made._map._tokens == []


# --------------------------------------------------------- a turn on offer


def _offer(**overrides) -> dict:
    action = {
        "id": "abc123",
        "combatant": 11,
        "who": "Brok",
        "move": [3, -1],
        "target": 12,
        "target_name": "Yeemik",
        "weapon": "battleaxe",
        "text": "Move to 3,-1 and attack Yeemik with a battleaxe.",
    }
    action.update(overrides)
    return action


def test_a_turn_on_offer_is_drawn_on_the_map(player_map):
    """The words say what; the map says where, which is what you recognise."""
    made, shared = player_map
    shared.set_encounter(_sent())
    made._ctx.bus.action_proposed.emit(_offer())

    preview = made._map._preview
    assert preview is not None
    assert preview.token == 11
    assert preview.to == (3, -1)
    assert preview.target == (4, 4), "the square the target is standing on"


def test_the_offer_is_said_in_words_too(player_map):
    made, shared = player_map
    shared.set_encounter(_sent())
    made._ctx.bus.action_proposed.emit(_offer())
    assert "battleaxe" in made._offer_text.text()
    # isHidden, not isVisible: nothing here is shown on screen in a test, and
    # isVisible would be False for a widget that is perfectly well set up.
    assert not made._offer_text.isHidden()


def test_the_map_is_held_while_it_waits(player_map):
    """Readable, but not fiddleable. Clicking about is not an answer."""
    made, shared = player_map
    shared.set_encounter(_sent())
    made._ctx.bus.action_proposed.emit(_offer())

    assert made._map.frozen is True
    assert not made._order.isEnabled()


def test_answering_gives_the_map_back(player_map):
    made, shared = player_map
    shared.set_encounter(_sent())
    made._ctx.bus.action_proposed.emit(_offer())
    made._ctx.bus.action_withdrawn.emit("abc123")

    assert made._map.frozen is False
    assert made._map._preview is None
    assert made._offer_text.isHidden()


def test_the_dms_copy_does_not_hold_their_map(player_map):
    """They are watching it happen, not being asked."""
    made, shared = player_map
    shared.set_encounter(_sent())
    made._ctx.bus.action_proposed.emit(_offer(watching=True))

    assert made._map.frozen is False
    assert "waiting on them" in made._offer_text.text()


def test_a_turn_with_no_move_still_shows_the_target(player_map):
    made, shared = player_map
    shared.set_encounter(_sent())
    made._ctx.bus.action_proposed.emit(_offer(move=None))

    assert made._map._preview.to is None
    assert made._map._preview.target == (4, 4)


# ------------------------------------------------------------------- the grid


def test_a_token_makes_initials_from_a_name():
    assert Token(id=1, label="Sable", x=0, y=0).initials == "SA"
    assert Token(id=1, label="Brok Ironfoot", x=0, y=0).initials == "BI"
    assert Token(id=1, label="", x=0, y=0).initials == "?"


def test_the_grid_forgets_a_selection_that_left_the_fight(qtbot):
    """Otherwise the next click on empty floor places a token that is gone."""
    grid = GridMap()
    qtbot.addWidget(grid)
    grid.set_tokens([Token(id=1, label="Sable", x=0, y=0)])
    grid.select(1)

    grid.set_tokens([])

    assert grid.selected is None
