"""The MCP server has exactly the authority of the login it holds.

That is the whole claim, and it is worth testing against a real host rather than
asserting it in a docstring. A tool call is an ordinary message on the wire, so
a player driving one by voice can do what that player could do by hand -- and
nothing else.

The interesting cases are the refusals: a request for someone else's character,
and an edit that comes back as "sent to your DM" rather than "done".
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import replace

import pytest

from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper_client import AgentSession
from canon_keeper_protocol import enrol
from canon_keeper_mcp.server import CanonKeeperTools, build_server


@pytest.fixture
def hosted(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Phandalin")
    elara = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Elara",
            data={"sheet": {"schema": 1, "hp": 12, "max_hp": 12}},
        )
    )
    villain = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Iarno Albrek",
            data={"secrets": "He is the Redbrand leader."},
        )
    )
    marco = repos.accounts.create(
        campaign.id,
        "marco",
        "goblin-teeth",
        display_name="Marco",
        character_entity_id=elara.id,
    )
    repos.entities.set_owner(elara.id, marco.id)

    server = SessionServer(repos, campaign.id, "MCP session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, campaign, elara, villain
    server.stop()


async def _seat(server, username, password):
    """A logged-in MCP tool surface, still pumping."""
    session = AgentSession(
        f"ws://127.0.0.1:{server.port}", username, password, _ignore
    )

    async def pump():
        try:
            await session.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    asyncio.create_task(pump())
    async with asyncio.timeout(10):
        while session.table.me is None:
            await asyncio.sleep(0.02)
    await asyncio.sleep(0.4)
    return CanonKeeperTools(session)


async def _ignore(*_args) -> None:
    pass


# ------------------------------------------------------------------- the tools


def test_the_server_advertises_its_tools(qapp, hosted):
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return await build_server(tools.session).list_tools()

    names = {tool.name for tool in _spin(qapp, go())}
    assert names == {
        "whats_happening",
        "who_and_where",
        "my_characters",
        "the_fight",
        "say",
        "roll",
        "update_my_character",
        # Enough to *play* a seat, not only to watch one. Reading the table and
        # saying things is a spectator; a player also has to be told something
        # happened, answer the turn put to them, and say when they are done.
        "read_pending",
        "wait_for_update",
        "the_turn_on_offer",
        "answer_a_proposal",
        "roll_my_death_save",
        "finish_my_turn",
    }


def test_the_seat_can_see_a_fight_but_not_touch_it(qapp, hosted, repos):
    """A player does not move tokens in the app, so neither does their seat.

    The read is worth having -- "whose turn is it, and what is next to me" is
    exactly what a player asks. Anything that moves a token is the DM's, and
    there is no tool here that even asks.
    """
    server, campaign, elara, _villain = hosted
    encounter = repos.encounters.create(campaign.id, "The cellar", width=8, height=6)
    repos.encounters.add(encounter.id, elara.id, initiative=15, x=2, y=2)
    repos.encounters.toggle_obstacle(encounter.id, 1, 1)
    repos.encounters.begin(encounter.id)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.the_fight(), await build_server(tools.session).list_tools()

    fight, offered = _spin(qapp, go())

    assert fight["fighting"] is True
    assert fight["name"] == "The cellar"
    assert fight["whose_turn"] == "Elara"
    assert fight["standing"][0]["x"] == 2
    assert fight["in_the_way"] == [[1, 1]]

    names = {tool.name for tool in offered}
    for forbidden in ("move", "place", "start_combat", "next_turn", "obstacle"):
        assert not any(forbidden in name for name in names), (
            f"a player's seat offers {forbidden}, which is the DM's to do"
        )


def test_no_fight_says_so(qapp, hosted):
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.the_fight()

    assert _spin(qapp, go()) == {"fighting": False}


def test_it_reports_the_scene(qapp, hosted):
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.whats_happening()

    scene = _spin(qapp, go())
    assert scene["campaign"] == "Phandalin"
    assert scene["you"] == "Elara"


def test_it_sees_its_own_character(qapp, hosted):
    server, _campaign, elara, _villain = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.my_characters()

    mine = _spin(qapp, go())
    assert [c["name"] for c in mine] == ["Elara"]


def test_it_never_sees_an_unshared_npcs_secrets(qapp, hosted):
    """Not filtered here. It never arrived."""
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.who_and_where()

    seen = _spin(qapp, go())
    assert "Iarno Albrek" not in {e["name"] for e in seen}


# ----------------------------------------------------------------- the actions


def test_saying_something_reaches_the_table(qapp, hosted):
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        await tools.say("I check the door for traps.")
        await asyncio.sleep(0.4)
        return server.history()

    said = [m["text"] for m in _spin(qapp, go())]
    assert "I check the door for traps." in said


def test_a_roll_is_asked_for_not_decided(qapp, hosted):
    """The host rolls. A client that invented the number would be ignored."""
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        answer = await tools.roll("2d6+3")
        await asyncio.sleep(0.4)
        return answer, server.history()

    answer, history = _spin(qapp, go())
    assert "host" in answer.lower()
    assert any("2d6+3" in m.get("text", "") for m in history)


def test_a_change_is_a_request_not_a_write(qapp, hosted, repos):
    """The honest return value is "sent", because nothing has been applied."""
    server, _campaign, elara, _villain = hosted
    before = repos.entities.get(elara.id).version

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        answer = await tools.update_my_character(
            elara.id, {"data": {"sheet": {"level": 5}}}
        )
        await asyncio.sleep(0.4)
        return answer

    answer = _spin(qapp, go())
    assert "DM" in answer
    assert "approve" in answer.lower() or "refuse" in answer.lower()
    assert repos.entities.get(elara.id).version == before, (
        "an MCP tool call must not write to the campaign"
    )


def test_it_cannot_touch_someone_elses_character(qapp, hosted, repos):
    server, _campaign, _elara, villain = hosted
    before = repos.entities.get(villain.id).version

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        await tools.update_my_character(villain.id, {"summary": "actually harmless"})
        await asyncio.sleep(0.4)

    _spin(qapp, go())
    assert repos.entities.get(villain.id).version == before
    assert repos.entities.get(villain.id).summary != "actually harmless"


# ------------------------------------------------------------------------ util


def _spin(qapp, coro, timeout=20.0):
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(coro)
        deadline = loop.time() + timeout
        while not task.done():
            qapp.processEvents()
            loop.run_until_complete(asyncio.sleep(0.005))
            if loop.time() > deadline:
                task.cancel()
                raise TimeoutError("the MCP seat did not finish in time")
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


# ------------------------------------------- a whole fight, from the seat
#
# The claim these make together: an agent holding one player's login can play a
# combat from beginning to end through these tools and nothing else. Not "can
# read a fight" -- can *play* one. Every step below is a thing a person does at
# the table, and each was unreachable from a seat until it had a tool.
#
# The DM stays where they are: Canon Keeper and autopilot. Nothing here needs a
# second privileged path, because every tool is an ordinary message the host
# checks exactly as it checks the app's.


def _fight_with(repos, campaign, hero, server, *, hp=12):
    """A begun fight with the player's character up first."""
    goblin = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Yeemik",
            data={"hp": 7, "max_hp": 7, "sheet": {"schema": 1, "level": 1}},
        )
    )
    enc = repos.encounters.create(campaign.id, "The cave", width=12, height=12)
    tokens = {
        "hero": repos.encounters.add(enc.id, hero.id, initiative=20, x=0, y=0),
        "goblin": repos.encounters.add(enc.id, goblin.id, initiative=5, x=1, y=0),
    }
    repos.encounters.begin(enc.id)
    server.publish_encounter()
    return enc, tokens


