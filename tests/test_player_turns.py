"""A player taking their own turn, on the map, themselves.

The wheel was the DM's. A player described their turn in words, the agent
formalised it and they said yes -- which is a good flow and a slow one for "I
step back and shoot". So the same wheel opens on a player's map, for one
creature and one moment: **their own character, while it is its turn.**

That is a new door into the fight, so what it does *not* open is the thing to
guard. It is not authority over the fight -- no passing the turn, no
initiative, no terrain, nobody else's creature -- and it is not inherited by
the stand-in sitting in that character's seat, which is minted against the
same account and would otherwise pick it up the moment its handover ended.

Everything it asks for is a request. The host applies the same rules to a
turn that arrives from a player's map as to one taken on the DM's: the route
round what is in the way, the movement it costs, the swings it provokes.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity

SHEET = {
    "schema": 1,
    "species": "human",
    "class_index": "fighter",
    "level": 3,
    "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 10},
    "equipment": ["battleaxe", "chain-mail"],
}


@pytest.fixture
def table(qtbot, repos):
    """Marco plays Brok. Elsa plays Marla. A goblin is in the way of neither."""
    campaign = repos.campaigns.ensure_default("The player's turn")
    marco = repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    elsa = repos.accounts.create(campaign.id, "elsa", "goblin-teeth")

    brok = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok",
               data={"hp": 28, "max_hp": 28, "sheet": SHEET})
    )
    marla = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Marla",
               data={"hp": 22, "max_hp": 22, "sheet": SHEET})
    )
    goblin = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik",
               data={"hp": 7, "max_hp": 7, "sheet": SHEET})
    )
    repos.entities.set_owner(brok.id, marco.id)
    repos.entities.set_owner(marla.id, elsa.id)
    repos.accounts.set_character(marco.id, brok.id)
    repos.accounts.set_character(elsa.id, marla.id)

    encounter = repos.encounters.create(campaign.id, "The cave", width=20, height=20)
    tokens = {
        "brok": repos.encounters.add(encounter.id, brok.id, initiative=20, x=0, y=0),
        "marla": repos.encounters.add(encounter.id, marla.id, initiative=15, x=5, y=5),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=10, x=8, y=8),
    }
    repos.encounters.begin(encounter.id)

    server = SessionServer(repos, campaign.id, "Player turn session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, encounter, tokens, brok, marla, goblin
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


# ------------------------------------------------------------- what it opens


def test_a_player_may_walk_their_own_character_on_their_turn(qtbot, table):
    server, repos, _encounter, tokens, *_ = table
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        marco.send_move(tokens["brok"].id, 0, 3)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["brok"].id).y == 3, timeout=5000
        )
    finally:
        marco.leave()


def test_a_player_may_swing_on_their_own_turn(qtbot, table):
    server, repos, _encounter, tokens, _brok, _marla, goblin = table
    # Standing next to the goblin, so the swing is a swing and not a reach check.
    repos.encounters.place(tokens["brok"].id, 7, 8)
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        before = repos.entities.get(goblin.id).data["hp"]
        marco.send_swing(tokens["brok"].id, tokens["goblin"].id, "Battleaxe")
        qtbot.wait(600)

        rolled = repos.entities.get(goblin.id).data["hp"]
        assert rolled <= before, "the host never rolled it"
        assert repos.encounters.get(_encounter_id(repos)).action_used, (
            "the swing was not charged against the turn"
        )
    finally:
        marco.leave()


def _encounter_id(repos) -> int:
    return repos.encounters.running(1).id


# -------------------------------------------------------- what it does not


def test_a_player_may_not_move_somebody_elses_character(qtbot, table):
    """The narrow door is the whole point. Marla is not Marco's to walk."""
    server, repos, _encounter, tokens, *_ = table
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(marco.failed, timeout=5000):
            marco.send_move(tokens["marla"].id, 5, 8)
        assert repos.encounters.combatant(tokens["marla"].id).y == 5
    finally:
        marco.leave()


