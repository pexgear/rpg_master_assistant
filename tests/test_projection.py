"""Accounts, sharing, and what a player is actually allowed to see.

These are the tests that matter most in the project. Everything else being wrong
costs an evening; this being wrong spoils the campaign.
"""

from __future__ import annotations

import pytest

from canon_keeper_protocol import auth
from canon_keeper.net.projection import (
    EditRefused,
    Viewer,
    apply_player_edit,
    project_entity,
    snapshot,
    visible_entity_ids,
)
from canon_keeper_core.repo.entities import KIND_LOCATION, KIND_NPC, KIND_PC, Entity


@pytest.fixture
def campaign(repos):
    return repos.campaigns.ensure_default("Test Campaign")


@pytest.fixture
def world(repos, campaign):
    """A small campaign: two PCs, a secret-laden NPC, and two places."""
    made = {}
    made["marco_pc"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Elara",
            summary="A tired cleric",
            data={"status": "alive", "hp": 14, "max_hp": 22, "secrets": "is a spy"},
        )
    )
    made["elsa_pc"] = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brakk")
    )
    made["sildar"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Sildar Hallwinter",
            summary="A weary man of the Alliance",
            data={
                "status": "alive",
                "motive": "Find Iarno before the others do",
                "secrets": "He is Iarno's brother",
                "voice": "clipped, formal",
                "party_knows": "He hired them in Neverwinter",
            },
        )
    )
    made["hidden_npc"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="The Black Spider",
            data={"secrets": "is a drow"},
        )
    )
    made["city"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_LOCATION,
            name="Phandalin",
            data={"place_type": "town", "notes": "DM notes", "rumours": "a hook"},
        )
    )
    made["hideout"] = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_LOCATION,
            name="Cragmaw Hideout",
            parent_id=made["city"].id,
            data={"place_type": "building", "notes": "the ambush"},
        )
    )
    return made


@pytest.fixture
def marco(repos, campaign, world):
    account = repos.accounts.create(
        campaign.id,
        "marco",
        "goblin-teeth",
        display_name="Marco",
        character_entity_id=world["marco_pc"].id,
    )
    repos.entities.set_owner(world["marco_pc"].id, account.id)
    return account


@pytest.fixture
def elsa(repos, campaign, world):
    account = repos.accounts.create(
        campaign.id, "elsa", "silver-moon", character_entity_id=world["elsa_pc"].id
    )
    repos.entities.set_owner(world["elsa_pc"].id, account.id)
    return account


@pytest.fixture
def viewer_for(repos):
    def build(account) -> Viewer:
        return Viewer(
            account_id=account.id,
            is_dm=account.is_dm,
            owned_entity_ids=repos.entities.owned_ids(account.id),
        )

    return build


# ------------------------------------------------------------------- accounts


def test_password_is_never_stored(repos, campaign):
    account = repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    assert b"goblin-teeth" not in account.verifier
    assert b"goblin-teeth" not in account.salt
    assert account.verifier != b""


def test_login_succeeds_without_sending_the_password(repos, campaign):
    repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    stored = repos.accounts.by_username(campaign.id, "marco")

    # What the client does: derive locally, prove against the server's nonce.
    nonce = auth.new_nonce()
    client_verifier = auth.derive_verifier("goblin-teeth", stored.salt)
    offered = auth.proof(client_verifier, nonce)

    assert repos.accounts.authenticate(campaign.id, "marco", nonce, offered) is not None


def test_the_wrong_password_is_refused(repos, campaign):
    repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    stored = repos.accounts.by_username(campaign.id, "marco")
    nonce = auth.new_nonce()
    offered = auth.proof(auth.derive_verifier("wrong", stored.salt), nonce)

    assert repos.accounts.authenticate(campaign.id, "marco", nonce, offered) is None


def test_a_proof_cannot_be_replayed_against_a_new_nonce(repos, campaign):
    """An eavesdropper on the LAN must not be able to reuse what they heard."""
    repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    stored = repos.accounts.by_username(campaign.id, "marco")
    captured = auth.proof(auth.derive_verifier("goblin-teeth", stored.salt), auth.new_nonce())

    assert repos.accounts.authenticate(campaign.id, "marco", auth.new_nonce(), captured) is None


