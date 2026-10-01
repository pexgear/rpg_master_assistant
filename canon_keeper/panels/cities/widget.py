"""Place hierarchy: region > city > district > building.

Nesting is just ``entity.parent_id``, which is the same column a character uses
to say where they are standing. That is why the occupants list below comes for
free: it is every non-location entity parented to the selected place.
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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from canon_keeper.panels.sharing import ShareBar
from canon_keeper.plugin import AppContext
from canon_keeper_core.repo.entities import KIND_LOCATION, Entity

PLACE_TYPES = ("region", "city", "town", "village", "district", "building", "room", "wilds")


class CitiesWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._current_id: int | None = None
        self._loading = False

        self._build_ui()

        ctx.bus.campaign_changed.connect(self._on_campaign_changed)
        ctx.bus.entity_changed.connect(self._on_entity_changed_elsewhere)
        ctx.bus.entity_deleted.connect(lambda _id: self.reload())

        self.reload()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # --- left: the tree ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._tree, 1)

        buttons = QHBoxLayout()
        btn_root = QPushButton("New place")
        btn_root.setToolTip("Add a top-level region or city")
        btn_root.clicked.connect(lambda: self._new_place(under_current=False))
        buttons.addWidget(btn_root)

        btn_child = QPushButton("New inside")
        btn_child.setToolTip("Add a district, building or room inside the selected place")
        btn_child.clicked.connect(lambda: self._new_place(under_current=True))
        buttons.addWidget(btn_child)

        btn_delete = QPushButton("Delete")
        btn_delete.clicked.connect(self._delete_place)
        buttons.addWidget(btn_delete)
        left_layout.addLayout(buttons)

        splitter.addWidget(left)

        # --- right: detail ---
        self._form_container = QWidget()
        form = QFormLayout(self._form_container)
        form.setContentsMargins(8, 8, 8, 8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._name = QLineEdit()
        self._name.textEdited.connect(self._mark_dirty)
        form.addRow("Name", self._name)

        self._place_type = QComboBox()
        self._place_type.setEditable(True)
        self._place_type.addItems(PLACE_TYPES)
        self._place_type.currentTextChanged.connect(self._mark_dirty)
        form.addRow("Type", self._place_type)

        self._summary = QLineEdit()
        self._summary.setPlaceholderText("One line of read-aloud flavour")
        self._summary.textEdited.connect(self._mark_dirty)
        form.addRow("One-liner", self._summary)

        self._notes = QPlainTextEdit()
        self._notes.setMinimumHeight(90)
        self._notes.textChanged.connect(self._mark_dirty)
        form.addRow("Notes", self._notes)

        self._shared_notes = QPlainTextEdit()
        self._shared_notes.setMinimumHeight(60)
        self._shared_notes.setPlaceholderText(
            "What the party may read about this place. Everything else here stays yours."
        )
        self._shared_notes.textChanged.connect(self._mark_dirty)
        form.addRow("Players read", self._shared_notes)

        self._share = ShareBar(self._ctx)
        form.addRow("Players see", self._share)

        self._rumours = QPlainTextEdit()
        self._rumours.setMinimumHeight(60)
        self._rumours.textChanged.connect(self._mark_dirty)
        form.addRow("Rumours & hooks", self._rumours)

        self._occupants = QListWidget()
        self._occupants.setMaximumHeight(140)
        self._occupants.itemDoubleClicked.connect(self._focus_occupant)
        form.addRow("Who is here", self._occupants)

        self._save_button = QPushButton("Save")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self.save_current)
        form.addRow("", self._save_button)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form_container)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._empty_hint = QLabel("Select a place, or press New place.")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self._empty_hint)
        right_layout.addWidget(scroll, 1)

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
        self._empty_hint.setVisible(not enabled)

    def _mark_dirty(self, *_args) -> None:
        if not self._loading:
            self._save_button.setEnabled(True)

    @property
    def _dirty(self) -> bool:
        return self._save_button.isEnabled()

    # ------------------------------------------------------------------ loads

    def reload(self, keep_selection: bool = True) -> None:
        previous = self._current_id if keep_selection else None
        places = self._ctx.repos.entities.list(self._ctx.campaign_id, kinds=(KIND_LOCATION,))

        by_parent: dict[int | None, list[Entity]] = {}
        known_ids = {p.id for p in places}
        for place in places:
            # A place whose parent is not itself a place (or is gone) is a root.
            parent = place.parent_id if place.parent_id in known_ids else None
            by_parent.setdefault(parent, []).append(place)

        self._loading = True
        self._tree.clear()
        selected_item: QTreeWidgetItem | None = None

        def add_children(parent_item: QTreeWidgetItem | None, parent_id: int | None) -> None:
            nonlocal selected_item
            for place in by_parent.get(parent_id, []):
                label = place.name
                place_type = place.data.get("place_type", "")
                if place_type:
                    label += f"   [{place_type}]"
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, place.id)
                if parent_item is None:
                    self._tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                if place.id == previous:
                    selected_item = item
                add_children(item, place.id)

        add_children(None, None)
        self._tree.expandAll()
        self._loading = False

        if selected_item is not None:
            self._tree.setCurrentItem(selected_item)
            self._refresh_occupants(self._current_id)
        else:
            self._current_id = None
            self._set_form_enabled(False)

    def _load_place(self, place: Entity) -> None:
        self._loading = True
        self._current_id = place.id
        self._name.setText(place.name)
        self._place_type.setCurrentText(place.data.get("place_type", "city"))
        self._summary.setText(place.summary)
        self._notes.setPlainText(place.data.get("notes", ""))
        self._rumours.setPlainText(place.data.get("rumours", ""))
        self._shared_notes.setPlainText(place.data.get("shared_notes", ""))
        self._share.set_entity(place.id)
        self._loading = False

        self._refresh_occupants(place.id)
        self._save_button.setEnabled(False)
        self._set_form_enabled(True)

    def _refresh_occupants(self, place_id: int | None) -> None:
        self._occupants.clear()
        if place_id is None:
            return
        for occupant in self._ctx.repos.entities.occupants(place_id):
            status = occupant.data.get("status", "")
            label = occupant.name + (f"  -  {status}" if status else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, occupant.id)
            self._occupants.addItem(item)

    # ----------------------------------------------------------------- events

    def _on_selection_changed(
        self, current: QTreeWidgetItem, previous: QTreeWidgetItem
    ) -> None:
        if self._loading:
            return
        if previous is not None and self._dirty:
            self.save_current(announce=False)

        if current is None:
            self._current_id = None
            self._set_form_enabled(False)
            return

        place_id = current.data(0, Qt.ItemDataRole.UserRole)
        place = self._ctx.repos.entities.get(place_id)
        if place is None:
            self.reload(keep_selection=False)
            return
        self._load_place(place)
        self._ctx.bus.active_location_changed.emit(place_id)

    def _on_campaign_changed(self, _campaign_id: int) -> None:
        self._current_id = None
        self._set_form_enabled(False)
        self.reload(keep_selection=False)

    def _on_entity_changed_elsewhere(self, entity_id: int) -> None:
        # A character may have moved into or out of the selected place.
        self._refresh_occupants(self._current_id)
        if self._dirty:
            return
        self.reload(keep_selection=True)

        if entity_id == self._current_id:
            place = self._ctx.repos.entities.get(entity_id)
            if place is not None:
                self._load_place(place)

    def _focus_occupant(self, item: QListWidgetItem) -> None:
        """Double-clicking someone in the room hands them to the Characters panel."""
        entity_id = item.data(Qt.ItemDataRole.UserRole)
        if entity_id is not None:
            self._ctx.bus.active_entity_changed.emit(entity_id)

    # ---------------------------------------------------------------- actions

    def _new_place(self, under_current: bool) -> None:
        if self._dirty:
            self.save_current(announce=False)
        parent_id = self._current_id if under_current else None
        if under_current and parent_id is None:
            QMessageBox.information(
                self, "New place", "Select the place this one sits inside first."
            )
            return

        place = self._ctx.repos.entities.create(
            Entity(
                id=None,
                campaign_id=self._ctx.campaign_id,
                kind=KIND_LOCATION,
                name="New place",
                parent_id=parent_id,
                data={"place_type": "district" if under_current else "city"},
            )
        )
        self.reload(keep_selection=False)
        self._select_place(place.id)
        self._name.selectAll()
        self._name.setFocus()
        self._ctx.bus.entity_changed.emit(place.id)

    def _delete_place(self) -> None:
        if self._current_id is None:
            return
        place = self._ctx.repos.entities.get(self._current_id)
        if place is None:
            return
        confirm = QMessageBox.question(
            self,
            "Delete place",
            f"Delete {place.name}? Places inside it are deleted too; "
            "characters standing there are only unparented.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        deleted_id = self._current_id
        self._ctx.repos.entities.delete(deleted_id)
        self._current_id = None
        self._save_button.setEnabled(False)
        self.reload(keep_selection=False)
        self._ctx.bus.entity_deleted.emit(deleted_id)
        self._ctx.bus.status_message.emit(f"Deleted {place.name}")

    def save_current(self, announce: bool = True) -> None:
        if self._current_id is None:
            return
        place = self._ctx.repos.entities.get(self._current_id)
        if place is None:
            return

        place.name = self._name.text().strip() or "Unnamed place"
        place.summary = self._summary.text().strip()
        place.data["place_type"] = self._place_type.currentText().strip()
        place.data["notes"] = self._notes.toPlainText().strip()
        place.data["rumours"] = self._rumours.toPlainText().strip()
        place.data["shared_notes"] = self._shared_notes.toPlainText().strip()

        self._ctx.repos.entities.update(place)
        self._save_button.setEnabled(False)
        self.reload(keep_selection=True)
        self._ctx.bus.entity_changed.emit(place.id)
        if announce:
            self._ctx.bus.status_message.emit(f"Saved {place.name}")

    def _select_place(self, place_id: int | None) -> None:
        iterator = self._tree.findItems(
            "", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive, 0
        )
        for item in iterator:
            if item.data(0, Qt.ItemDataRole.UserRole) == place_id:
                self._tree.setCurrentItem(item)
                return
