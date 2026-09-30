"""When the agent takes its turn.

The first version answered every line a player typed, which at a table is three
people talking to each other and being interrupted three times -- each answer
arriving seconds late, addressed to something the conversation has left behind.

A turn is a lull, not a message. These tests are about that, and about the two
things that fall out of it: only one answer in flight, and the human winning any
race with the machine.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from canon_keeper_client import Table
from canon_keeper_dm_agent.responder import Responder

#: Short enough to keep the suite quick, long enough to be a real pause.
PAUSE = 0.05


class _Member:
    def __init__(self, role: str, label: str = "Elara") -> None:
        self.role = role
        self.label = label


class _Session:
    def __init__(self, autopilot: bool = True, players: int = 1) -> None:
        self.table = Table(autopilot=autopilot)
        # Who else is at the table. It matters: the DM speaking means "I have
        # answered" when players are present, and "I am the table" when they
        # are not.
        self.table.members = [_Member("player", f"Player {i}") for i in range(players)]


@pytest.fixture
def spoken() -> list[str]:
    return []


@pytest.fixture
def turns() -> list[list[tuple[str, str]]]:
    """Every batch of lines the model was asked about."""
    return []


@pytest.fixture
def responder(spoken, turns) -> Responder:
    def answer(_table, lines):
        turns.append(list(lines))
        return f"answering {len(lines)} line(s)"

    async def say(text: str) -> None:
        spoken.append(text)

    # The monster pause is the same short one, so the suite stays quick.
    return Responder(answer, say, quiet_for=PAUSE, monster_pause=PAUSE)


async def _settle(multiple: float = 6.0) -> None:
    """Long enough for a pause to elapse and the answer to be delivered."""
    await asyncio.sleep(PAUSE * multiple)


# ------------------------------------------------------------------- one turn


@pytest.mark.asyncio
async def test_it_answers_after_the_table_pauses(responder, spoken):
    session = _Session()
    await responder.heard(session, _Member("player"), "I open the door.")

    assert spoken == [], "it must not answer instantly"
    await _settle()
    assert len(spoken) == 1


@pytest.mark.asyncio
async def test_a_burst_becomes_one_answer(responder, spoken, turns):
    """Three players talking is one turn, not three interruptions."""
    session = _Session()
    for text in ("I open the door.", "Wait!", "Too late."):
        await responder.heard(session, _Member("player"), text)
        await asyncio.sleep(PAUSE / 4)

    await _settle()

    assert len(spoken) == 1
    assert len(turns[0]) == 3, "all three lines should be answered together"


@pytest.mark.asyncio
async def test_every_speaker_in_the_burst_reaches_the_model(responder, turns):
    session = _Session()
    await responder.heard(session, _Member("player", "Elara"), "I go left.")
    await responder.heard(session, _Member("player", "Mirt"), "I go right.")

    await _settle()

    assert {speaker for speaker, _text in turns[0]} == {"Elara", "Mirt"}


@pytest.mark.asyncio
async def test_more_talking_resets_the_pause(responder, spoken):
    """It waits for the table to stop, not for a fixed time after the first line."""
    session = _Session()
    for _ in range(4):
        await responder.heard(session, _Member("player"), "still talking")
        await asyncio.sleep(PAUSE * 0.6)

    assert spoken == [], "it should still be waiting"
    await _settle()
    assert len(spoken) == 1


# -------------------------------------------------------- one answer in flight


@pytest.mark.asyncio
async def test_it_does_not_start_a_second_answer_while_thinking(spoken, turns):
    """Otherwise a slow model produces two replies racing each other."""
    started = asyncio.Event()

    def slow_answer(_table, lines):
        turns.append(list(lines))
        started.set()
        # Blocking on purpose: this runs in a worker thread, as the real one does.
        import time

        time.sleep(PAUSE * 4)
        return "a considered reply"

    async def say(text):
        spoken.append(text)

    responder = Responder(slow_answer, say, quiet_for=PAUSE)
    session = _Session()

    await responder.heard(session, _Member("player"), "first")
    await asyncio.wait_for(started.wait(), timeout=2)

    # The table keeps talking while it thinks.
    await responder.heard(session, _Member("player"), "second")
    await responder.heard(session, _Member("player"), "third")
    await _settle(20)

    assert len(spoken) == 2, "one answer for the first line, one for the rest"
    assert len(turns) == 2
    assert len(turns[1]) == 2, "what arrived while thinking becomes the next turn"


# ------------------------------------------------- the DM is at the table too
#
# This was once the other way round: a line from the DM dropped whatever was
# queued, on the grounds that the human had answered it. At a real table that
# is wrong. Somebody who has switched autopilot on and then types is talking
# *to* the agent, and swallowing it is how autopilot looks broken -- you switch
# it on, say something, and nothing ever happens.
#
# What takes the table back is the switch, which is instant and needs nothing
# from the agent. A sentence is just a sentence.


@pytest.mark.asyncio
async def test_the_dm_is_answered_like_anyone_else(responder, spoken):
    session = _Session(players=2)
    await responder.heard(session, _Member("dm", "Genna"), "Roll initiative.")
    await _settle()
    assert len(spoken) == 1


@pytest.mark.asyncio
async def test_a_lone_dm_is_answered(responder, spoken):
    """Trying it out alone must work: that is how anyone first sees it run."""
    session = _Session(players=0)
    await responder.heard(session, _Member("dm", "Genna"), "Is the innkeeper in?")
    await _settle()
    assert len(spoken) == 1


@pytest.mark.asyncio
async def test_the_dm_joins_the_turn_rather_than_cancelling_it(responder, turns):
    """A player and the DM in the same breath are one exchange, not a race."""
    session = _Session(players=1)
    await responder.heard(session, _Member("player"), "Is the innkeeper in?")
    await responder.heard(session, _Member("dm", "Genna"), "And is the door barred?")

    await _settle()

    assert len(turns) == 1
    assert len(turns[0]) == 2, "both lines, one answer"


# ------------------------------------------------------- the monsters' turns
#
# Nobody says "it is the goblin's turn" out loud. Without the map arriving,
# the agent would sit through its own turn waiting to be spoken to.


def _fight(turn=11, entity=3):
    return {
        "id": 1, "name": "x", "width": 10, "height": 10, "round": 1,
        "turn": turn,
        "combatants": [{"id": turn, "entity": entity, "x": 0, "y": 0}],
    }


@pytest.mark.asyncio
async def test_a_monsters_turn_is_taken(responder, turns):
    session = _Session(players=1)
    session.table.entities = {3: {"id": 3, "name": "Yeemik", "owner_account_id": None}}
    session.table.encounter = _fight()

    await responder.turn_came_round(session)
    await _settle()

    assert len(turns) == 1
    assert "Yeemik" in turns[0][0][1]


@pytest.mark.asyncio
async def test_a_players_turn_is_not(responder, turns):
    """Theirs is proposed and confirmed. Taking it would be playing for them."""
    session = _Session(players=1)
    session.table.entities = {3: {"id": 3, "name": "Brok", "owner_account_id": 7}}
    session.table.encounter = _fight()

    await responder.turn_came_round(session)
    await _settle()

    assert turns == []


@pytest.mark.asyncio
async def test_the_same_turn_twice_is_one_turn(responder, turns):
    """A map redrawn because somebody else moved is not a second turn."""
    session = _Session(players=1)
    session.table.entities = {3: {"id": 3, "name": "Yeemik", "owner_account_id": None}}
    session.table.encounter = _fight()

    await responder.turn_came_round(session)
    await responder.turn_came_round(session)
    await _settle()

    assert len(turns) == 1


@pytest.mark.asyncio
async def test_it_stays_out_of_it_with_autopilot_off(responder, turns):
    session = _Session(players=1)
    session.table.autopilot = False
    session.table.entities = {3: {"id": 3, "name": "Yeemik", "owner_account_id": None}}
    session.table.encounter = _fight()

    await responder.turn_came_round(session)
    await _settle()

    assert turns == []


@pytest.mark.asyncio
async def test_the_turn_moving_on_first_cancels_it(responder, turns):
    """Four seconds is long enough for the DM to press Next turn themselves."""
    session = _Session(players=1)
    session.table.entities = {3: {"id": 3, "name": "Yeemik", "owner_account_id": None}}
    session.table.encounter = _fight()

    await responder.turn_came_round(session)
    session.table.encounter = _fight(turn=99, entity=3)
    await _settle()

    assert turns == []


@pytest.mark.asyncio
async def test_the_switch_is_what_silences_it(responder, spoken):
    """Not a sentence. The DM turning it off mid-pause must end the turn."""
    session = _Session(players=1)
    await responder.heard(session, _Member("player"), "Is the innkeeper in?")
    session.table.autopilot = False
    await _settle()
    assert spoken == []


@pytest.mark.asyncio
async def test_a_lone_dm_does_not_cancel_their_own_question(responder, turns):
    session = _Session(players=0)
    await responder.heard(session, _Member("dm", "Genna"), "Is the innkeeper in?")
    await responder.heard(session, _Member("dm", "Genna"), "He looks up.")
    await _settle()

    assert len(turns) == 1
    assert len(turns[0]) == 2, "both lines, one answer"


@pytest.mark.asyncio
async def test_another_agent_is_never_answered(responder, spoken):
    """Two agents answering each other is a loop with a bill attached."""
    session = _Session()
    await responder.heard(session, _Member("agent", "Autopilot"), "The door creaks.")
    await _settle()
    assert spoken == []


# -------------------------------------------------------------- the switch


@pytest.mark.asyncio
async def test_nothing_happens_while_autopilot_is_off(responder, spoken, turns):
    session = _Session(autopilot=False)
    await responder.heard(session, _Member("player"), "I open the door.")
    await _settle()

    assert spoken == []
    assert turns == [], "no model call either -- that is real money"


@pytest.mark.asyncio
async def test_switching_off_during_the_pause_stops_the_answer(responder, spoken, turns):
    """The button promises to take effect immediately, pause or no pause."""
    session = _Session()
    await responder.heard(session, _Member("player"), "I open the door.")
    session.table.autopilot = False

    await _settle()

    assert spoken == []
    assert turns == [], "it should not even ask the model"


# ------------------------------------------------------------------ robustness


@pytest.mark.asyncio
async def test_a_failing_model_does_not_end_the_session(spoken):
    calls = []

    def explode(_table, lines):
        calls.append(lines)
        raise RuntimeError("the API is down")

    async def say(text):
        spoken.append(text)

    responder = Responder(explode, say, quiet_for=PAUSE)
    session = _Session()

    await responder.heard(session, _Member("player"), "hello")
    await _settle()

    assert calls, "it tried"
    assert spoken == [], "and said nothing rather than crashing"

    # And it still works next time.
    await responder.heard(session, _Member("player"), "still there?")
    await _settle()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_an_empty_reply_is_not_said(spoken):
    async def say(text):
        spoken.append(text)

    responder = Responder(lambda _t, _l: "  ", say, quiet_for=PAUSE)
    session = _Session()

    await responder.heard(session, _Member("player"), "hello")
    await _settle()

    assert spoken == [], "whitespace is not an answer"


@pytest.mark.asyncio
async def test_closing_drops_anything_queued(responder, spoken):
    session = _Session()
    await responder.heard(session, _Member("player"), "hello")

    await responder.aclose()
    await _settle()

    assert spoken == []


@pytest.mark.asyncio
async def test_heard_returns_immediately(responder):
    """It must never hold up the socket read: that is what caused the pile-up."""
    session = _Session()
    loop = asyncio.get_running_loop()

    before = loop.time()
    await responder.heard(session, _Member("player"), "hello")
    elapsed = loop.time() - before

    assert elapsed < PAUSE, "heard() must not wait for the answer"


# --------------------------------------------------------- saying what went wrong
#
# An agent that goes quiet is indistinguishable from an agent that is broken.
# The failure that actually happens is an expired or mistyped key, and until
# this existed the only symptom was silence.


@pytest.mark.asyncio
async def test_a_failure_is_reported(spoken):
    trouble: list[str] = []

    def explode(_table, _lines):
        raise RuntimeError("Error code: 401 - API key is invalid.")

    async def say(text):
        spoken.append(text)

    async def on_trouble(message):
        trouble.append(message)

    responder = Responder(explode, say, quiet_for=PAUSE, on_trouble=on_trouble)
    await responder.heard(_Session(), _Member("player"), "hello")
    await _settle()

    assert trouble == ["Error code: 401 - API key is invalid."]
    assert spoken == []


@pytest.mark.asyncio
async def test_only_the_first_line_of_a_failure_is_reported():
    """A stack trace belongs in the log, not in the chat."""
    from canon_keeper_dm_agent.responder import _readable

    reported = _readable(RuntimeError("API key is invalid.\nFile x\nFile y"))
    assert reported == "API key is invalid."


@pytest.mark.asyncio
async def test_a_very_long_failure_is_cut_short():
    from canon_keeper_dm_agent.responder import _MAX_TROUBLE, _readable

    reported = _readable(RuntimeError("x" * 500))
    assert len(reported) <= _MAX_TROUBLE


@pytest.mark.asyncio
async def test_an_exception_with_no_message_still_names_itself():
    from canon_keeper_dm_agent.responder import _readable

    assert _readable(TimeoutError()) == "TimeoutError"


@pytest.mark.asyncio
async def test_a_failure_to_report_the_failure_is_survivable(spoken):
    """Reporting trouble must not become its own source of trouble."""
    async def broken_report(_message):
        raise OSError("the socket went away")

    async def say(text):
        spoken.append(text)

    responder = Responder(
        lambda _t, _l: (_ for _ in ()).throw(RuntimeError("boom")),
        say,
        quiet_for=PAUSE,
        on_trouble=broken_report,
    )
    await responder.heard(_Session(), _Member("player"), "hello")
    await _settle()  # must not raise


# ------------------------------------------------- a line the DM asked about


@pytest.mark.asyncio
async def test_a_line_the_dm_asked_about_is_answered_at_once(responder, turns):
    """No lull. The click was the cue -- there is nothing left to wait for."""
    session = _Session()
    await responder.asked_to_translate(
        session, {"who": "Brok", "said": "I get behind the orc and hit it"}
    )

    assert len(turns) == 1, "it waited for a pause nobody was going to leave"
    asked = " ".join(text for _label, text in turns[0])
    assert "I get behind the orc and hit it" in asked
    assert "propose_turn" in asked, "the model was not told how to answer"


@pytest.mark.asyncio
async def test_the_dm_can_ask_with_autopilot_off(responder, turns):
    """The evening this exists for, and the one the lull path refuses.

    Autopilot off means the agent does nothing of its own accord. Being asked
    is not of its own accord, and the host grants exactly one proposal for it
    -- so the gate that belongs here is none at all.
    """
    session = _Session(autopilot=False)
    await responder.asked_to_translate(session, {"who": "Brok", "said": "I hit it"})

    assert len(turns) == 1, "a line the DM asked about went unanswered"


@pytest.mark.asyncio
async def test_being_asked_twice_over_says_so_rather_than_queueing(turns):
    """A click that produces nothing for ten seconds reads as a broken button.

    One answer at a time is the rule everywhere in this class. What differs
    here is that somebody is waiting on a thing they just pressed, so the
    second click is answered with a sentence rather than joining a queue it
    cannot see.
    """
    trouble: list[str] = []
    started = threading.Event()
    finish = threading.Event()

    def answer(_table, lines):
        turns.append(list(lines))
        started.set()
        # Blocking, exactly as the real model call is -- it runs off the loop.
        finish.wait(timeout=5)
        return ""

    async def say(_text: str) -> None:
        return None

    async def on_trouble(message: str) -> None:
        trouble.append(message)

    responder = Responder(answer, say, quiet_for=PAUSE, on_trouble=on_trouble)
    session = _Session(autopilot=False)

    first = asyncio.create_task(
        responder.asked_to_translate(session, {"who": "Brok", "said": "I hit it"})
    )
    await asyncio.to_thread(started.wait, 5)

    await responder.asked_to_translate(session, {"who": "Brok", "said": "Again"})
    assert len(trouble) == 1, "the second click was silently dropped"
    assert "Brok" in trouble[0]

    finish.set()
    await first
    assert len(turns) == 1, "two model calls for one answer in flight"


@pytest.mark.asyncio
async def test_an_empty_line_is_not_worth_a_model_call(responder, turns):
    await responder.asked_to_translate(_Session(), {"who": "Brok", "said": "   "})
    assert turns == []
