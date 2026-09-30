"""Settings, with one page in it.

A **Keyboard** page, because the keyboard is the one part of this app whose state
nobody can see. Every other setting is where you set it -- the theme is in View,
the agent's key is in the Table panel, a panel's name is where you rename it --
and each of those is findable by looking for the thing it affects. A key is not:
"why does Ctrl+N do nothing in here" is answered by knowing what *else* claimed
Ctrl+N, and there is nowhere that says.

So this page says. Every key, who owns it, how far it reaches, and what it does,
with the clashes first because a clash is the reason anybody opened it.

Deliberately one page. The scattered settings could move in here and probably
should one day, but moving them is a change to three panels for a reason that has
nothing to do with keyboards, and a dialog with one honest page is better than a
dialog with one honest page and three half-moved ones.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from canon_keeper.plugin import REACH_EVERYWHERE, REACH_PANEL, REACH_WINDOW
from canon_keeper.shell import keys

#: Plain words rather than the constants, because the reader is not a programmer.
_REACH_SAID = {
    REACH_PANEL: "this panel only",
    REACH_WINDOW: "the whole window",
    REACH_EVERYWHERE: "even other apps",
}


class KeyboardPage(QWidget):
    """What every key does, and which ones will not do it."""

    def __init__(self, claims, clashes, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)

        blurb = QLabel(
            "A key belongs to the panel you are looking at, so two panels may "
            "use the same one. Only a key that reaches past its own panel can "
            "take another's away."
        )
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._trouble = QLabel("")
        self._trouble.setWordWrap(True)
        self._trouble.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self._trouble)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Key", "Panel", "Reaches", "Does"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        outer.addWidget(self._table, 1)

        self.show_keys(claims, clashes)

    def show_keys(self, claims, clashes) -> None:
        """Fill it in. Separate from building it so it can be refilled."""
        if clashes:
            how_many = len(clashes)
            self._trouble.setText(
                f"{how_many} key{'s' if how_many > 1 else ''} will not do what "
                "the menu says:\n\n"
                + "\n".join(f"  •  {clash}" for clash in clashes)
            )
        else:
            self._trouble.setText("No clashes. Every key does what it says.")

        # Clashing keys first: it is the reason anybody opened this page, and a
        # sorted-by-key list would bury them among the twenty that are fine.
        # Compared in the one spelling. A clash names the *normalised* key --
        # "ctrl+z" -- while a claim carries whatever the panel wrote, so matching
        # them raw quietly finds nothing and the clashing rows sort in among the
        # ones that are fine.
        in_trouble = {keys.normalise(clash.key) for clash in clashes}
        ordered = sorted(
            claims,
            key=lambda c: (
                keys.normalise(c.key) not in in_trouble,
                c.owner.lower(),
                c.key.lower(),
            ),
        )

        self._table.setRowCount(len(ordered))
        for row, claim in enumerate(ordered):
            for column, text in enumerate(
                (
                    claim.key,
                    claim.owner,
                    _REACH_SAID.get(claim.reach, claim.reach),
                    claim.what,
                )
            ):
                cell = QTableWidgetItem(text)
                if keys.normalise(claim.key) in in_trouble:
                    font = cell.font()
                    font.setBold(True)
                    cell.setFont(font)
                self._table.setItem(row, column, cell)
        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )


class SettingsDialog(QDialog):
    """The window's own settings. One page for now; see the module docstring."""

    def __init__(self, claims, clashes, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(640, 420)

        outer = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self.keyboard = KeyboardPage(claims, clashes)
        self._tabs.addTab(self.keyboard, "Keyboard")
        outer.addWidget(self._tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)
