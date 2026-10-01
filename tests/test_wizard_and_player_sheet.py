"""Guided creation, and the sheet as a player sees it."""

from __future__ import annotations

import logging

import pytest
from PySide6.QtCore import Qt

from canon_keeper.bus import Bus
from canon_keeper.net.state import SharedState
from canon_keeper.panels.characters.player_widget import PlayerCharactersWidget
from canon_keeper.panels.characters.wizard import ARRAY, MANUAL, CharacterWizard
from canon_keeper.plugin import AppContext
from canon_keeper_core.rules import derive
from canon_keeper_core.rules.sheet import STANDARD_ARRAY, new_sheet


# ---------------------------------------------------------------------- wizard


@pytest.fixture
def wizard(ctx, qtbot) -> CharacterWizard:
    made = CharacterWizard(ctx)
    qtbot.addWidget(made)
    return made


def _fill_basics(wizard, name="Elara", species="elf", klass="wizard", level=5):
    page = wizard.basics
    page.initializePage()
    page.name.setText(name)
    page.species.setCurrentIndex(page.species.findData(species))
    page.klass.setCurrentIndex(page.klass.findData(klass))
    page.level.setValue(level)
    assert page.validatePage()
    return page


def test_the_wizard_collects_who_they_are(wizard):
    _fill_basics(wizard)

    assert wizard.sheet["species"] == "elf"
    assert wizard.sheet["class_index"] == "wizard"
    assert wizard.sheet["level"] == 5
    assert wizard.character_name == "Elara"


def test_subspecies_offered_only_where_they_exist(wizard):
    page = wizard.basics
    page.initializePage()

    page.species.setCurrentIndex(page.species.findData("elf"))
    assert page.subspecies.isEnabled() is True

    page.species.setCurrentIndex(page.species.findData("human"))
    assert page.subspecies.isEnabled() is False, "humans have no subraces in the SRD"


def test_the_standard_array_must_be_used_exactly_once_each(wizard):
    _fill_basics(wizard)
    page = wizard.abilities
    page.initializePage()

    assert page.isComplete() is False, "nothing assigned yet"

    for ability, score in zip(
        ("str", "dex", "con", "int", "wis", "cha"), STANDARD_ARRAY
    ):
        page._array_picks[ability].setCurrentIndex(
            page._array_picks[ability].findData(score)
        )
    assert page.isComplete() is True

    # Two abilities claiming the same number is not the standard array.
    page._array_picks["str"].setCurrentIndex(page._array_picks["str"].findData(14))
    assert page.isComplete() is False


def test_point_buy_refuses_to_go_over_budget(wizard):
    _fill_basics(wizard)
    page = wizard.abilities
    page.initializePage()
    for button in page._method.buttons():
        if button.property("method") == "point-buy":
            button.setChecked(True)

    for ability in ("str", "dex", "con", "int", "wis", "cha"):
        page._inputs[ability].setValue(15)

    assert page.isComplete() is False
    assert "Over budget" in page._status.text()


def test_ability_totals_show_species_bonuses_while_choosing(wizard):
    _fill_basics(wizard, species="elf")
    page = wizard.abilities
    page.initializePage()
    for button in page._method.buttons():
        if button.property("method") == MANUAL:
            button.setChecked(True)
    page._inputs["dex"].setValue(14)

    assert page._totals["dex"].text() == "16 (+3)", "elf +2 applied as you choose"


def test_skills_are_limited_to_what_the_class_allows(wizard):
    _fill_basics(wizard, klass="wizard")
    page = wizard.skills
    page.initializePage()

    offered = {
        page._skills.item(row).data(Qt.ItemDataRole.UserRole)
        for row in range(page._skills.count())
    }
    assert "arcana" in offered
    assert "athletics" not in offered, "a wizard cannot choose athletics"
    assert page._allowed == 2


def test_the_right_number_of_skills_must_be_chosen(wizard):
    _fill_basics(wizard, klass="wizard")
    page = wizard.skills
    page.initializePage()
    assert page.isComplete() is False

    for row in range(page._skills.count()):
        item = page._skills.item(row)
        if item.data(Qt.ItemDataRole.UserRole) in ("arcana", "history"):
            item.setCheckState(Qt.CheckState.Checked)

    assert page.isComplete() is True
    page.validatePage()
    assert set(wizard.sheet["skill_proficiencies"]) == {"arcana", "history"}


def test_a_non_caster_skips_the_spells_page(wizard):
    _fill_basics(wizard, klass="fighter")
    wizard.setStartId(wizard.pageIds()[0])
    assert derive.is_caster(wizard.sheet) is False


def test_the_spell_page_offers_the_classs_list(wizard):
    _fill_basics(wizard, klass="wizard", level=5)
    page = wizard.spells
    page.initializePage()

    offered = {
        page._spells.item(row).data(Qt.ItemDataRole.UserRole)
        for row in range(page._spells.count())
    }
    assert "magic-missile" in offered
    assert "cure-wounds" not in offered, "a cleric spell, not a wizard one"


