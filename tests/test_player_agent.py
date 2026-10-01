"""A machine playing one character, and only one.

Two claims, and the second is the reason the first is worth having.

**It plays.** An empty chair takes its turns instead of holding the table up.

**It plays blind.** It is on that player's seat, so it is sent what that player
is sent -- not the DM's view, which is what used to run a handed-over character
and is why one knew where the ambush was.
"""

from __future__ import annotations

import asyncio

import pytest

from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper_client.session import AgentSession, LoginFailed
from canon_keeper_player_agent.play import DEFAULT_SPEED, Stand_In
from canon_keeper_player_agent.tactics import decide, enemies_of


# ---------------------------------------------------------------- the tactics
#
# No host, no network, no key. Just the map and what a person would do with it.


def _fight(*combatants, **rest) -> dict:
    fight = {"round": 1, "turn": None, "combatants": list(combatants)}
    fight.update(rest)
    return fight


def _one(id, entity, x, y, team=1, **rest) -> dict:
    made = {"id": id, "entity": entity, "x": x, "y": y, "team": team}
    made.update(rest)
    return made


def test_it_closes_and_swings_when_it_can_reach():
    mine = _one(1, 1, 0, 0, team=1)
    goblin = _one(2, 2, 3, 0, team=2)

    chosen = decide(mine, _fight(mine, goblin), {2: {"name": "Yeemik"}}, speed=6)

    assert chosen.move == (2, 0), "one square short of them, which is reach"
    assert chosen.target == 2
    assert "Yeemik" in chosen.because


def test_it_swings_without_moving_when_already_in_reach():
    mine = _one(1, 1, 0, 0, team=1)
    goblin = _one(2, 2, 1, 0, team=2)

    chosen = decide(mine, _fight(mine, goblin), {2: {"name": "Yeemik"}})

    assert chosen.move is None
    assert chosen.target == 2


def test_it_walks_as_far_as_it_can_when_it_cannot_reach():
    mine = _one(1, 1, 0, 0, team=1)
    goblin = _one(2, 2, 20, 0, team=2)

    chosen = decide(mine, _fight(mine, goblin), {2: {"name": "Yeemik"}}, speed=6)

    assert chosen.move == (6, 0), "its whole speed, and no further"
    assert chosen.target is None, "nothing within reach to swing at"


def test_it_goes_for_the_nearest():
    mine = _one(1, 1, 0, 0, team=1)
    far = _one(2, 2, 9, 0, team=2)
    near = _one(3, 3, 2, 0, team=2)

    chosen = decide(mine, _fight(mine, far, near), {3: {"name": "Droop"}})

    assert chosen.target == 3


def test_it_does_not_attack_its_own_side():
    """Sides come off the fight, so a guard moved across is not a target."""
    mine = _one(1, 1, 0, 0, team=1)
    friend = _one(2, 2, 1, 0, team=1)

    assert enemies_of(mine, _fight(mine, friend)) == []
    assert decide(mine, _fight(mine, friend), {}).does_nothing


def test_it_leaves_the_fallen_alone():
    mine = _one(1, 1, 0, 0, team=1)
    body = _one(2, 2, 1, 0, team=2, down=True)

    assert decide(mine, _fight(mine, body), {}).does_nothing


def test_a_character_on_the_floor_takes_no_turn():
    mine = _one(1, 1, 0, 0, team=1, down=True)
    goblin = _one(2, 2, 1, 0, team=2)

    assert decide(mine, _fight(mine, goblin), {}).does_nothing


def test_somebody_off_the_map_is_not_walked_towards():
    mine = _one(1, 1, 0, 0, team=1)
    absent = {"id": 2, "entity": 2, "x": None, "y": None, "team": 2}

    assert decide(mine, _fight(mine, absent), {}).does_nothing


# ------------------------------------------------------------------- the loop


