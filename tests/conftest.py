from __future__ import annotations

import logging
import os

# Qt must be told to run headless before any QApplication is constructed, which
# pytest-qt does on import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from canon_keeper.bus import Bus
from canon_keeper.naming import PanelNames
from canon_keeper.net.state import SharedState
from canon_keeper_core.db import connect, migrate
from canon_keeper.plugin import AppContext
from canon_keeper_core.repo import Repos


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """Nothing in the suite may wait for somebody to click a button.

    This is not tidiness. A ``QMessageBox.information`` on a path a test walked
    blocked the whole run until GitHub killed it at its six-hour ceiling --
    reported as *cancelled*, naming nothing, and hiding every release from
    0.3.1 onwards for a month. It only happened on CI, because the dialog was
    behind "the ``anthropic`` package is missing" and a developer's machine has
    it installed.

    So the static dialogs answer themselves here, and a test that cares what
    was answered patches them again on top -- its patch wins, being later.
    """
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    )
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    )
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    )
    # No by default: a question nobody answered is not a yes.
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False))
    )
    monkeypatch.setattr(
        QInputDialog, "getInt", staticmethod(lambda *a, **k: (0, False))
    )


@pytest.fixture
def conn(tmp_path):
    """A migrated, empty campaign database in a temporary directory."""
    connection = connect(tmp_path / "campaign.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def repos(conn) -> Repos:
    return Repos(conn)


@pytest.fixture
def ctx(repos) -> AppContext:
    campaign = repos.campaigns.ensure_default("Test Campaign")
    # Assembled the way app.build_context does, so a test is not exercising a
    # context shape the application never produces.
    return AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
        shared=SharedState(),
        names=PanelNames(repos.settings, is_dm=True),
    )
