"""Schema, migrations, and the supersession rule the canon log depends on."""

from __future__ import annotations

from canon_keeper_core.db import connect, current_version, migrate
from canon_keeper_core.repo import Repos
from canon_keeper_core.repo.entities import KIND_LOCATION, KIND_NPC, Entity


def test_migrations_apply_from_empty(tmp_path):
    conn = connect(tmp_path / "fresh.sqlite3")
    assert current_version(conn) == 0
    version = migrate(conn)
    assert version >= 1
    assert current_version(conn) == version

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"campaign", "entity", "fact", "proposal", "app_layout"} <= tables


def test_migrations_are_idempotent(tmp_path):
    conn = connect(tmp_path / "twice.sqlite3")
    first = migrate(conn)
    # Running again must be a no-op rather than re-executing the CREATE TABLEs.
    assert migrate(conn) == first


def test_entity_slugs_stay_unique(repos: Repos):
    campaign = repos.campaigns.ensure_default()
    a = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar")
    )
    b = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar")
    )
    assert a.slug == "sildar"
    assert b.slug == "sildar-2"


def test_occupants_follow_parent_id(repos: Repos):
    campaign = repos.campaigns.ensure_default()
    ward = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_LOCATION, name="Dock Ward")
    )
    npc = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Sildar",
            parent_id=ward.id,
        )
    )
    occupants = repos.entities.occupants(ward.id)
    assert [o.id for o in occupants] == [npc.id]

    # A place nested inside another is not listed as one of its occupants.
    repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_LOCATION,
            name="The Sleeping Giant",
            parent_id=ward.id,
        )
    )
    assert [o.id for o in repos.entities.occupants(ward.id)] == [npc.id]


def test_contradicting_a_fact_supersedes_it(repos: Repos):
    campaign = repos.campaigns.ensure_default()
    sildar = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar")
    )

    alive = repos.facts.assert_fact(campaign.id, sildar.id, "status", "alive")
    dead = repos.facts.assert_fact(campaign.id, sildar.id, "status", "dead")

    current = repos.facts.current(campaign.id, sildar.id)
    assert len(current) == 1, "exactly one fact per predicate may be current"
    assert current[0].id == dead.id
    assert current[0].object == "dead"

    # The old row is superseded, never deleted: the history survives.
    history = repos.facts.history(campaign.id, sildar.id)
    assert {f.id for f in history} == {alive.id, dead.id}
    superseded = next(f for f in history if f.id == alive.id)
    assert superseded.superseded_by == dead.id


def test_multi_valued_predicates_do_not_supersede(repos: Repos):
    campaign = repos.campaigns.ensure_default()
    party = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="The party")
    )
    repos.facts.assert_fact(campaign.id, party.id, "knows", "Iarno is Glasstaff", supersede=False)
    repos.facts.assert_fact(campaign.id, party.id, "knows", "Cragmaw holds Gundren", supersede=False)

    assert len(repos.facts.current(campaign.id, party.id)) == 2


def test_facts_of_one_entity_do_not_supersede_another(repos: Repos):
    campaign = repos.campaigns.ensure_default()
    a = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar")
    )
    b = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Gundren")
    )
    repos.facts.assert_fact(campaign.id, a.id, "status", "alive")
    repos.facts.assert_fact(campaign.id, b.id, "status", "captured")

    assert repos.facts.current(campaign.id, a.id)[0].object == "alive"
    assert repos.facts.current(campaign.id, b.id)[0].object == "captured"