@pytest.fixture
def table(qtbot, repos):
    """Marla, handed over; a goblin two squares away; and a secret NPC."""
    campaign = repos.campaigns.ensure_default("Stand-in")
    marla = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Marla",
               data={"hp": 20, "max_hp": 20,
                     "sheet": {"schema": 1, "level": 3, "equipment": ["battleaxe"],
                               "abilities": {"str": 16, "dex": 12, "con": 12,
                                             "int": 10, "wis": 10, "cha": 10}}}))
    goblin = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik",
               data={"hp": 7, "max_hp": 7,
                     "sheet": {"schema": 1, "level": 1, "equipment": ["scimitar"],
                               "overrides": {"ac": 10, "hp_max": 7},
                               "abilities": {"str": 8, "dex": 14, "con": 10,
                                             "int": 10, "wis": 8, "cha": 8}}}))
    hidden = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="The ambusher",
               data={"secrets": "In the rafters."}))

    elsa = repos.accounts.create(campaign.id, "elsa", "goblin-teeth",
                                 character_entity_id=marla.id)
    repos.entities.set_owner(marla.id, elsa.id)
    repos.shares.share(campaign.id, marla.id)
    repos.shares.share(campaign.id, goblin.id)

    enc = repos.encounters.create(campaign.id, "The cave", width=16, height=16)
    tokens = {
        "marla": repos.encounters.add(enc.id, marla.id, initiative=20, x=0, y=0),
        "goblin": repos.encounters.add(enc.id, goblin.id, initiative=10, x=3, y=0),
    }
    repos.encounters.begin(enc.id)
    repos.encounters.sort_into_teams(enc.id)
    repos.encounters.set_simulated(tokens["marla"].id, True)

    server = SessionServer(repos, campaign.id, "Stand-in")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, enc, tokens, marla, goblin, hidden
    server.stop()


def _spin(qapp, coro, timeout=20.0):
    """Run a coroutine while keeping the Qt host's event loop turning.

    The host is Qt and the stand-in is asyncio; in real use they are separate
    processes. Here they share one, so both loops have to advance.
    """
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(coro)
        deadline = loop.time() + timeout
        while not task.done():
            qapp.processEvents()
            loop.run_until_complete(asyncio.sleep(0.001))
            if loop.time() > deadline:
                task.cancel()
                raise TimeoutError("the stand-in did not finish in time")
        return task.result()
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for leftover in pending:
            leftover.cancel()
        if pending:
            async def drain():
                await asyncio.gather(*pending, return_exceptions=True)

            loop.run_until_complete(drain())
        loop.close()


async def _sit(server, entity_id, pause=0.0):
    """Connect a stand-in on a seat and let it settle."""
    token = server.mint_seat(entity_id)
    assert token, "no seat was minted"

    stand_in: Stand_In | None = None

    async def said(_s, _m, _t) -> None:
        return None

    async def encounter(session) -> None:
        if stand_in is not None:
            await stand_in.on_encounter(session)

    session = AgentSession(
        f"ws://127.0.0.1:{server.port}", "", "", said, encounter, seat=token
    )
    stand_in = Stand_In(session, pause=pause)
    task = asyncio.create_task(session.run())
    for _ in range(80):
        await asyncio.sleep(0.05)
        if session.table.me is not None:
            break
    return session, stand_in, task


def test_a_stand_in_gets_in_on_its_seat(table, qapp):
    server, _repos, _enc, _tokens, marla, _goblin, _hidden = table

    async def go():
        session, _stand_in, task = await _sit(server, marla.id)
        try:
            assert session.table.me is not None
            assert session.table.me.character == "Marla"
        finally:
            task.cancel()

    _spin(qapp, go())


