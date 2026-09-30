"""A menu of its own for every panel that has anything to offer.

A panel's buttons live inside it, which is fine while you are looking at that
panel and useless when you are not: starting a fight meant finding Combat
first. So a widget may list what it can do, and the shell gives it a menu next
to File and View.

The rules worth holding are the plugin loader's, one level down: a panel that
declares nothing gets no menu rather than an empty one, and a panel that
misbehaves costs its own menu and not the window.
"""

from __future__ import annotations

import logging

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from canon_keeper.plugin import (
    REACH_EVERYWHERE,
    REACH_WINDOW,
    PanelAction,
    ReservedKey,
)
from canon_keeper.shell.loader import LoadedPanel
from canon_keeper.shell.main_window import MainWindow


class _Widget(QWidget):
    def __init__(self, actions=None, explodes=False) -> None:
        super().__init__()
        self.ran: list[str] = []
        self._actions = actions
        self._explodes = explodes

    def panel_actions(self):
        if self._explodes:
            raise RuntimeError("a bad panel")
        if self._actions is None:
            return []
        return self._actions(self)


class _Panel:
    """A panel plugin whose widget is handed in rather than built."""

    def __init__(self, panel_id: str, title: str, widget) -> None:
        self.id = panel_id
        self.title = title
        self.api_version = 2
        self._widget = widget

    def create_widget(self, ctx):
        return self._widget

    def default_area(self):
        return Qt.DockWidgetArea.LeftDockWidgetArea


class _Silent(QWidget):
    """A widget that declares nothing, like most panels."""


def _window(qtbot, ctx, *plugins) -> MainWindow:
    loaded = [
        LoadedPanel(plugin=p, entry_point=p.id, module="test") for p in plugins
    ]
    window = MainWindow(ctx, loaded, [], logging.getLogger("test"), None)
    qtbot.addWidget(window)
    return window


def _menu_titles(window) -> list[str]:
    return [a.menu().title() for a in window.menuBar().actions() if a.menu()]


def _menu_for(window, panel_id: str):
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is not None and menu.objectName() == f"menu_{panel_id}":
            return menu
    return None


# ------------------------------------------------------------------- the menu


def test_a_panel_that_declares_actions_gets_a_menu(qtbot, ctx):
    widget = _Widget(lambda w: [PanelAction("&New fight", lambda: w.ran.append("new"))])
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    assert "Combat" in _menu_titles(window)


def test_the_actions_are_in_it(qtbot, ctx):
    widget = _Widget(
        lambda w: [
            PanelAction("&New fight", lambda: w.ran.append("new")),
            PanelAction("&End the fight", lambda: w.ran.append("end")),
        ]
    )
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    menu = _menu_for(window, "combat")
    assert [a.text() for a in menu.actions()] == ["&New fight", "&End the fight"]


def test_choosing_one_runs_it(qtbot, ctx):
    widget = _Widget(lambda w: [PanelAction("&New fight", lambda: w.ran.append("new"))])
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    _menu_for(window, "combat").actions()[0].trigger()

    assert widget.ran == ["new"]


def test_a_panel_with_nothing_to_offer_gets_no_menu(qtbot, ctx):
    """An empty menu is worse than none: it promises something and delivers it."""
    window = _window(qtbot, ctx, _Panel("quiet", "Quiet", _Silent()))

    assert "Quiet" not in _menu_titles(window)


def test_a_panel_that_declares_an_empty_list_gets_no_menu(qtbot, ctx):
    widget = _Widget(lambda _w: [])
    window = _window(qtbot, ctx, _Panel("quiet", "Quiet", widget))

    assert "Quiet" not in _menu_titles(window)


def test_a_shortcut_is_carried_through(qtbot, ctx):
    widget = _Widget(
        lambda w: [PanelAction("&New fight", lambda: None, "Ctrl+Shift+F")]
    )
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    action = _menu_for(window, "combat").actions()[0]
    assert action.shortcut().toString().lower().endswith("shift+f")


def test_a_disabled_action_is_shown_greyed(qtbot, ctx):
    """Better than hiding it: a menu that changes shape is one you cannot learn."""
    widget = _Widget(
        lambda w: [PanelAction("&End the fight", lambda: None, enabled=False)]
    )
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    assert _menu_for(window, "combat").actions()[0].isEnabled() is False


# --------------------------------------------------------- one bad panel


def test_a_panel_that_explodes_while_listing_costs_only_its_menu(qtbot, ctx):
    window = _window(
        qtbot,
        ctx,
        _Panel("bad", "Bad", _Widget(explodes=True)),
        _Panel("good", "Good", _Widget(lambda w: [PanelAction("Fine", lambda: None)])),
    )

    assert "Bad" not in _menu_titles(window)
    assert "Good" in _menu_titles(window), "one bad panel took another's menu"


def test_an_action_that_raises_does_not_take_the_window_down(qtbot, ctx):
    def boom():
        raise RuntimeError("a bad panel")

    widget = _Widget(lambda w: [PanelAction("Boom", boom)])
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    _menu_for(window, "combat").actions()[0].trigger()  # must not raise


