"""The panels a player sees.

They render from data the host already filtered, so these tests are about the
view being honest: only what arrived is shown, only your own sheet is editable,
and an entity that goes away actually disappears.
"""

from __future__ import annotations

import logging

import pytest

from canon_keeper.bus import Bus
from canon_keeper.net.state import SharedState
from canon_keeper.panels.characters import CharactersPanel, PlayerCharactersWidget
from canon_keeper.panels.characters.widget import CharactersWidget
from canon_keeper.panels.cities import CitiesPanel, PlayerCitiesWidget
from canon_keeper.panels.cities.widget import CitiesWidget
from canon_keeper.plugin import AppContext
from canon_keeper_core.repo.entities import KIND_LOCATION, KIND_NPC, KIND_PC


@pytest.fixture
def player_ctx(repos):
    campaign = repos.campaigns.ensure_default("Test Campaign")
    return AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
        role="player",
        shared=SharedState(),
    )


def _select(widget, entity_id: int) -> None:
    """Pick a row by entity id; the list is sorted by name, not insertion."""
    for row in range(widget._list.count()):
        if widget._list.item(row).data(256) == entity_id:
            widget._list.setCurrentRow(row)
            return
    raise AssertionError(f"no row for entity {entity_id}")


def _received(**overrides) -> dict:
    entity = {
        "id": 1,
        "kind": KIND_NPC,
        "name": "Sildar",
        "summary": "A weary man",
        "data": {},
        "parent_id": None,
        "own": False,
    }
    entity.update(overrides)
    return entity


# ------------------------------------------------------------------ panel routing


def test_panels_serve_both_roles(player_ctx, ctx, qtbot):
    for panel, dm_type, player_type in (
        (CharactersPanel(), CharactersWidget, PlayerCharactersWidget),
        (CitiesPanel(), CitiesWidget, PlayerCitiesWidget),
    ):
        assert set(panel.roles) == {"dm", "player"}
        player_widget = panel.create_widget(player_ctx)
        qtbot.addWidget(player_widget)
        assert isinstance(player_widget, player_type)

        dm_widget = panel.create_widget(ctx)
        qtbot.addWidget(dm_widget)
        assert isinstance(dm_widget, dm_type)


# --------------------------------------------------------------------- characters


def test_the_list_shows_only_what_arrived(player_ctx, qtbot):
    widget = PlayerCharactersWidget(player_ctx)
    qtbot.addWidget(widget)
    assert widget._list.count() == 0, "nothing shared yet, so nothing shown"

    player_ctx.shared.replace_all(
        [_received(id=1, name="Sildar"), _received(id=2, name="Toblen")]
    )
    assert widget._list.count() == 2


def test_an_entity_taken_back_disappears(player_ctx, qtbot):
    widget = PlayerCharactersWidget(player_ctx)
    qtbot.addWidget(widget)
    player_ctx.shared.replace_all([_received(id=1, name="Sildar")])
    assert widget._list.count() == 1

    player_ctx.shared.remove(1)
    assert widget._list.count() == 0


def test_only_your_own_sheet_is_editable(player_ctx, qtbot):
    widget = PlayerCharactersWidget(player_ctx)
    qtbot.addWidget(widget)
    player_ctx.shared.replace_all(
        [
            _received(id=1, kind=KIND_PC, name="Elara", own=True, data={"hp": 12}),
            _received(id=2, kind=KIND_PC, name="Brakk", own=False, data={"hp": 9}),
        ]
    )

    # The list is sorted by name, so select by identity rather than position.
    _select(widget, 1)
    assert widget._hp.isEnabled() is True
    assert widget._save.isHidden() is False

    _select(widget, 2)
    assert widget._hp.isEnabled() is False
    assert widget._save.isHidden() is True


def test_another_players_notes_are_not_even_a_field(player_ctx, qtbot):
    """The projection never sends them; the view must not imply they exist."""
    widget = PlayerCharactersWidget(player_ctx)
    qtbot.addWidget(widget)
    player_ctx.shared.replace_all([_received(id=2, kind=KIND_PC, name="Brakk")])
    _select(widget, 2)

    assert widget._notes.isHidden() is True
    assert widget._inventory.isHidden() is True


