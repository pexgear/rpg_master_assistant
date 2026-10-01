"""The rules: what follows from a sheet, and what is legal.

Worked examples throughout, because a derivation that is merely self-consistent
is worthless -- these numbers are checked against the Player's Handbook by hand.
"""

from __future__ import annotations

import pytest

from canon_keeper_core.content import Content
from canon_keeper_core.rules import derive, sheet as sheet_module, validation
from canon_keeper_core.rules.sheet import new_sheet


@pytest.fixture
def content():
    return Content()


@pytest.fixture
def wizard():
    """A level-5 high elf wizard, rolled 4/5/3/6 for levels two to five."""
    return new_sheet(
        species="elf",
        subspecies="high-elf",
        class_index="wizard",
        level=5,
        abilities={"str": 8, "dex": 14, "con": 14, "int": 15, "wis": 12, "cha": 10},
        ability_improvements={"int": 2},
        hp_rolled=[4, 5, 3, 6],
        skill_proficiencies=["arcana", "investigation"],
    )


# ------------------------------------------------------------------- the basics


@pytest.mark.parametrize(
    "score,expected",
    [(1, -5), (7, -2), (8, -1), (9, -1), (10, 0), (11, 0), (12, 1), (17, 3), (18, 4), (20, 5)],
)
def test_ability_modifiers(score, expected):
    assert derive.modifier(score) == expected


@pytest.mark.parametrize(
    "level,expected",
    [(1, 2), (4, 2), (5, 3), (8, 3), (9, 4), (12, 4), (13, 5), (16, 5), (17, 6), (20, 6)],
)
def test_proficiency_bonus_steps_every_four_levels(level, expected):
    assert derive.proficiency_bonus(level) == expected


# ---------------------------------------------------------------- ability scores


def test_species_bonuses_are_applied(wizard, content):
    """Elf gives +2 dexterity; the high elf subrace adds +1 intelligence."""
    scores = derive.ability_scores(wizard, content)
    assert scores["dex"] == 16  # 14 + 2
    assert scores["int"] == 18  # 15 + 1 + 2 improvement
    assert scores["str"] == 8, "untouched abilities stay put"


def test_improvements_stack_on_top(content):
    sheet = new_sheet(
        species="human",  # +1 to everything
        abilities={a: 10 for a in ("str", "dex", "con", "int", "wis", "cha")},
        ability_improvements={"str": 4},
    )
    assert derive.ability_scores(sheet, content)["str"] == 15


def test_no_species_is_not_an_error(content):
    assert derive.ability_scores(new_sheet(), content)["str"] == 10


# ------------------------------------------------------------------- hit points


def test_hit_points_use_the_full_die_at_first_level_then_the_rolls(wizard, content):
    """d6 wizard, +2 con: 6+2, then (4+2)+(5+2)+(3+2)+(6+2) = 34."""
    assert derive.max_hit_points(wizard, content) == 34


def test_levels_without_a_roll_use_the_average(content):
    """A half-built sheet should still show a sensible number."""
    sheet = new_sheet(class_index="fighter", level=3, abilities={"con": 10, "str": 10,
                      "dex": 10, "int": 10, "wis": 10, "cha": 10})
    # d10 fighter: 10 at first, then two levels of average 6.
    assert derive.max_hit_points(sheet, content) == 22


def test_a_constitution_penalty_cannot_drop_you_below_one_per_level(content):
    sheet = new_sheet(
        class_index="wizard",
        level=3,
        abilities={"str": 10, "dex": 10, "con": 1, "int": 10, "wis": 10, "cha": 10},
        hp_rolled=[1, 1],
    )
    assert derive.max_hit_points(sheet, content) >= 3


def test_current_hit_points_start_full(wizard, content):
    current, maximum = derive.hit_points(wizard, content)
    assert current == maximum == 34


def test_current_hit_points_are_remembered_once_set(wizard, content):
    wizard["hp_current"] = 7
    assert derive.hit_points(wizard, content) == (7, 34)