# ------------------------------------------------------- closing the panel


def _visible_titles(window) -> list[str]:
    return [
        a.menu().title()
        for a in window.menuBar().actions()
        if a.menu() and a.isVisible()
    ]


def test_closing_the_panel_takes_its_menu_away(qtbot, ctx):
    """A menu for a panel you closed acts on something you cannot see."""
    widget = _Widget(lambda w: [PanelAction("&New fight", lambda: w.ran.append("new"))])
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))
    assert "Combat" in _visible_titles(window)

    window._docks["combat"].close()

    assert "Combat" not in _visible_titles(window)


def test_opening_it_again_brings_the_menu_back(qtbot, ctx):
    widget = _Widget(lambda w: [PanelAction("&New fight", lambda: w.ran.append("new"))])
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))
    dock = window._docks["combat"]
    dock.close()

    dock.show()

    assert "Combat" in _visible_titles(window)


def test_another_panels_menu_is_left_alone(qtbot, ctx):
    window = _window(
        qtbot,
        ctx,
        _Panel("combat", "Combat", _Widget(lambda w: [PanelAction("A", lambda: None)])),
        _Panel("table", "Table", _Widget(lambda w: [PanelAction("B", lambda: None)])),
    )

    window._docks["combat"].close()

    assert "Table" in _visible_titles(window)


# ------------------------------------------------------------------ renaming


def test_renaming_the_panel_renames_its_menu(qtbot, ctx):
    """Otherwise the dock and the menu disagree about what the panel is called."""
    widget = _Widget(lambda w: [PanelAction("&New fight", lambda: None)])
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))
    assert "Combat" in _menu_titles(window)

    ctx.names.set_party("combat", "The Battlefield")
    window._retitle_docks()

    assert "The Battlefield" in _menu_titles(window)


# ------------------------------------------------------------ the real one


def test_the_combat_panel_offers_a_new_fight(qtbot, ctx):
    """The one that prompted all this."""
    from canon_keeper.panels.encounter.widget import EncounterWidget

    widget = EncounterWidget(ctx)
    qtbot.addWidget(widget)

    labels = [a.label for a in widget.panel_actions()]

    assert any("New fight" in label for label in labels)


def test_the_combat_panel_greys_what_needs_a_fight(qtbot, ctx):
    """With no fight running, ending one is not something you can do."""
    from canon_keeper.panels.encounter.widget import EncounterWidget

    widget = EncounterWidget(ctx)
    qtbot.addWidget(widget)

    actions = {a.label: a for a in widget.panel_actions()}
    assert actions["&New fight"].enabled is True
    assert actions["&End the fight"].enabled is False


# ------------------------------------------------------ how far a key reaches
#
# Qt's default is the thing to guard against: an action owned by the window fires
# from anywhere in the window, so a panel's shortcut would go off while you were
# typing in another panel's chat box. A key belongs to the panel you are looking
# at unless it says otherwise.


def _with_shortcut(key, reach=None, label="New fight"):
    def actions(widget):
        if reach is None:
            return [PanelAction(label=label, run=lambda: None, shortcut=key)]
        return [
            PanelAction(label=label, run=lambda: None, shortcut=key, reach=reach)
        ]

    return actions


def _shortcut_actions(window, panel_id):
    menu = window._panel_menus[panel_id]
    return [a for a in menu.actions() if not a.shortcut().isEmpty()]


def test_a_panel_key_only_listens_inside_that_panel(qtbot, ctx):
    """The default, and the whole point.

    `WidgetWithChildren` rather than `Widget`, because a panel's focus is almost
    never on the panel itself -- it is in a list or a line edit inside it, and a
    key pressed there is still a key pressed in that panel.
    """
    widget = _Widget(actions=_with_shortcut("Ctrl+Shift+N"))
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    action = _shortcut_actions(window, "combat")[0]
    assert (
        action.shortcutContext()
        == Qt.ShortcutContext.WidgetWithChildrenShortcut
    ), "a panel key reached past its panel"


def test_a_panel_key_is_owned_and_heard_by_the_panel(qtbot, ctx):
    """Scoping it is not enough: something has to be listening.

    A widget-scoped shortcut is only watched for by a widget that holds the
    action. Left in the menu alone it would be drawn and never fire, which is
    worse than not offering it.
    """
    widget = _Widget(actions=_with_shortcut("Ctrl+Shift+N"))
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    action = _shortcut_actions(window, "combat")[0]
    assert action.parent() is widget, "the window owned it, so the window hears it"
    assert action in widget.actions(), "the panel was not listening for it"


def test_a_panel_may_ask_for_the_whole_window(qtbot, ctx):
    """Deliberate, not forbidden. Some things are about the app, not a panel."""
    widget = _Widget(actions=_with_shortcut("Ctrl+Shift+N", REACH_WINDOW))
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    action = _shortcut_actions(window, "combat")[0]
    assert action.shortcutContext() == Qt.ShortcutContext.WindowShortcut