def test_a_seat_is_told_its_turn_came_round(qapp, hosted, repos):
    """Without this a seat can only poll and compare, which is not playing."""
    server, campaign, elara, _villain = hosted
    _fight_with(repos, campaign, elara, server)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        tools.read_pending()  # start from now
        server._broadcast_system("It is Elara's turn.")
        return await tools.wait_for_update(5.0)

    news = _spin(qapp, go())
    assert news["said"], "nothing came back from waiting"
    assert any("Elara" in line["text"] for line in news["said"])
    assert news["whose_turn"] == "Elara"


def test_waiting_returns_at_once_when_something_is_already_unread(qapp, hosted, repos):
    """Waiting for the *next* thing while holding an unread one misses turns."""
    server, campaign, elara, _villain = hosted
    _fight_with(repos, campaign, elara, server)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        tools.read_pending()
        server._broadcast_system("Something happened.")
        await asyncio.sleep(0.3)
        return await tools.wait_for_update(30.0)

    news = _spin(qapp, go())
    assert news["waited"] == 0.0, "it waited for a thing it had already been told"
    assert news["said"]


def test_reading_twice_does_not_say_it_twice(qapp, hosted, repos):
    """The watermark moves when it is read, so a catch-up is not a re-read."""
    server, campaign, elara, _villain = hosted
    _fight_with(repos, campaign, elara, server)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        tools.read_pending()
        server._broadcast_system("Once.")
        await asyncio.sleep(0.3)
        return tools.read_pending(), tools.read_pending()

    first, second = _spin(qapp, go())
    assert any("Once." in line["text"] for line in first["said"])
    assert second["said"] == []


