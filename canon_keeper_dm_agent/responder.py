"""Deciding *when* to answer, which matters more than what it answers.

The first version replied to every line a player said. At a real table that is
unbearable: three people talking to each other becomes three interruptions, and
because a model call takes seconds, each one arrives answering something the
conversation has already moved past.

So a turn is not a message. A turn is **a lull**. Lines are gathered as they
arrive, and the agent speaks only once the table has stopped for a moment --
which is roughly what a person waits for.

Three rules follow from that, and each one exists because the alternative is
worse at a table rather than because it is tidier:

- **Only one answer in flight.** Anything said while it is thinking joins the
  next turn instead of starting a competing one.
- **While autopilot is on, everyone gets answered -- the DM included.** This
  was once the other way round: a line from the DM dropped whatever was queued,
  on the grounds that the human had answered it. At a real table that is wrong.
  A DM with autopilot on who says "there is something behind the door" is
  talking *to* the agent, and having their line swallowed makes autopilot look
  broken. Taking the table back is the switch, which is instant and needs no
  cooperation; a sentence is just a sentence.
- **Nothing blocks the socket.** :meth:`heard` returns immediately, so the
  client keeps reading while the model is busy.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("canonkeeper.agent.responder")

#: How long the table has to be quiet before the agent takes its turn. People
#: finish a thought across two or three messages, and answering the first half
#: is worse than answering slowly.
QUIET_FOR = 2.5

#: How long the table gets before the agent takes a monster's turn. Long
#: enough for somebody to say "wait" and short enough that a fight does not
#: stall on a machine being polite. Anything said in that window is folded into
#: the same turn rather than racing it.
MONSTER_PAUSE = 4.0

#: Whose lines are worth answering. Everyone at the table, which while
#: autopilot is on includes the human DM: they switched it on, and a line they
#: type is addressed to the agent as much as to the room.
#:
#: Not another agent's, though. Two of these answering each other is a loop
#: with a bill attached.
ANSWERS_TO = ("player", "dm")


class Responder:
    """Gathers what was said and answers once the table pauses."""

    def __init__(
        self,
        answer,
        say,
        *,
        quiet_for: float = QUIET_FOR,
        answers_to: tuple[str, ...] = ANSWERS_TO,
        monster_pause: float = MONSTER_PAUSE,
        on_busy=None,
        on_trouble=None,
    ) -> None:
        #: ``answer(table, lines) -> str``. Blocking; run off the loop.
        self._answer = answer
        #: ``say(text) -> awaitable[bool]``.
        self._say = say
        self._quiet_for = quiet_for
        self._answers_to = answers_to
        #: ``on_busy(bool)`` -- called around the model call, so the table can
        #: see that the silence is someone thinking rather than nothing at all.
        self._on_busy = on_busy
        #: ``on_trouble(str)`` -- a turn failed, and the DM should hear why. An
        #: agent that goes quiet is indistinguishable from one that is broken.
        self._on_trouble = on_trouble

        self._monster_pause = monster_pause

        self._pending: list[tuple[str, str]] = []
        self._timer: asyncio.Task | None = None
        self._answering = False
        #: The monster's turn we are already handling, so a map redrawn twice
        #: does not become two turns.
        self._monsters_turn = None
        self._monster_timer: asyncio.Task | None = None

    # -------------------------------------------------------------- listening

    async def heard(self, session, member, text: str) -> None:
        """Note a line. Returns at once -- the answer happens on its own task."""
        if member.role not in self._answers_to:
            return
        if not session.table.autopilot:
            log.debug("staying quiet: autopilot is off")
            return

        self._pending.append((member.label, text))
        self._restart_timer(session)

    async def asked_to_translate(self, session, request: dict) -> None:
        """The DM pointed at a line and asked for it in rules. Answer now.

        Two of this class's rules do not apply here, and both for the same
        reason -- a lull is a guess that the table wants an answer, and this is
        somebody asking for one:

        - **No pause.** There is nothing to wait for. The DM clicked.
        - **Autopilot need not be on.** The evening this exists for is the one
          where the DM is running their own table and wants a sentence worked
          out. The host grants exactly one proposal for exactly that creature,
          so "off" still means the agent does nothing of its own accord.

        The one rule that does still apply is one answer at a time, and here it
        is worth saying out loud rather than queueing: a click that produces
        nothing for ten seconds reads as a broken button.
        """
        said = str(request.get("said", "")).strip()
        who = str(request.get("who", "")) or "They"
        if not said:
            return
        if self._answering:
            await self._say_trouble(
                f"Still writing the last answer -- ask again for {who} in a moment."
            )
            return

        self._pending.append(
            (
                "(the DM)",
                f'{who} said: "{said}". It is their turn. Use propose_turn to '
                "write that as the move and attack they meant, and put it to "
                "them. Say nothing at the table: the DM asked for the turn, not "
                "for narration.",
            )
        )
        await self._take_a_turn(session, asked=True)

    async def turn_came_round(self, session) -> None:
        """The fight moved. If it is a monster's turn, that is ours to take.

        Nobody says "it is the goblin's turn" out loud, so without this the
        agent sits through its own turn waiting to be spoken to. A player's
        character is never taken this way -- theirs is proposed and confirmed.
        """
        if not session.table.autopilot:
            return
        acting = session.table.whose_turn()
        if not session.table.is_mine_to_play(acting):
            self._monsters_turn = None
            return
        if self._monsters_turn == acting.get("id"):
            return  # already in hand; a redrawn map is not a second turn

        self._monsters_turn = acting.get("id")
        if self._monster_timer is not None:
            self._monster_timer.cancel()
        self._monster_timer = asyncio.create_task(
            self._after_the_pause(session, acting)
        )

    async def _after_the_pause(self, session, acting: dict) -> None:
        try:
            await asyncio.sleep(self._monster_pause)
        except asyncio.CancelledError:
            return

        # Re-checked, because four seconds is long enough for the DM to take
        # the table back or move the turn on themselves.
        if not session.table.autopilot:
            return
        still = session.table.whose_turn()
        if not still or still.get("id") != acting.get("id"):
            return

        entity = session.table.entities.get(acting.get("entity")) or {}
        name = entity.get("name") or "the creature"
        self._pending.append(("(the fight)", f"It is {name}'s turn. Take it."))
        await self._take_a_turn(session)

    def _restart_timer(self, session) -> None:
        """Wait for the lull again, because the table is still talking."""
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.create_task(self._after_the_lull(session))

    async def _after_the_lull(self, session) -> None:
        try:
            await asyncio.sleep(self._quiet_for)
        except asyncio.CancelledError:
            return

        if self._answering:
            # Still finishing the last turn. Leave the lines queued: whatever
            # finishes will pick them up rather than starting a second answer.
            return
        await self._take_a_turn(session)

    # ---------------------------------------------------------------- speaking

    async def _take_a_turn(self, session, asked: bool = False) -> None:
        lines, self._pending = self._pending, []
        self._timer = None
        if not lines:
            return

        # Re-checked here rather than only when the line arrived: the DM may
        # have taken the table back during the lull, and the whole promise of
        # that button is that it takes effect immediately.
        #
        # ``asked`` is the exception, and the only one: the DM asked for this
        # particular answer, so autopilot is not what authorises it. The host
        # still decides what may come of it -- it granted one proposal and
        # nothing else.
        if not asked and not session.table.autopilot:
            log.info("autopilot went off during the pause; saying nothing")
            return

        self._answering = True
        await self._say_busy(True)
        try:
            reply = await asyncio.to_thread(self._answer, session.table, lines)
        except Exception as exc:  # noqa: BLE001 - one bad turn must not end it
            log.exception("the model call failed")
            await self._say_trouble(_readable(exc))
            return
        finally:
            self._answering = False
            # In `finally` on purpose: a crash that left the table showing
            # "writing..." forever would be worse than the crash.
            await self._say_busy(False)

        reply = (reply or "").strip()
        if reply:
            await self._say(reply)

        # Said while we were thinking: those are the next turn, and they get
        # their own pause rather than an immediate second answer.
        if self._pending:
            self._restart_timer(session)

    async def _say_trouble(self, message: str) -> None:
        if self._on_trouble is None or not message:
            return
        try:
            await self._on_trouble(message)
        except Exception:  # noqa: BLE001 - reporting a failure must not fail
            log.debug("could not report the failure", exc_info=True)

    async def _say_busy(self, on: bool) -> None:
        if self._on_busy is None:
            return
        try:
            await self._on_busy(on)
        except Exception:  # noqa: BLE001 - an indicator is not worth a crash
            log.debug("could not report busy", exc_info=True)

    # ----------------------------------------------------------------- closing

    def _discard(self) -> None:
        self._pending.clear()
        for timer in (self._timer, self._monster_timer):
            if timer is not None:
                timer.cancel()
        self._timer = None
        self._monster_timer = None
        self._monsters_turn = None

    async def aclose(self) -> None:
        self._discard()


#: Long enough to carry an API error that tells you what to do about it. The
#: first version cut at 200 and truncated a message precisely where it started
#: explaining the fix.
_MAX_TROUBLE = 600


def _readable(exc: BaseException) -> str:
    """One line a person can act on, out of whatever the SDK raised.

    A stack trace belongs in the log. What reaches the DM has to fit in a chat
    message and name the thing they can fix -- most often an expired key.
    """
    text = str(exc).strip() or exc.__class__.__name__
    first = text.split("\n", 1)[0]
    if len(first) > _MAX_TROUBLE:
        first = first[: _MAX_TROUBLE - 3].rstrip() + "..."
    return first