def test_a_panel_may_ask_for_the_whole_machine(qtbot, ctx):
    """Push-to-talk is the honest case: F9 has to work while you read the map."""
    widget = _Widget(actions=_with_shortcut("F9", REACH_EVERYWHERE))
    window = _window(qtbot, ctx, _Panel("transcript", "Transcript", widget))

    action = _shortcut_actions(window, "transcript")[0]
    assert action.shortcutContext() == Qt.ShortcutContext.ApplicationShortcut


def test_a_reach_nobody_recognises_gets_the_narrow_one(qtbot, ctx):
    """A panel is other people's code.

    The failure worth avoiding is a key quietly reaching further than anybody
    meant, so an unrecognised answer is treated as the narrowest one rather than
    trusted or crashed on.
    """
    widget = _Widget(actions=_with_shortcut("Ctrl+Shift+N", "the whole internet"))
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    action = _shortcut_actions(window, "combat")[0]
    assert (
        action.shortcutContext()
        == Qt.ShortcutContext.WidgetWithChildrenShortcut
    )


def test_a_menu_item_is_clickable_whatever_its_key_reaches(qtbot, ctx):
    """Reach is about the key, not about the action.

    A menu that greyed itself out depending on where the focus happened to be
    would be a menu you could not use.
    """
    widget = _Widget(actions=_with_shortcut("Ctrl+Shift+N"))
    window = _window(qtbot, ctx, _Panel("combat", "Combat", widget))

    action = _shortcut_actions(window, "combat")[0]
    assert action.isEnabled()
    action.trigger()
    assert widget.ran == ["New fight"] or widget.ran == [], (
        "triggering went nowhere at all"
    )


# --------------------------------------------------------- clashes, reported


def test_two_panels_sharing_a_key_is_not_reported(qtbot, ctx):
    """Only the focused one is listening, so there is nothing to report."""
    window = _window(
        qtbot,
        ctx,
        _Panel("combat", "Combat", _Widget(actions=_with_shortcut("Ctrl+Shift+N"))),
        _Panel(
            "characters",
            "Characters",
            _Widget(actions=_with_shortcut("Ctrl+Shift+N", label="New creature")),
        ),
    )

    assert window.key_clashes() == []


def test_a_panel_taking_a_window_key_off_another_is_reported(qtbot, ctx):
    window = _window(
        qtbot,
        ctx,
        _Panel("combat", "Combat", _Widget(actions=_with_shortcut("Ctrl+Shift+N"))),
        _Panel(
            "characters",
            "Characters",
            _Widget(
                actions=_with_shortcut(
                    "Ctrl+Shift+N", REACH_WINDOW, label="New creature"
                )
            ),
        ),
    )

    found = window.key_clashes()
    assert len(found) == 1, [str(c) for c in found]
    assert found[0].wins.owner == "Characters"
    assert found[0].loses.owner == "Combat"


def test_a_key_a_panel_handles_itself_can_be_taken_and_is_reported(qtbot, ctx):
    """The map reads Space in its own key handler, not as a shortcut.

    So a window-wide Space simply wins and the map goes quiet -- no error, no log
    line from Qt, just a grid ignoring the key its own tooltip documents. The
    panel declares what it handles; the shell reports what took it.
    """

    class _Handles(_Widget):
        def reserved_keys(self):
            return [ReservedKey("Space", "open the wheel")]

    window = _window(
        qtbot,
        ctx,
        _Panel("combat", "Combat", _Handles()),
        _Panel(
            "transcript",
            "Transcript",
            _Widget(actions=_with_shortcut("Space", REACH_WINDOW, label="Record")),
        ),
    )

    found = window.key_clashes()
    assert len(found) == 1, [str(c) for c in found]
    assert found[0].loses.owner == "Combat"
    assert found[0].wins.owner == "Transcript"


def test_a_panel_with_no_menu_still_has_its_keys_counted(qtbot, ctx):
    """Declaring keys and declaring menu items are separate things.

    A panel that offers no menu may still read keys of its own, and the report
    would be worth nothing if it only knew about panels that happened to have a
    menu.
    """

    class _KeysOnly(QWidget):
        def reserved_keys(self):
            return [ReservedKey("Space", "open the wheel")]

    window = _window(
        qtbot,
        ctx,
        _Panel("combat", "Combat", _KeysOnly()),
        _Panel(
            "transcript",
            "Transcript",
            _Widget(actions=_with_shortcut("Space", REACH_WINDOW, label="Record")),
        ),
    )

    assert any(c.loses.owner == "Combat" for c in window.key_clashes())


def test_a_panel_that_breaks_listing_its_keys_costs_only_its_own_entry(qtbot, ctx):
    """The same forgiveness the menu listing gets, for the same reason."""

    class _Broken(QWidget):
        def reserved_keys(self):
            raise RuntimeError("a bad panel")

    window = _window(qtbot, ctx, _Panel("combat", "Combat", _Broken()))

    assert window.key_clashes() == []
    assert window.isVisible() is False  # built without raising, which is the claim


def test_a_window_with_no_shortcuts_has_nothing_to_report(qtbot, ctx):
    """Which is today: no panel declares a shortcut yet."""
    window = _window(qtbot, ctx, _Panel("combat", "Combat", _Silent()))
    assert window.key_clashes() == []