def test_a_seat_plays_a_whole_turn_and_the_host_rolls_it(qapp, hosted, repos):
    """Say it, read what was worked out, accept it, and be done.

    The loop a player actually goes round, with no step of it left to the app.
    """
    server, campaign, elara, _villain = hosted
    _enc, tokens = _fight_with(repos, campaign, elara, server)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        tools.read_pending()

        # 1. The player says what they mean to do.
        await tools.say("I step up and swing at the goblin.")
        await asyncio.sleep(0.3)

        # 2. The DM -- or autopilot -- works that out in rules and puts it back.
        assert server.offer_turn(tokens["hero"].id, move=[1, 1]) == ""
        await asyncio.sleep(0.4)

        # 3. The seat reads what is on offer, in words.
        offered = tools.the_turn_on_offer()

        # 4. And accepts it. Only now does anything move.
        said = await tools.answer_a_proposal(True)
        await asyncio.sleep(0.5)

        # 5. And says it is finished.
        done = await tools.finish_my_turn()
        await asyncio.sleep(0.3)
        return offered, said, done

    offered, said, done = _spin(qapp, go())
    assert offered["waiting"] is True
    assert "1,1" in offered["what_it_would_do"], offered
    assert "Accepted" in said
    assert "done" in done.lower()
    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (1, 1), "accepting did not make it happen"


def test_a_seat_can_refuse_and_say_what_it_meant_instead(qapp, hosted, repos):
    """The difference between "no" and "no, I go round the other side"."""
    server, campaign, elara, _villain = hosted
    _enc, tokens = _fight_with(repos, campaign, elara, server)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        assert server.offer_turn(tokens["hero"].id, move=[1, 1]) == ""
        await asyncio.sleep(0.4)
        said = await tools.answer_a_proposal(False, "I stay back and watch the door.")
        await asyncio.sleep(0.4)
        return said, tools.the_turn_on_offer()

    said, after = _spin(qapp, go())
    assert "Refused" in said and "door" in said
    assert after["waiting"] is False, "a refused turn was still on offer"
    assert repos.encounters.combatant(tokens["hero"].id).x == 0, "it moved anyway"


def test_answering_nothing_says_so_rather_than_answering_into_silence(qapp, hosted, repos):
    server, campaign, elara, _villain = hosted
    _fight_with(repos, campaign, elara, server)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return await tools.answer_a_proposal(True)

    said = _spin(qapp, go())
    assert "no turn waiting" in said.lower()


def test_a_withdrawn_offer_stops_being_on_offer(qapp, hosted, repos):
    """An offer can be taken back between reading it and answering it.

    A seat that held the first one would answer a turn nobody is waiting on.
    """
    server, campaign, elara, _villain = hosted
    _enc, tokens = _fight_with(repos, campaign, elara, server)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        assert server.offer_turn(tokens["hero"].id, move=[1, 1]) == ""
        await asyncio.sleep(0.4)
        before = tools.the_turn_on_offer()
        server._withdraw_for(tokens["hero"].id)
        await asyncio.sleep(0.4)
        return before, tools.the_turn_on_offer()

    before, after = _spin(qapp, go())
    assert before["waiting"] is True
    assert after["waiting"] is False


