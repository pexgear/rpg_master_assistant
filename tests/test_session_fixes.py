"""Three things that were quietly wrong at the table.

A refusal that left the rejected change on screen. A chat log where the game was
buried under people arriving and leaving. And updates landing in panels nobody
was looking at, with nothing to say so.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDockWidget, QMainWindow

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper.panels.table.widget import TableWidget
from canon_keeper_core.repo.entities import KIND_PC, Entity
from canon_keeper.shell.attention import TINT_ALPHA, Attention
from canon_keeper_protocol import MessageType, SystemKind


# --------------------------------------------------------------- the refusal


@pytest.fixture
def table_with_player(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Refusals")
    elara = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Elara",
            summary="A wizard.",
        )
    )
    marco = repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", display_name="Marco",
        character_entity_id=elara.id,
    )
    repos.entities.set_owner(elara.id, marco.id)

    server = SessionServer(repos, campaign.id, "Refusals")
    assert server.start(0, announce=False)
    yield server, repos.entities.get(elara.id)
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def test_a_refusal_tells_the_player_which_character(qtbot, table_with_player):
    """Without the id, a panel cannot know what to put back."""
    server, elara = table_with_player
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.system, timeout=5000):
            player.send_edit(elara.id, {"summary": "A wizard, and secretly a god."})

        waiting = server.proposals
        assert waiting, "the request should be queued"

        with qtbot.waitSignal(player.edit_refused, timeout=5000) as blocker:
            server.decide(waiting[0]["id"], False, "not yet")

        entity_id, reason = blocker.args
        assert entity_id == elara.id
        assert reason == "not yet"
    finally:
        player.leave()


def test_a_refusal_also_sends_back_what_is_true(qtbot, table_with_player):
    """The reason alone leaves the rejected change sitting on their screen."""
    server, elara = table_with_player
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.system, timeout=5000):
            player.send_edit(elara.id, {"summary": "A wizard, and secretly a god."})
        waiting = server.proposals

        with qtbot.waitSignal(player.state.changed, timeout=5000):
            server.decide(waiting[0]["id"], False, "not yet")

        held = player.state.get(elara.id)
        assert held["summary"] == "A wizard.", (
            "the player should be holding what the DM actually allows, not what "
            "they asked for"
        )
    finally:
        player.leave()


def test_an_approval_does_not_look_like_a_refusal(qtbot, table_with_player):
    server, elara = table_with_player
    player = _join(qtbot, server, "marco", "goblin-teeth")
    refusals: list = []
    player.edit_refused.connect(lambda *args: refusals.append(args))
    try:
        with qtbot.waitSignal(player.system, timeout=5000):
            player.send_edit(elara.id, {"summary": "A wizard, and a good one."})
        waiting = server.proposals

        with qtbot.waitSignal(player.state.changed, timeout=5000):
            server.decide(waiting[0]["id"], True)

        assert refusals == []
        assert player.state.get(elara.id)["summary"] == "A wizard, and a good one."
    finally:
        player.leave()


# ------------------------------------------------------------- the chat filter


@pytest.fixture
def table(ctx, qtbot) -> TableWidget:
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    return widget


def test_the_log_is_hidden_by_default(table):
    table._append_system("Marco joined", SystemKind.CHATTER.value)
    assert "Marco joined" not in table._log.toPlainText()


def test_but_they_are_kept_and_can_be_shown(table):
    table._append_system("Marco joined", SystemKind.CHATTER.value)

    table._show_chatter.setChecked(True)

    assert "Marco joined" in table._log.toPlainText()


def test_and_hidden_again(table):
    table._append_system("Marco joined", SystemKind.CHATTER.value)
    table._show_chatter.setChecked(True)
    table._show_chatter.setChecked(False)

    assert "Marco joined" not in table._log.toPlainText()


def test_a_notice_is_never_hidden(table):
    """The whole point of the distinction: a refusal is not housekeeping."""
    table._append_system("Your DM said no to a level change", SystemKind.NOTICE.value)
    assert "said no" in table._log.toPlainText()


def test_nor_is_anything_said_at_the_table(table):
    """The game itself is never filtered. Diagnostics are -- see below."""
    table._append("said", "Elara: I check the door.")
    table._append("roll", "Elara rolled 17")

    shown = table._log.toPlainText()
    assert "I check the door." in shown
    assert "rolled 17" in shown


def test_the_filter_keeps_the_order(table):
    table._append("said", "Elara: one")
    table._append_system("Marco joined", SystemKind.CHATTER.value)
    table._append("said", "Elara: two")

    table._show_chatter.setChecked(True)
    shown = table._log.toPlainText()

    assert shown.index("one") < shown.index("Marco joined") < shown.index("two")


def test_replayed_history_is_filtered_the_same_way(table):
    """Old joins are the same housekeeping as live ones."""
    table._on_history(
        [
            {"kind": "system", "text": "Marco joined", "at": 0},
            {"kind": "said", "speaker": "Elara", "text": "I check the door.", "at": 1},
        ]
    )

    assert "Marco joined" not in table._log.toPlainText()
    assert "I check the door." in table._log.toPlainText()


# ------------------------------------------------------------ panel attention


@pytest.fixture
def flagged(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    attention = Attention(window)
    dock = QDockWidget("Characters", window)
    dock.setObjectName("characters")
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    attention.watch("characters", dock)
    return attention, dock, window


def test_a_hidden_panel_is_tinted_when_something_arrives(flagged):
    attention, dock, _window = flagged
    dock.hide()

    attention.flag("characters")

    assert attention.is_waiting("characters")
    assert "background" in dock.styleSheet()


def test_a_panel_you_are_looking_at_is_not(flagged, qtbot):
    """Lighting up what is already on screen tells you nothing."""
    attention, dock, window = flagged
    window.show()
    qtbot.waitExposed(window)

    attention.flag("characters")

    assert not attention.is_waiting("characters")
    assert dock.styleSheet() == ""


def test_seeing_it_clears_the_flag(flagged, qtbot):
    attention, dock, window = flagged
    window.show()
    qtbot.waitExposed(window)
    dock.hide()
    attention.flag("characters")
    assert attention.is_waiting("characters")

    dock.show()  # visibilityChanged fires

    assert not attention.is_waiting("characters")


def test_the_tint_follows_the_theme(flagged):
    attention, dock, _window = flagged
    dock.hide()
    attention.flag("characters")

    attention.set_colour(QColor(200, 30, 30))

    assert "200, 30, 30" in dock.styleSheet()


def test_flagging_an_unknown_panel_is_harmless(flagged):
    attention, _dock, _window = flagged
    attention.flag("a-panel-that-is-not-installed")  # must not raise


def test_clearing_something_not_waiting_is_harmless(flagged):
    attention, _dock, _window = flagged
    attention.clear("characters")


def test_the_tint_is_visible_but_not_a_shout():
    assert 40 <= TINT_ALPHA <= 140, (
        "a title bar the reader cannot ignore is worse than no highlight at all"
    )


def test_the_message_types_exist():
    assert MessageType.REFUSED == "refused"
    assert SystemKind.CHATTER != SystemKind.NOTICE


# ---------------------------------------------------------- errors in the log
#
# An error is the app talking about itself, so it belongs in the log. But a
# problem hidden *and* unannounced is the worst of both: the reader neither
# sees it nor knows to look.


def test_an_error_goes_in_the_log(table):
    table._append("error", "The agent stopped.")
    assert "The agent stopped." not in table._log.toPlainText()


def test_but_the_checkbox_says_so(table):
    table._append("error", "The agent stopped.")

    assert "went wrong" in table._show_chatter.text()
    assert table._show_chatter.styleSheet(), "the mark should be visible"


def test_showing_the_log_clears_the_mark(table):
    table._append("error", "The agent stopped.")

    table._show_chatter.setChecked(True)

    assert table._show_chatter.text() == "Show log"
    assert table._show_chatter.styleSheet() == ""
    assert "The agent stopped." in table._log.toPlainText()


def test_an_error_while_the_log_is_open_needs_no_mark(table):
    """It is already on screen; marking it would be telling them twice."""
    table._show_chatter.setChecked(True)

    table._append("error", "The agent stopped.")

    assert table._show_chatter.styleSheet() == ""
    assert "The agent stopped." in table._log.toPlainText()


def test_chatter_alone_does_not_raise_the_mark(table):
    """Someone leaving is not something going wrong."""
    table._append_system("Marco left", SystemKind.CHATTER.value)
    assert table._show_chatter.styleSheet() == ""


def test_errors_are_red_and_follow_the_theme(table):
    """A red that ignores dark mode is unreadable in one of the two."""
    light = table._colours["error"]
    assert light.isValid()

    # The colour is chosen from the palette, so a repaint under another
    # appearance produces a different one.
    table._refresh_colours()
    assert table._colours["error"].isValid()


def test_the_mark_is_repainted_when_the_theme_changes(table):
    table._append("error", "The agent stopped.")
    before = table._show_chatter.styleSheet()

    table._refresh_colours()

    assert table._show_chatter.styleSheet(), "the mark should survive a repaint"
    assert before


# ------------------------------------------------------------------ the roster
#
# The agent was listed as a player. It answers *for* the DM, and the cause was
# a two-way branch: "DM if role == dm else player" makes anything that is not
# a DM into a player, which is right until a third role exists.


def test_the_agent_is_not_listed_as_a_player(table):
    from canon_keeper.panels.table.widget import ROLE_LABELS
    from canon_keeper_protocol import Member, Role

    table._on_roster([Member(id="1", name="Autopilot", role=Role.AGENT.value)])

    shown = table._roster.item(0).text()
    assert "player" not in shown
    assert ROLE_LABELS[Role.AGENT.value] in shown


def test_it_is_listed_as_the_dm_it_stands_in_for(table):
    from canon_keeper_protocol import Member, Role

    table._on_roster([Member(id="1", name="Autopilot", role=Role.AGENT.value)])

    assert "DM" in table._roster.item(0).text()


def test_but_you_can_still_tell_which_one_you_are_talking_to(table):
    """A table deserves to know when it is being answered by a machine."""
    from canon_keeper.panels.table.widget import ROLE_LABELS
    from canon_keeper_protocol import Role

    assert ROLE_LABELS[Role.AGENT.value] != ROLE_LABELS[Role.DM.value]


def test_a_player_is_still_a_player(table):
    from canon_keeper_protocol import Member, Role

    table._on_roster([Member(id="2", name="Marco", role=Role.PLAYER.value)])
    assert "player" in table._roster.item(0).text()


def test_every_role_has_a_label():
    """The bug was a role with no label falling into the wrong one."""
    from canon_keeper.panels.table.widget import ROLE_LABELS
    from canon_keeper_protocol import Role

    for role in Role:
        assert role.value in ROLE_LABELS, f"{role} would be mislabelled"


# --------------------------------------------------------- the DM's own buttons


def test_the_dm_is_not_offered_join(ctx, qtbot):
    """Joining someone else's session from inside your own campaign is not a
    thing you would ever want."""
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)

    assert widget._join_button.isVisible() is False


def test_a_player_still_is(ctx, qtbot):
    ctx.role = "player"
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    widget._update_state()

    assert widget._join_button.isVisibleTo(widget) is True


def test_there_is_no_separate_share_button(ctx, qtbot):
    """Going online and being reachable are the same wish."""
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)

    assert not hasattr(widget, "_funnel_button")