def test_finishing_produces_a_usable_sheet(ctx, wizard):
    _fill_basics(wizard, name="Elara", klass="wizard", level=5)
    page = wizard.abilities
    page.initializePage()
    for ability, score in zip(
        ("str", "dex", "con", "int", "wis", "cha"), STANDARD_ARRAY
    ):
        page._array_picks[ability].setCurrentIndex(
            page._array_picks[ability].findData(score)
        )
    page.validatePage()

    name, sheet = wizard.finished_sheet()

    assert name == "Elara"
    assert sheet["level"] == 5
    assert sheet["hp_current"] is None, "starts at full; the sheet derives it"
    assert sheet["equipment"], "the class's starting equipment came along"
    from canon_keeper_core.rules.validation import validate

    assert validate(sheet, wizard.content).ok


# ----------------------------------------------------------- the player's sheet


@pytest.fixture
def player_ctx(repos):
    campaign = repos.campaigns.ensure_default("Test Campaign")
    return AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
        role="player",
        shared=SharedState(),
    )


def _received(own: bool, **sheet_fields) -> dict:
    return {
        "id": 1 if own else 2,
        "kind": "pc",
        "name": "Elara" if own else "Brakk",
        "summary": "",
        "parent_id": None,
        "own": own,
        "version": 3,
        "data": {"sheet": new_sheet(class_index="wizard", level=5, **sheet_fields)},
    }


@pytest.fixture
def player_panel(player_ctx, qtbot) -> PlayerCharactersWidget:
    widget = PlayerCharactersWidget(player_ctx)
    qtbot.addWidget(widget)
    return widget


def _select(panel, entity_id):
    for row in range(panel._list.count()):
        if panel._list.item(row).data(256) == entity_id:
            panel._list.setCurrentRow(row)
            return
    raise AssertionError(f"no row for {entity_id}")


def test_a_player_gets_a_sheet_tab_too(player_panel):
    assert [
        player_panel._tabs.tabText(i) for i in range(player_panel._tabs.count())
    ] == ["Story", "Sheet"]


def test_your_own_sheet_shows_its_numbers(player_ctx, player_panel):
    player_ctx.shared.replace_all([_received(own=True)])
    _select(player_panel, 1)

    tab = player_panel._sheet_tab
    assert tab._hp_max.text() != "of 0"
    assert "save DC" in tab._spell_summary.text()


def test_a_player_may_fill_anything_in_but_nothing_applies_by_itself(
    player_ctx, player_panel
):
    """Everything they change is a request, hit points included."""
    player_ctx.shared.replace_all([_received(own=True)])
    _select(player_panel, 1)
    tab = player_panel._sheet_tab

    assert tab._hp_current.isEnabled() is True
    assert tab._level.isEnabled() is True, "they may ask for a level up"
    assert tab._save_button.text() == "Ask my DM"
    assert "your DM, who decides" in tab._problems.text()


def test_someone_elses_sheet_is_read_only(player_ctx, player_panel):
    player_ctx.shared.replace_all([_received(own=False)])
    _select(player_panel, 2)
    tab = player_panel._sheet_tab

    assert tab._hp_current.isEnabled() is False
    assert tab._save_button.isHidden() is True
    assert "Someone else" in tab._problems.text()


def test_saving_asks_the_host_rather_than_writing_locally(
    player_ctx, player_panel, qtbot
):
    """A player has no campaign to write to; the host decides."""
    player_ctx.shared.replace_all([_received(own=True)])
    _select(player_panel, 1)
    tab = player_panel._sheet_tab

    tab._hp_current.setValue(4)
    with qtbot.waitSignal(player_ctx.bus.player_edit_requested, timeout=1000) as blocker:
        tab.save()

    entity_id, changes = blocker.args
    assert entity_id == 1
    assert changes["data"]["sheet"]["hp_current"] == 4
    # No version travels with an edit. The host checks against the copy it sent,
    # so a client can neither choose the version it claims nor omit one and be
    # written unconditionally.
    assert "version" not in changes


def test_nothing_is_written_to_the_players_own_database(
    player_ctx, player_panel, qtbot
):
    player_ctx.shared.replace_all([_received(own=True)])
    _select(player_panel, 1)
    player_panel._sheet_tab._hp_current.setValue(4)
    player_panel._sheet_tab.save()

    assert player_ctx.repos.entities.list(player_ctx.campaign_id) == []


def test_a_player_is_never_offered_a_create_sheet_button(player_ctx, player_panel):
    """They cannot conjure a character into someone else's campaign."""
    player_ctx.shared.replace_all(
        [{"id": 5, "kind": "pc", "name": "Sheetless", "own": True, "data": {}}]
    )
    _select(player_panel, 5)

    assert player_panel._sheet_tab._create_button.isHidden() is True
