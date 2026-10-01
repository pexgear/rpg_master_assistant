"""Making a character, one question at a time.

The wizard writes the same sheet the Sheet tab edits -- it is a guided path
through fields that already exist, not a second model. Anything it does not ask
about can be filled in afterwards, and anything it gets wrong can be corrected
there, which is why it can afford to be opinionated about the common path.

It asks in the order people actually decide: who you are, then what you can do,
then the details.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from canon_keeper_core.content import Content
from canon_keeper_core.rules import derive
from canon_keeper_core.rules.sheet import (
    ABILITIES,
    ABILITY_NAMES,
    POINT_BUY_BUDGET,
    POINT_BUY_COST,
    STANDARD_ARRAY,
    new_sheet,
    point_buy_spent,
)

_NONE = "(none)"

MANUAL = "manual"
ARRAY = "array"
POINT_BUY = "point-buy"


class _Page(QWizardPage):
    """A page that can see the wizard's sheet-in-progress."""

    @property
    def sheet(self) -> dict:
        return self.wizard().sheet

    @property
    def content(self) -> Content:
        return self.wizard().content


# ------------------------------------------------------------------ who they are


class BasicsPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Who are they?")
        self.setSubTitle("Name, ancestry and calling. All of it can change later.")

        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Elara Nightbreeze")
        form.addRow("Name", self.name)
        self.registerField("name*", self.name)

        self.species = QComboBox()
        self.species.currentIndexChanged.connect(self._species_changed)
        form.addRow("Species", self.species)

        self.subspecies = QComboBox()
        form.addRow("Subspecies", self.subspecies)

        self.klass = QComboBox()
        self.klass.currentIndexChanged.connect(self._class_changed)
        form.addRow("Class", self.klass)

        self.subclass = QComboBox()
        self.subclass.setToolTip("Most classes choose this at level 2 or 3.")
        form.addRow("Subclass", self.subclass)

        self.level = QSpinBox()
        self.level.setRange(1, 20)
        form.addRow("Level", self.level)

        self.background = QComboBox()
        form.addRow("Background", self.background)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        form.addRow("", self.hint)

    def initializePage(self) -> None:
        for combo, entries in (
            (self.species, self.content.species()),
            (self.klass, self.content.classes()),
            (self.background, self.content.backgrounds()),
        ):
            combo.clear()
            combo.addItem(_NONE, "")
            for entry in entries:
                combo.addItem(entry.get("name", entry["index"]), entry["index"])
        self._species_changed()
        self._class_changed()

    def _species_changed(self, *_args) -> None:
        self.subspecies.clear()
        self.subspecies.addItem(_NONE, "")
        for entry in self.content.subspecies_of(self.species.currentData() or ""):
            self.subspecies.addItem(entry.get("name", entry["index"]), entry["index"])
        self.subspecies.setEnabled(self.subspecies.count() > 1)

    def _class_changed(self, *_args) -> None:
        index = self.klass.currentData() or ""
        self.subclass.clear()
        self.subclass.addItem(_NONE, "")
        for entry in self.content.subclasses_of(index):
            self.subclass.addItem(entry.get("name", entry["index"]), entry["index"])
        self.subclass.setEnabled(self.subclass.count() > 1)

        klass = self.content.get("classes", index) or {}
        if klass:
            self.hint.setText(f"Hit die d{klass.get('hit_die', 8)}.")
        else:
            self.hint.setText("")

    def validatePage(self) -> bool:
        self.sheet.update(
            {
                "species": self.species.currentData() or "",
                "subspecies": self.subspecies.currentData() or "",
                "class_index": self.klass.currentData() or "",
                "subclass": self.subclass.currentData() or "",
                "level": self.level.value(),
                "background": self.background.currentData() or "",
            }
        )
        self.wizard().character_name = self.name.text().strip()
        return True


# ------------------------------------------------------------------- abilities


class AbilitiesPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Ability scores")
        self.setSubTitle(
            "Species bonuses are added on top, so these are the numbers before them."
        )

        layout = QVBoxLayout(self)
        self._method = QButtonGroup(self)
        for value, caption in (
            (ARRAY, "Standard array (15, 14, 13, 12, 10, 8)"),
            (POINT_BUY, f"Point buy ({POINT_BUY_BUDGET} points)"),
            (MANUAL, "Enter them myself"),
        ):
            button = QRadioButton(caption)
            button.setProperty("method", value)
            self._method.addButton(button)
            layout.addWidget(button)
            if value == ARRAY:
                button.setChecked(True)
        self._method.buttonToggled.connect(lambda *_: self._method_changed())

        grid = QGridLayout()
        self._inputs: dict[str, QSpinBox] = {}
        self._array_picks: dict[str, QComboBox] = {}
        self._totals: dict[str, QLabel] = {}

        for row, ability in enumerate(ABILITIES):
            grid.addWidget(QLabel(ABILITY_NAMES[ability]), row, 0)

            pick = QComboBox()
            pick.addItem("-", None)
            for score in STANDARD_ARRAY:
                pick.addItem(str(score), score)
            pick.currentIndexChanged.connect(self._recalculate)
            grid.addWidget(pick, row, 1)
            self._array_picks[ability] = pick

            spin = QSpinBox()
            spin.setRange(1, 20)
            spin.setValue(10)
            spin.valueChanged.connect(self._recalculate)
            grid.addWidget(spin, row, 2)
            self._inputs[ability] = spin

            total = QLabel("10 (+0)")
            grid.addWidget(total, row, 3)
            self._totals[ability] = total
        layout.addLayout(grid)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addStretch(1)

    @property
    def method(self) -> str:
        button = self._method.checkedButton()
        return button.property("method") if button else ARRAY

    def initializePage(self) -> None:
        self._method_changed()

    def _method_changed(self) -> None:
        using_array = self.method == ARRAY
        for ability in ABILITIES:
            self._array_picks[ability].setVisible(using_array)
            self._inputs[ability].setVisible(not using_array)
            if self.method == POINT_BUY:
                self._inputs[ability].setRange(8, 15)
            else:
                self._inputs[ability].setRange(1, 20)
        self._recalculate()

    def _scores(self) -> dict[str, int]:
        if self.method == ARRAY:
            return {
                ability: self._array_picks[ability].currentData() or 10
                for ability in ABILITIES
            }
        return {ability: self._inputs[ability].value() for ability in ABILITIES}

    def _recalculate(self, *_args) -> None:
        scores = self._scores()
        preview = dict(self.sheet)
        preview["abilities"] = scores
        final = derive.ability_scores(preview, self.content)

        for ability, label in self._totals.items():
            label.setText(
                f"{final[ability]} ({derive.format_bonus(derive.modifier(final[ability]))})"
            )

        if self.method == POINT_BUY:
            spent = point_buy_spent(scores)
            self._status.setText(
                f"{spent} of {POINT_BUY_BUDGET} points spent."
                + ("  Over budget." if spent > POINT_BUY_BUDGET else "")
            )
        elif self.method == ARRAY:
            chosen = [
                self._array_picks[a].currentData()
                for a in ABILITIES
                if self._array_picks[a].currentData() is not None
            ]
            duplicates = len(chosen) != len(set(chosen))
            missing = len(chosen) < len(ABILITIES)
            self._status.setText(
                "Each number from the array is used once."
                if not (duplicates or missing)
                else "Assign each of the six numbers exactly once."
            )
        else:
            self._status.setText("")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        scores = self._scores()
        if self.method == ARRAY:
            chosen = [
                self._array_picks[a].currentData()
                for a in ABILITIES
                if self._array_picks[a].currentData() is not None
            ]
            return len(chosen) == len(ABILITIES) and len(set(chosen)) == len(chosen)
        if self.method == POINT_BUY:
            return point_buy_spent(scores) <= POINT_BUY_BUDGET and all(
                score in POINT_BUY_COST for score in scores.values()
            )
        return True

    def validatePage(self) -> bool:
        self.sheet["abilities"] = self._scores()
        return True


# ---------------------------------------------------------------------- skills


class SkillsPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Proficiencies")
        self.setSubTitle("What they are good at.")

        layout = QVBoxLayout(self)
        self._prompt = QLabel("")
        self._prompt.setWordWrap(True)
        layout.addWidget(self._prompt)

        self._skills = QListWidget()
        self._skills.itemChanged.connect(lambda _i: self._check_count())
        layout.addWidget(self._skills, 1)

        self._status = QLabel("")
        layout.addWidget(self._status)
        self._allowed = 0

    def initializePage(self) -> None:
        klass = self.content.get("classes", self.sheet.get("class_index", "")) or {}
        choices = [
            choice
            for choice in klass.get("proficiency_choices", ())
            if choice.get("type") == "proficiencies"
        ]

        options: list[str] = []
        self._allowed = 0
        for choice in choices:
            self._allowed += int(choice.get("choose", 0))
            for option in (choice.get("from") or {}).get("options", ()):
                index = (option.get("item") or {}).get("index", "")
                if index.startswith("skill-"):
                    options.append(index[len("skill-") :])

        if not options:
            # A homebrew class may not describe its choices; offer everything.
            options = [skill["index"] for skill in self.content.skills()]
            self._allowed = 2

        self._prompt.setText(
            f"Choose {self._allowed} from the list, as {klass.get('name', 'this class')} allows."
        )

        self._skills.clear()
        for index in sorted(set(options)):
            skill = self.content.get("skills", index) or {}
            item = QListWidgetItem(skill.get("name", index))
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._skills.addItem(item)
        self._check_count()

    def _chosen(self) -> list[str]:
        return [
            self._skills.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self._skills.count())
            if self._skills.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _check_count(self) -> None:
        chosen = len(self._chosen())
        self._status.setText(f"{chosen} of {self._allowed} chosen.")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return len(self._chosen()) == self._allowed

    def validatePage(self) -> bool:
        self.sheet["skill_proficiencies"] = self._chosen()
        return True


