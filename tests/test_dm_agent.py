"""The autopilot agent, against a real host.

These matter more than most tests here, because this is the first client that
is not the app. Anything both ends of the wire quietly assumed is visible for
the first time when a second implementation has to read it.

No model is called anywhere below. What is being checked is the plumbing --
login, what it learns, and above all what it is allowed to say -- and all of
that is decided by the host, not by the model.
"""

from __future__ import annotations

import asyncio

import pytest

from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_NPC, Entity
from canon_keeper_dm_agent.context import build_prompt
from canon_keeper_client import AgentSession, LoginFailed, Table
from canon_keeper_protocol import Member


@pytest.fixture
def hosted(qtbot, repos):
    """A running session with a player and an agent login."""
    campaign = repos.campaigns.ensure_default("Phandalin")
    repos.accounts.create(campaign.id, "marco", "goblin-teeth", display_name="Marco")
    repos.accounts.create(
        campaign.id, "autopilot", "let-me-run-it", role="agent", display_name="Autopilot"
    )
    npc = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Toblen Stonehill",
            summary="Runs the inn.",
            data={"secrets": "He is hiding a Redbrand deserter in the cellar."},
        )
    )
    repos.facts.assert_fact(campaign.id, npc.id, "is hiding", "a Redbrand deserter")

    server = SessionServer(repos, campaign.id, "Agent session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, campaign, npc
    server.stop()


async def _login(server, username, password, timeout=10.0):
    """Log in and keep pumping.

    The pump is deliberately left running: a test that stops reading the socket
    the moment login finishes would see nothing sent afterwards, and would pass
    for the wrong reason when checking that something did *not* arrive.
    """
    heard: list[tuple[Member, str]] = []

    async def on_said(_session, member, text):
        heard.append((member, text))

    session = AgentSession(
        f"ws://127.0.0.1:{server.port}", username, password, on_said
    )

    async def pump():
        try:
            await session.run()
        except asyncio.CancelledError:
            raise
        except Exception:  # the socket closing is how this ends
            pass

    asyncio.create_task(pump())
    async with asyncio.timeout(timeout):
        while session.table.me is None:
            await asyncio.sleep(0.02)
    return session, heard


async def _settle(seconds: float = 0.4) -> None:
    """Let everything in flight arrive."""
    await asyncio.sleep(seconds)


# ------------------------------------------------------------------- logging in


def test_a_second_implementation_can_log_in(hosted, qapp):
    """The protocol is real if something that is not the app can speak it."""
    server, campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        return session

    session = _spin(qapp, go())
    assert session.table.me is not None
    assert session.table.me.role == "agent"
    assert session.table.campaign == "Phandalin"


def test_a_wrong_password_is_refused(hosted, qapp):
    server, _campaign, _npc = hosted

    async def go():
        session = AgentSession(
            f"ws://127.0.0.1:{server.port}",
            "autopilot",
            "not-the-password",
            lambda *_: None,
        )
        with pytest.raises(LoginFailed):
            await session.run()

    _spin(qapp, go())


# ---------------------------------------------------------------- what it knows


def test_it_receives_the_canon(hosted, qapp):
    """It answers from what is true, so the fact log has to arrive."""
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        return session

    session = _spin(qapp, go())
    assert any(f["object"] == "a Redbrand deserter" for f in session.table.facts)


def test_it_receives_the_dms_secrets(hosted, qapp):
    """Standing in for the DM means seeing what they see. A player would not."""
    server, _campaign, npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        return session

    session = _spin(qapp, go())
    entity = session.table.entities[npc.id]
    assert "Redbrand" in entity["data"]["secrets"]


def test_a_player_connecting_the_same_way_gets_neither(hosted, qapp):
    """The same client, a different login: the host decides, not the client."""
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "marco", "goblin-teeth")
        await _settle()
        return session

    session = _spin(qapp, go())
    assert session.table.facts == [], "a player must never receive the canon"
    for entity in session.table.entities.values():
        assert "secrets" not in (entity.get("data") or {})


# ------------------------------------------------------------- what it may say


def test_it_will_not_speak_with_autopilot_off(hosted, qapp):
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        return await session.say("The innkeeper looks up.")

    assert _spin(qapp, go()) is False


def test_it_speaks_once_autopilot_is_on(hosted, qapp):
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        server.set_autopilot(True, by="Genna")
        await _settle()
        return await session.say("The innkeeper looks up.")

    assert _spin(qapp, go()) is True


def test_it_learns_autopilot_changed_without_reconnecting(hosted, qapp):
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        assert session.table.autopilot is False
        server.set_autopilot(True, by="Genna")
        await _settle()
        return session.table.autopilot

    assert _spin(qapp, go()) is True


# ------------------------------------------------------------------ the prompt


def test_the_prompt_leads_with_the_canon():
    table = Table(campaign="Phandalin")
    table.entities = {1: {"id": 1, "name": "Toblen", "kind": "npc"}}
    table.facts = [{"subject": 1, "predicate": "is hiding", "object": "a deserter"}]

    prompt = build_prompt(table, "Elara", "Who are you?")

    assert prompt.index("CANON") < prompt.index("WHO AND WHERE")
    assert "Toblen is hiding a deserter" in prompt


def test_the_prompt_carries_the_line_being_answered():
    prompt = build_prompt(Table(), "Elara", "I check the door for traps.")
    assert "Elara: I check the door for traps." in prompt
    assert "what you are answering" in prompt


def test_the_whole_conversation_goes_in_not_the_last_line():
    """Lines arrive in bursts, so a short window starts halfway through.

    Three people typing at once are three messages in two seconds. A prompt
    holding only those three has cut off the question they are all answering.
    """
    table = Table()
    for index in range(12):
        table.remember("Marco", "player", f"line {index}", at=1000.0 + index)

    prompt = build_prompt(table, "Marco", "and what about the cellar?")

    assert "line 0" in prompt, "the exchange began well above the last line"
    assert "line 11" in prompt


