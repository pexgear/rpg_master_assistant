"""The approval queue, reconnect sync, and gear on the sheet."""

from __future__ import annotations

import pytest

from canon_keeper.net.cache import forget, load, save, versions
from canon_keeper.net.projection import Viewer, changed_sheet_fields, snapshot_since
from canon_keeper.net.server import describe_changes
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper_core.rules.sheet import new_sheet


@pytest.fixture
def campaign(repos):
    return repos.campaigns.ensure_default("Test Campaign")


@pytest.fixture
def marco(repos, campaign):
    return repos.accounts.create(campaign.id, "marco", "goblin-teeth", display_name="Marco")


@pytest.fixture
def elara(repos, campaign, marco):
    entity = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Elara",
            data={"sheet": new_sheet(class_index="wizard", level=5)},
        )
    )
    repos.entities.set_owner(entity.id, marco.id)
    return repos.entities.get(entity.id)


@pytest.fixture
def viewer(repos, marco):
    return Viewer(
        account_id=marco.id,
        is_dm=False,
        owned_entity_ids=repos.entities.owned_ids(marco.id),
    )


# --------------------------------------------------------- splitting an edit


def test_only_changed_fields_are_reported():
    """A client sends the whole sheet back; unchanged fields are not a request.

    Without comparing, every save would ask the DM to approve forty fields
    being set to what they already are.
    """
    existing = {"level": 5, "hp_current": 20, "class_index": "wizard"}
    proposed = {"level": 5, "hp_current": 12, "class_index": "wizard"}

    assert changed_sheet_fields(existing, proposed) == {"hp_current": 12}


def test_hit_points_are_a_request_like_anything_else():
    existing = {"level": 5, "hp_current": 20}
    proposed = {"level": 6, "hp_current": 12}

    assert changed_sheet_fields(existing, proposed) == {"level": 6, "hp_current": 12}


def test_unknown_fields_are_dropped():
    """An older or modified client cannot smuggle in a key we do not know."""
    assert changed_sheet_fields({}, {"mystery": 1, "hp_current": 3}) == {
        "hp_current": 3
    }


def test_changes_are_described_readably():
    assert describe_changes({"level": 6}) == "level to 6"
    assert "class" in describe_changes({"class_index": "bard"})


# ------------------------------------------------------------------ proposals


def test_a_proposal_is_recorded(repos, campaign, elara, marco):
    proposal = repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)

    assert proposal.is_open
    assert repos.proposals.open_count(campaign.id) == 1
    assert repos.proposals.open_for(campaign.id)[0].changes == {"level": 6}


def test_a_second_proposal_supersedes_the_first(repos, campaign, elara, marco):
    """Fiddling with a level must not leave the DM five things to answer."""
    repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)
    repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 7}, 1)

    open_now = repos.proposals.open_for(campaign.id)
    assert len(open_now) == 1
    assert open_now[0].changes == {"level": 7}


def test_proposals_for_different_characters_coexist(repos, campaign, elara, marco):
    other = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brakk")
    )
    repos.entities.set_owner(other.id, marco.id)

    repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)
    repos.proposals.propose(campaign.id, other.id, marco.id, {"level": 2}, 1)

    assert repos.proposals.open_count(campaign.id) == 2


def test_deciding_closes_it(repos, campaign, elara, marco):
    proposal = repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)
    repos.proposals.decide(proposal.id, "approved")

    assert repos.proposals.open_count(campaign.id) == 0
    assert repos.proposals.get(proposal.id).status == "approved"


def test_only_the_proposed_fields_are_applied(repos, campaign, elara, marco):
    """Storing a whole sheet would reinstate whatever the DM changed meanwhile."""
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    proposal = repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)

    # The DM edits something else before answering.
    meanwhile = repos.entities.get(elara.id)
    meanwhile.data["sheet"]["species"] = "elf"
    repos.entities.update(meanwhile)

    assert server.decide(proposal.id, approve=True)

    sheet = repos.entities.get(elara.id).data["sheet"]
    assert sheet["level"] == 6, "the proposal applied"
    assert sheet["species"] == "elf", "the DM's own edit survived"


def test_refusing_changes_nothing(repos, campaign, elara, marco):
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    proposal = repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)

    server.decide(proposal.id, approve=False)

    assert repos.entities.get(elara.id).data["sheet"]["level"] == 5
    assert repos.proposals.get(proposal.id).status == "rejected"


def test_a_proposal_against_an_old_version_is_flagged_not_hidden(
    repos, campaign, elara, marco
):
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, 1)

    moved_on = repos.entities.get(elara.id)
    moved_on.summary = "changed"
    repos.entities.update(moved_on)

    [described] = server.proposals
    assert described["stale"] is True
    assert described["character"] == "Elara"
    assert described["who"] == "Marco"


# ------------------------------------------------------------- reconnect sync


def test_nothing_is_resent_when_nothing_changed(repos, campaign, elara, viewer):
    known = {elara.id: repos.entities.get(elara.id).version}

    changed, gone = snapshot_since(repos, campaign.id, viewer, known)

    assert changed == []
    assert gone == []