def test_usernames_are_case_insensitive_and_unique(repos, campaign):
    repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    assert repos.accounts.by_username(campaign.id, "MARCO") is not None
    with pytest.raises(ValueError, match="taken"):
        repos.accounts.create(campaign.id, "Marco", "another")


def test_a_disabled_account_cannot_log_in(repos, campaign):
    account = repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    repos.accounts.set_disabled(account.id, True)
    stored = repos.accounts.by_username(campaign.id, "marco")
    nonce = auth.new_nonce()
    offered = auth.proof(auth.derive_verifier("goblin-teeth", stored.salt), nonce)

    assert repos.accounts.authenticate(campaign.id, "marco", nonce, offered) is None


def test_failures_are_indistinguishable(repos, campaign):
    """The login screen must not reveal who plays in the campaign."""
    assert auth.explain("no such user") == auth.explain("wrong password")


# ------------------------------------------------------------------- sharing


def test_a_player_sees_nothing_until_something_is_shared(repos, campaign, world, marco, viewer_for):
    visible = visible_entity_ids(repos, campaign.id, viewer_for(marco))
    # Their own character, and nothing else.
    assert visible == {world["marco_pc"].id}


def test_sharing_with_the_party_reaches_every_player(repos, campaign, world, marco, elsa, viewer_for):
    repos.shares.share(campaign.id, world["sildar"].id)

    assert world["sildar"].id in visible_entity_ids(repos, campaign.id, viewer_for(marco))
    assert world["sildar"].id in visible_entity_ids(repos, campaign.id, viewer_for(elsa))


def test_sharing_with_one_player_excludes_the_others(repos, campaign, world, marco, elsa, viewer_for):
    """Only the rogue knows about the contact."""
    repos.shares.share(campaign.id, world["hidden_npc"].id, marco.id)

    assert world["hidden_npc"].id in visible_entity_ids(repos, campaign.id, viewer_for(marco))
    assert world["hidden_npc"].id not in visible_entity_ids(repos, campaign.id, viewer_for(elsa))


def test_unsharing_takes_it_back(repos, campaign, world, marco, viewer_for):
    repos.shares.share(campaign.id, world["sildar"].id)
    repos.shares.unshare_all(world["sildar"].id)

    assert world["sildar"].id not in visible_entity_ids(repos, campaign.id, viewer_for(marco))


def test_the_dm_sees_everything(repos, campaign, world):
    visible = visible_entity_ids(repos, campaign.id, Viewer.dungeon_master())
    assert visible == {e.id for e in world.values()}


def test_sharing_is_idempotent(repos, campaign, world):
    repos.shares.share(campaign.id, world["sildar"].id)
    repos.shares.share(campaign.id, world["sildar"].id)
    assert repos.shares.audiences(world["sildar"].id) == (True, set())


def test_set_audiences_replaces_the_whole_set(repos, campaign, world, marco, elsa):
    repos.shares.share(campaign.id, world["sildar"].id)
    repos.shares.set_audiences(campaign.id, world["sildar"].id, False, {elsa.id})

    party, accounts = repos.shares.audiences(world["sildar"].id)
    assert party is False
    assert accounts == {elsa.id}


# ---------------------------------------------------------------- projection


def test_dm_secrets_never_reach_a_player(repos, campaign, world, marco, viewer_for):
    repos.shares.share(campaign.id, world["sildar"].id)
    [projected] = [
        e for e in snapshot(repos, campaign.id, viewer_for(marco)) if e["id"] == world["sildar"].id
    ]

    assert projected["name"] == "Sildar Hallwinter"
    assert projected["data"].get("party_knows") == "He hired them in Neverwinter"
    for forbidden in ("secrets", "motive", "voice"):
        assert forbidden not in projected["data"], f"{forbidden} leaked to a player"


def test_dm_notes_on_a_place_never_reach_a_player(repos, campaign, world, marco, viewer_for):
    repos.shares.share(campaign.id, world["city"].id)
    [projected] = [
        e for e in snapshot(repos, campaign.id, viewer_for(marco)) if e["id"] == world["city"].id
    ]

    assert projected["data"].get("place_type") == "town"
    assert "notes" not in projected["data"]
    assert "rumours" not in projected["data"]