def test_an_override_wins_over_the_rules(wizard, content):
    """DMs hand out bonuses no rule predicts."""
    wizard["overrides"]["hp_max"] = 50
    assert derive.max_hit_points(wizard, content) == 50


# ----------------------------------------------------------------- armour class


def test_unarmoured_is_ten_plus_dexterity(wizard, content):
    assert derive.armour_class(wizard, content) == 13  # dex 16


def test_worn_armour_replaces_the_base(content):
    sheet = new_sheet(
        abilities={"str": 10, "dex": 16, "con": 10, "int": 10, "wis": 10, "cha": 10},
        equipment=[{"index": "chain-mail", "qty": 1}],
    )
    # Chain mail is AC 16 and allows no dexterity at all.
    assert derive.armour_class(sheet, content) == 16


def test_medium_armour_caps_the_dexterity_it_allows(content):
    sheet = new_sheet(
        abilities={"str": 10, "dex": 18, "con": 10, "int": 10, "wis": 10, "cha": 10},
        equipment=[{"index": "half-plate-armor", "qty": 1}],
    )
    # Half plate is 15 and allows at most +2, not the +4 dexterity would give.
    assert derive.armour_class(sheet, content) == 17


def test_a_shield_adds_on_top(content):
    sheet = new_sheet(
        abilities={"str": 10, "dex": 14, "con": 10, "int": 10, "wis": 10, "cha": 10},
        equipment=[{"index": "shield", "qty": 1}],
    )
    assert derive.armour_class(sheet, content) == 14  # 10 + 2 + 2


def test_an_ac_override_wins(wizard, content):
    wizard["overrides"]["ac"] = 21
    assert derive.armour_class(wizard, content) == 21


# ------------------------------------------------------- saves and skill checks


def test_saving_throws_use_the_classs_proficiencies(wizard, content):
    """A wizard is proficient in intelligence and wisdom saves."""
    saves = derive.saving_throws(wizard, content)
    assert saves["int"] == 7  # +4 modifier, +3 proficiency
    assert saves["wis"] == 4  # +1 modifier, +3 proficiency
    assert saves["dex"] == 3, "not proficient, so just the modifier"


def test_skill_bonuses_add_proficiency_only_where_chosen(wizard, content):
    skills = derive.skill_bonuses(wizard, content)
    assert skills["arcana"] == 7  # int +4, proficient +3
    assert skills["history"] == 4, "intelligence, but not proficient"
    assert skills["athletics"] == -1  # strength 8


def test_every_skill_is_listed_not_only_the_proficient_ones(wizard, content):
    assert len(derive.skill_bonuses(wizard, content)) == 18


def test_passive_perception(wizard, content):
    assert derive.passive_perception(wizard, content) == 11  # 10 + wis 1


# ------------------------------------------------------------------ spellcasting


def test_spell_slots_come_from_the_class_table(wizard, content):
    assert derive.spell_slots(wizard, content) == {1: 4, 2: 3, 3: 2}


def test_spell_save_dc_and_attack_bonus(wizard, content):
    assert derive.spell_save_dc(wizard, content) == 15  # 8 + 3 + 4
    assert derive.spell_attack_bonus(wizard, content) == 7  # 3 + 4


def test_a_fighter_is_not_a_caster(content):
    fighter = new_sheet(class_index="fighter", level=5)
    assert derive.is_caster(fighter) is False
    assert derive.spell_save_dc(fighter, content) is None
    assert derive.spell_slots(fighter, content) == {}


def test_used_slots_are_subtracted(wizard, content):
    wizard["slots_used"] = {"1": 2, "3": 2}
    remaining = derive.slots_remaining(wizard, content)
    assert remaining == {1: 2, 2: 3, 3: 0}


def test_cantrips_known(wizard, content):
    assert derive.cantrips_known(wizard, content) == 4


# ---------------------------------------------------------------------- summary


def test_the_one_line_description(wizard, content):
    assert derive.describe(wizard, content) == "Level 5 Elf Wizard"