def test_only_what_moved_comes_back(repos, campaign, elara, viewer, marco):
    other = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar")
    )
    repos.shares.share(campaign.id, other.id)
    known = {
        elara.id: repos.entities.get(elara.id).version,
        other.id: repos.entities.get(other.id).version,
    }

    touched = repos.entities.get(other.id)
    touched.summary = "changed"
    repos.entities.update(touched)

    changed, gone = snapshot_since(repos, campaign.id, viewer, known)

    assert [e["id"] for e in changed] == [other.id]
    assert gone == []


def test_a_revoked_share_comes_back_as_gone(repos, campaign, elara, viewer):
    """Silence would leave a stale copy sitting on their screen."""
    npc = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar")
    )
    repos.shares.share(campaign.id, npc.id)
    known = {elara.id: 1, npc.id: 1}

    repos.shares.unshare_all(npc.id)
    _changed, gone = snapshot_since(repos, campaign.id, viewer, known)

    assert gone == [npc.id]


def test_an_unknown_client_gets_everything(repos, campaign, elara, viewer):
    changed, _gone = snapshot_since(repos, campaign.id, viewer, {})
    assert [e["id"] for e in changed] == [elara.id]


# -------------------------------------------------------------- the disk cache


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CANONKEEPER_DATA_DIR", str(tmp_path))


def test_the_cache_round_trips():
    entities = {1: {"id": 1, "name": "Elara", "version": 3}}
    save("ws://host:8765", "marco", entities, campaign_key="abc")

    held, key = load("ws://host:8765", "marco")
    assert held == entities
    assert key == "abc"


def test_two_logins_on_one_machine_do_not_mix():
    save("ws://host:8765", "marco", {1: {"id": 1, "name": "Elara", "version": 1}})
    save("ws://host:8765", "elsa", {2: {"id": 2, "name": "Brakk", "version": 1}})

    assert list(load("ws://host:8765", "marco")[0]) == [1]
    assert list(load("ws://host:8765", "elsa")[0]) == [2]


def test_two_campaigns_do_not_mix():
    save("ws://a:8765", "marco", {1: {"id": 1, "version": 1}})
    save("ws://b:8765", "marco", {2: {"id": 2, "version": 1}})

    assert list(load("ws://a:8765", "marco")[0]) == [1]


def test_the_cache_filename_reveals_nothing():
    from canon_keeper.net import cache

    name = cache._key("ws://192.168.1.10:8765", "marco")
    assert "marco" not in name
    assert "192.168" not in name


def test_a_corrupt_cache_is_survivable():
    from canon_keeper.net import cache

    path = cache._path("ws://host:8765", "marco")
    path.write_text("{ not json", encoding="utf-8")

    assert load("ws://host:8765", "marco") == ({}, "")


def test_forgetting_removes_it():
    save("ws://host:8765", "marco", {1: {"id": 1, "version": 1}})
    forget("ws://host:8765", "marco")
    assert load("ws://host:8765", "marco") == ({}, "")


def test_versions_are_string_keyed_for_the_wire():
    """JSON object keys are always strings; being explicit avoids a subtle bug."""
    assert versions({1: {"version": 4}}) == {"1": 4}


def test_entities_without_a_version_are_not_claimed():
    assert versions({1: {"name": "no version here"}}) == {}


def test_a_cache_from_another_campaign_is_not_believed(repos, campaign):
    """The bug behind two apps showing different characters under one name.

    Entity ids and versions both restart in a new campaign, so a cache from a
    different one looks perfectly current. The host must not take the client's
    word for what it holds without knowing which game it came from.
    """
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)

    assert server._trusted_versions({"known": {"1": 2}, "campaign": "someone-else"}) == {}
    assert server._trusted_versions(
        {"known": {"1": 2}, "campaign": server.campaign_key}
    ) == {1: 2}
    assert server._trusted_versions({"known": {"1": 2}}) == {}, "no key means no trust"


def test_a_campaign_keeps_its_key(repos, campaign):
    from canon_keeper.net.server import SessionServer

    first = SessionServer(repos, campaign.id).campaign_key
    assert SessionServer(repos, campaign.id).campaign_key == first
    assert len(first) >= 16


def test_two_campaigns_get_different_keys(repos, campaign, tmp_path):
    from canon_keeper_core.db import connect, migrate
    from canon_keeper.net.server import SessionServer
    from canon_keeper_core.repo import Repos

    other_conn = connect(tmp_path / "other.sqlite3")
    migrate(other_conn)
    other = Repos(other_conn)
    other_campaign = other.campaigns.ensure_default("Other")

    assert (
        SessionServer(repos, campaign.id).campaign_key
        != SessionServer(other, other_campaign.id).campaign_key
    )


# -------------------------------------------------- conflicts refuse themselves