def test_unknown_fields_are_private_by_default(repos, campaign, world, marco, viewer_for):
    """An allowlist, so a field invented later cannot leak on the next release."""
    sildar = repos.entities.get(world["sildar"].id)
    sildar.data["blackmail_material"] = "the letters"
    repos.entities.update(sildar)
    repos.shares.share(campaign.id, sildar.id)

    [projected] = [
        e for e in snapshot(repos, campaign.id, viewer_for(marco)) if e["id"] == sildar.id
    ]
    assert "blackmail_material" not in projected["data"]


def test_an_unshared_entity_is_absent_not_redacted(repos, campaign, world, marco, viewer_for):
    """Existence is itself a secret: no stub, no placeholder, no id."""
    repos.shares.share(campaign.id, world["sildar"].id)
    ids = {e["id"] for e in snapshot(repos, campaign.id, viewer_for(marco))}
    assert world["hidden_npc"].id not in ids


def test_a_parent_place_is_hidden_unless_it_too_is_shared(repos, campaign, world, marco, viewer_for):
    """Otherwise 'inside somewhere you have never heard of' leaks the somewhere."""
    repos.shares.share(campaign.id, world["hideout"].id)
    [projected] = [
        e for e in snapshot(repos, campaign.id, viewer_for(marco)) if e["id"] == world["hideout"].id
    ]
    assert projected["parent_id"] is None

    repos.shares.share(campaign.id, world["city"].id)
    [projected] = [
        e for e in snapshot(repos, campaign.id, viewer_for(marco)) if e["id"] == world["hideout"].id
    ]
    assert projected["parent_id"] == world["city"].id


def test_a_player_sees_their_own_character_without_a_share(repos, campaign, world, marco, viewer_for):
    ids = {e["id"] for e in snapshot(repos, campaign.id, viewer_for(marco))}
    assert world["marco_pc"].id in ids


def test_own_pc_shows_player_fields_but_not_dm_notes(repos, campaign, world, marco, viewer_for):
    pc = repos.entities.get(world["marco_pc"].id)
    pc.data["inventory"] = "a rope"
    repos.entities.update(pc)

    [projected] = [
        e for e in snapshot(repos, campaign.id, viewer_for(marco)) if e["id"] == pc.id
    ]
    assert projected["own"] is True
    assert projected["data"]["hp"] == 14
    assert projected["data"]["inventory"] == "a rope"
    # The DM's private note about someone's character stays the DM's.
    assert "secrets" not in projected["data"]


def test_another_players_pc_does_not_expose_their_private_fields(
    repos, campaign, world, marco, elsa, viewer_for
):
    pc = repos.entities.get(world["elsa_pc"].id)
    pc.data.update({"hp": 9, "player_notes": "I suspect the cleric"})
    repos.entities.update(pc)
    repos.shares.share(campaign.id, pc.id)

    [projected] = [
        e for e in snapshot(repos, campaign.id, viewer_for(marco)) if e["id"] == pc.id
    ]
    assert projected["own"] is False
    assert projected["data"]["hp"] == 9, "party HP is shared on purpose"
    assert "player_notes" not in projected["data"]


def test_the_dm_projection_is_complete(repos, campaign, world):
    [projected] = [
        e
        for e in snapshot(repos, campaign.id, Viewer.dungeon_master())
        if e["id"] == world["sildar"].id
    ]
    assert projected["data"]["secrets"] == "He is Iarno's brother"


# --------------------------------------------------------------- player edits


def test_a_player_can_edit_their_own_character(repos, campaign, world, marco, viewer_for):
    apply_player_edit(
        repos, viewer_for(marco), world["marco_pc"].id, {"data": {"hp": 3, "conditions": "prone"}}
    )
    updated = repos.entities.get(world["marco_pc"].id)
    assert updated.data["hp"] == 3
    assert updated.data["conditions"] == "prone"


def test_a_player_cannot_edit_another_character(repos, campaign, world, marco, viewer_for):
    with pytest.raises(EditRefused):
        apply_player_edit(repos, viewer_for(marco), world["elsa_pc"].id, {"data": {"hp": 999}})
    assert "hp" not in repos.entities.get(world["elsa_pc"].id).data