def test_summary_gathers_everything_a_sheet_shows(wizard, content):
    summary = derive.summary(wizard, content)
    assert summary["hp_max"] == 34
    assert summary["ac"] == 13
    assert summary["proficiency_bonus"] == 3
    assert summary["spell_save_dc"] == 15


# ------------------------------------------------------------ state versus build


def test_fields_are_classified_for_the_approval_flow():
    """State applies at once; build waits for the DM."""
    assert sheet_module.classify("hp_current") == "state"
    assert sheet_module.classify("conditions") == "state"
    assert sheet_module.classify("level") == "build"
    assert sheet_module.classify("abilities") == "build"
    assert sheet_module.classify("nonsense") == "other"


def test_state_and_build_do_not_overlap():
    assert not (sheet_module.STATE_FIELDS & sheet_module.BUILD_FIELDS)


# -------------------------------------------------------------------- point buy


def test_the_standard_array_is_affordable_by_point_buy():
    assert sheet_module.point_buy_spent(
        dict(zip(("str", "dex", "con", "int", "wis", "cha"), sheet_module.STANDARD_ARRAY))
    ) == 27


def test_point_buy_refuses_scores_outside_its_range():
    assert not sheet_module.point_buy_is_legal({"str": 16})
    assert not sheet_module.point_buy_is_legal({"str": 7})


def test_point_buy_refuses_going_over_budget():
    assert sheet_module.point_buy_is_legal({a: 15 for a in ("str", "dex", "con")}), "27 exactly"
    assert not sheet_module.point_buy_is_legal(
        {a: 15 for a in ("str", "dex", "con", "int")}
    )


# -------------------------------------------------------------------- validation


def test_a_reasonable_sheet_passes(wizard, content):
    assert validation.validate(wizard, content).ok


@pytest.mark.parametrize("level", [0, 21, 100, -3, "five", True])
def test_impossible_levels_are_refused(wizard, content, level):
    wizard["level"] = level
    assert not validation.validate(wizard, content).ok


def test_an_impossible_ability_score_is_refused(wizard, content):
    """The reason a field allowlist is not enough once sheets exist."""
    wizard["abilities"]["str"] = 300
    report = validation.validate(wizard, content)
    assert not report.ok
    assert "abilities.str" in report.summary()


def test_a_class_that_does_not_exist_is_refused(wizard, content):
    wizard["class_index"] = "god-emperor"
    assert not validation.validate(wizard, content).ok


def test_a_subclass_from_another_class_is_refused(wizard, content):
    wizard["subclass"] = "berserker"  # a barbarian path
    report = validation.validate(wizard, content)
    assert not report.ok
    assert "subclass" in report.summary()


def test_a_subspecies_from_another_species_is_refused(content):
    sheet = new_sheet(species="dwarf", subspecies="high-elf")
    assert not validation.validate(sheet, content).ok


def test_an_invented_skill_is_refused(wizard, content):
    wizard["skill_proficiencies"] = ["arcana", "backstabbing"]
    assert not validation.validate(wizard, content).ok


def test_more_hit_dice_rolls_than_levels_is_refused(wizard, content):
    wizard["hp_rolled"] = [6] * 12
    assert not validation.validate(wizard, content).ok


def test_an_absurd_spell_list_is_refused(wizard, content):
    wizard["spells_known"] = ["magic-missile"] * 5000
    assert not validation.validate(wizard, content).ok


def test_homebrew_content_validates(repos, wizard):
    """A campaign's own class must not be rejected as unknown."""
    campaign_content = Content(repos.settings)
    campaign_content.add_homebrew(
        "classes", {"index": "artificer", "name": "Artificer", "hit_die": 8}
    )
    wizard["class_index"] = "artificer"
    wizard["subclass"] = ""

    assert validation.validate(wizard, campaign_content).ok


def test_something_that_is_not_a_sheet_is_refused(content):
    assert not validation.validate({"hello": "world"}, content).ok


def test_every_problem_is_reported_not_only_the_first(wizard, content):
    wizard["level"] = 99
    wizard["class_index"] = "nonsense"
    assert len(validation.validate(wizard, content).problems) >= 2