def test_a_seat_can_make_its_own_death_save(qapp, hosted, repos):
    """The loudest moment the game has, and it was the host's to roll alone.

    The host still rolls it when the clock runs out, so this is not the
    difference between dying and not. It is the difference between making your
    own death save and watching a number change in a list.
    """
    server, campaign, elara, _villain = hosted
    _enc, tokens = _fight_with(repos, campaign, elara, server)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        # Down, and *owed* a save -- which is not the same as being down. The
        # host owes one when the turn lands on a dying character, and refuses a
        # save nobody asked for, so the turn is passed round rather than the
        # flag being set by hand.
        repos.entities.update(
            replace(
                repos.entities.get(elara.id),
                data={**(repos.entities.get(elara.id).data or {}), "hp": 0},
            )
        )
        repos.encounters.set_down(tokens["hero"].id, True)
        server.run_turn("next")   # on to the goblin
        server.run_turn("next")   # and round to Elara, who is dying
        server.publish_encounter()
        # Waited for rather than slept past. A fixed pause is long enough until
        # the machine is busy, and then it is a test that fails once a fortnight
        # and teaches you to run it again.
        async with asyncio.timeout(10):
            while not any(
                c.get("down") for c in (tools.session.table.encounter or {}).get(
                    "combatants"
                ) or []
            ):
                await asyncio.sleep(0.05)
        seen = tools.the_fight()
        said = await tools.roll_my_death_save()
        await asyncio.sleep(0.4)
        return seen, said

    seen, said = _spin(qapp, go())
    mine = next(c for c in seen["standing"] if c["who"] == "Elara")
    assert mine["down"] is True, "a seat could not tell it was dying"
    assert "Rolled" in said
    # Resolved, not which way. A natural twenty on a death save revives the
    # character and *clears* the tally, so "the counters moved" is an assertion
    # about a d20 -- it holds nineteen runs in twenty and then looks like a bug
    # in the tool. Either outcome is the save having happened.
    after = repos.encounters.combatant(tokens["hero"].id)
    tallied = (after.death_successes + after.death_failures) >= 1
    revived = not after.down
    assert tallied or revived, "nothing was rolled"


# --------------------------------------------------- getting in for the first time
#
# A seat had two ways in: a username and password that already existed, and a
# seat token the DM minted. Neither is what a new player has. What they have is
# an invite -- so an agent playing a seat had to borrow an account made somewhere
# else first, which is a strange first step for the client that is supposed to be
# the whole client.


def test_a_seat_can_join_on_an_invite_and_choose_its_own_password(qapp, hosted, repos):
    """The account does not exist until this runs, and then it does.

    Enrolment and login stay two round trips, exactly as in the app: this makes
    the account and stops, and the same client turns round and logs in with what
    it just chose. A host that admitted somebody straight off an enrolment would
    have two doors into a session and the second is the one nobody looks at
    again.
    """
    server, campaign, _elara, _villain = hosted
    newcomer = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Sable")
    )
    code = server.invite_for(newcomer.id)
    assert code, "the host would not mint an invite"
    assert repos.accounts.by_username(campaign.id, "sable") is None

    async def go():
        session = AgentSession(
            f"ws://127.0.0.1:{server.port}",
            "sable",
            "a-password-of-my-own",
            _ignore,
            invite=code,
        )

        async def pump():
            try:
                await session.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        asyncio.create_task(pump())
        async with asyncio.timeout(15):
            while session.table.me is None:
                await asyncio.sleep(0.02)
        await asyncio.sleep(0.4)
        return session

    session = _spin(qapp, go())
    assert session.table.me is not None, "never got in"
    account = repos.accounts.by_username(campaign.id, "sable")
    assert account is not None, "the account was not made"
    assert session._invite == "", "the code was kept after it was used"