# ---------------------------------------------------------------------- spells


class SpellsPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Spells")
        self.setSubTitle("Tick what they know. You can change this at any time.")

        layout = QVBoxLayout(self)
        self._prompt = QLabel("")
        self._prompt.setWordWrap(True)
        layout.addWidget(self._prompt)

        self._spells = QListWidget()
        layout.addWidget(self._spells, 1)

    def initializePage(self) -> None:
        class_index = self.sheet.get("class_index", "")
        cantrips = derive.cantrips_known(self.sheet, self.content)
        self._prompt.setText(
            f"Around {cantrips} cantrips to start with, plus the spells your class "
            "prepares or knows. Nothing here is enforced."
        )

        self._spells.clear()
        highest = max(derive.spell_slots(self.sheet, self.content) or {0: 0})
        for level in range(0, highest + 1):
            spells = self.content.spells_for(class_index, level=level)
            if not spells:
                continue
            header = QListWidgetItem("Cantrips" if level == 0 else f"Level {level}")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self._spells.addItem(header)
            for spell in spells:
                item = QListWidgetItem("    " + spell.get("name", spell["index"]))
                item.setData(Qt.ItemDataRole.UserRole, spell["index"])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self._spells.addItem(item)

    def validatePage(self) -> bool:
        chosen = [
            self._spells.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self._spells.count())
            if self._spells.item(row).checkState() == Qt.CheckState.Checked
            and self._spells.item(row).data(Qt.ItemDataRole.UserRole)
        ]
        self.sheet["spells_known"] = chosen
        self.sheet["spells_prepared"] = list(chosen)
        return True


# ---------------------------------------------------------------------- review


class ReviewPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Ready")
        self.setSubTitle("What this adds up to.")
        layout = QVBoxLayout(self)
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._summary)
        layout.addStretch(1)

    def initializePage(self) -> None:
        sheet = self.sheet
        summary = derive.summary(sheet, self.content)
        abilities = "   ".join(
            f"{ABILITY_NAMES[a][:3].upper()} {summary['abilities'][a]}"
            f" ({derive.format_bonus(summary['modifiers'][a])})"
            for a in ABILITIES
        )
        skills = ", ".join(
            (self.content.get("skills", index) or {}).get("name", index)
            for index in sheet.get("skill_proficiencies", ())
        )
        lines = [
            f"<b>{self.wizard().character_name}</b>",
            derive.describe(sheet, self.content),
            "",
            abilities,
            "",
            f"Hit points {summary['hp_max']}   ·   Armour class {summary['ac']}"
            f"   ·   Proficiency {derive.format_bonus(summary['proficiency_bonus'])}",
        ]
        if skills:
            lines.append(f"Proficient in {skills}")
        if summary["spell_slots"]:
            lines.append(
                "Slots: "
                + ", ".join(f"{n}× level {lvl}" for lvl, n in sorted(summary["spell_slots"].items()))
                + f"   ·   save DC {summary['spell_save_dc']}"
            )
        if sheet.get("spells_known"):
            lines.append(f"{len(sheet['spells_known'])} spells chosen")
        self._summary.setText("<br>".join(lines))


# ---------------------------------------------------------------------- wizard


class CharacterWizard(QWizard):
    """Guided creation. Produces a sheet and a name; the caller stores them."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.content = Content(ctx.repos.settings)
        self.sheet = new_sheet()
        self.character_name = ""

        self.setWindowTitle("New character")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(620, 560)

        self.basics = BasicsPage()
        self.abilities = AbilitiesPage()
        self.skills = SkillsPage()
        self.spells = SpellsPage()
        self.review = ReviewPage()

        for page in (self.basics, self.abilities, self.skills, self.spells, self.review):
            self.addPage(page)

    def nextId(self) -> int:
        """Skip the spells page for anyone who cannot cast."""
        current = self.currentPage()
        if current is self.skills and not derive.is_caster(self.sheet):
            return self.pageIds()[-1]
        return super().nextId()

    def finished_sheet(self) -> tuple[str, dict]:
        """The name and sheet to store, with the class's starting equipment."""
        sheet = dict(self.sheet)
        klass = self.content.get("classes", sheet.get("class_index", "")) or {}
        sheet["equipment"] = [
            {
                "index": (item.get("equipment") or {}).get("index", ""),
                "qty": int(item.get("quantity", 1)),
            }
            for item in klass.get("starting_equipment", ())
            if (item.get("equipment") or {}).get("index")
        ]
        # Start at full health; the sheet derives the maximum.
        sheet["hp_current"] = None
        return self.character_name or "New character", sheet