def test_the_agents_own_lines_are_marked_as_its_own():
    """A transcript it cannot pick itself out of is one it will contradict."""
    table = Table()
    table.remember("Marco", "player", "Is the innkeeper in?", at=1000.0)
    table.remember("Autopilot", "agent", "He looks up from the bar.", at=1001.0)

    prompt = build_prompt(table, "Marco", "I ask about the cellar.")

    assert "you: He looks up from the bar." in prompt


def test_the_dm_is_named_as_the_dm():
    table = Table()
    table.remember("Genna", "dm", "There is something behind the door.", at=1000.0)
    prompt = build_prompt(table, "Marco", "I listen at it.")
    assert "(the DM)" in prompt


def test_a_long_gap_is_marked_as_one():
    """Where the current exchange began, rather than leaving it to be guessed."""
    table = Table()
    table.remember("Marco", "player", "Goodnight then.", at=1000.0)
    table.remember("Marco", "player", "Right, where were we?", at=5000.0)

    prompt = build_prompt(table, "Marco", "I open the door.")

    assert "\n---\n" in prompt


def test_lines_close_together_are_one_exchange():
    table = Table()
    table.remember("Marco", "player", "I look at the door.", at=1000.0)
    table.remember("Elsa", "player", "I look at Marco.", at=1002.0)

    prompt = build_prompt(table, "Marco", "Fine, I open it.")

    # The marker is a line of its own; the heading mentions it in quotes.
    assert "\n---\n" not in prompt


def test_what_is_being_answered_is_not_printed_twice():
    """It is already in the transcript; every line remembered as it arrives."""
    table = Table()
    table.remember("Marco", "player", "I check the door for traps.", at=1000.0)

    prompt = build_prompt(table, "Marco", "I check the door for traps.")

    assert prompt.count("I check the door for traps.") == 1


def test_the_fight_is_in_the_prompt():
    """It cannot narrate a combat it is not told the shape of."""
    table = Table()
    table.entities = {1: {"id": 1, "name": "Brok Ironfoot", "kind": "pc"}}
    table.encounter = {
        "name": "The cave",
        "width": 10,
        "height": 8,
        "round": 2,
        "turn": 11,
        "combatants": [{"id": 11, "entity": 1, "x": 3, "y": 4}],
        "obstacles": [[5, 5]],
    }

    prompt = build_prompt(table, "Marco", "I charge the goblin.")

    assert "THE FIGHT:" in prompt
    assert "Brok Ironfoot at 3,4" in prompt
    assert "round 2" in prompt


def test_there_is_no_fight_section_when_there_is_no_fight():
    assert "THE FIGHT:" not in build_prompt(Table(), "Marco", "Hello?")


def test_the_instructions_say_to_answer_the_dm():
    from canon_keeper_dm_agent.context import SYSTEM

    assert "the DM" in SYSTEM
    assert "take the table back with the switch" in SYSTEM


def test_a_private_line_is_marked_as_one():
    """The DM's direction did not reach the players, and it must read that way."""
    table = Table()
    table.remember(
        "Genna", "dm", "Make them nervous.", at=1000.0, aside=True
    )
    prompt = build_prompt(table, "Marco", "I listen at the door.")
    assert "privately to you" in prompt


def test_a_public_dm_line_is_not_marked_private():
    table = Table()
    table.remember("Genna", "dm", "The innkeeper looks up.", at=1000.0)
    prompt = build_prompt(table, "Marco", "I wave.")
    assert "privately to you" not in prompt


def test_the_instructions_say_to_hide_the_hand_on_the_tiller():
    """The players must not be able to tell the DM stepped in.

    Half of it is not repeating the words back. The other half is that it goes
    on applying: a standing instruction stays standing, so working it in once
    and forgetting it is as visible as quoting it.
    """
    from canon_keeper_dm_agent.context import SYSTEM

    assert "in your own words" in SYSTEM
    assert "as though it had always been true" in SYSTEM
    assert "standing instruction stays standing" in SYSTEM
    assert "do not restart the scene" in SYSTEM


def test_the_map_instructions_are_separate():
    """Only sent when the tools are, so it is never told about a missing button."""
    from canon_keeper_dm_agent.context import SYSTEM, WITH_TOOLS

    assert "start_combat" in WITH_TOOLS
    assert "start_combat" not in SYSTEM


def test_recent_chat_is_bounded():
    """An agent needs the scene, not the campaign. Unbounded is a slow leak."""
    table = Table()
    for i in range(200):
        table.remember("Elara", "player", f"line {i}")

    assert len(table.recent) == Table.RECENT_LIMIT
    assert table.recent[-1]["text"] == "line 199"


def test_an_empty_table_still_builds_a_prompt():
    """First message of a brand new campaign must not crash it."""
    assert "Answer as the DM" in build_prompt(Table(), "Elara", "Hello?")


# ------------------------------------------------------------------------ util


def _spin(qapp, coro, timeout=15.0):
    """Run a coroutine while keeping the Qt host's event loop turning.

    The host is a Qt server and the agent is asyncio; in real use they are in
    different processes. Here they share one, so both loops have to advance.
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
                raise TimeoutError("the agent did not finish in time")
        return task.result()
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for leftover in pending:
            leftover.cancel()
        if pending:
            # Built inside the loop: asyncio.gather() called from out here
            # binds to whatever loop is current, which is not this one.
            async def drain():
                await asyncio.gather(*pending, return_exceptions=True)

            loop.run_until_complete(drain())
        loop.close()