def test_a_dm_change_refuses_what_was_proposed_against_the_old_sheet(
    repos, campaign, elara, marco
):
    """Approving it would apply a decision made about a different character."""
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    version = repos.entities.get(elara.id).version
    repos.proposals.propose(campaign.id, elara.id, marco.id, {"level": 6}, version)

    changed = repos.entities.get(elara.id)
    changed.data["sheet"]["species"] = "elf"
    repos.entities.update(changed)

    refused = server.refuse_conflicting(elara.id)

    assert refused == 1
    assert repos.proposals.open_count(campaign.id) == 0
    assert repos.entities.get(elara.id).data["sheet"]["level"] == 5


def test_an_unaffected_proposal_survives_a_dm_change_elsewhere(
    repos, campaign, elara, marco
):
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    other = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brakk")
    )
    repos.entities.set_owner(other.id, marco.id)
    repos.proposals.propose(
        campaign.id, elara.id, marco.id, {"level": 6},
        repos.entities.get(elara.id).version,
    )

    touched = repos.entities.get(other.id)
    touched.summary = "changed"
    repos.entities.update(touched)
    server.refuse_conflicting(other.id)

    assert repos.proposals.open_count(campaign.id) == 1, "a different character"


def test_a_proposal_still_matching_the_sheet_is_left_alone(repos, campaign, elara, marco):
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    repos.proposals.propose(
        campaign.id, elara.id, marco.id, {"level": 6},
        repos.entities.get(elara.id).version,
    )

    assert server.refuse_conflicting(elara.id) == 0
    assert repos.proposals.open_count(campaign.id) == 1


# ------------------------------------------------------------------ the chat log


def test_what_is_said_is_kept(repos, campaign):
    from canon_keeper_core.repo.chat import SAID

    repos.chat.add(campaign.id, SAID, "I check the door", speaker="Elara", role="player")

    [kept] = repos.chat.recent(campaign.id)
    assert kept.text == "I check the door"
    assert kept.speaker == "Elara"


def test_the_log_reads_in_order(repos, campaign):
    from canon_keeper_core.repo.chat import SAID

    for line in ("first", "second", "third"):
        repos.chat.add(campaign.id, SAID, line)

    assert [m.text for m in repos.chat.recent(campaign.id)] == [
        "first",
        "second",
        "third",
    ]


def test_only_the_tail_is_handed_out(repos, campaign):
    """Everything is kept; nobody rejoining wants to scroll through last month."""
    from canon_keeper_core.repo.chat import SAID

    for n in range(250):
        repos.chat.add(campaign.id, SAID, f"line {n}")

    recent = repos.chat.recent(campaign.id, limit=100)

    assert len(recent) == 100
    assert recent[0].text == "line 150", "the tail, not the beginning"
    assert recent[-1].text == "line 249"
    assert repos.chat.count(campaign.id) == 250, "nothing was discarded"


def test_each_evening_is_its_own_log(repos, campaign):
    from canon_keeper_core.repo.chat import SAID

    first = repos.sessions.start(campaign.id, "Session one")
    repos.chat.add(campaign.id, SAID, "last week", session_id=first.id)
    repos.sessions.end(first.id)

    second = repos.sessions.start(campaign.id, "Session two")
    repos.chat.add(campaign.id, SAID, "tonight", session_id=second.id)

    assert [m.text for m in repos.chat.for_session(first.id)] == ["last week"]
    assert [m.text for m in repos.chat.for_session(second.id)] == ["tonight"]
    assert len(repos.chat.recent(campaign.id)) == 2, "the tail spans sessions"


def test_the_speaker_is_a_copy_not_a_reference(repos, campaign, elara, marco):
    """A log should still read correctly after a character is renamed."""
    from canon_keeper_core.repo.chat import SAID

    repos.chat.add(campaign.id, SAID, "hello", speaker="Elara")

    renamed = repos.entities.get(elara.id)
    renamed.name = "Someone Else"
    repos.entities.update(renamed)

    assert repos.chat.recent(campaign.id)[0].speaker == "Elara"


def test_a_roll_keeps_its_detail(repos, campaign):
    from canon_keeper_core.repo.chat import ROLLED

    repos.chat.add(
        campaign.id, ROLLED, "2d6+3 = [4, 6] +3 = 13",
        speaker="Marco", payload={"total": 13, "rolls": [4, 6]},
    )

    assert repos.chat.recent(campaign.id)[0].payload["total"] == 13


def test_the_server_records_and_serves_history(repos, campaign, marco):
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)
    server._record("said", "I check the door", speaker="Elara", role="player")

    history = server.history()

    assert history[-1]["text"] == "I check the door"
    assert history[-1]["speaker"] == "Elara"
    assert history[-1]["at"] > 0


def test_a_broken_log_never_stops_the_game(repos, campaign, monkeypatch):
    """Hosting must not fail because a log write did."""
    from canon_keeper.net.server import SessionServer

    server = SessionServer(repos, campaign.id)

    def explode(*_a, **_k):
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(repos.chat, "add", explode)
    monkeypatch.setattr(repos.chat, "recent", explode)

    server._record("said", "still fine")  # must not raise
    assert server.history() == []
