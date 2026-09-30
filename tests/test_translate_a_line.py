"""Turning what a player typed into the turn they meant, when the DM asks.

The agent could already do this -- ``propose_turn`` is the whole of it -- but
only ever on its own initiative, after a pause, with autopilot on. This is the
other way round: the DM reads "I get behind the orc and hit it with my axe",
points at it, and asks for it in rules.

Which makes the interesting claim an authority one rather than a language one.
Asking has to work with autopilot **off**, because that is the evening it
exists for -- somebody running their own table who wants one sentence worked
out. So the host grants the agent exactly one proposal, for exactly the
creature whose turn it is, and nothing else. Every test below is about the
edges of that grant, because a grant that leaked would be autopilot arriving
without being switched on.
"""

from __future__ import annotations

import json

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import Entity
from canon_keeper_protocol import MessageType


@pytest.fixture
def table(repos):
    """A fight begun, with the player's own character up first.

    Initiative is rigged rather than rolled: every test here is about whose
    turn it is, so the one thing none of them should depend on is a d20.
    """
    campaign = repos.campaigns.ensure_default("Translate Night")
    gm = repos.accounts.create(
        campaign.id, "gm", "run-the-game", role="dm", display_name="The DM"
    )
    marco = repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", display_name="Marco"
    )
    # A second player, so "not your turn" can be tested against somebody who
    # plays a character rather than against a monster -- which would be refused
    # a step earlier, for a different reason.
    ada = repos.accounts.create(
        campaign.id, "ada", "arrows-please", display_name="Ada"
    )
    repos.accounts.create(
        campaign.id,
        "autopilot",
        "let-me-run-it",
        role="agent",
        display_name="Autopilot",
    )

    hero = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind="pc",
            name="Brok",
            data={"hp": 28, "hp_max": 28, "sheet": {"weapons": ["battleaxe"]}},
        )
    )
    archer = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind="pc", name="Ada")
    )
    orc = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind="npc", name="Yeemik")
    )
    repos.entities.set_owner(hero.id, marco.id)
    repos.accounts.set_character(marco.id, hero.id)
    repos.entities.set_owner(archer.id, ada.id)
    repos.accounts.set_character(ada.id, archer.id)

    encounter = repos.encounters.create(campaign.id, "The cave", width=12, height=12)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=20, x=0, y=0),
        "ada": repos.encounters.add(encounter.id, archer.id, initiative=10, x=0, y=3),
        "orc": repos.encounters.add(encounter.id, orc.id, initiative=4, x=2, y=0),
    }
    repos.encounters.begin(encounter.id)
    assert repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id, (
        "the fixture is only useful if the player's turn is the one up"
    )
    return repos, campaign.id, encounter, tokens, gm, marco


