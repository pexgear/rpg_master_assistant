"""Characters, as a player sees them.

A separate widget from the DM's rather than the same one with fields hidden.
Hiding is a presentation trick, and presentation tricks leak: this one cannot
show a secret because it is never given one -- what arrives from the host has
already been filtered.

The player's own character is editable; everyone else's is read-only.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QTabWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from canon_keeper.panels.characters.sheet_tab import SheetWidget
from canon_keeper.plugin import AppContext
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC

_ID_ROLE = 256


class PlayerCharactersWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._current_id: int | None = None
        self._loading = False

        self._build_ui()
        ctx.shared.changed.connect(self._on_shared_changed)
        # A refusal has to override whatever is on screen, including a form the
        # player is still editing: the host's copy is the true one, and leaving
        # a rejected change visible reads exactly like it was accepted.
        ctx.bus.edit_refused.connect(self._on_refused)
        self.reload()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._list, 1)
        splitter.addWidget(left)

        self._form_container = QWidget()
        form = QFormLayout(self._form_container)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._name = QLabel()
        self._name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Name", self._name)

        self._status = QLineEdit()
        form.addRow("Status", self._status)

        hp_row = QHBoxLayout()
        self._hp = QSpinBox()
        self._hp.setRange(-99, 999)
        hp_row.addWidget(self._hp)
        hp_row.addWidget(QLabel("of"))
        self._max_hp = QSpinBox()
        self._max_hp.setRange(0, 999)
        hp_row.addWidget(self._max_hp)
        hp_row.addStretch(1)
        form.addRow("Hit points", hp_row)

        self._conditions = QLineEdit()
        self._conditions.setPlaceholderText("prone, poisoned...")
        form.addRow("Conditions", self._conditions)

        self._summary = QLineEdit()
        form.addRow("One-liner", self._summary)

        self._known = QPlainTextEdit()
        self._known.setReadOnly(True)
        self._known.setMinimumHeight(70)
        form.addRow("What you know", self._known)

        self._inventory = QPlainTextEdit()
        self._inventory.setMinimumHeight(60)
        form.addRow("Inventory", self._inventory)

        self._notes = QPlainTextEdit()
        self._notes.setMinimumHeight(60)
        self._notes.setPlaceholderText("Only you see these.")
        form.addRow("Your notes", self._notes)

        self._save = QPushButton("Save")
        self._save.clicked.connect(self._save_own)
        form.addRow("", self._save)

        for widget in (
            self._status,
            self._conditions,
            self._summary,
            self._inventory,
            self._notes,
        ):
            signal = (
                widget.textChanged
                if isinstance(widget, QPlainTextEdit)
                else widget.textEdited
            )
            signal.connect(self._mark_dirty)
        self._hp.valueChanged.connect(self._mark_dirty)
        self._max_hp.valueChanged.connect(self._mark_dirty)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form_container)

        self._tabs = QTabWidget()
        self._tabs.addTab(scroll, "Story")

        # The same sheet widget the DM uses, fed from what the host sent rather
        # than from a campaign this machine does not have.
        self._sheet_tab = SheetWidget(self._ctx)
        self._tabs.addTab(self._sheet_tab, "Sheet")

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._hint = QLabel("Join a session to see the party.")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        right_layout.addWidget(self._hint)
        right_layout.addWidget(self._tabs, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)
        self._show_form(False)

    def _show_form(self, visible: bool) -> None:
        self._form_container.setVisible(visible)
        self._tabs.setVisible(visible)
        self._hint.setVisible(not visible)

    def _mark_dirty(self, *_args) -> None:
        if not self._loading:
            self._save.setEnabled(True)

    # ------------------------------------------------------------------ loads

    def reload(self) -> None:
        previous = self._current_id
        people = self._ctx.shared.of_kind(KIND_PC, KIND_NPC)

        self._loading = True
        self._list.clear()
        for entity in people:
            label = entity.get("name", "?")
            if entity.get("own"):
                label += "   (you)"
            elif entity.get("kind") == KIND_PC:
                label += "   (party)"
            item = QListWidgetItem(label)
            item.setData(_ID_ROLE, entity["id"])
            self._list.addItem(item)
            if entity["id"] == previous:
                self._list.setCurrentItem(item)
        self._loading = False

        if not people:
            self._current_id = None
            self._show_form(False)
            self._hint.setText(
                "Nothing yet. Join a session, and characters appear here as the "
                "DM shares them."
            )
        elif self._list.currentItem() is None:
            self._list.setCurrentRow(0)
        else:
            # Rebuilding the list leaves the same row selected, so Qt emits no
            # selection change and the detail pane keeps showing the old copy.
            # An update that arrives for the character already on screen is
            # exactly the case that matters, so re-read it explicitly.
            self._refresh_shown()

    def _on_shared_changed(self) -> None:
        """The host sent something. Reload, and say so if nobody is looking."""
        self.reload()
        self._ctx.bus.panel_attention.emit("characters")

    def _on_refused(self, entity_id: int, reason: str) -> None:
        """The DM said no. Put back what is actually true."""
        if entity_id != self._current_id:
            # Not on screen, but their next visit must not show the old form.
            return
        # Cleared first: _refresh_shown deliberately skips a dirty form, and
        # this is the one case where the form loses.
        self._save.setEnabled(False)
        entity = self._ctx.shared.get(entity_id)
        if entity is not None:
            self._load(entity)
        self._ctx.bus.status_message.emit(
            f"Your DM said no{f': {reason}' if reason else ''}"
        )
        self._ctx.bus.panel_attention.emit("characters")

    def _refresh_shown(self) -> None:
        """Re-read the character on screen, unless it is being edited."""
        if self._current_id is None or self._save.isEnabled():
            return
        entity = self._ctx.shared.get(self._current_id)
        if entity is not None:
            self._load(entity)

    def _on_selection_changed(self, current, _previous) -> None:
        if self._loading or current is None:
            return
        entity = self._ctx.shared.get(current.data(_ID_ROLE))
        if entity is None:
            return
        self._load(entity)

    def _load(self, entity: dict) -> None:
        self._loading = True
        self._current_id = entity["id"]
        own = bool(entity.get("own"))
        data = entity.get("data", {})

        self._name.setText(entity.get("name", ""))
        self._status.setText(str(data.get("status", "")))
        self._hp.setValue(int(data.get("hp", 0) or 0))
        self._max_hp.setValue(int(data.get("max_hp", 0) or 0))
        self._conditions.setText(str(data.get("conditions", "")))
        self._summary.setText(entity.get("summary", ""))
        self._known.setPlainText(
            str(data.get("party_knows", "")) or str(data.get("shared_notes", ""))
        )
        self._inventory.setPlainText(str(data.get("inventory", "")))
        self._notes.setPlainText(str(data.get("player_notes", "")))

        # Only your own sheet is yours to change.
        for widget in (
            self._status,
            self._hp,
            self._max_hp,
            self._conditions,
            self._summary,
            self._inventory,
            self._notes,
        ):
            widget.setEnabled(own)
        self._inventory.setVisible(own)
        self._notes.setVisible(own)
        self._save.setVisible(own)
        self._save.setEnabled(False)

        self._sheet_tab.set_received(entity, on_commit=self._commit_sheet)

        self._loading = False
        self._show_form(True)

    # --------------------------------------------------------------- actions

    def _commit_sheet(self, entity_id: int, sheet: dict) -> None:
        """Ask the host to store a sheet change. It decides, then echoes back.

        No version is sent: the host checks against the copy it gave us.
        """
        self._ctx.bus.player_edit_requested.emit(
            entity_id, {"data": {"sheet": sheet}}
        )

    def _save_own(self) -> None:
        entity = self._ctx.shared.get(self._current_id) if self._current_id else None
        if entity is None or not entity.get("own"):
            return
        changes = {
            "summary": self._summary.text().strip(),
            "data": {
                "status": self._status.text().strip(),
                "hp": self._hp.value(),
                "max_hp": self._max_hp.value(),
                "conditions": self._conditions.text().strip(),
                "inventory": self._inventory.toPlainText().strip(),
                "player_notes": self._notes.toPlainText().strip(),
            },
        }
        # The host applies it and echoes the result back, so what we display is
        # always what was actually accepted.
        self._ctx.bus.player_edit_requested.emit(self._current_id, changes)
        self._save.setEnabled(False)
