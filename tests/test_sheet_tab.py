"""The Sheet tab: derivations on screen, and the traps around saving one."""

from __future__ import annotations

import pytest

from canon_keeper.panels.characters.widget import CharactersWidget
from canon_keeper_core.repo.entities import KIND_PC, Entity


@pytest.fixture
def panel(ctx, qtbot) -> CharactersWidget:
    widget = CharactersWidget(ctx)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def elara(ctx, panel):
    """A character selected in the panel, with a blank sheet."""
    entity = ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_PC, name="Elara")
    )
    panel.reload(keep_selection=False)
    panel._select_entity(entity.id)
    panel._sheet_tab._create_sheet()
    return ctx.repos.entities.get(entity.id)


def _build_wizard(sheet_tab):
    sheet_tab._species.setCurrentIndex(sheet_tab._species.findData("elf"))
    sheet_tab._subspecies.setCurrentIndex(sheet_tab._subspecies.findData("high-elf"))
    sheet_tab._class.setCurrentIndex(sheet_tab._class.findData("wizard"))
    sheet_tab._level.setValue(5)
    for ability, score in (
        ("str", 8), ("dex", 14), ("con", 14), ("int", 15), ("wis", 12), ("cha", 10)
    ):
        sheet_tab._ability_inputs[ability].setValue(score)


# ----------------------------------------------------------------------- layout


def test_the_detail_pane_has_both_tabs(panel):
    assert [panel._tabs.tabText(i) for i in range(panel._tabs.count())] == [
        "Story",
        "Sheet",
    ]


def test_a_character_without_a_sheet_is_offered_one(ctx, panel):
    ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_PC, name="Brakk")
    )
    panel.reload(keep_selection=False)
    panel._list.setCurrentRow(0)

    assert panel._sheet_tab._create_button.isHidden() is False
    assert panel._sheet_tab._save_button.isHidden() is True


def test_creating_a_sheet_swaps_the_prompt_for_the_form(panel, elara):
    assert panel._sheet_tab._create_button.isHidden() is True
    assert panel._sheet_tab._save_button.isHidden() is False


# ------------------------------------------------------------------ derivations


def test_the_numbers_appear_before_saving(panel, elara):
    """Derived, not stored: the sheet answers while you are still typing."""
    tab = panel._sheet_tab
    _build_wizard(tab)

    assert tab._ability_totals["dex"].text() == "16 (+3)", "elf +2 dexterity"
    assert tab._ability_totals["int"].text() == "16 (+3)", "high elf +1 intelligence"
    assert tab._hp_max.text() == "of 32"
    assert tab._derived_labels["ac"].text() == "13"
    assert tab._derived_labels["proficiency_bonus"].text() == "+3"


def test_proficient_saves_are_marked(panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)

    assert tab._save_labels["int"].text().endswith("*"), "wizards save on intelligence"
    assert tab._save_labels["wis"].text().endswith("*")
    assert not tab._save_labels["dex"].text().endswith("*")


