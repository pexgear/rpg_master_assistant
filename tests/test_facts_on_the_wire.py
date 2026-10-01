"""The canon log crosses the wire to a DM, and to nobody else.

An agent on autopilot answers from these, so they have to travel. They are also
the single most spoiler-dense table in the app -- half of it is things the party
has not worked out, and which entity a fact hangs off gives away the rest. So
the rule is not "filter it for players", it is "players do not get it".
"""

from __future__ import annotations

from canon_keeper.net.projection import Viewer, project_facts
from canon_keeper_core.repo.entities import KIND_NPC, Entity
from canon_keeper_protocol import MessageType


def _npc(repos, campaign_id: int, name: str) -> int:
    return repos.entities.create(
        Entity(id=None, campaign_id=campaign_id, kind=KIND_NPC, name=name)
    ).id


# ------------------------------------------------------------------ who gets it


def test_the_dm_gets_the_canon(ctx):
    subject = _npc(ctx.repos, ctx.campaign_id, "Sildar Hallwinter")
    ctx.repos.facts.assert_fact(
        ctx.campaign_id, subject, "works for", "the Lords' Alliance"
    )

    facts = project_facts(ctx.repos, ctx.campaign_id, Viewer.dungeon_master())

    assert len(facts) == 1
    assert facts[0]["predicate"] == "works for"
    assert facts[0]["object"] == "the Lords' Alliance"
    assert facts[0]["subject"] == subject


def test_a_player_gets_nothing(ctx):
    """Not a redacted list. Nothing."""
    subject = _npc(ctx.repos, ctx.campaign_id, "Sildar Hallwinter")
    ctx.repos.facts.assert_fact(ctx.campaign_id, subject, "is secretly", "a Zhent")

    player = Viewer(account_id=7, is_dm=False, owned_entity_ids={subject})

    assert project_facts(ctx.repos, ctx.campaign_id, player) == []


def test_owning_the_character_does_not_earn_you_its_facts(ctx):
    """The DM's notes about a character are the DM's, whoever plays it."""
    pc = _npc(ctx.repos, ctx.campaign_id, "Mirt")
    ctx.repos.facts.assert_fact(ctx.campaign_id, pc, "is marked by", "the Xanathar")

    owner = Viewer(account_id=3, is_dm=False, owned_entity_ids={pc})

    assert project_facts(ctx.repos, ctx.campaign_id, owner) == []


# --------------------------------------------------------------- what it holds


def test_superseded_facts_do_not_travel(ctx):
    """What the DM changed their mind about is not part of what is true."""
    subject = _npc(ctx.repos, ctx.campaign_id, "Sildar")
    ctx.repos.facts.assert_fact(ctx.campaign_id, subject, "status", "captured")
    ctx.repos.facts.assert_fact(ctx.campaign_id, subject, "status", "rescued")

    facts = project_facts(ctx.repos, ctx.campaign_id, Viewer.dungeon_master())

    assert [f["object"] for f in facts] == ["rescued"]


def test_an_empty_campaign_sends_an_empty_list(ctx):
    assert project_facts(ctx.repos, ctx.campaign_id, Viewer.dungeon_master()) == []


def test_facts_about_several_entities_all_come(ctx):
    a = _npc(ctx.repos, ctx.campaign_id, "Sildar")
    b = _npc(ctx.repos, ctx.campaign_id, "Iarno")
    ctx.repos.facts.assert_fact(ctx.campaign_id, a, "is in", "Phandalin")
    ctx.repos.facts.assert_fact(ctx.campaign_id, b, "is in", "Tresendar Manor")

    facts = project_facts(ctx.repos, ctx.campaign_id, Viewer.dungeon_master())

    assert {f["subject"] for f in facts} == {a, b}


# ------------------------------------------------------------------ the message


def test_the_message_type_exists():
    assert MessageType.FACTS == "facts"