def test_a_player_cannot_edit_an_npc(repos, campaign, world, marco, viewer_for):
    repos.shares.share(campaign.id, world["sildar"].id)
    with pytest.raises(EditRefused):
        apply_player_edit(
            repos, viewer_for(marco), world["sildar"].id, {"data": {"status": "dead"}}
        )
    assert repos.entities.get(world["sildar"].id).data["status"] == "alive"


def test_a_player_cannot_rewrite_dm_fields_on_their_own_sheet(repos, campaign, world, marco, viewer_for):
    """The one that matters: an edit is an allowlist, not a merge."""
    apply_player_edit(
        repos,
        viewer_for(marco),
        world["marco_pc"].id,
        {
            "data": {"hp": 20, "secrets": "rewritten", "motive": "mine now"},
            "name": "Elara the Unkillable",
            "kind": "npc",
        },
    )

    updated = repos.entities.get(world["marco_pc"].id)
    assert updated.data["hp"] == 20
    assert updated.data["secrets"] == "is a spy", "a DM field was overwritten"
    assert "motive" not in updated.data
    assert updated.name == "Elara"
    assert updated.kind == KIND_PC


def test_a_player_cannot_move_themselves_into_an_unshared_place(
    repos, campaign, world, marco, viewer_for
):
    apply_player_edit(
        repos, viewer_for(marco), world["marco_pc"].id, {"parent_id": world["hideout"].id}
    )
    assert repos.entities.get(world["marco_pc"].id).parent_id is None


def test_a_player_with_no_character_can_edit_nothing(repos, campaign, world, viewer_for):
    spectator = repos.accounts.create(campaign.id, "watcher", "passing-by")
    with pytest.raises(EditRefused):
        apply_player_edit(repos, viewer_for(spectator), world["sildar"].id, {"data": {"hp": 1}})


# ------------------------------------------------------ owning more than one


def test_a_player_can_own_several_characters(repos, campaign, world, marco, viewer_for):
    """A player with a main and a backup sees the whole of both."""
    spare = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brakk the Spare")
    )
    repos.entities.set_owner(spare.id, marco.id)

    visible = visible_entity_ids(repos, campaign.id, viewer_for(marco))
    assert {world["marco_pc"].id, spare.id} <= visible

    owned = {e["id"] for e in snapshot(repos, campaign.id, viewer_for(marco)) if e["own"]}
    assert owned == {world["marco_pc"].id, spare.id}


def test_ownership_is_listed_per_account(repos, campaign, world, marco, elsa):
    assert repos.entities.owned_ids(marco.id) == {world["marco_pc"].id}
    assert repos.entities.owned_ids(elsa.id) == {world["elsa_pc"].id}
    assert [e.name for e in repos.entities.owned_by(marco.id)] == ["Elara"]


def test_a_player_can_edit_any_character_they_own(repos, campaign, marco, viewer_for):
    spare = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brakk the Spare")
    )
    repos.entities.set_owner(spare.id, marco.id)

    apply_player_edit(repos, viewer_for(marco), spare.id, {"data": {"hp": 4}})
    assert repos.entities.get(spare.id).data["hp"] == 4


# ------------------------------------------------------------- sheets on the wire


def _with_sheet(repos, entity_id, **fields):
    from canon_keeper_core.rules.sheet import new_sheet

    entity = repos.entities.get(entity_id)
    entity.data["sheet"] = new_sheet(**fields)
    return repos.entities.update(entity)


def test_you_see_the_whole_of_your_own_sheet(repos, campaign, world, marco, viewer_for):
    _with_sheet(repos, world["marco_pc"].id, class_index="wizard", level=5,
                player_notes="I suspect the cleric", spells_known=["fireball"])

    [mine] = [e for e in snapshot(repos, campaign.id, viewer_for(marco))
              if e["id"] == world["marco_pc"].id]
    sheet = mine["data"]["sheet"]

    assert sheet["level"] == 5
    assert sheet["player_notes"] == "I suspect the cleric"
    assert sheet["spells_known"] == ["fireball"]