def test_a_stand_in_is_not_shown_the_dms_secrets(table, qapp):
    """The reason for the whole arrangement.

    Played by the DM's agent, a handed-over character knows where the ambush is
    and walks around it. On its own seat it cannot: the NPC is not in the bytes.
    """
    server, _repos, _enc, _tokens, marla, _goblin, _hidden = table

    async def go():
        session, _stand_in, task = await _sit(server, marla.id)
        try:
            names = {e.get("name") for e in session.table.entities.values()}
            assert "The ambusher" not in names
            assert "In the rafters" not in str(session.table.entities)
        finally:
            task.cancel()

    _spin(qapp, go())


def test_it_plays_with_autopilot_off(table, qapp):
    """Handing a character over and handing the *table* over are two decisions.

    Somebody who has stepped out should not have their character stand still
    because the DM is running the game themselves. With nothing there to turn
    a sentence into a proposal, the stand-in takes the turn on its own seat.
    """
    server, repos, enc, tokens, marla, _goblin, _hidden = table
    assert not server.autopilot, "this is the case under test"
    assert repos.encounters.get(enc.id).turn_combatant_id == tokens["marla"].id

    async def go():
        _session, stand_in, task = await _sit(server, marla.id)
        try:
            assert stand_in.somebody_will_formalise() is False
            await stand_in.take_the_turn()
            for _ in range(60):
                await asyncio.sleep(0.05)
                if repos.encounters.combatant(tokens["marla"].id).x == 2:
                    break
        finally:
            task.cancel()

    _spin(qapp, go())
    moved = repos.encounters.combatant(tokens["marla"].id)
    assert (moved.x, moved.y) == (2, 0), "it closed to reach and stopped"


def test_it_says_what_it_is_doing_either_way(table, qapp):
    """The table reads the same sentence whichever path the turn took."""
    server, _repos, _enc, _tokens, marla, _goblin, _hidden = table
    said = []

    async def go():
        session, stand_in, task = await _sit(server, marla.id)
        try:
            session.say = lambda text: said.append(text) or _yes()
            await stand_in.take_the_turn()
        finally:
            task.cancel()

    _spin(qapp, go())

    assert said, "it said nothing at all"
    assert said[0].startswith("I "), "it speaks as the player would type"


async def _yes() -> bool:
    return True


def test_it_accepts_the_turn_that_comes_back():
    """The other half. A proposal for our character is answered yes."""
    mine = _one(1, 1, 0, 0, team=1, simulated=True)
    session = _StubSession(_fight(mine, turn=1), {1: {"name": "Marla"}}, "Marla")
    answers = []

    async def answer(action_id, accept, note=""):
        answers.append((action_id, accept))
        return True

    session.answer = answer
    stand_in = Stand_In(session, pause=0.0)

    asyncio.run(stand_in.on_action(session, {"id": "abc", "combatant": 1}))

    assert answers == [("abc", True)]


def test_it_leaves_somebody_elses_proposal_alone():
    """A turn for another character is not ours to answer, machine or not."""
    mine = _one(1, 1, 0, 0, team=1, simulated=True)
    session = _StubSession(_fight(mine, turn=1), {1: {"name": "Marla"}}, "Marla")
    answers = []

    async def answer(action_id, accept, note=""):
        answers.append((action_id, accept))
        return True

    session.answer = answer
    stand_in = Stand_In(session, pause=0.0)

    asyncio.run(stand_in.on_action(session, {"id": "abc", "combatant": 99}))

    assert answers == []


class _StubSession:
    """Just enough of a session to ask "is it my turn?" -- no socket at all.

    The guard under test is about the host publishing the same fight twice,
    which is a thing that happens after *every* change including the stand-in's
    own. Testing it over a real connection would mean the turn had already
    passed by the time the second copy arrived, which is a different question.
    """

    def __init__(self, encounter: dict, entities: dict, character: str) -> None:
        from types import SimpleNamespace

        self.table = SimpleNamespace(
            encounter=encounter,
            entities=entities,
            me=SimpleNamespace(character=character),
        )
        self.said: list[str] = []

    async def say(self, text: str) -> bool:
        self.said.append(text)
        return True

    async def turn_done(self) -> bool:
        return True


