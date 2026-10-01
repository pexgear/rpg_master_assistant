"""The character sheet, as a tab beside the story.

Everything derived is recomputed as you type, so the sheet argues with you
immediately rather than after saving: raise constitution and the hit points move
while you watch. That is the whole benefit of deriving rather than storing, made
visible.

What is editable depends on who you are. The DM edits anything. A player edits
their own characters, and the *build* half of those -- level, class, ability
scores -- is proposed rather than applied, because those are changes to what the
character is rather than what is happening to it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from canon_keeper_core.content import Content
from canon_keeper_core.rules import derive
from canon_keeper_core.rules.sheet import ABILITIES, ABILITY_NAMES, new_sheet, sheet_of
from canon_keeper_core.rules.validation import validate

_NONE = "(none)"


class SheetWidget(QWidget):
    """One character's mechanical sheet."""

    #: The sheet was saved; the panel reloads and the host republishes.
    saved = Signal(int)  # entity id

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._content = Content(ctx.repos.settings)
        self._entity = None
        self._entity_id: int | None = None
        self._sheet: dict | None = None
        self._loading = False
        #: False for a character that is not yours: you may look, not touch.
        self._editable = True
        #: False for a player. Level, class and ability scores describe what the
        #: character *is*, and changing that is the DM's to confirm.
        self._build_editable = True
        #: How to save. The DM writes to the campaign; a player asks the host.
        self._commit = None
        #: The maximum as of the last refresh, so a character sitting at full
        #: health follows the maximum up when constitution or level changes
        #: instead of being stranded on the old number.
        self._last_max_hp: int | None = None
        #: Which class the spell picker was filled for, so it is refilled when
        #: the class changes and not on every keystroke.
        self._picker_class: str | None = None

        self._build_ui()
        self.set_entity(None)

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._empty = QLabel(
            "No character sheet yet.\n\nPress Create sheet to give this character "
            "one, or leave it as a purely narrative entry."
        )
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        self._create_button = QPushButton("Create sheet")
        self._create_button.clicked.connect(self._create_sheet)
        outer.addWidget(self._create_button, alignment=Qt.AlignmentFlag.AlignCenter)

        self._body = QWidget()
        body = QVBoxLayout(self._body)
        body.setContentsMargins(6, 6, 6, 6)

        body.addWidget(self._build_identity())
        body.addWidget(self._build_abilities())
        body.addWidget(self._build_derived())
        body.addWidget(self._build_saves())
        body.addWidget(self._build_skills())
        body.addWidget(self._build_equipment())
        self._spell_box = self._build_spellcasting()
        body.addWidget(self._spell_box)
        self._spellbook = self._build_spellbook()
        body.addWidget(self._spellbook)
        body.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._body)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self._save_button = QPushButton("Save sheet")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self.save)
        buttons.addWidget(self._save_button)

        self._problems = QLabel("")
        self._problems.setWordWrap(True)
        buttons.addWidget(self._problems, 1)
        outer.addLayout(buttons)

    def _build_identity(self) -> QWidget:
        box = QGroupBox("Who they are")
        form = QFormLayout(box)

        self._species = QComboBox()
        self._species.currentIndexChanged.connect(self._on_species_changed)
        form.addRow("Species", self._species)

        self._subspecies = QComboBox()
        self._subspecies.currentIndexChanged.connect(self._changed)
        form.addRow("Subspecies", self._subspecies)

        self._class = QComboBox()
        self._class.currentIndexChanged.connect(self._on_class_changed)
        form.addRow("Class", self._class)

        self._subclass = QComboBox()
        self._subclass.currentIndexChanged.connect(self._changed)
        form.addRow("Subclass", self._subclass)

        self._level = QSpinBox()
        self._level.setRange(1, 20)
        self._level.valueChanged.connect(self._on_level_changed)
        form.addRow("Level", self._level)

        self._background = QComboBox()
        self._background.currentIndexChanged.connect(self._changed)
        form.addRow("Background", self._background)
        return box

    def _build_abilities(self) -> QWidget:
        box = QGroupBox("Ability scores")
        grid = QGridLayout(box)
        self._ability_inputs: dict[str, QSpinBox] = {}
        self._ability_totals: dict[str, QLabel] = {}

        for column, ability in enumerate(ABILITIES):
            grid.addWidget(
                QLabel(ABILITY_NAMES[ability][:3].upper()), 0, column,
                alignment=Qt.AlignmentFlag.AlignCenter,
            )

            spin = QSpinBox()
            spin.setRange(1, 30)
            spin.setToolTip(f"{ABILITY_NAMES[ability]} before species and improvements")
            spin.valueChanged.connect(self._changed)
            grid.addWidget(spin, 1, column)
            self._ability_inputs[ability] = spin

            total = QLabel("10 (+0)")
            total.setAlignment(Qt.AlignmentFlag.AlignCenter)
            total.setToolTip("With species bonuses and improvements applied")
            grid.addWidget(total, 2, column)
            self._ability_totals[ability] = total
        return box

    def _build_derived(self) -> QWidget:
        box = QGroupBox("In play")
        grid = QGridLayout(box)

        self._hp_current = QSpinBox()
        self._hp_current.setRange(-99, 999)
        self._hp_current.valueChanged.connect(self._changed)
        grid.addWidget(QLabel("Hit points"), 0, 0)
        grid.addWidget(self._hp_current, 0, 1)
        self._hp_max = QLabel("of 0")
        grid.addWidget(self._hp_max, 0, 2)

        self._derived_labels: dict[str, QLabel] = {}
        for column, (key, caption) in enumerate(
            (
                ("ac", "Armour class"),
                ("initiative", "Initiative"),
                ("speed", "Speed"),
                ("proficiency_bonus", "Proficiency"),
                ("passive_perception", "Passive perception"),
            )
        ):
            grid.addWidget(QLabel(caption), 1, column)
            value = QLabel("-")
            value.setAlignment(Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(value, 2, column)
            self._derived_labels[key] = value
        return box

    def _build_saves(self) -> QWidget:
        box = QGroupBox("Saving throws")
        grid = QGridLayout(box)
        self._save_labels: dict[str, QLabel] = {}
        for column, ability in enumerate(ABILITIES):
            grid.addWidget(
                QLabel(ABILITY_NAMES[ability][:3].upper()), 0, column,
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
            value = QLabel("+0")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(value, 1, column)
            self._save_labels[ability] = value
        return box

    def _build_skills(self) -> QWidget:
        box = QGroupBox("Skills")
        grid = QGridLayout(box)
        self._skill_checks: dict[str, QCheckBox] = {}
        self._skill_labels: dict[str, QLabel] = {}

        skills = sorted(self._content.skills(), key=lambda s: s.get("name", ""))
        half = (len(skills) + 1) // 2
        for position, skill in enumerate(skills):
            index = skill["index"]
            row, column = (position % half), (position // half) * 2

            check = QCheckBox(skill.get("name", index))
            check.setToolTip(
                (skill.get("ability_score") or {}).get("name", "")
            )
            check.toggled.connect(self._changed)
            grid.addWidget(check, row, column)
            self._skill_checks[index] = check

            value = QLabel("+0")
            grid.addWidget(value, row, column + 1)
            self._skill_labels[index] = value
        return box

    def _build_equipment(self) -> QWidget:
        box = QGroupBox("Equipment")
        layout = QVBoxLayout(box)

        self._equipment = QListWidget()
        self._equipment.setMaximumHeight(140)
        self._equipment.setToolTip(
            "Worn armour and a held shield change the armour class above."
        )
        layout.addWidget(self._equipment)

        row = QHBoxLayout()
        self._equipment_picker = QComboBox()
        self._equipment_picker.setEditable(True)
        self._equipment_picker.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._equipment_picker.setToolTip("Type to search")
        row.addWidget(self._equipment_picker, 1)

        add = QPushButton("Add")
        add.clicked.connect(self._add_equipment)
        row.addWidget(add)

        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_equipment)
        row.addWidget(remove)
        layout.addLayout(row)
        return box

    def _build_spellbook(self) -> QWidget:
        box = QGroupBox("Spells")
        layout = QVBoxLayout(box)

        self._spell_list = QListWidget()
        self._spell_list.setMaximumHeight(180)
        self._spell_list.itemChanged.connect(self._on_prepared_changed)
        self._spell_list.setToolTip(
            "Ticked spells are prepared. Untick to keep it known but not ready."
        )
        layout.addWidget(self._spell_list)

        row = QHBoxLayout()
        self._spell_picker = QComboBox()
        self._spell_picker.setEditable(True)
        self._spell_picker.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        row.addWidget(self._spell_picker, 1)

        learn = QPushButton("Learn")
        learn.clicked.connect(self._add_spell)
        row.addWidget(learn)

        forget = QPushButton("Forget")
        forget.clicked.connect(self._remove_spell)
        row.addWidget(forget)
        layout.addLayout(row)
        return box

    def _build_spellcasting(self) -> QWidget:
        box = QGroupBox("Spellcasting")
        layout = QVBoxLayout(box)
        self._spell_summary = QLabel("-")
        layout.addWidget(self._spell_summary)
        self._slots = QLabel("-")
        self._slots.setWordWrap(True)
        layout.addWidget(self._slots)
        return box

    # ------------------------------------------------------------------ state

    def set_entity(self, entity) -> None:
        """Show one character from the campaign. The DM's path."""
        self._entity = entity
        self._entity_id = entity.id if entity is not None else None
        self._commit = None
        self._editable = True
        self._build_editable = True
        self._show(sheet_of(entity.data) if entity is not None else None,
                   present=entity is not None)

    def set_received(self, received: dict | None, on_commit=None) -> None:
        """Show a character that arrived over the wire. A player's path.

        ``received`` is the projected dict, not an Entity: a player never holds
        the campaign, only what the host chose to send them.
        """
        self._entity = None
        self._entity_id = received.get("id") if received else None
        self._commit = on_commit
        owned = bool(received and received.get("own"))
        self._editable = owned
        # Even on your own character, the build half waits for the DM.
        self._build_editable = False
        sheet = (received or {}).get("data", {}).get("sheet")
        self._show(sheet if isinstance(sheet, dict) and "abilities" in sheet else None,
                   present=received is not None)

    def _show(self, sheet: dict | None, present: bool) -> None:
        self._sheet = sheet

        has_entity = present
        has_sheet = self._sheet is not None

        self._empty.setVisible(has_entity and not has_sheet)
        self._create_button.setVisible(has_entity and not has_sheet)
        self._body.parentWidget().parentWidget().setVisible(has_sheet)
        self._save_button.setVisible(has_sheet)

        if not has_entity:
            self._empty.setText("Select a character.")
            self._empty.setVisible(True)
        elif not has_sheet:
            self._empty.setText(
                "No character sheet yet.\n\nPress Create sheet to give this "
                "character one, or leave it as a purely narrative entry."
            )

        # Only the DM can conjure a sheet from nothing; a player's arrives.
        self._create_button.setVisible(
            has_entity and not has_sheet and self._entity is not None
        )
        if not has_entity:
            self._empty.setText("Select a character.")
        elif not has_sheet and self._entity is None:
            self._empty.setText("This character has no sheet yet.")

        if has_sheet:
            self._populate_choices()
            self._load()
            self._apply_permissions()

    def _apply_permissions(self) -> None:
        """Grey out what this person may not change, and say why."""
        build_widgets = (
            self._species,
            self._subspecies,
            self._class,
            self._subclass,
            self._level,
            self._background,
            *self._ability_inputs.values(),
            *self._skill_checks.values(),
        )
        # A player may fill in anything; none of it applies until the DM says
        # so, which is what the button and the note below explain.
        for widget in build_widgets:
            widget.setEnabled(self._editable)
            if self._editable and not self._build_editable:
                widget.setToolTip("Your DM has to agree to this.")

        # Equipment and prepared spells are state, not build: a player picks
        # up a torch and prepares different spells without asking anyone.
        for widget in (self._hp_current, self._equipment, self._spell_list):
            widget.setEnabled(self._editable)

        self._save_button.setVisible(self._editable)
        if self._editable and not self._build_editable:
            self._save_button.setText("Ask my DM")
            self._problems.setText(
                "Changes to your character are sent to your DM, who decides."
            )
        elif not self._editable:
            self._problems.setText("Someone else's character.")

    def _create_sheet(self) -> None:
        if self._entity is None:
            return
        # Re-read first: the copy we are holding may predate an edit made on the
        # Story tab, and writing it back would quietly undo that edit.
        entity = self._ctx.repos.entities.get(self._entity.id)
        if entity is None:
            return
        entity.data["sheet"] = new_sheet()
        self._ctx.repos.entities.update(entity)
        self._ctx.bus.entity_changed.emit(entity.id)
        self.set_entity(self._ctx.repos.entities.get(entity.id))

    # ------------------------------------------------------------------ filling

    def _populate_choices(self) -> None:
        self._loading = True
        for combo, entries in (
            (self._species, self._content.species()),
            (self._class, self._content.classes()),
            (self._background, self._content.backgrounds()),
        ):
            combo.clear()
            combo.addItem(_NONE, "")
            for entry in entries:
                combo.addItem(entry.get("name", entry["index"]), entry["index"])
        self._loading = False

    def _populate_subspecies(self, species: str, chosen: str = "") -> None:
        self._subspecies.clear()
        self._subspecies.addItem(_NONE, "")
        for entry in self._content.subspecies_of(species):
            self._subspecies.addItem(entry.get("name", entry["index"]), entry["index"])
        self._select(self._subspecies, chosen)

    def _populate_subclasses(self, class_index: str, chosen: str = "") -> None:
        self._subclass.clear()
        self._subclass.addItem(_NONE, "")
        for entry in self._content.subclasses_of(class_index):
            self._subclass.addItem(entry.get("name", entry["index"]), entry["index"])
        self._select(self._subclass, chosen)

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value or "")
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _load(self) -> None:
        sheet = self._sheet or {}
        self._loading = True

        self._select(self._species, sheet.get("species", ""))
        self._populate_subspecies(sheet.get("species", ""), sheet.get("subspecies", ""))
        self._select(self._class, sheet.get("class_index", ""))
        self._populate_subclasses(sheet.get("class_index", ""), sheet.get("subclass", ""))
        self._select(self._background, sheet.get("background", ""))
        self._level.setValue(int(sheet.get("level", 1)))

        base = sheet.get("abilities") or {}
        for ability, spin in self._ability_inputs.items():
            spin.setValue(int(base.get(ability, 10)))

        proficient = set(sheet.get("skill_proficiencies") or ())
        for index, check in self._skill_checks.items():
            check.setChecked(index in proficient)

        current, maximum = derive.hit_points(sheet, self._content)
        self._hp_current.setValue(current)
        self._last_max_hp = maximum

        self._load_equipment(sheet)
        self._load_spells(sheet)

        self._loading = False
        self._refresh()
        self._save_button.setEnabled(False)

    def _load_equipment(self, sheet: dict) -> None:
        self._equipment.clear()
        for item in sheet.get("equipment") or ():
            index = item.get("index") if isinstance(item, dict) else str(item)
            quantity = int(item.get("qty", 1)) if isinstance(item, dict) else 1
            entry = self._content.get("equipment", index) or {}
            name = entry.get("name", index)
            listed = QListWidgetItem(f"{name}  ×{quantity}" if quantity > 1 else name)
            listed.setData(Qt.ItemDataRole.UserRole, index)
            self._equipment.addItem(listed)

        if self._equipment_picker.count() == 0:
            for entry in self._content.equipment():
                self._equipment_picker.addItem(
                    entry.get("name", entry["index"]), entry["index"]
                )

    def _load_spells(self, sheet: dict) -> None:
        was_loading = self._loading
        self._loading = True

        prepared = set(sheet.get("spells_prepared") or ())
        self._spell_list.clear()
        for index in sheet.get("spells_known") or ():
            entry = self._content.get("spells", index) or {}
            level = entry.get("level")
            caption = entry.get("name", index)
            if level == 0:
                caption += "   (cantrip)"
            elif level:
                caption += f"   (level {level})"
            listed = QListWidgetItem(caption)
            listed.setData(Qt.ItemDataRole.UserRole, index)
            listed.setFlags(listed.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # Cantrips are always available, so ticking one would be a lie.
            listed.setCheckState(
                Qt.CheckState.Checked
                if index in prepared or level == 0
                else Qt.CheckState.Unchecked
            )
            self._spell_list.addItem(listed)

        self._refill_spell_picker(sheet)
        self._picker_class = sheet.get("class_index")
        self._loading = was_loading

    def _refill_spell_picker(self, sheet: dict) -> None:
        """Offer this class's list, minus what they already know."""
        known = set(sheet.get("spells_known") or ())
        self._spell_picker.clear()
        class_index = sheet.get("class_index", "")
        if not class_index:
            return
        for entry in self._content.spells_for(class_index):
            if entry["index"] in known:
                continue
            level = entry.get("level", 0)
            label = f"{entry.get('name', entry['index'])}"
            label += "  (cantrip)" if level == 0 else f"  (level {level})"
            self._spell_picker.addItem(label, entry["index"])

    # -------------------------------------------------------- gear and spells

    def _add_equipment(self) -> None:
        index = self._equipment_picker.currentData()
        if not index or self._sheet is None:
            return
        items = list(self._sheet.get("equipment") or [])
        for item in items:
            if isinstance(item, dict) and item.get("index") == index:
                item["qty"] = int(item.get("qty", 1)) + 1
                break
        else:
            items.append({"index": index, "qty": 1})
        self._sheet["equipment"] = items
        self._load_equipment(self._sheet)
        self._changed()

    def _remove_equipment(self) -> None:
        item = self._equipment.currentItem()
        if item is None or self._sheet is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        self._sheet["equipment"] = [
            entry
            for entry in (self._sheet.get("equipment") or [])
            if (entry.get("index") if isinstance(entry, dict) else entry) != index
        ]
        self._load_equipment(self._sheet)
        self._changed()

    def _add_spell(self) -> None:
        index = self._spell_picker.currentData()
        if not index or self._sheet is None:
            return
        known = list(self._sheet.get("spells_known") or [])
        if index in known:
            return
        known.append(index)
        self._sheet["spells_known"] = known
        self._load_spells(self._sheet)
        self._changed()

    def _remove_spell(self) -> None:
        item = self._spell_list.currentItem()
        if item is None or self._sheet is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        self._sheet["spells_known"] = [
            spell for spell in (self._sheet.get("spells_known") or []) if spell != index
        ]
        self._sheet["spells_prepared"] = [
            spell
            for spell in (self._sheet.get("spells_prepared") or [])
            if spell != index
        ]
        self._load_spells(self._sheet)
        self._changed()

    def _on_prepared_changed(self, _item) -> None:
        if not self._loading:
            self._changed()

    def _prepared(self) -> list[str]:
        return [
            self._spell_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self._spell_list.count())
            if self._spell_list.item(row).checkState() == Qt.CheckState.Checked
        ]

    # ----------------------------------------------------------------- editing

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self._save_button.setEnabled(True)
        self._refresh()

    def _on_species_changed(self, *_args) -> None:
        if not self._loading:
            self._populate_subspecies(self._species.currentData() or "")
        self._changed()

    def _on_class_changed(self, *_args) -> None:
        if not self._loading:
            self._populate_subclasses(self._class.currentData() or "")
        self._changed()

    def _on_level_changed(self, *_args) -> None:
        self._changed()

    def _gather(self) -> dict:
        """The sheet as the form currently reads, without saving it."""
        sheet = dict(self._sheet or new_sheet())
        sheet["species"] = self._species.currentData() or ""
        sheet["subspecies"] = self._subspecies.currentData() or ""
        sheet["class_index"] = self._class.currentData() or ""
        sheet["subclass"] = self._subclass.currentData() or ""
        sheet["background"] = self._background.currentData() or ""
        sheet["level"] = self._level.value()
        sheet["abilities"] = {a: s.value() for a, s in self._ability_inputs.items()}
        sheet["skill_proficiencies"] = [
            index for index, check in self._skill_checks.items() if check.isChecked()
        ]
        sheet["hp_current"] = self._hp_current.value()
        sheet["equipment"] = list((self._sheet or {}).get("equipment") or [])
        sheet["spells_known"] = list((self._sheet or {}).get("spells_known") or [])
        sheet["spells_prepared"] = self._prepared()
        return sheet

    # ---------------------------------------------------------------- derived

    def _refresh(self) -> None:
        """Recompute everything the sheet shows. Cheap, so do it on every keystroke."""
        if self._sheet is None:
            return
        sheet = self._gather()
        summary = derive.summary(sheet, self._content)

        for ability, label in self._ability_totals.items():
            score = summary["abilities"][ability]
            label.setText(f"{score} ({derive.format_bonus(summary['modifiers'][ability])})")

        maximum = summary["hp_max"]
        if (
            self._last_max_hp is not None
            and maximum != self._last_max_hp
            and self._hp_current.value() == self._last_max_hp
        ):
            was_loading = self._loading
            self._loading = True
            self._hp_current.setValue(maximum)
            self._loading = was_loading
        self._last_max_hp = maximum

        self._hp_max.setText(f"of {maximum}")
        self._derived_labels["ac"].setText(str(summary["ac"]))
        self._derived_labels["initiative"].setText(
            derive.format_bonus(summary["initiative"])
        )
        self._derived_labels["speed"].setText(f"{summary['speed']} ft")
        self._derived_labels["proficiency_bonus"].setText(
            derive.format_bonus(summary["proficiency_bonus"])
        )
        self._derived_labels["passive_perception"].setText(
            str(summary["passive_perception"])
        )

        proficient_saves = derive.saving_throw_proficiencies(sheet, self._content)
        for ability, label in self._save_labels.items():
            text = derive.format_bonus(summary["saving_throws"][ability])
            label.setText(f"{text} *" if ability in proficient_saves else text)
            label.setToolTip(
                "Proficient" if ability in proficient_saves else "Not proficient"
            )

        for index, label in self._skill_labels.items():
            label.setText(derive.format_bonus(summary["skills"].get(index, 0)))

        self._refresh_spellcasting(sheet, summary)

    def _refresh_spellcasting(self, sheet: dict, summary: dict) -> None:
        if not derive.is_caster(sheet):
            self._spell_box.setVisible(False)
            self._spellbook.setVisible(False)
            return

        self._spell_box.setVisible(True)
        self._spellbook.setVisible(True)

        if self._picker_class != sheet.get("class_index"):
            self._picker_class = sheet.get("class_index")
            self._refill_spell_picker(sheet)
        ability = derive.spellcasting_ability(sheet)
        self._spell_summary.setText(
            f"{ABILITY_NAMES[ability]} caster  ·  save DC {summary['spell_save_dc']}"
            f"  ·  attack {derive.format_bonus(summary['spell_attack_bonus'])}"
            f"  ·  {derive.cantrips_known(sheet, self._content)} cantrips"
        )
        slots = summary["spell_slots"]
        if slots:
            self._slots.setText(
                "Slots:  "
                + "   ".join(f"level {lvl}: {count}" for lvl, count in sorted(slots.items()))
            )
        else:
            self._slots.setText("No spell slots at this level.")

    # ------------------------------------------------------------------- saving

    def save(self) -> bool:
        if self._sheet is None or not self._editable:
            return False

        sheet = self._gather()
        report = validate(sheet, self._content)
        if not report.ok:
            self._problems.setText(report.summary())
            QMessageBox.warning(self, "Character sheet", report.summary())
            return False

        self._problems.setText("")

        if self._commit is not None:
            # A player: the host decides, and the change comes back to us.
            self._commit(self._entity_id, sheet)
            self._save_button.setEnabled(False)
            self._problems.setText("Sent to your DM.")
            return True

        if self._entity is None:
            return False
        entity = self._ctx.repos.entities.get(self._entity.id)
        if entity is None:
            return False
        entity.data["sheet"] = sheet
        self._ctx.repos.entities.update(entity)

        self._entity = entity
        self._sheet = sheet
        self._save_button.setEnabled(False)
        self._ctx.bus.entity_changed.emit(entity.id)
        self._ctx.bus.status_message.emit(
            f"Saved {entity.name}: {derive.describe(sheet, self._content)}"
        )
        self.saved.emit(entity.id)
        return True

    @property
    def is_dirty(self) -> bool:
        return self._save_button.isEnabled()