@pytest.fixture
def live(qtbot, table):
    repos, campaign_id, encounter, _tokens, _gm, _marco = table
    server = SessionServer(repos, campaign_id, "Translate session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, encounter
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def _record(client) -> list[dict]:
    """Every frame this connection is sent, decoded.

    Reached for because what the agent is *told* is the whole of this feature's
    output on the host side -- the model is not in the test, so the frame is
    the observable thing.
    """
    seen: list[dict] = []

    def keep(raw: str) -> None:
        try:
            seen.append(json.loads(raw))
        except ValueError:
            pass

    client._socket.textMessageReceived.connect(keep)
    return seen


def _is(frame: dict, message_type) -> bool:
    """Whether a raw frame is of this type. The wire calls the field ``t``."""
    return frame.get("t") == str(message_type)


def _member_of(client) -> str:
    assert client.me is not None, "never got a welcome"
    return client.me.id


# ---------------------------------------------------------------- the asking


def test_the_dm_can_hand_the_agent_a_line_to_write_as_a_turn(qtbot, live, table):
    """The words reach the agent, and the DM did not have to name a square.

    What travels is the sentence and who said it. Which creature that is, and
    whether it is their turn, is worked out on the host -- a client asserting
    "this member plays that token" would be asserting the half of it that
    drifts.
    """
    _repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    dm = _join(qtbot, server, "gm", "run-the-game")
    told = _record(agent)
    try:
        dm.send_translate(_member_of(player), "I get behind the orc and hit it")

        qtbot.waitUntil(
            lambda: any(_is(f, MessageType.TRANSLATE_THIS) for f in told),
            timeout=5000,
        )
        asked = next(f for f in told if _is(f, MessageType.TRANSLATE_THIS))["d"]
        assert asked["said"] == "I get behind the orc and hit it"
        assert asked["combatant"] == tokens["hero"].id
        assert asked["who"] == "Brok"
    finally:
        dm.leave()
        player.leave()
        agent.leave()


def test_asking_buys_one_proposal_with_autopilot_off(qtbot, live, table):
    """The whole point of it. Off is off, and this is still allowed.

    ``_may_run_the_table`` refuses an agent everything while autopilot is off,
    and deliberately so. A proposal is the one thing worth granting on its own,
    because it is an offer: it reaches the player as something to accept, and
    refusing it costs them a click.
    """
    _repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    dm = _join(qtbot, server, "gm", "run-the-game")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        assert server.autopilot is False
        dm.send_translate(_member_of(player), "I hit the orc")
        qtbot.waitUntil(lambda: server._translating == tokens["hero"].id, timeout=5000)

        agent._send(
            MessageType.PROPOSE,
            combatant=tokens["hero"].id,
            move=[1, 0],
            target=tokens["orc"].id,
            weapon="battleaxe",
            text="Move to 1,0 and attack Yeemik with a battleaxe.",
        )

        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        assert offered[0]["combatant"] == tokens["hero"].id
    finally:
        dm.leave()
        player.leave()
        agent.leave()


def test_the_agent_cannot_propose_without_being_asked(qtbot, live, table):
    """The other half of the same claim, and the one that fails open.

    Without this the grant is not a grant, it is just autopilot spelled
    differently.
    """
    _repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        assert server.autopilot is False
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent._send(
                MessageType.PROPOSE,
                combatant=tokens["hero"].id,
                move=[1, 0],
                text="Move to 1,0.",
            )
    finally:
        agent.leave()


def test_the_grant_is_spent_on_the_first_proposal(qtbot, live, table):
    """One line asked about, one turn written. Not a mode that stays on."""
    _repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    dm = _join(qtbot, server, "gm", "run-the-game")
    try:
        dm.send_translate(_member_of(player), "I hit the orc")
        qtbot.waitUntil(lambda: server._translating == tokens["hero"].id, timeout=5000)

        agent._send(
            MessageType.PROPOSE,
            combatant=tokens["hero"].id,
            move=[1, 0],
            text="Move to 1,0.",
        )
        qtbot.waitUntil(lambda: server._translating is None, timeout=5000)

        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent._send(
                MessageType.PROPOSE,
                combatant=tokens["hero"].id,
                move=[0, 1],
                text="And again.",
            )
    finally:
        dm.leave()
        player.leave()
        agent.leave()


def test_being_asked_for_a_turn_does_not_let_the_agent_move_a_token(qtbot, live, table):
    """The reason the grant is not a widened ``_may_run_the_table``.

    That predicate answers for moving a token, passing the turn and setting an
    initiative all at once. Loosening it so a DM could ask for one translation
    would have handed over the other three as well -- a rule relaxed at one
    door and thereby at doors nobody was looking at, which is how every
    serious bug in this area has arrived.
    """
    repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    dm = _join(qtbot, server, "gm", "run-the-game")
    try:
        dm.send_translate(_member_of(player), "I hit the orc")
        qtbot.waitUntil(lambda: server._translating == tokens["hero"].id, timeout=5000)

        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_move(tokens["hero"].id, 4, 4)
        assert repos.encounters.combatant(tokens["hero"].id).x == 0

        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_turn("next")
        assert (
            repos.encounters.get(_fight.id).turn_combatant_id == tokens["hero"].id
        ), "the turn moved on an authority nobody granted"
    finally:
        dm.leave()
        player.leave()
        agent.leave()


# --------------------------------------------------------------- the refusals


def test_a_player_cannot_ask_for_a_translation(qtbot, live, table):
    """It spends the DM's money and speaks with the DM's authority."""
    _repos, _campaign_id, _encounter, _tokens, _gm, _marco = table
    server, _fight = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.failed, timeout=5000):
            player.send_translate(_member_of(player), "I hit the orc")
        assert server._translating is None
    finally:
        player.leave()
        agent.leave()


def test_asking_about_somebody_who_is_not_up_is_refused_in_those_words(
    qtbot, live, table
):
    """The mark sits beside a line that may have scrolled past long ago.

    So the refusal is about the turn rather than about the sentence, and it
    arrives from the host rather than as a model declining politely several
    seconds later.

    The turn is passed to *another player's* character rather than to the orc,
    which matters: with a monster up, "nobody plays that character" refuses it
    first and this claim would never be reached. A test that passes by way of
    the wrong refusal is not testing anything.
    """
    repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, fight = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    dm = _join(qtbot, server, "gm", "run-the-game")
    try:
        assert server.run_turn("next") == ""
        qtbot.waitUntil(
            lambda: repos.encounters.get(fight.id).turn_combatant_id
            == tokens["ada"].id,
            timeout=5000,
        )

        with qtbot.waitSignal(dm.failed, timeout=5000) as blocker:
            dm.send_translate(_member_of(player), "I hit the orc")

        said = " ".join(str(a) for a in blocker.args)
        assert "not theirs" in said, f"refused, but for another reason: {said!r}"
        assert server._translating is None
    finally:
        dm.leave()
        player.leave()
        agent.leave()


