"""The two questions the Combat panel has to ask: what fight, and who is in it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
)

from canon_keeper_core.repo.encounters import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    MAX_SIZE,
    MIN_SIZE,
)


class FightDialog(QDialog):
    """Name a fight, or change the size of its grid.

    Reached deliberately, never in the way: starting a fight asks nothing, on
    the grounds that "what is it called and how big is the room" are questions
    you can answer once there is something on screen to answer them about --
    and usually will not bother to.
    """

    def __init__(self, parent=None, name: str = "", width: int = DEFAULT_WIDTH,
                 height: int = DEFAULT_HEIGHT) -> None:
        super().__init__(parent)
        self.setWindowTitle("This fight")

        self._name = QLineEdit(name)
        self._name.setPlaceholderText("The cave mouth")
        self._width = QSpinBox()
        self._width.setRange(MIN_SIZE, MAX_SIZE)
        self._width.setValue(width)
        self._height = QSpinBox()
        self._height.setRange(MIN_SIZE, MAX_SIZE)
        self._height.setValue(height)

        form = QFormLayout(self)
        form.addRow("Called", self._name)
        form.addRow("Squares across", self._width)
        form.addRow("Squares down", self._height)

        note = QLabel(
            "Five feet to the square. Growing the grid is free; anyone left "
            "outside a smaller one is taken off the map, not deleted."
        )
        note.setWordWrap(True)
        form.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, int, int]:
        return self._name.text().strip(), self._width.value(), self._height.value()


class AttackDialog(QDialog):
    """Who is being hit, and with what.

    Two questions and no more. Advantage, cover and the rest are the DM's to
    rule on out loud -- putting them here would mean the app deciding half of
    them, and a table then checking every result to find out which half.
    """

    def __init__(self, attacker: str, targets: list[tuple[int, str]],
                 weapons: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{attacker} attacks")

        self._targets = QComboBox()
        for combatant_id, name in targets:
            self._targets.addItem(name, combatant_id)

        self._weapons = QComboBox()
        self._weapons.setEditable(True)
        for name in weapons:
            self._weapons.addItem(name)
        if not weapons:
            self._weapons.setPlaceholderText("nothing on their sheet")

        form = QFormLayout(self)
        form.addRow("Attacks", self._targets)
        form.addRow("With", self._weapons)

        note = QLabel(
            "The host rolls it: a d20 and their bonus against the target's "
            "armour class, then damage off the weapon. Melee reaches one "
            "square."
        )
        note.setWordWrap(True)
        form.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def ask(self) -> tuple[int | None, str]:
        """``(target id, weapon)``, or ``(None, "")`` if they thought better."""
        if not self.exec():
            return None, ""
        return self._targets.currentData(), self._weapons.currentText().strip()


class AddCombatantsDialog(QDialog):
    """Pick who joins the fight.

    Only characters and NPCs, and only ones not already in it. A location has
    no initiative, and a creature in the order twice is a mistake nobody means
    to make.
    """

    def __init__(self, candidates: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Into the fight")
        self.resize(360, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Who is in this fight?"))

        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        for entity in candidates:
            item = QListWidgetItem(f"{entity.name}  --  {entity.kind}")
            item.setData(Qt.ItemDataRole.UserRole, entity.id)
            self._list.addItem(item)
        # Double-click is the impatient way to add one, and matches the
        # campaign chooser.
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self._list, 1)

        if not candidates:
            layout.addWidget(
                QLabel("Everyone in this campaign is already in the fight.")
            )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self) -> list[int]:
        return [
            item.data(Qt.ItemDataRole.UserRole) for item in self._list.selectedItems()
        ]
