"""Layout persistence — the feature that breaks silently when it breaks.

Qt drops docks with no objectName when restoring state, and the symptom the user
sees is "my layout keeps resetting itself". These tests pin the round-trip.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from canon_keeper.plugin import API_VERSION
from canon_keeper_core.repo.layouts import AUTOSAVE_NAME
from canon_keeper.shell.loader import LoadedPanel
from canon_keeper.shell.main_window import MainWindow

log = logging.getLogger("canonkeeper.test")


class _Panel:
    api_version = API_VERSION

    def __init__(self, panel_id: str, area: Qt.DockWidgetArea):
        self.id = panel_id
        self.title = panel_id.title()
        self._area = area

    def create_widget(self, ctx):
        return QLabel(self.id)

    def default_area(self):
        return self._area


def _panels() -> list[LoadedPanel]:
    return [
        LoadedPanel(
            _Panel("alpha", Qt.DockWidgetArea.LeftDockWidgetArea), "alpha", "tests:Alpha"
        ),
        LoadedPanel(
            _Panel("beta", Qt.DockWidgetArea.RightDockWidgetArea), "beta", "tests:Beta"
        ),
    ]


def _window(ctx, qtbot, panels=None) -> MainWindow:
    window = MainWindow(ctx, panels if panels is not None else _panels(), [], log)
    qtbot.addWidget(window)
    return window


def test_docks_are_named_after_their_panel(ctx, qtbot):
    window = _window(ctx, qtbot)
    assert {d.objectName() for d in window._docks.values()} == {"alpha", "beta"}


def test_floating_state_survives_a_round_trip(ctx, qtbot):
    window = _window(ctx, qtbot)
    window._docks["alpha"].setFloating(True)
    window._save_layout("At the table")

    window._apply_default_arrangement()
    assert not window._docks["alpha"].isFloating()

    assert window._apply_layout("At the table")
    assert window._docks["alpha"].isFloating()


def test_hidden_panel_survives_a_round_trip(ctx, qtbot):
    window = _window(ctx, qtbot)
    window._docks["beta"].hide()
    window._save_layout("Prep")

    window._show_all_panels()
    assert not window._docks["beta"].isHidden()

    window._apply_layout("Prep")
    assert window._docks["beta"].isHidden()


def test_layout_restores_when_a_panel_is_missing(ctx, qtbot):
    """An uninstalled plugin costs you its dock, not the whole arrangement."""
    window = _window(ctx, qtbot)
    window._docks["alpha"].setFloating(True)
    window._save_layout("Both panels")
    window.close()

    # Reopen with 'beta' alone, as if the alpha plugin had been uninstalled.
    survivor = [p for p in _panels() if p.plugin.id == "beta"]
    reopened = _window(ctx, qtbot, panels=survivor)
    assert set(reopened._docks) == {"beta"}
    assert reopened._apply_layout("Both panels")
    assert not reopened._docks["beta"].isHidden()


def test_default_layout_is_applied_on_startup(ctx, qtbot):
    window = _window(ctx, qtbot)
    window._docks["alpha"].setFloating(True)
    window._save_layout("Combat", is_default=True)
    window.close()

    reopened = _window(ctx, qtbot)
    assert reopened._docks["alpha"].isFloating()


def test_closing_autosaves_the_arrangement(ctx, qtbot):
    window = _window(ctx, qtbot)
    window._docks["beta"].setFloating(True)
    window.close()

    saved = ctx.repos.layouts.get(AUTOSAVE_NAME)
    assert saved is not None

    reopened = _window(ctx, qtbot)
    assert reopened._docks["beta"].isFloating()


def test_autosave_is_hidden_from_the_layouts_menu(ctx, qtbot):
    window = _window(ctx, qtbot)
    window.close()
    assert ctx.repos.layouts.get(AUTOSAVE_NAME) is not None
    assert AUTOSAVE_NAME not in {layout.name for layout in ctx.repos.layouts.list()}


def test_only_one_layout_is_default(ctx, qtbot):
    window = _window(ctx, qtbot)
    window._save_layout("Prep", is_default=True)
    window._save_layout("Combat", is_default=True)

    defaults = [layout.name for layout in ctx.repos.layouts.list() if layout.is_default]
    assert defaults == ["Combat"]