def test_a_player_may_not_move_a_monster(qtbot, table):
    server, repos, _encounter, tokens, *_ = table
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(marco.failed, timeout=5000):
            marco.send_move(tokens["goblin"].id, 8, 4)
        assert repos.encounters.combatant(tokens["goblin"].id).y == 8
    finally:
        marco.leave()


def test_a_player_may_not_act_when_it_is_not_their_turn(qtbot, table):
    """Brok is up. Elsa waiting her turn is exactly when this must refuse."""
    server, repos, _encounter, tokens, *_ = table
    elsa = _join(qtbot, server, "elsa", "goblin-teeth")
    try:
        with qtbot.waitSignal(elsa.failed, timeout=5000):
            elsa.send_move(tokens["marla"].id, 5, 8)
        assert repos.encounters.combatant(tokens["marla"].id).y == 5
    finally:
        elsa.leave()


def test_a_player_still_cannot_pass_the_turn(qtbot, table):
    """Acting for yourself is not running the fight, and never becomes it."""
    server, repos, encounter, tokens, *_ = table
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(marco.failed, timeout=5000):
            marco.send_turn("next")
        assert repos.encounters.get(encounter.id).turn_combatant_id == tokens["brok"].id
    finally:
        marco.leave()


def test_a_player_still_cannot_set_an_initiative(qtbot, table):
    server, repos, _encounter, tokens, *_ = table
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(marco.failed, timeout=5000):
            marco.send_initiative(tokens["goblin"].id, 99)
        assert repos.encounters.combatant(tokens["goblin"].id).initiative == 10
    finally:
        marco.leave()


def test_a_player_may_not_swing_twice(qtbot, table):
    """One action a turn, on the wire as much as on the DM's own map.

    This door spent the action and never checked it. For a long while only the
    agent could reach it; the wheel on a player's map made it reachable by
    everybody, and a character could swing until the goblin fell over.
    """
    server, repos, _encounter, tokens, _brok, _marla, goblin = table
    repos.encounters.place(tokens["brok"].id, 7, 8)
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        marco.send_swing(tokens["brok"].id, tokens["goblin"].id, "Battleaxe")
        qtbot.wait(600)
        after_one = repos.entities.get(goblin.id).data["hp"]

        with qtbot.waitSignal(marco.failed, timeout=5000):
            marco.send_swing(tokens["brok"].id, tokens["goblin"].id, "Battleaxe")

        assert repos.entities.get(goblin.id).data["hp"] == after_one
    finally:
        marco.leave()


def test_having_swung_they_may_still_walk(qtbot, table):
    """The two halves of a turn are separate. Spending one is not spending both."""
    server, repos, _encounter, tokens, _brok, _marla, _goblin = table
    repos.encounters.place(tokens["brok"].id, 7, 8)
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        marco.send_swing(tokens["brok"].id, tokens["goblin"].id, "Battleaxe")
        qtbot.wait(600)

        marco.send_move(tokens["brok"].id, 5, 8)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["brok"].id).x == 5, timeout=5000
        )
    finally:
        marco.leave()


# ---------------------------------------------------------- the same rules


def test_a_players_walk_obeys_the_speed(qtbot, table):
    """Six squares. The host does not care which map the turn came from."""
    server, repos, _encounter, tokens, *_ = table
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        marco.send_move(tokens["brok"].id, 0, 9)
        qtbot.wait(600)
        assert repos.encounters.combatant(tokens["brok"].id).y == 0
    finally:
        marco.leave()


def test_a_players_walk_goes_round_what_is_in_the_way(qtbot, table):
    server, repos, encounter, tokens, *_ = table
    repos.encounters.place(tokens["goblin"].id, 0, 2)
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        marco.send_move(tokens["brok"].id, 0, 4)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["brok"].id).y == 4, timeout=5000
        )
        # Four squares as the crow flies, and the same four going round it.
        assert repos.encounters.get(encounter.id).moved_squares == 4
    finally:
        marco.leave()
