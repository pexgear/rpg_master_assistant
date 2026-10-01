"""Character list and detail form.

Deliberately narrative-first: who this person is, what they want, what they are
hiding, and what the party has actually worked out. Stat blocks live in your
books. Everything beyond the four identity columns is persisted in
``entity.data``, so adding a field later costs a widget and no migration.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from canon_keeper import entity_actions
from canon_keeper.panels.characters.sheet_tab import SheetWidget
from canon_keeper.panels.characters.wizard import CharacterWizard
from canon_keeper.panels.sharing import ShareBar
from canon_keeper.plugin import AppContext
from canon_keeper_core.repo.entities import KIND_LOCATION, KIND_NPC, KIND_PC, Entity
from canon_keeper_core.rules import derive
from canon_keeper_core.rules.sheet import sheet_of

CHARACTER_KINDS = (KIND_NPC, KIND_PC)
STATUSES = ("alive", "dead", "unknown", "captured", "missing")

# data_json keys owned by this panel.
_TEXT_FIELDS = {
    "voice": "Voice & mannerism",
    "motive": "What they want",
    "secrets": "Secrets (DM only)",
    "party_knows": "What the party knows",
}


class CharactersWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._current_id: int | None = None
        self._loading = False  # guards the edit signals while we populate the form

        self._build_ui()

        ctx.bus.campaign_changed.connect(self._on_campaign_changed)
        ctx.bus.entity_changed.connect(self._on_entity_changed_elsewhere)

        self.reload()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # --- left: search + list + new/delete ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search characters...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self.reload(keep_selection=True))
        left_layout.addWidget(self._search)

        self._kind_filter = QComboBox()
        self._kind_filter.addItem("All characters", None)
        self._kind_filter.addItem("NPCs", KIND_NPC)
        self._kind_filter.addItem("Player characters", KIND_PC)
        self._kind_filter.currentIndexChanged.connect(lambda _i: self.reload())
        left_layout.addWidget(self._kind_filter)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        # Right-clicking somebody here offers what is true of them anywhere --
        # inviting a player to a character, most of all. This panel adds
        # nothing of its own yet, so the menu is only the general half.
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_menu)
        left_layout.addWidget(self._list, 1)

        buttons = QHBoxLayout()
        btn_new = QPushButton("New")
        btn_new.setToolTip("A blank entry to write notes against")
        btn_new.clicked.connect(self._new_character)
        buttons.addWidget(btn_new)

        btn_build = QPushButton("Build...")
        btn_build.setToolTip("Create a character step by step, with a full sheet")
        btn_build.clicked.connect(self._build_character)
        buttons.addWidget(btn_build)
        btn_delete = QPushButton("Delete")
        btn_delete.clicked.connect(self._delete_character)
        buttons.addWidget(btn_delete)
        left_layout.addLayout(buttons)

        splitter.addWidget(left)

        # --- right: detail form ---
        self._form_container = QWidget()
        form = QFormLayout(self._form_container)
        form.setContentsMargins(8, 8, 8, 8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._name = QLineEdit()
        self._name.textEdited.connect(self._mark_dirty)
        form.addRow("Name", self._name)

        self._kind = QComboBox()
        for kind in CHARACTER_KINDS:
            self._kind.addItem("NPC" if kind == KIND_NPC else "Player character", kind)
        self._kind.currentIndexChanged.connect(self._mark_dirty)
        form.addRow("Kind", self._kind)

        self._status = QComboBox()
        self._status.setEditable(True)
        self._status.addItems(STATUSES)
        self._status.currentTextChanged.connect(self._mark_dirty)
        form.addRow("Status", self._status)

        self._location = QComboBox()
        self._location.currentIndexChanged.connect(self._mark_dirty)
        form.addRow("Location", self._location)

        self._summary = QLineEdit()
        self._summary.setPlaceholderText("One line you could say out loud at the table")
        self._summary.textEdited.connect(self._mark_dirty)
        form.addRow("One-liner", self._summary)

        self._texts: dict[str, QPlainTextEdit] = {}
        for key, label in _TEXT_FIELDS.items():
            edit = QPlainTextEdit()
            edit.setMinimumHeight(60)
            edit.textChanged.connect(self._mark_dirty)
            self._texts[key] = edit
            form.addRow(label, edit)

        self._owner = QComboBox()
        self._owner.setToolTip(
            "The player who owns this character. They see the whole sheet and "
            "keep their own hit points; you still confirm changes of level."
        )
        self._owner.currentIndexChanged.connect(self._mark_dirty)
        form.addRow("Played by", self._owner)

        self._share = ShareBar(self._ctx)
        form.addRow("Players see", self._share)

        self._save_button = QPushButton("Save")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self.save_current)
        form.addRow("", self._save_button)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form_container)

        # Story and Sheet are the same character seen two ways: who they are,
        # and what they can do. Tabs rather than one long form, because a full
        # sheet would bury the narrative fields the panel exists for.
        self._tabs = QTabWidget()
        self._tabs.addTab(scroll, "Story")

        self._sheet_tab = SheetWidget(self._ctx)
        self._sheet_tab.saved.connect(lambda _id: self.reload(keep_selection=True))
        self._tabs.addTab(self._sheet_tab, "Sheet")

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._empty_hint = QLabel("Select a character, or press New.")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self._empty_hint)
        right_layout.addWidget(self._tabs, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self._set_form_enabled(False)

    # ------------------------------------------------------------------ state

    def _set_form_enabled(self, enabled: bool) -> None:
        self._form_container.setEnabled(enabled)
        self._tabs.setVisible(enabled)
        self._empty_hint.setVisible(not enabled)

    def _mark_dirty(self, *_args) -> None:
        if not self._loading:
            self._save_button.setEnabled(True)

    @property
    def _dirty(self) -> bool:
        return self._save_button.isEnabled()

    # ------------------------------------------------------------------ loads

    def reload(self, keep_selection: bool = True) -> None:
        """Refill the list from the database, preserving the selection if asked."""
        previous = self._current_id if keep_selection else None
        kind = self._kind_filter.currentData()
        kinds = (kind,) if kind else CHARACTER_KINDS

        entities = self._ctx.repos.entities.list(
            self._ctx.campaign_id, kinds=kinds, search=self._search.text().strip()
        )

        self._loading = True
        self._list.clear()
        for entity in entities:
            label = entity.name
            described = self._describe_sheet(entity)
            if described:
                label += f"   {described}"
            elif entity.kind == KIND_PC:
                label += "  (PC)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entity.id)
            self._list.addItem(item)
            if entity.id == previous:
                self._list.setCurrentItem(item)
        self._loading = False

        self._reload_locations()
        self._reload_owners()

        if self._list.currentItem() is None:
            self._current_id = None
            self._share.set_entity(None)
            self._sheet_tab.set_entity(None)
            self._set_form_enabled(False)

    def _describe_sheet(self, entity) -> str:
        """'Level 5 Elf Wizard', for characters that have a sheet."""
        sheet = sheet_of(entity.data)
        if sheet is None:
            return ""
        return derive.describe(sheet, self._sheet_tab._content)

    def _reload_owners(self) -> None:
        """Who could own a character: the campaign's player logins."""
        current = self._owner.currentData()
        self._loading = True
        self._owner.clear()
        self._owner.addItem("(nobody — it is yours)", None)
        for account in self._ctx.repos.accounts.players(self._ctx.campaign_id):
            label = account.display_name or account.username
            self._owner.addItem(label, account.id)
        index = self._owner.findData(current)
        self._owner.setCurrentIndex(index if index >= 0 else 0)
        self._loading = False

    def _reload_locations(self) -> None:
        current = self._location.currentData()
        self._loading = True
        self._location.clear()
        self._location.addItem("(nowhere in particular)", None)
        for location in self._ctx.repos.entities.list(
            self._ctx.campaign_id, kinds=(KIND_LOCATION,)
        ):
            self._location.addItem(location.name, location.id)
        index = self._location.findData(current)
        self._location.setCurrentIndex(index if index >= 0 else 0)
        self._loading = False

    def _load_entity(self, entity: Entity) -> None:
        self._loading = True
        self._current_id = entity.id
        self._name.setText(entity.name)

        kind_index = self._kind.findData(entity.kind)
        self._kind.setCurrentIndex(kind_index if kind_index >= 0 else 0)

        self._status.setCurrentText(entity.data.get("status", "alive"))

        location_index = self._location.findData(entity.parent_id)
        self._location.setCurrentIndex(location_index if location_index >= 0 else 0)

        self._summary.setText(entity.summary)
        for key, edit in self._texts.items():
            edit.setPlainText(entity.data.get(key, ""))

        owner_index = self._owner.findData(entity.owner_account_id)
        self._owner.setCurrentIndex(owner_index if owner_index >= 0 else 0)

        self._share.set_entity(entity.id)
        self._sheet_tab.set_entity(entity)

        self._loading = False
        self._save_button.setEnabled(False)
        self._set_form_enabled(True)

    # ----------------------------------------------------------------- events

    def _on_menu(self, position) -> None:
        """What this creature offers, wherever it is shown.

        Everything in here is general -- see :mod:`canon_keeper.entity_actions`.
        Anything specific to this panel would go in first, above a separator,
        because that is the reason somebody right-clicked *here*.
        """
        item = self._list.itemAt(position)
        if item is None:
            return
        entity_id = item.data(Qt.ItemDataRole.UserRole)
        entity = self._ctx.repos.entities.get(entity_id) if entity_id else None
        if entity is None:
            return

        menu = QMenu(self)
        entity_actions.fill(
            menu,
            self._ctx,
            entity_actions.Target(
                entity_id=entity.id,
                kind=entity.kind,
                name=entity.name,
                panel="characters",
            ),
        )
        if not menu.isEmpty():
            menu.exec(self._list.mapToGlobal(position))

    def _on_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        if self._loading:
            return
        # Save without asking. A DM mid-session should never lose a note to a
        # dialog they did not expect.
        if previous is not None and self._dirty:
            self.save_current(announce=False)

        if current is None:
            self._current_id = None
            self._set_form_enabled(False)
            return

        entity_id = current.data(Qt.ItemDataRole.UserRole)
        entity = self._ctx.repos.entities.get(entity_id)
        if entity is None:
            self.reload(keep_selection=False)
            return
        self._load_entity(entity)
        self._ctx.bus.active_entity_changed.emit(entity_id)

    def _on_campaign_changed(self, _campaign_id: int) -> None:
        self._current_id = None
        self._set_form_enabled(False)
        self.reload(keep_selection=False)

    def _on_entity_changed_elsewhere(self, entity_id: int) -> None:
        # Another panel, or a player, touched an entity. Refresh the list, but
        # never clobber a form the DM is currently typing into.
        if self._dirty and entity_id == self._current_id:
            return
        self.reload(keep_selection=True)

        # The list rebuild leaves the same row selected, so nothing re-reads the
        # detail. Without this a player's hit point change never appears.
        if entity_id == self._current_id and not self._dirty:
            entity = self._ctx.repos.entities.get(entity_id)
            if entity is not None:
                self._load_entity(entity)

    # ---------------------------------------------------------------- actions

    def _new_character(self) -> None:
        if self._dirty:
            self.save_current(announce=False)
        entity = self._ctx.repos.entities.create(
            Entity(
                id=None,
                campaign_id=self._ctx.campaign_id,
                kind=KIND_NPC,
                name="New character",
                data={"status": "alive"},
            )
        )
        self.reload(keep_selection=False)
        self._select_entity(entity.id)
        self._name.selectAll()
        self._name.setFocus()
        self._ctx.bus.entity_changed.emit(entity.id)

    def _build_character(self) -> None:
        """Guided creation. The wizard fills in the same sheet the tab edits."""
        if self._dirty:
            self.save_current(announce=False)

        wizard = CharacterWizard(self._ctx, self)
        if not wizard.exec():
            return

        name, sheet = wizard.finished_sheet()
        entity = self._ctx.repos.entities.create(
            Entity(
                id=None,
                campaign_id=self._ctx.campaign_id,
                kind=KIND_PC,
                name=name,
                data={"status": "alive", "sheet": sheet},
            )
        )
        self.reload(keep_selection=False)
        self._select_entity(entity.id)
        self._tabs.setCurrentIndex(1)
        self._ctx.bus.entity_changed.emit(entity.id)
        self._ctx.bus.status_message.emit(f"Created {name}")

    def _delete_character(self) -> None:
        if self._current_id is None:
            return
        entity = self._ctx.repos.entities.get(self._current_id)
        if entity is None:
            return
        confirm = QMessageBox.question(
            self,
            "Delete character",
            f"Delete {entity.name}? This cannot be undone.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        deleted_id = self._current_id
        self._ctx.repos.entities.delete(deleted_id)
        self._current_id = None
        self._save_button.setEnabled(False)
        self.reload(keep_selection=False)
        self._ctx.bus.entity_deleted.emit(deleted_id)
        self._ctx.bus.status_message.emit(f"Deleted {entity.name}")

    def save_current(self, announce: bool = True) -> None:
        if self._current_id is None:
            return
        entity = self._ctx.repos.entities.get(self._current_id)
        if entity is None:
            return

        entity.name = self._name.text().strip() or "Unnamed"
        entity.kind = self._kind.currentData()
        entity.parent_id = self._location.currentData()
        entity.summary = self._summary.text().strip()
        entity.owner_account_id = self._owner.currentData()
        entity.data["status"] = self._status.currentText().strip() or "unknown"
        for key, edit in self._texts.items():
            entity.data[key] = edit.toPlainText().strip()

        self._ctx.repos.entities.update(entity)
        self._save_button.setEnabled(False)
        self.reload(keep_selection=True)
        self._ctx.bus.entity_changed.emit(entity.id)
        if announce:
            self._ctx.bus.status_message.emit(f"Saved {entity.name}")

    def _select_entity(self, entity_id: int | None) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == entity_id:
                self._list.setCurrentItem(item)
                return