def test_the_same_password_works_on_the_next_run(qapp, hosted, repos):
    """Which is the point of choosing it. The invite is used once.

    An agent that needed the code every time would need a DM to mint one every
    time, and the code is the thing worth stealing.
    """
    server, campaign, _elara, _villain = hosted
    newcomer = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Sable")
    )
    code = server.invite_for(newcomer.id)

    async def join(invite):
        session = AgentSession(
            f"ws://127.0.0.1:{server.port}", "sable", "a-password-of-my-own",
            _ignore, invite=invite,
        )

        async def pump():
            try:
                await session.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        asyncio.create_task(pump())
        async with asyncio.timeout(15):
            while session.table.me is None:
                await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        return session

    async def go():
        first = await join(code)
        await first.close() if hasattr(first, "close") else None
        await asyncio.sleep(0.3)
        # No code this time.
        second = await join("")
        return first, second

    first, second = _spin(qapp, go())
    assert first.table.me is not None
    assert second.table.me is not None, "the password it chose did not work again"


def test_a_wrong_invite_is_refused_and_makes_nothing(qapp, hosted, repos):
    """A guess costs a scrypt and leaves no account behind."""
    server, campaign, _elara, _villain = hosted
    repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Sable")
    )

    async def go():
        session = AgentSession(
            f"ws://127.0.0.1:{server.port}", "sable", "whatever",
            _ignore, invite="ZZZZZ-ZZZZZ",
        )
        with contextlib.suppress(Exception):
            await asyncio.wait_for(session.run(), 10)
        return session

    _spin(qapp, go())
    assert repos.accounts.by_username(campaign.id, "sable") is None, (
        "a refused invite still made an account"
    )


def test_a_whole_invite_carries_its_own_code(qapp, hosted, repos):
    """A DM sends one string. The code is in the fragment, which never goes to
    a server -- so the thing you paste is safe to be the thing you paste.
    """
    server, campaign, _elara, _villain = hosted
    newcomer = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Sable")
    )
    code = server.invite_for(newcomer.id)
    whole = enrol.wrap(f"ws://127.0.0.1:{server.port}", code)

    address, carried = enrol.unwrap(whole)
    assert address == f"ws://127.0.0.1:{server.port}"
    assert enrol.clean_code(carried) == enrol.clean_code(code)


# ------------------------------------------------ one player, however many ways in


def test_one_login_from_two_places_is_one_person_at_the_table(qapp, hosted, repos):
    """Reported from a real table: "I had mike joining three times."

    A login may hold several connections at once and that is ordinary -- the app
    and an MCP seat, or a client that has not noticed it was replaced. The roster
    was one entry per socket, so somebody who joined from two places was two
    people at the table. That is the list a DM reads to see who has arrived.
    """
    server, _campaign, _elara, _villain = hosted

    async def go():
        first = await _seat(server, "marco", "goblin-teeth")
        second = await _seat(server, "marco", "goblin-teeth")
        await asyncio.sleep(0.4)
        return first, second, server.members

    _first, _second, members = _spin(qapp, go())
    marcos = [m for m in members if m.name == "Marco"]
    assert len(marcos) == 1, f"Marco appears {len(marcos)} times: {members}"


def test_two_different_logins_are_still_two_people(qapp, hosted, repos):
    """The other half. Collapsing on the account must not collapse the table."""
    server, campaign, _elara, _villain = hosted
    other = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Sable")
    )
    account = repos.accounts.create(
        campaign.id, "ada", "arrows-please", display_name="Ada",
        character_entity_id=other.id,
    )
    repos.entities.set_owner(other.id, account.id)

    async def go():
        await _seat(server, "marco", "goblin-teeth")
        await _seat(server, "ada", "arrows-please")
        await asyncio.sleep(0.4)
        return server.members

    members = _spin(qapp, go())
    names = sorted(m.name for m in members)
    assert names == ["Ada", "Marco"], names


# ----------------------------------------------- does the waiting actually work
#
# The tests above run the session and the tools on one loop. The real program
# does not: MCPServer.run owns the main thread's loop and the socket lives on
# another, in another thread. So the single-loop tests exercise the branch of
# `on_my_loop` that is a plain await, and say nothing about the crossing -- which
# is the branch that carries every write and every wait in production.


