"""Sides in a fight.

The app used to guess: player characters together, everything else against
them. That is right almost always and wrong exactly when it is interesting --
the captured guard who fights beside the party, the rival adventurers who are
not monsters, the summoned thing that answers to nobody.

So the guess is still the starting point, but it is now *written down* the first
time anybody asks. A guess cannot be corrected; a row can.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.encounters import HOSTILE, PARTY
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity


@pytest.fixture
def fight(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Sides")
    hero = repos.entities.create(
        Entity(
            id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok",
            data={"hp": 20, "max_hp": 20, "sheet": {"schema": 1, "level": 3}},
        )
    )
    guard = repos.entities.create(
        Entity(
            id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar",
            data={"hp": 9, "max_hp": 9, "sheet": {"schema": 1, "level": 1}},
        )
    )
    goblin = repos.entities.create(
        Entity(
            id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik",
            data={"hp": 7, "max_hp": 7, "sheet": {"schema": 1, "level": 1}},
        )
    )

    encounter = repos.encounters.create(campaign.id, "The cave", width=12, height=12)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=20, x=0, y=0),
        "guard": repos.encounters.add(encounter.id, guard.id, initiative=12, x=1, y=0),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=8, x=2, y=0),
    }
    repos.encounters.begin(encounter.id)

    server = SessionServer(repos, campaign.id, "Sides session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, encounter, tokens, hero, guard, goblin
    server.stop()


def _team_of(repos, encounter, combatant_id) -> str:
    combatant = repos.encounters.combatant(combatant_id)
    for team in repos.encounters.teams(encounter.id):
        if team.id == combatant.team_id:
            return team.name
    return ""


# ------------------------------------------------------------------ the two


def test_a_fight_is_made_with_two_sides(repos):
    campaign = repos.campaigns.ensure_default("Sides")
    encounter = repos.encounters.create(campaign.id, "Anywhere")

    teams = repos.encounters.teams(encounter.id)
    assert [t.name for t in teams] == [PARTY, HOSTILE]
    assert teams[0].is_party is True


def test_nobody_has_to_set_them_up(fight):
    """Prefilled by the old guess, so a fight runs without being configured."""
    server, repos, encounter, tokens, _hero, _guard, _goblin = fight
    repos.encounters.sort_into_teams(encounter.id)

    assert _team_of(repos, encounter, tokens["hero"].id) == PARTY
    assert _team_of(repos, encounter, tokens["goblin"].id) == HOSTILE


def test_a_fight_from_before_teams_existed_gets_them(repos):
    """Every campaign already on disk is this case."""
    campaign = repos.campaigns.ensure_default("Sides")
    encounter = repos.encounters.create(campaign.id, "Old")
    for team in repos.encounters.teams(encounter.id):
        repos.encounters.remove_team(team.id)
    assert repos.encounters.teams(encounter.id) == []

    assert len(repos.encounters.ensure_teams(encounter.id)) == 2


# ------------------------------------------------------- changing your mind


def test_the_guard_can_be_moved_to_the_party(fight):
    """The case the old guess got wrong, and the reason any of this exists."""
    server, repos, encounter, tokens, _hero, _guard, _goblin = fight
    repos.encounters.sort_into_teams(encounter.id)
    assert _team_of(repos, encounter, tokens["guard"].id) == HOSTILE

    party = next(t for t in repos.encounters.teams(encounter.id) if t.is_party)
    repos.encounters.set_team(tokens["guard"].id, party.id)

    assert _team_of(repos, encounter, tokens["guard"].id) == PARTY


def test_sorting_does_not_overrule_a_decision(fight):
    """Once a DM has said, nothing re-guesses it -- not even the next publish."""
    server, repos, encounter, tokens, _hero, _guard, _goblin = fight
    party = next(t for t in repos.encounters.teams(encounter.id) if t.is_party)
    repos.encounters.set_team(tokens["guard"].id, party.id)

    server.publish_encounter()

    assert _team_of(repos, encounter, tokens["guard"].id) == PARTY


def test_a_dm_can_add_a_third(fight):
    server, repos, encounter, tokens, _hero, _guard, _goblin = fight
    third = repos.encounters.add_team(encounter.id, "The cult")
    repos.encounters.set_team(tokens["goblin"].id, third.id)

    assert _team_of(repos, encounter, tokens["goblin"].id) == "The cult"
    assert len(repos.encounters.teams(encounter.id)) == 3


def test_deleting_a_side_does_not_delete_who_was_on_it(fight):
    server, repos, encounter, tokens, _hero, _guard, _goblin = fight
    third = repos.encounters.add_team(encounter.id, "The cult")
    repos.encounters.set_team(tokens["goblin"].id, third.id)

    repos.encounters.remove_team(third.id)

    assert repos.encounters.combatant(tokens["goblin"].id) is not None
    assert repos.encounters.combatant(tokens["goblin"].id).team_id is None


# ---------------------------------------------------- what sides are good for


def test_an_ally_does_not_swing_at_you_for_walking_away(fight, monkeypatch):
    """The whole point. Moved onto the party's side, the guard is a friend."""
    import canon_keeper.net.server as server_module

    class _Always:
        def __call__(self, notation: str):
            class _Result:
                total = 19
                rolls = [19]

            return _Result()

    monkeypatch.setattr(server_module, "roll", _Always())
    server, repos, encounter, tokens, hero, _guard, _goblin = fight
    repos.encounters.sort_into_teams(encounter.id)
    party = next(t for t in repos.encounters.teams(encounter.id) if t.is_party)
    repos.encounters.set_team(tokens["guard"].id, party.id)
    # And put the goblin out of reach, so the guard is the only thing adjacent.
    repos.encounters.place(tokens["goblin"].id, 8, 8)
    before = repos.entities.get(hero.id).data["hp"]

    server._do_move(tokens["hero"].id, 0, 5, spending=True)

    assert repos.entities.get(hero.id).data["hp"] == before


def test_the_sides_reach_the_players(fight):
    """A party can see who it is fighting. That is not a secret worth keeping."""
    server, repos, encounter, _tokens, _hero, _guard, _goblin = fight
    repos.encounters.sort_into_teams(encounter.id)

    from canon_keeper.net.projection import Viewer, project_encounter

    sent = project_encounter(
        repos.encounters.get(encounter.id),
        repos.encounters.combatants(encounter.id),
        Viewer(account_id=1, is_dm=True),
        teams=repos.encounters.teams(encounter.id),
    )

    assert [t["name"] for t in sent["teams"]] == [PARTY, HOSTILE]
    assert all(c["team"] is not None for c in sent["combatants"])
