"""Entity name matching — what gets highlighted in the transcript."""

from __future__ import annotations

from canon_keeper.matching import EntityMatcher
from canon_keeper_core.repo.entities import KIND_LOCATION, KIND_NPC, Entity


def _make(repos, campaign_id, name, kind=KIND_NPC, aliases=None):
    return repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign_id,
            kind=kind,
            name=name,
            aliases=aliases or [],
        )
    )


def test_finds_a_name_and_reports_its_span(repos):
    campaign = repos.campaigns.ensure_default()
    sildar = _make(repos, campaign.id, "Sildar Hallwinter")

    text = "Then Sildar Hallwinter draws his sword."
    matches = list(EntityMatcher.from_repos(repos, campaign.id).finditer(text))

    assert len(matches) == 1
    assert text[matches[0].start : matches[0].end] == "Sildar Hallwinter"
    assert matches[0].entity_id == sildar.id
    assert matches[0].kind == KIND_NPC


def test_matching_is_case_insensitive(repos):
    campaign = repos.campaigns.ensure_default()
    _make(repos, campaign.id, "Cragmaw Castle", KIND_LOCATION)

    matcher = EntityMatcher.from_repos(repos, campaign.id)
    assert len(list(matcher.finditer("they reach cragmaw castle at dusk"))) == 1


def test_longest_name_wins(repos):
    """With both on file, 'Cragmaw Castle' must not be reported as 'Cragmaw'."""
    campaign = repos.campaigns.ensure_default()
    _make(repos, campaign.id, "Cragmaw", KIND_NPC)
    castle = _make(repos, campaign.id, "Cragmaw Castle", KIND_LOCATION)

    matches = list(
        EntityMatcher.from_repos(repos, campaign.id).finditer("they reach Cragmaw Castle")
    )
    assert len(matches) == 1
    assert matches[0].entity_id == castle.id
    assert matches[0].text == "Cragmaw Castle"


def test_partial_words_are_not_matched(repos):
    campaign = repos.campaigns.ensure_default()
    _make(repos, campaign.id, "Gundren")

    matcher = EntityMatcher.from_repos(repos, campaign.id)
    assert list(matcher.finditer("Gundrenson was here")) == []
    assert list(matcher.finditer("the Gundren, finally")) != []


def test_aliases_resolve_to_their_entity(repos):
    campaign = repos.campaigns.ensure_default()
    castle = _make(
        repos, campaign.id, "Cragmaw Castle", KIND_LOCATION, aliases=["the Castle"]
    )

    matches = list(
        EntityMatcher.from_repos(repos, campaign.id).finditer("back at the Castle")
    )
    assert matches[0].entity_id == castle.id
    assert matches[0].text == "the Castle"
    assert matches[0].name == "Cragmaw Castle", "reports the canonical name"


def test_short_and_placeholder_names_are_ignored(repos):
    campaign = repos.campaigns.ensure_default()
    _make(repos, campaign.id, "Al")
    _make(repos, campaign.id, "New character")

    matcher = EntityMatcher.from_repos(repos, campaign.id)
    assert not matcher, "nothing worth matching should build no pattern"
    assert list(matcher.finditer("Al met a New character")) == []


def test_names_with_punctuation_do_not_break_the_pattern(repos):
    """A name is user input, so it can contain regex metacharacters."""
    campaign = repos.campaigns.ensure_default()
    _make(repos, campaign.id, "K'thriss (the Warlock)")

    matches = list(
        EntityMatcher.from_repos(repos, campaign.id).finditer(
            "K'thriss (the Warlock) nods"
        )
    )
    assert len(matches) == 1


def test_lookup_resolves_an_exact_selection(repos):
    campaign = repos.campaigns.ensure_default()
    castle = _make(repos, campaign.id, "Cragmaw Castle", KIND_LOCATION, aliases=["the Castle"])

    matcher = EntityMatcher.from_repos(repos, campaign.id)
    assert matcher.lookup("cragmaw castle").entity_id == castle.id
    assert matcher.lookup("  the Castle  ").entity_id == castle.id
    assert matcher.lookup("Neverwinter") is None


def test_empty_campaign_matches_nothing(repos):
    campaign = repos.campaigns.ensure_default()
    matcher = EntityMatcher.from_repos(repos, campaign.id)
    assert not matcher
    assert list(matcher.finditer("anything at all")) == []