def test_it_does_not_take_the_turn_twice():
    """The host publishes the fight after every change, including ours."""
    mine = _one(1, 1, 0, 0, team=1, simulated=True)
    goblin = _one(2, 2, 3, 0, team=2)
    session = _StubSession(
        _fight(mine, goblin, turn=1), {1: {"name": "Marla"}}, "Marla"
    )
    stand_in = Stand_In(session, pause=0.0)

    turns = []

    async def counted():
        turns.append(1)

    stand_in.take_the_turn = counted

    asyncio.run(_twice(stand_in, session))

    assert len(turns) == 1, "the second copy of the same fight took the turn again"


async def _twice(stand_in, session) -> None:
    await stand_in.on_encounter(session)
    await stand_in.on_encounter(session)


def test_a_turn_that_is_not_ours_is_left_alone():
    """Somebody else is up. Nothing to do, and nothing said."""
    mine = _one(1, 1, 0, 0, team=1, simulated=True)
    goblin = _one(2, 2, 3, 0, team=2)
    session = _StubSession(
        _fight(mine, goblin, turn=2), {1: {"name": "Marla"}}, "Marla"
    )
    stand_in = Stand_In(session, pause=0.0)

    turns = []

    async def counted():
        turns.append(1)

    stand_in.take_the_turn = counted
    asyncio.run(stand_in.on_encounter(session))

    assert turns == []


def test_a_character_not_handed_over_is_not_played():
    """Without the flag there is no handover, so there is no standing in."""
    mine = _one(1, 1, 0, 0, team=1)  # no `simulated`
    session = _StubSession(_fight(mine, turn=1), {1: {"name": "Marla"}}, "Marla")
    stand_in = Stand_In(session, pause=0.0)

    turns = []

    async def counted():
        turns.append(1)

    stand_in.take_the_turn = counted
    asyncio.run(stand_in.on_encounter(session))

    assert turns == []


def test_a_character_taken_back_is_not_played(table, qapp):
    server, repos, _enc, tokens, marla, _goblin, _hidden = table
    turns = []

    async def go():
        session, stand_in, task = await _sit(server, marla.id)
        try:
            repos.encounters.set_simulated(tokens["marla"].id, False)
            server.publish_encounter()
            await asyncio.sleep(0.3)

            async def counted():
                turns.append(1)

            stand_in.take_the_turn = counted
            await stand_in.on_encounter(session)
        finally:
            task.cancel()

    _spin(qapp, go())
    assert turns == [], "it kept playing a character it had been given back"


def test_a_seat_that_was_revoked_cannot_connect(table, qapp):
    server, _repos, _enc, _tokens, marla, _goblin, _hidden = table
    token = server.mint_seat(marla.id)
    server.revoke_seat(marla.id)

    async def go():
        session = AgentSession(
            f"ws://127.0.0.1:{server.port}", "", "",
            lambda *a: asyncio.sleep(0), seat=token,
        )
        with pytest.raises(LoginFailed):
            await session.run()

    _spin(qapp, go())


# ------------------------------------------------------- knowing the rules


def test_it_takes_the_speed_limit_from_the_host():
    """Not a guess. The host sends the turn's budget to whoever's turn it is,
    and it is the same figure the move will be judged against."""
    mine = _one(1, 1, 0, 0, team=1, simulated=True)
    fight = _fight(mine, turn=1, budget={"combatant": 1, "speed": 6, "left": 2})
    stand_in = Stand_In(_StubSession(fight, {1: {"name": "Marla"}}, "Marla"))

    assert stand_in.squares_left(fight, mine) == 2


def test_it_falls_back_when_the_host_says_nothing():
    mine = _one(1, 1, 0, 0, team=1, simulated=True)
    fight = _fight(mine, turn=1)
    stand_in = Stand_In(_StubSession(fight, {1: {"name": "Marla"}}, "Marla"))

    assert stand_in.squares_left(fight, mine) == DEFAULT_SPEED


