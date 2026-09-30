"""The fight on the wire: who sees which tokens, and who may move them.

Two claims, and both are the ones that fail open if they are wrong.

**A token is only sent to someone the creature has been shared with.** Putting a
monster on the map is not the act of revealing it. The DM sees the ambush; the
party does not, and not because the widget declines to draw it -- because the
bytes never contained it.

**Running the fight is an authority, not a message.** The DM has it. An agent
has it exactly while autopilot is on, which is the same gate its chat goes
through, checked on the host. A player has it never.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.projection import Viewer, project_encounter
from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import Entity
from canon_keeper_protocol import MessageType


@pytest.fixture
def table(repos):
    """A campaign with a fight in it, one PC shared and one goblin not."""
    campaign = repos.campaigns.ensure_default("Fight Night")
    marco = repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", display_name="Marco"
    )
    repos.accounts.create(
        campaign.id, "autopilot", "let-me-run-it", role="agent", display_name="Autopilot"
    )

    hero = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind="pc", name="Sable")
    )
    seen = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind="npc", name="Yeemik")
    )
    hidden = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind="npc", name="The thing above")
    )
    repos.entities.set_owner(hero.id, marco.id)
    repos.shares.share(campaign.id, seen.id)

    encounter = repos.encounters.create(campaign.id, "The cave", width=10, height=10)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=18, x=1, y=1),
        "seen": repos.encounters.add(encounter.id, seen.id, initiative=12, x=2, y=2),
        "hidden": repos.encounters.add(encounter.id, hidden.id, initiative=5, x=3, y=3),
        # A token the DM typed a name for and never made an entity of.
        "nameless": repos.encounters.add(encounter.id, None, name="Goblin four", x=4, y=4),
    }
    return repos, campaign.id, marco, encounter, hero, tokens


# ------------------------------------------------------------- the projection


def _project(repos, encounter, viewer, visible):
    return project_encounter(
        encounter,
        repos.encounters.combatants(encounter.id),
        viewer,
        visible,
        repos.encounters.obstacles(encounter.id),
    )


def test_the_dm_sees_the_whole_fight(table):
    repos, campaign_id, _marco, encounter, _hero, tokens = table
    fight = _project(repos, encounter, Viewer.dungeon_master(), None)
    assert len(fight["combatants"]) == len(tokens)


def test_a_player_is_not_sent_a_creature_nobody_shared(table):
    repos, campaign_id, marco, encounter, hero, _tokens = table
    viewer = Viewer(
        account_id=marco.id, is_dm=False, owned_entity_ids={hero.id}
    )
    visible = repos.shares.visible_entity_ids(campaign_id, marco.id) | {hero.id}

    fight = _project(repos, encounter, viewer, visible)
    names = {c["entity"] for c in fight["combatants"]}

    assert hero.id in names, "you can always see your own character"
    assert len(fight["combatants"]) == 2, "only the shared goblin and their own PC"


def test_a_nameless_token_never_reaches_a_player(table):
    """No entity means no share, and no share means nothing to check."""
    repos, campaign_id, marco, encounter, hero, _tokens = table
    viewer = Viewer(account_id=marco.id, is_dm=False, owned_entity_ids={hero.id})
    fight = _project(repos, encounter, viewer, {hero.id})
    assert all(c["entity"] is not None for c in fight["combatants"])


def test_a_players_frame_carries_no_names(table):
    """Names travel on entities. A second copy is a second thing to leak."""
    repos, campaign_id, marco, encounter, hero, _tokens = table
    viewer = Viewer(account_id=marco.id, is_dm=False, owned_entity_ids={hero.id})
    fight = _project(repos, encounter, viewer, {hero.id})
    assert all("name" not in c for c in fight["combatants"])


def test_the_grid_and_the_round_reach_everyone(table):
    repos, campaign_id, marco, encounter, hero, _tokens = table
    repos.encounters.begin(encounter.id)
    encounter = repos.encounters.get(encounter.id)

    viewer = Viewer(account_id=marco.id, is_dm=False, owned_entity_ids={hero.id})
    fight = _project(repos, encounter, viewer, {hero.id})

    assert fight["width"] == 10
    assert fight["round"] == 1
    # Whose turn it is travels even when they cannot see whose it is: "something
    # is acting" is what the DM would say out loud anyway.
    assert fight["turn"] is not None


def test_the_terrain_goes_to_everyone(table):
    """A pillar is the room, not one of its occupants. Hiding it helps nobody."""
    repos, campaign_id, marco, encounter, hero, _tokens = table
    repos.encounters.toggle_obstacle(encounter.id, 0, 0)

    viewer = Viewer(account_id=marco.id, is_dm=False, owned_entity_ids={hero.id})
    fight = _project(repos, encounter, viewer, {hero.id})

    assert fight["obstacles"] == [[0, 0]]


def test_the_terrain_is_sent_in_a_stable_order(table):
    """Two clients disagreeing must mean they hold different fights."""
    repos, _campaign_id, _marco, encounter, _hero, _tokens = table
    # Squares nobody in the fixture is standing on -- an obstacle under someone
    # is refused, which is a different test.
    for square in ((-1, -1), (0, 1), (3, -2)):
        repos.encounters.toggle_obstacle(encounter.id, *square)

    fight = _project(repos, encounter, Viewer.dungeon_master(), None)
    assert fight["obstacles"] == [[-1, -1], [0, 1], [3, -2]]


def test_no_fight_projects_to_nothing():
    assert project_encounter(None, [], Viewer.dungeon_master()) == {}


# ------------------------------------------------------- the gate, over a socket


@pytest.fixture
def live(qtbot, table):
    repos, campaign_id, _marco, encounter, _hero, _tokens = table
    server = SessionServer(repos, campaign_id, "Fight session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, encounter
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def test_the_fight_arrives_on_joining(qtbot, live, table):
    """Joining halfway through a fight shows you the fight."""
    server, encounter = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        qtbot.waitUntil(lambda: player.state.encounter is not None, timeout=5000)
        assert player.state.encounter["name"] == "The cave"
    finally:
        player.leave()


def test_a_player_cannot_move_anything(qtbot, live, table):
    repos = table[0]
    server, encounter = live
    tokens = table[5]
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.failed, timeout=5000):
            player.send_move(tokens["hero"].id, 7, 7)
        assert repos.encounters.combatant(tokens["hero"].id).x == 1
    finally:
        player.leave()


def test_the_agent_cannot_move_anything_while_autopilot_is_off(qtbot, live, table):
    """The same gate its chat goes through, for the same reason."""
    repos = table[0]
    server, encounter = live
    tokens = table[5]
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        assert server.autopilot is False
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_move(tokens["seen"].id, 8, 8)
        assert repos.encounters.combatant(tokens["seen"].id).x == 2
    finally:
        agent.leave()


def test_the_agent_may_move_once_the_table_is_handed_over(qtbot, live, table):
    repos = table[0]
    server, encounter = live
    tokens = table[5]
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        server.set_autopilot(True, by="the DM")
        agent.send_move(tokens["seen"].id, 0, 3)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["seen"].id).x == 0, timeout=5000
        )
    finally:
        agent.leave()


def test_the_agent_may_run_the_turns_that_are_its_own(qtbot, live, table):
    """The API the DM's buttons use, reachable by whoever is running the fight.

    With one limit: a turn belonging to somebody who plays for themselves ends
    when they end it. Autopilot begins and ends the fight and passes its own
    turns; it does not get to decide a player has had long enough.
    """
    repos = table[0]
    server, encounter = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        server.set_autopilot(True, by="the DM")

        agent.send_turn("begin")
        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).round == 1, timeout=5000
        )
        first = repos.encounters.get(encounter.id).turn_combatant_id

        # The first turn belongs to a person, and theirs is not the agent's to
        # end. Being told to pass the turn on is an instruction; this is the
        # rule, and without it a model calling next_turn early skipped them.
        agent.send_turn("next")
        qtbot.wait(300)
        assert repos.encounters.get(encounter.id).turn_combatant_id == first

        # Once it is a monster's turn, passing it on is exactly its job.
        server.run_turn("next")
        machine = repos.encounters.get(encounter.id).turn_combatant_id
        assert machine != first
        agent.send_turn("next")
        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).turn_combatant_id != machine,
            timeout=5000,
        )

        agent.send_turn("end")
        qtbot.waitUntil(
            lambda: not repos.encounters.get(encounter.id).running, timeout=5000
        )
    finally:
        agent.leave()


def test_the_agent_may_set_an_initiative(qtbot, live, table):
    repos = table[0]
    server, _encounter = live
    tokens = table[5]
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        server.set_autopilot(True, by="the DM")
        agent.send_initiative(tokens["seen"].id, 20)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["seen"].id).initiative == 20,
            timeout=5000,
        )
    finally:
        agent.leave()


def test_the_agent_can_build_a_fight_from_nothing(qtbot, live, table):
    """The whole point of the tools: describe an ambush and it is on the map.

    Every frame goes through the same repository call the DM's own buttons use,
    so an agent cannot produce a fight the app could not have produced itself.
    """
    repos, campaign_id, _marco, _old_fight, hero, _tokens = table
    server, _encounter = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        server.set_autopilot(True, by="the DM")

        agent._send(MessageType.FIGHT, name="The rubbish heap", width=12, height=9)
        qtbot.waitUntil(
            lambda: (repos.encounters.running(campaign_id) or _old_fight).name
            == "The rubbish heap",
            timeout=5000,
        )
        built = repos.encounters.running(campaign_id)

        agent._send(MessageType.ENLIST, entity=hero.id, x=2, y=3, initiative=17)
        qtbot.waitUntil(
            lambda: len(repos.encounters.combatants(built.id)) == 1, timeout=5000
        )
        placed = repos.encounters.combatants(built.id)[0]
        assert (placed.x, placed.y, placed.initiative) == (2, 3, 17)

        agent._send(MessageType.TERRAIN, x=1, y=1, on=True)
        qtbot.waitUntil(
            lambda: repos.encounters.obstacles(built.id) == {(1, 1)}, timeout=5000
        )
    finally:
        agent.leave()


def test_the_agent_cannot_build_one_with_autopilot_off(qtbot, live, table):
    repos, campaign_id, _marco, _fight, _hero, _tokens = table
    server, encounter = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent._send(MessageType.FIGHT, name="Not allowed", width=10, height=10)
        assert repos.encounters.running(campaign_id).id == encounter.id
    finally:
        agent.leave()


def test_a_player_cannot_build_one_either(qtbot, live, table):
    repos, campaign_id, _marco, _fight, hero, _tokens = table
    server, _encounter = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.failed, timeout=5000):
            player._send(MessageType.TERRAIN, x=1, y=1, on=True)
    finally:
        player.leave()


def test_enlisting_somebody_who_does_not_exist_is_refused(qtbot, live, table):
    server, _encounter = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        server.set_autopilot(True, by="the DM")
        with qtbot.waitSignal(agent.failed, timeout=5000) as blocker:
            agent._send(MessageType.ENLIST, entity=9999, x=0, y=0)
        assert "no such creature" in " ".join(str(a) for a in blocker.args).lower()
    finally:
        agent.leave()


def test_enlisting_onto_a_square_the_map_does_not_have_is_refused(qtbot, live, table):
    """And said out loud, because silence is what autopilot narrates over.

    This door wrote the square straight to the database, so a goblin could be
    stood outside the room -- and the agent, told nothing, would describe it
    standing there. The square is the same three facts everywhere else asks
    about, and none of them is a rule the DM can wave through.
    """
    repos, campaign_id, _marco, _old_fight, _hero, _tokens = table
    server, encounter = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        server.set_autopilot(True, by="the DM")
        far = repos.entities.create(
            Entity(id=None, campaign_id=campaign_id, kind="npc", name="Far away")
        )
        before = len(repos.encounters.combatants(encounter.id))

        with qtbot.waitSignal(agent.failed, timeout=5000) as blocker:
            agent._send(MessageType.ENLIST, entity=far.id, x=99, y=99)

        assert "off the map" in " ".join(str(a) for a in blocker.args).lower()
        assert len(repos.encounters.combatants(encounter.id)) == before, (
            "nothing was added"
        )
    finally:
        agent.leave()


def test_a_player_cannot_pass_the_turn(qtbot, live, table):
    repos = table[0]
    server, encounter = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.failed, timeout=5000):
            player.send_turn("begin")
        assert repos.encounters.get(encounter.id).round == 0
    finally:
        player.leave()


def test_a_move_reaches_the_other_screens(qtbot, live, table):
    """The point of the whole thing: the DM moves it, the player sees it move."""
    repos = table[0]
    server, _encounter = live
    tokens = table[5]
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        qtbot.waitUntil(lambda: player.state.encounter is not None, timeout=5000)

        repos.encounters.place(tokens["seen"].id, 0, 4)
        server.publish_encounter()

        def moved() -> bool:
            fight = player.state.encounter or {}
            return any(
                c.get("x") == 0 and c.get("y") == 4
                for c in fight.get("combatants", [])
            )

        qtbot.waitUntil(moved, timeout=5000)
    finally:
        player.leave()


def test_the_messages_exist():
    """Named here so renaming one breaks a test rather than a table."""
    assert MessageType.ENCOUNTER == "encounter"
    assert MessageType.MOVE == "move"
    assert MessageType.TURN == "turn"
    assert MessageType.INITIATIVE == "initiative"