def test_ticking_a_skill_adds_the_proficiency_bonus(panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    before = tab._skill_labels["arcana"].text()

    tab._skill_checks["arcana"].setChecked(True)

    assert before == "+3"
    assert tab._skill_labels["arcana"].text() == "+6"


def test_spellcasting_appears_only_for_casters(panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    assert tab._spell_box.isHidden() is False
    assert "save DC 14" in tab._spell_summary.text()
    assert "level 3: 2" in tab._slots.text()

    tab._class.setCurrentIndex(tab._class.findData("fighter"))
    assert tab._spell_box.isHidden() is True


def test_hit_points_follow_the_maximum_while_at_full(panel, elara):
    """A character at full health should not be stranded on the old number."""
    tab = panel._sheet_tab
    _build_wizard(tab)
    assert tab._hp_current.value() == 32

    tab._ability_inputs["con"].setValue(18)

    assert tab._hp_max.text() == "of 42"
    assert tab._hp_current.value() == 42


def test_a_wounded_character_stays_wounded(panel, elara):
    """The rule is 'follow the maximum when at full', not 'always heal'."""
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab._hp_current.setValue(5)

    tab._ability_inputs["con"].setValue(18)

    assert tab._hp_current.value() == 5


def test_subspecies_follow_the_species(panel, elara):
    tab = panel._sheet_tab
    tab._species.setCurrentIndex(tab._species.findData("elf"))
    assert tab._subspecies.findData("high-elf") >= 0

    tab._species.setCurrentIndex(tab._species.findData("dwarf"))
    assert tab._subspecies.findData("high-elf") == -1


# --------------------------------------------------------------------- saving


def test_saving_stores_the_sheet(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab._skill_checks["arcana"].setChecked(True)

    assert tab.save() is True

    sheet = ctx.repos.entities.get(elara.id).data["sheet"]
    assert sheet["class_index"] == "wizard"
    assert sheet["level"] == 5
    assert "arcana" in sheet["skill_proficiencies"]


def test_saving_an_illegal_sheet_is_refused(ctx, panel, elara, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    tab = panel._sheet_tab
    _build_wizard(tab)
    # Reach past the spin box, as a modified client or a bad migration could.
    tab._sheet["skill_proficiencies"] = ["arcana"]
    tab._gather = lambda: {**tab._sheet, "level": 99}

    assert tab.save() is False
    assert "level" in tab._problems.text()


def test_creating_a_sheet_does_not_undo_an_edit_on_the_story_tab(ctx, panel):
    """The sheet tab held a stale copy and wrote it back, losing the name."""
    entity = ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_PC, name="New character")
    )
    panel.reload(keep_selection=False)
    panel._select_entity(entity.id)

    panel._name.setText("Elara")
    panel._mark_dirty()
    panel.save_current()

    panel._sheet_tab._create_sheet()

    assert ctx.repos.entities.get(entity.id).name == "Elara"


def test_saving_the_sheet_does_not_undo_a_story_edit(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)

    # Something changes the summary between loading the sheet and saving it.
    meanwhile = ctx.repos.entities.get(elara.id)
    meanwhile.summary = "A tired cleric"
    ctx.repos.entities.update(meanwhile)

    tab.save()

    assert ctx.repos.entities.get(elara.id).summary == "A tired cleric"


def test_the_list_says_what_a_character_is(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab.save()

    labels = [panel._list.item(i).text() for i in range(panel._list.count())]
    assert any("Level 5 Elf Wizard" in label for label in labels)


def test_saving_bumps_the_version(ctx, panel, elara):
    before = ctx.repos.entities.get(elara.id).version
    _build_wizard(panel._sheet_tab)
    panel._sheet_tab.save()

    assert ctx.repos.entities.get(elara.id).version > before


# ------------------------------------------------------------ gear and spells


def test_equipment_can_be_added_and_changes_the_armour_class(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    before = tab._derived_labels["ac"].text()

    tab._equipment_picker.setCurrentIndex(
        tab._equipment_picker.findData("chain-mail")
    )
    tab._add_equipment()

    assert tab._equipment.count() == 1
    assert tab._derived_labels["ac"].text() != before, "worn armour should count"
    assert tab._derived_labels["ac"].text() == "16"


def test_adding_the_same_item_twice_stacks_it(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab._equipment_picker.setCurrentIndex(tab._equipment_picker.findData("dagger"))
    tab._add_equipment()
    tab._add_equipment()

    assert tab._equipment.count() == 1
    assert "×2" in tab._equipment.item(0).text()


def test_equipment_can_be_removed(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab._equipment_picker.setCurrentIndex(tab._equipment_picker.findData("dagger"))
    tab._add_equipment()
    tab._equipment.setCurrentRow(0)
    tab._remove_equipment()

    assert tab._equipment.count() == 0


def test_the_spellbook_offers_only_the_classs_list(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)

    offered = {
        tab._spell_picker.itemData(i) for i in range(tab._spell_picker.count())
    }
    assert "magic-missile" in offered
    assert "cure-wounds" not in offered


def test_learning_a_spell_puts_it_in_the_book(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab._spell_picker.setCurrentIndex(tab._spell_picker.findData("magic-missile"))
    tab._add_spell()

    assert tab._spell_list.count() == 1
    assert "Magic Missile" in tab._spell_list.item(0).text()
    assert "magic-missile" not in {
        tab._spell_picker.itemData(i) for i in range(tab._spell_picker.count())
    }, "a known spell should not be offered again"


def test_cantrips_are_always_prepared(ctx, panel, elara):
    """Ticking a cantrip would imply it could be unprepared, which is a lie."""
    from PySide6.QtCore import Qt

    tab = panel._sheet_tab
    _build_wizard(tab)
    tab._spell_picker.setCurrentIndex(tab._spell_picker.findData("fire-bolt"))
    tab._add_spell()

    assert tab._spell_list.item(0).checkState() == Qt.CheckState.Checked


def test_preparing_and_unpreparing_is_saved(ctx, panel, elara):
    from PySide6.QtCore import Qt

    tab = panel._sheet_tab
    _build_wizard(tab)
    for index in ("magic-missile", "shield"):
        tab._spell_picker.setCurrentIndex(tab._spell_picker.findData(index))
        tab._add_spell()

    tab._spell_list.item(0).setCheckState(Qt.CheckState.Checked)
    tab._spell_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    tab.save()

    sheet = ctx.repos.entities.get(elara.id).data["sheet"]
    assert len(sheet["spells_known"]) == 2
    assert len(sheet["spells_prepared"]) == 1


def test_forgetting_a_spell_also_unprepares_it(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab._spell_picker.setCurrentIndex(tab._spell_picker.findData("magic-missile"))
    tab._add_spell()
    tab._spell_list.setCurrentRow(0)
    tab._remove_spell()
    tab.save()

    sheet = ctx.repos.entities.get(elara.id).data["sheet"]
    assert sheet["spells_known"] == []
    assert sheet["spells_prepared"] == []


def test_the_spellbook_hides_for_a_non_caster(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    assert tab._spellbook.isHidden() is False

    tab._class.setCurrentIndex(tab._class.findData("fighter"))
    assert tab._spellbook.isHidden() is True


# --------------------------------------------------------------------- ownership


def test_a_character_can_be_handed_to_a_player(ctx, panel, elara):
    """Without this there was no way to give anyone a character at all."""
    marco = ctx.repos.accounts.create(ctx.campaign_id, "marco", "goblin-teeth")
    panel.reload(keep_selection=True)
    panel._select_entity(elara.id)

    panel._owner.setCurrentIndex(panel._owner.findData(marco.id))
    panel._mark_dirty()
    panel.save_current()

    assert ctx.repos.entities.get(elara.id).owner_account_id == marco.id
    assert ctx.repos.entities.owned_ids(marco.id) == {elara.id}


def test_ownership_can_be_taken_back(ctx, panel, elara):
    marco = ctx.repos.accounts.create(ctx.campaign_id, "marco", "goblin-teeth")
    ctx.repos.entities.set_owner(elara.id, marco.id)
    panel.reload(keep_selection=False)
    panel._select_entity(elara.id)

    panel._owner.setCurrentIndex(panel._owner.findData(None))
    panel._mark_dirty()
    panel.save_current()

    assert ctx.repos.entities.get(elara.id).owner_account_id is None


def test_the_owner_shown_is_the_one_stored(ctx, panel, elara):
    marco = ctx.repos.accounts.create(
        ctx.campaign_id, "marco", "goblin-teeth", display_name="Marco"
    )
    ctx.repos.entities.set_owner(elara.id, marco.id)

    panel.reload(keep_selection=False)
    panel._select_entity(elara.id)

    assert panel._owner.currentData() == marco.id
    assert panel._owner.currentText() == "Marco"


def test_only_players_are_offered_as_owners(ctx, panel, elara):
    ctx.repos.accounts.create(ctx.campaign_id, "gm", "run-the-game", role="dm")
    ctx.repos.accounts.create(ctx.campaign_id, "marco", "goblin-teeth")
    panel.reload(keep_selection=True)

    offered = {panel._owner.itemText(i) for i in range(panel._owner.count())}
    assert "marco" in offered
    assert "gm" not in offered, "the DM does not need to be given a character"


def test_moving_a_login_to_a_character_grants_ownership(ctx, qtbot):
    """The other place you naturally think about who plays what.

    The dialog cannot make a login any more -- everybody arrives by invitation
    -- but it can still move an existing one onto a different character, and
    ownership has to follow or the player cannot edit their own sheet.
    """
    from canon_keeper.panels.table.dialogs import AccountsDialog

    elara = ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_PC, name="Elara")
    )
    account = ctx.repos.accounts.create(ctx.campaign_id, "marco", "goblin-teeth")

    dialog = AccountsDialog(ctx)
    qtbot.addWidget(dialog)
    row = next(
        dialog._list.item(i)
        for i in range(dialog._list.count())
        if dialog._list.item(i).data(256) == account.id
    )
    dialog._list.setCurrentItem(row)
    dialog._character.setCurrentIndex(dialog._character.findData(elara.id))
    dialog._save()

    assert ctx.repos.entities.get(elara.id).owner_account_id == account.id


def test_the_players_dialog_cannot_make_a_login(ctx, qtbot):
    """The whole point: a DM who set your password would know it."""
    from canon_keeper.panels.table.dialogs import AccountsDialog

    dialog = AccountsDialog(ctx)
    qtbot.addWidget(dialog)

    assert not hasattr(dialog, "_password")
    assert dialog._username.isReadOnly()
