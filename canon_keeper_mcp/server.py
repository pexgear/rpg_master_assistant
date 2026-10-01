"""Canon Keeper as an MCP server.

So that "I drink the potion and check the door for traps" becomes real changes
to real sheets, instead of the player typing them in.

The design decision that matters is what this is *not*. It is not a privileged
back door with its own rules -- it is a client, holding one login, and every
tool below turns into an ordinary message on the wire. A player's MCP session
can do exactly what that player could do by hand:

- ``say`` is a chat message.
- ``roll`` is rolled **on the host**; a result invented here would be ignored.
- ``update_my_character`` is a *request*. The host writes nothing on a client's
  say-so, so this returns "sent to your DM", never "done".

None of that is enforced here. It is what the host does with these messages,
which is why this file can be short and why a bug in it cannot corrupt a
campaign.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from mcp.server.mcpserver import MCPServer

from canon_keeper_client import AgentSession
from canon_keeper_protocol import MessageType

log = logging.getLogger("canonkeeper.mcp")


class CanonKeeperTools:
    """One connected session, exposed as MCP tools."""

    def __init__(self, session: AgentSession) -> None:
        self.session = session
        #: How far this reader has read. Here rather than on the table, because
        #: it is a fact about *this* reader and not about the session: two
        #: things reading the same table have their own places in it.
        self._read_up_to = session.table.said_so_far

    # ------------------------------------------------------------------ reading

    def whats_happening(self) -> dict[str, Any]:
        table = self.session.table
        return {
            "campaign": table.campaign,
            "session": table.session,
            "you": table.me.label if table.me else "",
            "at_the_table": [m.label for m in table.members],
            "recent": table.recent[-15:],
            "autopilot": table.autopilot,
        }

    def who_and_where(self) -> list[dict[str, Any]]:
        """Everyone and everywhere this login has been told about.

        Deliberately the whole of what the host sent and nothing more. An entity
        the DM has not shared is not filtered out here -- it never arrived.
        """
        return [
            {
                "id": entity.get("id"),
                "name": entity.get("name"),
                "kind": entity.get("kind"),
                "summary": entity.get("summary", ""),
            }
            for entity in self.session.table.entities.values()
        ]

    def the_fight(self) -> dict[str, Any]:
        """The map, if there is one, as this login was told it.

        Read-only, and it stays that way. A player does not move tokens in the
        app either, so a tool that let one do it over MCP would be this package
        handing out authority its login does not have. Running a fight belongs
        to the DM, and to the agent while autopilot is on; the host refuses this
        login whatever is asked here.

        Names are resolved from the entities that arrived, so a creature the DM
        has not shared is a token with no name -- which is what it is. It is on
        the map because somebody can see it; who it is, this login has not been
        told.
        """
        table = self.session.table
        fight = table.encounter
        if not fight:
            return {"fighting": False}

        def named(combatant: dict) -> str:
            entity = table.entities.get(combatant.get("entity"))
            return (entity or {}).get("name") or "someone"

        return {
            "fighting": True,
            "name": fight.get("name", ""),
            "grid": {"width": fight.get("width"), "height": fight.get("height")},
            "round": fight.get("round", 0),
            "whose_turn": next(
                (
                    named(c)
                    for c in fight.get("combatants") or []
                    if c.get("id") == fight.get("turn")
                ),
                "",
            ),
            "standing": [
                {
                    "who": named(combatant),
                    "x": combatant.get("x"),
                    "y": combatant.get("y"),
                    "initiative": combatant.get("initiative"),
                    "on_the_map": combatant.get("x") is not None,
                    # A seat that cannot tell it is dying cannot play a fight to
                    # the end: being down is the one state where what you may do
                    # changes completely, and there is a save to roll.
                    "down": bool(combatant.get("down")),
                    "death_saves_made": combatant.get("death_successes", 0),
                    "death_saves_failed": combatant.get("death_failures", 0),
                }
                for combatant in fight.get("combatants") or []
            ],
            "in_the_way": fight.get("obstacles") or [],
        }

    def my_characters(self) -> list[dict[str, Any]]:
        table = self.session.table
        return [
            entity
            for entity in table.entities.values()
            if isinstance(entity.get("data"), dict) and "sheet" in entity["data"]
        ]

    # ------------------------------------------------------------------ acting

    async def _send(self, message_type, **payload) -> bool:
        """Put one message on the wire, from whichever loop we are on.

        The tools run on the server's loop and the socket belongs to the
        session's, which in the real program is a different loop in a different
        thread. Sending on it directly is undefined rather than merely slow --
        it happens to work in the tests only because they run everything on one
        loop, which is exactly the shape that hides this.
        """
        if self.session._socket is None or self.session.loop is None:
            return False
        await self.session.on_my_loop(
            lambda: self.session.ask(message_type, **payload)
        )
        return True

    async def say(self, text: str) -> str:
        if not await self._send(MessageType.CHAT, text=text):
            return "Not connected."
        return f"Said: {text}"

    async def roll(self, notation: str) -> str:
        """Ask the host to roll. It rolls; we do not.

        A client that rolled its own dice and reported the number would be
        trusted by nobody at a real table, and is not trusted by the host
        either -- it ignores any result a client sends.
        """
        if not await self._send(MessageType.ROLL, notation=notation):
            return "Not connected."
        return f"Asked the host to roll {notation}. The result appears in the chat."

    # ------------------------------------------------- knowing something happened
    #
    # The rest of these tools answer a question. A seat played by a model needs
    # the other direction as well: *something has happened, look*. Without it the
    # only way to notice your turn came round is to call `whats_happening` over
    # and over and compare it with what you remember, which is slow, expensive,
    # and wrong the moment two things happen between polls.
    #
    # So: `read_pending` says what is new since you last read, and
    # `wait_for_update` blocks until there is something to read. Together they
    # are an event loop a model can actually drive a seat with.

    def read_pending(self) -> dict[str, Any]:
        """Everything that has happened since the last time this was called.

        The watermark moves only when this is called, so nothing is missed by
        calling it late -- but the table keeps a bounded window of chat, so a
        seat that says nothing for a very long evening can have the oldest lines
        fall off the front. It says so when that happens rather than pretending
        the gap is not there.
        """
        table = self.session.table
        since = self._read_up_to
        fresh = [line for line in table.recent if line.get("seq", 0) > since]
        self._read_up_to = table.said_so_far

        missed = 0
        if fresh:
            oldest = fresh[0].get("seq", 0)
            # A gap between the watermark and the oldest line still held.
            missed = max(0, oldest - since - 1)

        offered = self.session.offered or {}
        return {
            "said": [
                {
                    "speaker": line.get("speaker", ""),
                    "text": line.get("text", ""),
                    "aside": bool(line.get("aside")),
                }
                for line in fresh
            ],
            "dropped_before_you_read_them": missed,
            "your_turn_is_waiting": bool(offered),
            "turn_on_offer": offered.get("text", "") if offered else "",
            "whose_turn": self._whose_turn_name(),
        }

    def _whose_turn_name(self) -> str:
        acting = self.session.table.whose_turn()
        if not acting:
            return ""
        entity = self.session.table.entities.get(acting.get("entity"))
        return (entity or {}).get("name", "") or acting.get("stand_in_name", "")

    def _anything_unread(self) -> bool:
        """Whether a read would return anything. Cheap, and no side effects.

        Deliberately does not move the watermark: it is asked in a loop while
        waiting, and a check that consumed what it found would hide it from the
        read that follows.
        """
        return (
            self.session.table.said_so_far > self._read_up_to
            or bool(self.session.offered)
        )

    async def wait_for_update(self, seconds: float = 30.0) -> dict[str, Any]:
        """Wait until something happens, then say what. Returns early if it has.

        Anything already unread comes back at once -- waiting for the *next*
        thing while holding something unread is how a seat misses its own turn.

        The wait itself happens on the session's loop, because that is where the
        socket and the event live. ``seconds`` is capped: a tool that never
        returns looks identical to one that has crashed.
        """
        waited = max(0.0, min(float(seconds), 120.0))
        already = self.read_pending()
        if already["said"] or already["your_turn_is_waiting"]:
            already["waited"] = 0.0
            return already

        if self.session.loop is None:
            return {"error": "Not connected.", "waited": 0.0}

        started = time.monotonic()
        deadline = started + waited

        async def listen() -> None:
            event = self.session.something_happened
            if event is None:
                return
            # Re-checked on a short beat rather than waiting once for a single
            # wake. `_stir` sets the event and clears it again, so a wake that
            # lands between reading and arming is *lost* -- and waiting once for
            # the next one would hold an unread turn for the whole timeout,
            # which is the exact failure this tool exists to prevent. The beat
            # is the floor on noticing; the event is what makes it usually
            # instant rather than a poll.
            while True:
                if self._anything_unread():
                    return
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(event.wait(), min(0.2, left))

        await self.session.on_my_loop(listen)
        fresh = self.read_pending()
        fresh["waited"] = round(time.monotonic() - started, 2)
        return fresh

    # ------------------------------------------------------- taking your turn
    #
    # A seat could read the fight and say things about it, and could do neither
    # of the two things a turn actually consists of: answering the turn somebody
    # worked out for you, and saying you are finished. Both already existed on
    # the wire and in the client -- ACTED and DONE, `answer` and `turn_done` --
    # and only the tools were missing, so a seat sat through its own turn.
    #
    # Neither of them decides anything. Accepting is the host rolling it, the
    # way pressing the button is; finishing is saying so, not passing the turn,
    # which is the DM's.

    def the_turn_on_offer(self) -> dict[str, Any]:
        """The turn put to this seat and not yet answered, if there is one.

        Read before answering rather than remembered from a notification: an
        offer can be withdrawn or replaced between one call and the next -- a
        second proposal replaces the first -- and answering a stale one answers
        into silence.
        """
        offered = self.session.offered
        if not offered:
            return {"waiting": False}
        return {
            "waiting": True,
            "what_it_would_do": offered.get("text", ""),
            "who": offered.get("who", ""),
            "move_to": offered.get("move"),
            "attacking": offered.get("target_name") or None,
            "weapon": offered.get("weapon") or None,
        }

    async def answer_a_proposal(self, accept: bool, note: str = "") -> str:
        """Yes, no, or "I meant something else" to the turn on offer.

        The same message the button sends, and the same authority: the host
        checks this login plays that character rather than trusting the answer.
        ``note`` is how a refusal says what you meant instead, which is the
        difference between "no" and "no, I go round the other side".
        """
        offered = self.session.offered
        if not offered:
            return (
                "There is no turn waiting on you. Check the_turn_on_offer -- one "
                "may have been withdrawn, or replaced by a newer one."
            )
        what = offered.get("text", "that turn")
        self.session.offered = None
        if not await self._send(
            MessageType.ACTED,
            id=str(offered.get("id", "")),
            accept=bool(accept),
            note=note,
        ):
            return "Not connected."
        if accept:
            return f"Accepted: {what}. The host rolls it; watch the chat for what happened."
        return f"Refused: {what}." + (f" Said instead: {note}" if note else "")

    async def roll_my_death_save(self) -> str:
        """Make the death save this seat owes, rather than have it made for you.

        Carries nothing: the host knows which character this login plays and
        whether one is owed, and refuses if it is not. It rolls the save itself
        after the clock runs out either way -- so this is not the difference
        between dying and not, it is the difference between making your own and
        watching a number change in a list.
        """
        if not await self._send(MessageType.DEATH_SAVE):
            return "Not connected."
        return (
            "Rolled. Three successes and you are stable; three failures and you "
            "are out of the fight. Read the chat for which it was."
        )

    async def finish_my_turn(self) -> str:
        """"That is my turn." Not the same as passing it on.

        Passing the turn is running the table and belongs to the DM. This says
        only that *this* character has finished, which is what the player's own
        Done button says -- and it stops the clock that would otherwise end the
        turn for you after half a minute.
        """
        if not await self._send(MessageType.DONE):
            return "Not connected."
        return "Said you are done. The turn moves on."

    async def update_my_character(
        self, entity_id: int, changes: dict[str, Any]
    ) -> str:
        """Ask the DM for a change. This is a request, not a write.

        Everything a player changes is decided by their DM, hit points included.
        The honest return value is that it was sent.
        """
        if not await self._send(MessageType.EDIT, id=entity_id, changes=changes):
            return "Not connected."
        return (
            "Sent to your DM. They will approve or refuse it, and a refusal "
            "comes back with a reason."
        )


def build_server(session: AgentSession) -> MCPServer:
    tools = CanonKeeperTools(session)
    server = MCPServer(
        name="canon-keeper",
        instructions=(
            "Tools for one seat at a Dungeons & Dragons table running on Canon "
            "Keeper. You act as the person holding this login and have exactly "
            "their authority: you can say things, ask the host to roll, take "
            "your own character's turn when one is put to you, and request "
            "changes to their own characters. Requests go to the DM, who "
            "approves or refuses them -- never report a requested change as "
            "though it had been applied.\n\n"
            "In a fight you do not move anything yourself. Somebody works out "
            "what you said in rules and puts it to you; you read it with "
            "the_turn_on_offer and answer it. Refusing with a note is how you "
            "say what you meant instead."
        ),
    )

    @server.tool(description="What is going on at the table right now.")
    def whats_happening() -> dict[str, Any]:
        return tools.whats_happening()

    @server.tool(description="Everyone and everywhere you have been told about.")
    def who_and_where() -> list[dict[str, Any]]:
        return tools.who_and_where()

    @server.tool(description="The characters this login owns, with their sheets.")
    def my_characters() -> list[dict[str, Any]]:
        return tools.my_characters()

    @server.tool(
        description=(
            "The fight, if there is one: the grid, who is standing where, "
            "whose turn it is, and what is in the way. Read-only -- moving "
            "anything is the DM's, so ask them."
        )
    )
    def the_fight() -> dict[str, Any]:
        return tools.the_fight()

    @server.tool(description="Say something at the table, in character.")
    async def say(text: str) -> str:
        return await tools.say(text)

    @server.tool(
        description=(
            "Ask the host to roll dice, e.g. '2d6+3', '4d6kh3', '2d20kl1'. "
            "The host rolls; you never decide the result."
        )
    )
    async def roll(notation: str) -> str:
        return await tools.roll(notation)

    @server.tool(
        description=(
            "Ask your DM to change one of your characters -- hit points, "
            "conditions, inventory, or the build. This sends a request. It is "
            "not applied until the DM approves it."
        )
    )
    async def update_my_character(entity_id: int, changes: dict[str, Any]) -> str:
        return await tools.update_my_character(entity_id, changes)

    @server.tool(
        description=(
            "Everything said or done since you last called this. Call it after "
            "wait_for_update, or whenever you want to catch up. The watermark "
            "only moves when you call it, so nothing is missed by reading late."
        )
    )
    def read_pending() -> dict[str, Any]:
        return tools.read_pending()

    @server.tool(
        description=(
            "Wait until something happens at the table, then say what -- your "
            "turn coming round, somebody speaking, a roll. Returns at once if "
            "anything is already unread. This is how you sit at the table "
            "without asking 'anything yet?' over and over."
        )
    )
    async def wait_for_update(seconds: float = 30.0) -> dict[str, Any]:
        return await tools.wait_for_update(seconds)

    @server.tool(
        description=(
            "The turn somebody has worked out for your character and put to "
            "you, if there is one. Read this before answering: an offer can be "
            "withdrawn or replaced between calls."
        )
    )
    def the_turn_on_offer() -> dict[str, Any]:
        return tools.the_turn_on_offer()

    @server.tool(
        description=(
            "Answer the turn on offer: accept it, or refuse it and say what you "
            "meant instead in 'note'. Accepting is what makes it happen -- the "
            "host then rolls it. Nothing touches your character until you do."
        )
    )
    async def answer_a_proposal(accept: bool, note: str = "") -> str:
        return await tools.answer_a_proposal(accept, note)

    @server.tool(
        description=(
            "Make the death save your character owes, when the host has asked "
            "for one. You are at nought hit points and this is yours to roll."
        )
    )
    async def roll_my_death_save() -> str:
        return await tools.roll_my_death_save()

    @server.tool(
        description=(
            "Say your character has finished their turn. This is not passing "
            "the turn on -- that is the DM's -- it only says you are done, and "
            "stops the clock that would end it for you."
        )
    )
    async def finish_my_turn() -> str:
        return await tools.finish_my_turn()

    return server