def test_asking_with_nothing_running_to_read_it_says_so(qtbot, live, table):
    """A DM who clicked and got silence would conclude the button is broken.

    The agent is a separate process the DM starts. Not started is the ordinary
    case, not an error, and it has an answer that tells them what to do.
    """
    _repos, _campaign_id, _encounter, _tokens, _gm, _marco = table
    server, _fight = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    dm = _join(qtbot, server, "gm", "run-the-game")
    try:
        with qtbot.waitSignal(dm.failed, timeout=5000) as blocker:
            dm.send_translate(_member_of(player), "I hit the orc")

        assert "agent" in " ".join(str(a) for a in blocker.args).lower()
        assert server._translating is None, "a grant outlived the ask that failed"
    finally:
        dm.leave()
        player.leave()


def test_an_empty_line_is_not_worth_a_model_call(qtbot, live, table):
    _repos, _campaign_id, _encounter, _tokens, _gm, _marco = table
    server, _fight = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    dm = _join(qtbot, server, "gm", "run-the-game")
    try:
        with qtbot.waitSignal(dm.failed, timeout=5000):
            dm.send_translate(_member_of(player), "   ")
        assert server._translating is None
    finally:
        dm.leave()
        player.leave()
        agent.leave()


# ------------------------------------------- the DM proposing to the player
#
# Either side may propose; whoever is asked answers once. Staging the turn was
# the DM's consent, so an accepted offer is carried out without going back to
# them -- that would be asking the same person the same question twice.


def test_a_turn_for_a_player_who_is_here_is_offered_not_carried_out(qtbot, live, table):
    """Their character, their call. The DM staging it was the asking."""
    repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        qtbot.waitUntil(lambda: server.is_played_here(tokens["hero"].id), timeout=5000)

        assert server.offer_turn(tokens["hero"].id, move=[0, 1]) == ""

        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        assert offered[0]["combatant"] == tokens["hero"].id
        assert repos.encounters.combatant(tokens["hero"].id).x == 0, (
            "the turn was carried out rather than offered"
        )
        assert repos.encounters.combatant(tokens["hero"].id).y == 0
    finally:
        player.leave()


def test_the_offer_describes_the_turn_it_carries(qtbot, live, table):
    """Built from the checked fields, so it cannot describe a different turn.

    The sentence a player reads before committing their character used to be
    written separately from the move it came with, and nothing compared the two.
    """
    _repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        qtbot.waitUntil(lambda: server.is_played_here(tokens["hero"].id), timeout=5000)
        server.offer_turn(
            tokens["hero"].id, move=[0, 1], target=tokens["orc"].id, weapon="battleaxe"
        )
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        said = offered[0]["text"]
        assert "0,1" in said, said
        assert "Yeemik" in said, said
        assert "battleaxe" in said, said
    finally:
        player.leave()


def test_nobody_here_to_ask_means_the_dm_plays_them(qtbot, live, table):
    """A character whose player has gone home is the DM's to run.

    Offering a turn to an empty chair would hold the fight until the clock gave
    up, which is a worse answer than the DM simply taking it.
    """
    _repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    assert server.is_played_here(tokens["hero"].id) is False, (
        "nobody has joined, so there is nobody to put a turn to"
    )


def test_a_monster_is_never_offered_to_anybody(qtbot, live, table):
    _repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        assert server.is_played_here(tokens["orc"].id) is False
    finally:
        player.leave()


def test_an_accepted_offer_is_not_put_back_to_the_dm(qtbot, live, table):
    """The DM confirmed by staging it. Asking again is asking twice."""
    repos, _campaign_id, _encounter, tokens, _gm, _marco = table
    server, _fight = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        qtbot.waitUntil(lambda: server.is_played_here(tokens["hero"].id), timeout=5000)
        server.offer_turn(tokens["hero"].id, move=[0, 1])
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        player.send_answer(offered[0]["id"], True)

        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["hero"].id).y == 1, timeout=5000
        )
        assert all(
            held.get("combatant") != tokens["hero"].id
            for held in server._proposed.values()
        ), "the offer was still outstanding, so somebody was being asked again"
    finally:
        player.leave()