def test_editing_your_sheet_asks_the_host(player_ctx, qtbot):
    """The client never writes locally: the host decides, then echoes back."""
    widget = PlayerCharactersWidget(player_ctx)
    qtbot.addWidget(widget)
    player_ctx.shared.replace_all(
        [_received(id=1, kind=KIND_PC, name="Elara", own=True, data={"hp": 12, "max_hp": 20})]
    )
    _select(widget, 1)

    with qtbot.waitSignal(player_ctx.bus.player_edit_requested, timeout=1000) as blocker:
        widget._hp.setValue(4)
        widget._save_own()

    entity_id, changes = blocker.args
    assert entity_id == 1
    assert changes["data"]["hp"] == 4


def test_a_player_edit_carries_only_player_fields(player_ctx, qtbot):
    widget = PlayerCharactersWidget(player_ctx)
    qtbot.addWidget(widget)
    player_ctx.shared.replace_all(
        [_received(id=1, kind=KIND_PC, name="Elara", own=True)]
    )
    _select(widget, 1)

    with qtbot.waitSignal(player_ctx.bus.player_edit_requested, timeout=1000) as blocker:
        widget._save_own()

    _id, changes = blocker.args
    assert set(changes["data"]) <= {
        "status",
        "hp",
        "max_hp",
        "conditions",
        "inventory",
        "player_notes",
    }
    assert "name" not in changes
    assert "kind" not in changes


# ------------------------------------------------------------------------ cities


def test_places_appear_as_a_tree(player_ctx, qtbot):
    widget = PlayerCitiesWidget(player_ctx)
    qtbot.addWidget(widget)
    player_ctx.shared.replace_all(
        [
            _received(id=10, kind=KIND_LOCATION, name="Phandalin"),
            _received(id=11, kind=KIND_LOCATION, name="Dock Ward", parent_id=10),
        ]
    )

    assert widget._tree.topLevelItemCount() == 1
    root = widget._tree.topLevelItem(0)
    assert "Phandalin" in root.text(0)
    assert root.childCount() == 1


def test_a_place_whose_parent_is_unknown_becomes_a_root(player_ctx, qtbot):
    """The host blanks the parent it did not share; the tree must not lose it."""
    widget = PlayerCitiesWidget(player_ctx)
    qtbot.addWidget(widget)
    player_ctx.shared.replace_all(
        [_received(id=11, kind=KIND_LOCATION, name="Dock Ward", parent_id=None)]
    )

    assert widget._tree.topLevelItemCount() == 1


def test_who_is_here_lists_shared_occupants(player_ctx, qtbot):
    widget = PlayerCitiesWidget(player_ctx)
    qtbot.addWidget(widget)
    player_ctx.shared.replace_all(
        [
            _received(id=10, kind=KIND_LOCATION, name="Phandalin"),
            _received(
                id=1, kind=KIND_NPC, name="Sildar", parent_id=10, data={"status": "alive"}
            ),
        ]
    )
    widget._tree.setCurrentItem(widget._tree.topLevelItem(0))

    labels = [widget._occupants.item(i).text() for i in range(widget._occupants.count())]
    assert any("Sildar" in label for label in labels)


def test_only_shared_notes_are_shown_for_a_place(player_ctx, qtbot):
    widget = PlayerCitiesWidget(player_ctx)
    qtbot.addWidget(widget)
    player_ctx.shared.replace_all(
        [
            _received(
                id=10,
                kind=KIND_LOCATION,
                name="Phandalin",
                data={"place_type": "town", "shared_notes": "A muddy street"},
            )
        ]
    )
    widget._tree.setCurrentItem(widget._tree.topLevelItem(0))

    assert widget._notes.toPlainText() == "A muddy street"
    assert widget._notes.isReadOnly()


def test_empty_state_explains_itself(player_ctx, qtbot):
    widget = PlayerCitiesWidget(player_ctx)
    qtbot.addWidget(widget)
    assert widget._tree.topLevelItemCount() == 0
    assert "DM shares" in widget._hint.text()