def test_you_see_only_the_public_part_of_someone_elses_sheet(
    repos, campaign, world, marco, elsa, viewer_for
):
    _with_sheet(repos, world["elsa_pc"].id, class_index="cleric", level=4,
                player_notes="my secret plan", spells_known=["bless"],
                equipment=[{"index": "mace"}], currency={"gp": 900})
    repos.shares.share(campaign.id, world["elsa_pc"].id)

    [theirs] = [e for e in snapshot(repos, campaign.id, viewer_for(marco))
                if e["id"] == world["elsa_pc"].id]
    sheet = theirs["data"]["sheet"]

    assert sheet["level"] == 4, "the party knows what level you are"
    assert sheet["class_index"] == "cleric"
    for private in ("player_notes", "spells_known", "equipment", "currency"):
        assert private not in sheet, f"{private} leaked to another player"


def test_an_npcs_statblock_is_never_sent(repos, campaign, world, marco, viewer_for):
    """Sharing that an NPC exists must not reveal how hard it is to kill."""
    _with_sheet(repos, world["sildar"].id, class_index="fighter", level=9)
    repos.shares.share(campaign.id, world["sildar"].id)

    [npc] = [e for e in snapshot(repos, campaign.id, viewer_for(marco))
             if e["id"] == world["sildar"].id]

    assert "sheet" not in npc["data"]


def test_a_player_may_change_state_on_their_sheet(repos, campaign, world, marco, viewer_for):
    _with_sheet(repos, world["marco_pc"].id, class_index="wizard", level=5)

    apply_player_edit(repos, viewer_for(marco), world["marco_pc"].id,
                      {"data": {"sheet": {"hp_current": 9, "conditions": ["prone"]}}})

    sheet = repos.entities.get(world["marco_pc"].id).data["sheet"]
    assert sheet["hp_current"] == 9
    assert sheet["conditions"] == ["prone"]


def test_a_player_cannot_level_themselves_up_through_a_state_edit(
    repos, campaign, world, marco, viewer_for
):
    """The whole point of splitting state from build."""
    _with_sheet(repos, world["marco_pc"].id, class_index="wizard", level=5)

    apply_player_edit(repos, viewer_for(marco), world["marco_pc"].id,
                      {"data": {"sheet": {"hp_current": 9, "level": 20,
                                          "abilities": {"str": 30}}}})

    sheet = repos.entities.get(world["marco_pc"].id).data["sheet"]
    assert sheet["hp_current"] == 9, "the state change went through"
    assert sheet["level"] == 5, "the build change did not"
    assert sheet["abilities"]["str"] != 30


# --------------------------------------------------------------------- versions


def test_every_write_bumps_the_version(repos, campaign, world):
    entity = repos.entities.get(world["sildar"].id)
    first = entity.version

    entity.summary = "changed"
    repos.entities.update(entity)

    assert repos.entities.get(world["sildar"].id).version == first + 1


def test_the_version_is_sent_with_the_entity(repos, campaign, world, marco, viewer_for):
    projected = snapshot(repos, campaign.id, viewer_for(marco))
    assert all("version" in e for e in projected)


def test_an_edit_against_a_stale_version_is_refused(repos, campaign, world, marco, viewer_for):
    """Otherwise the later of two edits silently erases the earlier."""
    from canon_keeper_core.repo.entities import StaleWrite

    _with_sheet(repos, world["marco_pc"].id, class_index="wizard", level=5)
    stale = repos.entities.get(world["marco_pc"].id).version

    # The DM changes something in the meantime.
    dm_copy = repos.entities.get(world["marco_pc"].id)
    dm_copy.summary = "the DM edited this"
    repos.entities.update(dm_copy)

    with pytest.raises(StaleWrite):
        apply_player_edit(repos, viewer_for(marco), world["marco_pc"].id,
                          {"data": {"sheet": {"hp_current": 1}}},
                          expected_version=stale)

    assert repos.entities.get(world["marco_pc"].id).summary == "the DM edited this"


def test_an_edit_against_the_current_version_goes_through(
    repos, campaign, world, marco, viewer_for
):
    _with_sheet(repos, world["marco_pc"].id, class_index="wizard", level=5)
    current = repos.entities.get(world["marco_pc"].id).version

    apply_player_edit(repos, viewer_for(marco), world["marco_pc"].id,
                      {"data": {"sheet": {"hp_current": 3}}}, expected_version=current)

    assert repos.entities.get(world["marco_pc"].id).data["sheet"]["hp_current"] == 3
