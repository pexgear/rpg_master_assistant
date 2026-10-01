"""The content layer: SRD plus whatever the campaign invented."""

from __future__ import annotations

import pytest

from canon_keeper_core.content import Content, srd


@pytest.fixture
def content(repos):
    return Content(repos.settings)


@pytest.fixture
def srd_only():
    return Content()


# --------------------------------------------------------------------- the SRD


def test_the_data_shipped():
    assert srd.is_available(), "the SRD JSON is missing from the package"


def test_the_collections_load(srd_only):
    assert len(srd_only.classes()) == 12
    assert len(srd_only.species()) == 9
    assert len(srd_only.spells()) == 319


def test_the_srd_really_does_have_one_background(srd_only):
    """The reason the merge layer exists. If this ever changes, good."""
    assert [name for _, name in srd_only.names("backgrounds")] == ["Acolyte"]


def test_lookup_by_index(srd_only):
    wizard = srd_only.get("classes", "wizard")
    assert wizard["name"] == "Wizard"
    assert wizard["hit_die"] == 6


def test_an_unknown_index_is_none_not_an_error(srd_only):
    assert srd_only.get("classes", "artificer") is None


def test_subclasses_belong_to_their_class(srd_only):
    names = {s["index"] for s in srd_only.subclasses_of("wizard")}
    assert "evocation" in names
    assert not names & {s["index"] for s in srd_only.subclasses_of("cleric")}


def test_subspecies_belong_to_their_species(srd_only):
    assert {s["index"] for s in srd_only.subspecies_of("elf")} == {"high-elf"}
    assert srd_only.subspecies_of("human") == ()


def test_spells_can_be_filtered_by_class_and_level(srd_only):
    wizard_first = srd_only.spells_for("wizard", level=1)
    assert wizard_first, "a wizard has first-level spells"
    assert all(s["level"] == 1 for s in wizard_first)
    assert any(s["index"] == "magic-missile" for s in wizard_first)
    assert not any(s["index"] == "cure-wounds" for s in wizard_first)


def test_level_rows_carry_the_progression(srd_only):
    row = srd_only.level_row("wizard", 5)
    assert row["prof_bonus"] == 3
    assert row["spellcasting"]["spell_slots_level_3"] == 2


def test_an_unknown_collection_is_refused():
    with pytest.raises(KeyError):
        srd.load("hats")


# ------------------------------------------------------------------- homebrew


def test_a_campaign_can_add_a_background(content):
    """The gap that made this layer necessary."""
    content.add_homebrew(
        "backgrounds", {"index": "sailor", "name": "Sailor", "desc": "Salt and rope."}
    )

    names = {name for _, name in content.names("backgrounds")}
    assert names == {"Acolyte", "Sailor"}
    assert content.get("backgrounds", "sailor")["name"] == "Sailor"


def test_homebrew_is_marked_as_such(content):
    content.add_homebrew("backgrounds", {"index": "sailor", "name": "Sailor"})

    assert content.is_homebrew("backgrounds", "sailor") is True
    assert content.is_homebrew("backgrounds", "acolyte") is False


def test_a_campaign_can_replace_an_srd_entry(content):
    """Their Acolyte, not the book's, without editing the bundled files."""
    content.add_homebrew(
        "backgrounds", {"index": "acolyte", "name": "Acolyte of the Raven"}
    )

    assert content.get("backgrounds", "acolyte")["name"] == "Acolyte of the Raven"
    assert len(content.backgrounds()) == 1, "replaced, not added alongside"


def test_homebrew_survives_a_new_content_object(repos):
    Content(repos.settings).add_homebrew(
        "backgrounds", {"index": "sailor", "name": "Sailor"}
    )
    assert Content(repos.settings).get("backgrounds", "sailor") is not None


def test_adding_the_same_index_twice_replaces_it(content):
    content.add_homebrew("backgrounds", {"index": "sailor", "name": "Sailor"})
    content.add_homebrew("backgrounds", {"index": "sailor", "name": "Old Salt"})

    assert content.get("backgrounds", "sailor")["name"] == "Old Salt"
    assert len(content.backgrounds()) == 2


def test_homebrew_can_be_removed(content):
    content.add_homebrew("backgrounds", {"index": "sailor", "name": "Sailor"})
    content.remove_homebrew("backgrounds", "sailor")

    assert content.get("backgrounds", "sailor") is None
    assert content.get("backgrounds", "acolyte") is not None, "the SRD is untouched"


def test_content_without_a_campaign_cannot_be_written_to(srd_only):
    with pytest.raises(RuntimeError):
        srd_only.add_homebrew("backgrounds", {"index": "sailor", "name": "Sailor"})


def test_content_needs_an_index(content):
    with pytest.raises(ValueError, match="index"):
        content.add_homebrew("backgrounds", {"name": "Nameless"})


def test_rubbish_in_the_setting_does_not_break_lookups(repos):
    repos.settings.set("content.backgrounds", "not a list at all")
    assert len(Content(repos.settings).backgrounds()) == 1


def test_a_campaign_can_add_a_class(content):
    """Not just backgrounds -- every collection goes through the same door."""
    content.add_homebrew(
        "classes", {"index": "artificer", "name": "Artificer", "hit_die": 8}
    )
    assert content.get("classes", "artificer")["hit_die"] == 8
    assert len(content.classes()) == 13