def test_waiting_wakes_when_the_session_is_on_another_thread(qapp, hosted, repos):
    """The shape the real program has, which no other test here exercises.

    `on_my_loop` marshals onto the loop that owns the socket. On one loop it is
    a plain await, so a single-loop test cannot tell a working bridge from a
    missing one.
    """
    server, campaign, elara, _villain = hosted

    ready = threading.Event()
    box: dict = {}

    def run_session() -> None:
        async def go() -> None:
            session = AgentSession(
                f"ws://127.0.0.1:{server.port}", "marco", "goblin-teeth", _ignore
            )
            box["session"] = session
            task = asyncio.create_task(session.run())
            while session.table.me is None:
                await asyncio.sleep(0.02)
            ready.set()
            with contextlib.suppress(Exception):
                await task

        asyncio.run(go())

    thread = threading.Thread(target=run_session, daemon=True)
    thread.start()
    # Pumped from here, because the host is a Qt server: it cannot accept the
    # connection unless somebody is turning its loop, and the session is on a
    # thread of its own rather than inside `_spin`.
    deadline = time.monotonic() + 15
    while not ready.is_set() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert ready.is_set(), "the session never logged in"
    tools = CanonKeeperTools(box["session"])

    # Drain, then make something happen and wait for it -- from this thread,
    # on a different loop from the socket's.
    tools.read_pending()

    async def go():
        async def poke():
            await asyncio.sleep(0.3)
            server._broadcast_system("Something happened across the thread.")
        asyncio.get_running_loop().run_in_executor(None, lambda: None)
        waiter = asyncio.create_task(tools.wait_for_update(8.0))
        await poke()
        return await waiter

    news = _spin(qapp, go())
    assert news.get("said"), f"nothing came back across the loops: {news}"
    assert any("across the thread" in line["text"] for line in news["said"])
    assert news["waited"] < 8.0, "it waited out the whole timeout"


# There was a test here for the lost-wake race -- a line remembered with no stir
# behind it, checking that waiting noticed it promptly. It passed with the race
# *and* with the fix, so it proved nothing: a live session is chatty enough that
# the next frame from the host arrives within milliseconds and wakes the waiter
# anyway. The fix below is still worth having (a wake that lands between reading
# and arming is genuinely lost, and the re-check floor bounds the cost at 0.2s),
# but it is not guarded by anything here, and a green test that cannot fail is
# worse than an honest gap.


def test_waiting_gives_up_rather_than_hanging(qapp, hosted, repos):
    """A tool that never returns looks exactly like one that has crashed."""
    server, _campaign, _elara, _villain = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        tools.read_pending()
        return await tools.wait_for_update(1.0)

    news = _spin(qapp, go())
    assert news["said"] == []
    assert 0.5 <= news["waited"] <= 3.0, f"waited {news['waited']}s for a 1s wait"


def test_saying_something_works_from_the_other_thread(qapp, hosted, repos):
    """The arrangement the real program has, for the tool people use most.

    `say` goes through `on_my_loop`, which on a single loop is a plain await --
    so every other test of it passes whether the bridge works or not. This one
    puts the session on its own loop in its own thread, which is what
    `MCPServer.run` does, and calls the tool from the other side.
    """
    server, _campaign, _elara, _villain = hosted
    ready = threading.Event()
    box: dict = {}

    def run_session() -> None:
        async def go() -> None:
            session = AgentSession(
                f"ws://127.0.0.1:{server.port}", "marco", "goblin-teeth", _ignore
            )
            box["session"] = session
            task = asyncio.create_task(session.run())
            while session.table.me is None:
                await asyncio.sleep(0.02)
            ready.set()
            with contextlib.suppress(Exception):
                await task

        asyncio.run(go())

    threading.Thread(target=run_session, daemon=True).start()
    deadline = time.monotonic() + 15
    while not ready.is_set() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert ready.is_set(), "the session never logged in"

    tools = CanonKeeperTools(box["session"])

    async def go():
        return await tools.say("I check the door for traps.")

    said = _spin(qapp, go())
    assert "Said:" in said

    # It reached the *host*, not just the tool. Pumped, because the host is Qt.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        qapp.processEvents()
        if any(
            "traps" in (line.get("text") or "")
            for line in server.history(limit=50, for_dm=True)
        ):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the line never reached the host")