def test_a_budget_for_somebody_else_is_not_ours():
    mine = _one(1, 1, 0, 0, team=1, simulated=True)
    fight = _fight(mine, turn=1, budget={"combatant": 99, "left": 1})
    stand_in = Stand_In(_StubSession(fight, {1: {"name": "Marla"}}, "Marla"))

    assert stand_in.squares_left(fight, mine) == DEFAULT_SPEED


def test_it_walks_only_as_far_as_it_has_left():
    """Half a turn's movement already spent means half a turn's walk."""
    mine = _one(1, 1, 0, 0, team=1)
    goblin = _one(2, 2, 20, 0, team=2)

    chosen = decide(mine, _fight(mine, goblin), {}, speed=2)

    assert chosen.move == (2, 0)


def test_it_does_not_ask_for_a_second_attack():
    """The host allows one. Asking twice is a refusal the table has to read."""
    mine = _one(1, 1, 0, 0, team=1, simulated=True)
    fight = _fight(mine, turn=1, budget={"combatant": 1, "acted": True})
    stand_in = Stand_In(_StubSession(fight, {1: {"name": "Marla"}}, "Marla"))

    assert stand_in.action_spent(fight, mine) is True


# ------------------------------------- the two switches are separate


def _standing(autopilot: bool, members=()):
    """A stand-in whose turn it is, with the table in a given state."""
    mine = _one(1, 1, 0, 0, team=1, simulated=True)
    goblin = _one(2, 2, 3, 0, team=2)
    session = _StubSession(
        _fight(mine, goblin, turn=1), {1: {"name": "Marla"}, 2: {"name": "Yeemik"}},
        "Marla",
    )
    session.table.autopilot = autopilot
    session.table.members = list(members)
    return session, Stand_In(session, pause=0.0), mine


class _Member:
    def __init__(self, role: str) -> None:
        self.role = role


def test_autopilot_off_means_nobody_will_formalise():
    _session, stand_in, _mine = _standing(autopilot=False)
    assert stand_in.somebody_will_formalise() is False


def test_the_switch_on_with_no_agent_is_still_nobody():
    """The switch can be on with nothing connected. Waiting for something that
    is not coming is how a fight stops."""
    _session, stand_in, _mine = _standing(autopilot=True, members=[_Member("player")])
    assert stand_in.somebody_will_formalise() is False


def test_an_agent_with_the_switch_off_is_also_nobody():
    """The host refuses an agent's chat while the switch is off, so it cannot
    formalise anything even though it is sitting there."""
    _session, stand_in, _mine = _standing(autopilot=False, members=[_Member("agent")])
    assert stand_in.somebody_will_formalise() is False


def test_both_together_means_somebody_will():
    _session, stand_in, _mine = _standing(autopilot=True, members=[_Member("agent")])
    assert stand_in.somebody_will_formalise() is True


def test_it_waits_rather_than_acting_when_somebody_will_formalise():
    """With autopilot there, the turn goes the long way round and the DM sees
    the proposal before anything moves."""
    session, stand_in, _mine = _standing(autopilot=True, members=[_Member("agent")])
    done = []
    stand_in.do_it = lambda *a: done.append(1) or _yes()

    asyncio.run(stand_in.take_the_turn())

    assert session.said, "it should still say what it wants"
    assert done == [], "it acted instead of waiting for the proposal"


def test_it_acts_when_nobody_will():
    session, stand_in, _mine = _standing(autopilot=False)
    done = []

    async def acted(mine, chosen):
        done.append(chosen)

    stand_in.do_it = acted

    asyncio.run(stand_in.take_the_turn())

    assert session.said, "it should say what it is doing"
    assert len(done) == 1, "it waited for a proposal nobody was going to make"
