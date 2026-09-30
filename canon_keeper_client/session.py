"""A headless connection to a Canon Keeper session.

The app's own client is built on Qt. This one is not, because an agent has no
window and should not need 660 MB of it to hold a socket. It speaks exactly the
same protocol -- and being a second, independent implementation is the point: an
ambiguity both ends share is invisible until something else has to read it.

What it deliberately does not do: reach into a campaign database. It holds a
socket and a login, and everything it learns arrives over the wire. That is what
keeps "the agent has exactly the authority its login has" a fact about the
system rather than a promise about the code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import websockets

from canon_keeper_protocol import (
    MAX_HOST_FRAME_BYTES,
    Member,
    Message,
    MessageType,
    ProtocolError,
    auth,
    decode,
    encode,
)

log = logging.getLogger("canonkeeper.agent.session")

#: How long to wait for the host to answer the handshake before giving up.
LOGIN_TIMEOUT = 20.0

#: How long to wait for a polite close before dropping the socket. The library
#: default is ten seconds, which is a long time to hold a process open for a
#: goodbye nobody is waiting to hear -- and it is felt every time the agent is
#: stopped or a test tears one down.
CLOSE_TIMEOUT = 2.0


class LoginFailed(RuntimeError):
    """The host refused us, and the reason is safe to print."""


@dataclass
class Table:
    """What the agent knows about the session it is sitting at.

    Kept as plain dicts rather than re-implementing the app's entity classes:
    this is the far end of a wire, and the only consumer is a prompt builder.
    """

    campaign: str = ""
    session: str = ""
    me: Member | None = None
    #: entity id -> the projected entity, as the host chose to send it.
    entities: dict[int, dict] = field(default_factory=dict)
    #: The canon log. Arrives only because this login sees what a DM sees.
    facts: list[dict] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    #: Recent chat, oldest first. Bounded -- an agent needs the scene, not the
    #: campaign's whole history, and an unbounded list is a slow leak.
    recent: list[dict] = field(default_factory=list)
    autopilot: bool = False
    #: The fight, as the host projected it for this login. Empty when there is
    #: none. An agent running a combat needs to know where everybody is.
    encounter: dict = field(default_factory=dict)

    #: Deep enough to hold an evening's exchange rather than the last thing
    #: anybody said. Lines arrive in bursts -- three people typing at once are
    #: three messages in two seconds -- so a short window routinely cuts off the
    #: beginning of the thing being answered.
    RECENT_LIMIT = 120

    def remember(
        self,
        speaker: str,
        role: str,
        text: str,
        at: float = 0.0,
        aside: bool = False,
    ) -> None:
        """Keep a line, and when it was said.

        The timestamp is not decoration. It is what lets a reader tell one
        exchange from the next: six lines in four seconds are one conversation,
        and the same six lines over ten minutes are not.

        ``aside`` marks a DM speaking while autopilot is on. The party did not
        hear it -- it is direction, not dialogue, and repeating it back to them
        word for word would be the one thing it must not do with it.
        """
        self.recent.append(
            {
                "speaker": speaker,
                "role": role,
                "text": text,
                "at": float(at or 0.0),
                "aside": bool(aside),
            }
        )
        if len(self.recent) > self.RECENT_LIMIT:
            del self.recent[: -self.RECENT_LIMIT]

    @property
    def fighting(self) -> bool:
        return bool(self.encounter)

    def whose_turn(self) -> dict | None:
        """The combatant that is up, as the host sent it."""
        turn = (self.encounter or {}).get("turn")
        for combatant in (self.encounter or {}).get("combatants") or []:
            if combatant.get("id") == turn:
                return combatant
        return None

    def is_mine_to_play(self, combatant: dict | None) -> bool:
        """Whether this one's turns are the agent's to take outright.

        Everything nobody plays -- which is every monster -- and any character
        whose player has handed it over for this fight. Anyone else's is
        proposed to them and waits for a yes, because moving somebody's
        character without asking is a different product.
        """
        if not combatant:
            return False
        if combatant.get("simulated"):
            return True
        entity = self.entities.get(combatant.get("entity"))
        return bool(entity) and entity.get("owner_account_id") is None

    def entity_named(self, name: str) -> dict | None:
        """Find a creature by what it is called, the way a person would.

        Exact first, then case-insensitively, then by first name -- a model
        told "Brok Ironfoot" will write "Brok", and refusing that would make it
        look broken over a surname.
        """
        wanted = (name or "").strip().lower()
        if not wanted:
            return None
        entities = list(self.entities.values())

        for entity in entities:
            if str(entity.get("name", "")).lower() == wanted:
                return entity
        for entity in entities:
            words = str(entity.get("name", "")).lower().split()
            if words and (words[0] == wanted or wanted in words):
                return entity
        for entity in entities:
            if wanted in str(entity.get("name", "")).lower():
                return entity
        return None


class AgentSession:
    """Connects, logs in, and calls ``on_said`` for everything spoken."""

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        on_said: Callable[["AgentSession", Member, str], Awaitable[None]],
        on_encounter: Callable[["AgentSession"], Awaitable[None]] | None = None,
        seat: str = "",
        on_action: Callable[["AgentSession", dict], Awaitable[None]] | None = None,
        on_translate: Callable[["AgentSession", dict], Awaitable[None]] | None = None,
    ) -> None:
        self._url = url
        self._username = username
        self._password = password
        #: A seat token, for a machine standing in for one character. It arrives
        #: as that player and sees what they see; there is no password, because
        #: nobody -- not even their DM -- has one to give.
        self._seat = seat
        self._on_said = on_said
        #: Called whenever the host says what the fight looks like. It is how
        #: an agent finds out the turn has come round to something it is
        #: running -- nobody says that out loud, so nothing would otherwise.
        self._on_encounter = on_encounter
        #: A turn somebody worked out for a character of ours, waiting on a
        #: yes. Only ever arrives on a connection that plays a character --
        #: which, for a stand-in, is the whole of what it is here to answer.
        self._on_action = on_action
        #: A line the DM has pointed at and asked to have written as a turn.
        #: Unlike everything else here it is not something noticed, it is
        #: something requested -- so it is answered at once rather than after
        #: the pause the rest of a turn waits for.
        self._on_translate = on_translate
        self._socket: Any = None
        self.table = Table()
        #: Set every time the host tells us what the fight looks like. A tool
        #: that has just asked for a change waits on this rather than guessing
        #: how long the host takes -- and gets the host's answer, not its own.
        self.encounter_arrived = asyncio.Event()

    # ------------------------------------------------------------------ running

    async def run(self) -> None:
        """Connect and pump messages until the connection closes."""
        async with websockets.connect(
            self._url,
            max_size=MAX_HOST_FRAME_BYTES,
            close_timeout=CLOSE_TIMEOUT,
        ) as socket:
            self._socket = socket
            await self._handshake(socket)
            log.info(
                "logged in to %s as %s",
                self.table.campaign or self._url,
                self.table.me.name if self.table.me else self._username,
            )
            async for raw in socket:
                try:
                    message = decode(raw, max_bytes=MAX_HOST_FRAME_BYTES)
                except ProtocolError as exc:
                    log.warning("unreadable frame: %s", exc)
                    continue
                await self._dispatch(message)

    async def say(self, text: str) -> bool:
        """Speak at the table.

        The host refuses an *agent* speaking while autopilot is off, and that
        refusal is the host's to make -- asking first would be the agent
        policing itself, which is not a guarantee. This declines to ask only
        because there is no reason to make it refuse something we already know
        it will.

        A **seat** is not covered by that rule and does not skip the ask. It is
        a player, and a player may always speak: a character standing in for
        somebody who has stepped out is at the table whether or not the DM has
        handed the *table* to a machine. The two switches are separate, and
        conflating them here is what would make them look joined.
        """
        if self._socket is None:
            return False
        if not self._seat and not self.table.autopilot:
            log.debug("not speaking: autopilot is off")
            return False
        await self._socket.send(encode(MessageType.CHAT, text=text))
        return True

    async def set_busy(self, on: bool) -> None:
        """Say whether we are composing, so the table is not left guessing."""
        if self._socket is None:
            return
        await self._socket.send(encode(MessageType.BUSY, on=bool(on)))

    async def report_trouble(self, message: str) -> None:
        """Say that a turn failed. The host passes it to the DM, privately."""
        if self._socket is None:
            return
        await self._socket.send(encode(MessageType.TROUBLE, message=message))

    async def report_spend(self, **spend) -> None:
        """Tell the host what we have cost. It shows the DM, and nobody else."""
        if self._socket is None:
            return
        await self._socket.send(encode(MessageType.SPENT, **spend))

    # ------------------------------------------------------------- the fight
    #
    # Every one of these is a *request*. The host decides whether this login may
    # run a fight -- it may, exactly while autopilot is on -- and nothing here
    # changes anything by itself. What comes back is an ENCOUNTER frame, which
    # is the only thing that updates `table.encounter`.

    async def ask(self, message_type, **payload) -> bool:
        if self._socket is None:
            return False
        await self._socket.send(encode(message_type, **payload))
        return True

    async def start_fight(self, name: str, width: int, height: int) -> bool:
        return await self.ask(
            MessageType.FIGHT, name=name, width=int(width), height=int(height)
        )

    async def enlist(
        self,
        entity_id: int,
        x: int | None = None,
        y: int | None = None,
        initiative: int | None = None,
    ) -> bool:
        return await self.ask(
            MessageType.ENLIST, entity=entity_id, x=x, y=y, initiative=initiative
        )

    async def move(self, combatant_id: int, x: int | None, y: int | None) -> bool:
        return await self.ask(MessageType.MOVE, combatant=combatant_id, x=x, y=y)

    async def set_terrain(self, x: int, y: int, on: bool = True) -> bool:
        return await self.ask(MessageType.TERRAIN, x=int(x), y=int(y), on=bool(on))

    async def turn(self, action: str) -> bool:
        return await self.ask(MessageType.TURN, action=action)

    async def answer(self, action_id: str, accept: bool, note: str = "") -> bool:
        """Yes, no, or "I meant something else" to a turn put to us.

        The same message a person's app sends when they press the button. A
        stand-in answers its own character's turns and no others -- the host
        checks that rather than trusting it.
        """
        return await self.ask(
            MessageType.ACTED, id=action_id, accept=accept, note=note
        )

    async def turn_done(self) -> bool:
        """"That is my turn." The one way a seat may pass the turn on.

        Not ``turn("next")``: that is running the table, which a stand-in may
        not do. This says only that *this* character has finished, which is the
        same thing the player would have pressed.
        """
        return await self.ask(MessageType.DONE)

    async def swing(self, combatant_id: int, target_id: int, weapon: str = "") -> bool:
        """Attack. The host rolls it -- a result reported here would be ignored."""
        return await self.ask(
            MessageType.SWING, combatant=combatant_id, target=target_id, weapon=weapon
        )

    async def propose(
        self,
        combatant_id: int,
        move: list | None = None,
        target: int | None = None,
        weapon: str = "",
        text: str = "",
    ) -> bool:
        """Put a formalised turn to the player whose character it is.

        A proposal and nothing more. The host checks it is legal, the player
        accepts or refuses, and only then does anything move.
        """
        return await self.ask(
            MessageType.PROPOSE,
            combatant=combatant_id,
            move=move,
            target=target,
            weapon=weapon,
            text=text,
        )

    async def set_initiative(self, combatant_id: int, value: int | None) -> bool:
        return await self.ask(
            MessageType.INITIATIVE, combatant=combatant_id, value=value
        )

    async def wait_for_the_fight(self, timeout: float = 2.0) -> dict:
        """Wait for the host's next word on the fight, then return it.

        A tool that reported success the moment it sent a frame would be
        reporting that it typed, not that anything happened. Timing out returns
        whatever we last knew, which is still the host's account and not ours.
        """
        self.encounter_arrived.clear()
        try:
            async with asyncio.timeout(timeout):
                await self.encounter_arrived.wait()
        except TimeoutError:
            log.debug("no word from the host about the fight within %ss", timeout)
        return self.table.encounter

    # --------------------------------------------------------------- handshake

    async def _handshake(self, socket) -> None:
        if self._seat:
            await self._sit_down(socket)
            return
        await socket.send(encode(MessageType.HELLO, username=self._username))

        async with asyncio.timeout(LOGIN_TIMEOUT):
            challenge = decode(await socket.recv(), max_bytes=MAX_HOST_FRAME_BYTES)
            if challenge.type == MessageType.ERROR:
                raise LoginFailed(str(challenge.get("message", "refused")))
            if challenge.type != MessageType.CHALLENGE:
                raise LoginFailed(f"expected a challenge, got {challenge.type}")

            salt = bytes.fromhex(str(challenge.get("salt", "")))
            nonce = bytes.fromhex(str(challenge.get("nonce", "")))
            if not salt or not nonce:
                raise LoginFailed("the host sent an unusable challenge")

            # The password is turned into a verifier here and never sent. See
            # canon_keeper_protocol.auth for why this is worth the round trip.
            verifier = auth.derive_verifier(self._password, salt)
            await socket.send(
                encode(MessageType.LOGIN, proof=auth.proof(verifier, nonce))
            )

            while True:
                message = decode(await socket.recv(), max_bytes=MAX_HOST_FRAME_BYTES)
                if message.type == MessageType.ERROR:
                    raise LoginFailed(str(message.get("message", "refused")))
                if message.type == MessageType.WELCOME:
                    await self._dispatch(message)
                    return
                await self._dispatch(message)

    async def _sit_down(self, socket) -> None:
        """Log in on a seat token. One message, and no challenge to answer.

        There is nothing to prove: the token *is* the proof, and it was minted
        by the host for this one character. What it buys is deliberately small
        -- see ``_may_act_for`` on the host -- so it is worth no more than the
        handover it belongs to.
        """
        await socket.send(encode(MessageType.HELLO, seat=self._seat))
        async with asyncio.timeout(LOGIN_TIMEOUT):
            while True:
                message = decode(await socket.recv(), max_bytes=MAX_HOST_FRAME_BYTES)
                if message.type == MessageType.ERROR:
                    raise LoginFailed(str(message.get("message", "refused")))
                await self._dispatch(message)
                if message.type == MessageType.WELCOME:
                    return

    # ---------------------------------------------------------------- dispatch

    async def _dispatch(self, message: Message) -> None:
        table = self.table

        if message.type == MessageType.WELCOME:
            table.campaign = str(message.get("campaign", ""))
            table.session = str(message.get("session", ""))
            you = message.get("you")
            if isinstance(you, dict):
                table.me = Member.from_dict(you)

        elif message.type == MessageType.SNAPSHOT:
            if not message.get("partial"):
                table.entities.clear()
            for entity in message.get("entities") or []:
                if isinstance(entity.get("id"), int):
                    table.entities[entity["id"]] = entity
            for gone in message.get("gone") or []:
                table.entities.pop(gone, None)

        elif message.type == MessageType.ENTITY:
            entity = message.get("entity") or {}
            if isinstance(entity.get("id"), int):
                table.entities[entity["id"]] = entity

        elif message.type == MessageType.ENTITY_GONE:
            table.entities.pop(message.get("id"), None)

        elif message.type == MessageType.ENCOUNTER:
            fight = message.get("encounter")
            table.encounter = fight if isinstance(fight, dict) else {}
            self.encounter_arrived.set()
            if self._on_encounter is not None:
                await self._on_encounter(self)

        elif message.type == MessageType.FACTS:
            facts = message.get("facts")
            table.facts = facts if isinstance(facts, list) else []

        elif message.type == MessageType.AUTOPILOT:
            was = table.autopilot
            table.autopilot = bool(message.get("on"))
            if was != table.autopilot:
                log.info("autopilot %s", "on" if table.autopilot else "off")

        elif message.type == MessageType.ROSTER:
            table.members = [
                Member.from_dict(m)
                for m in (message.get("members") or [])
                if isinstance(m, dict)
            ]

        elif message.type == MessageType.HISTORY:
            for entry in message.get("messages") or []:
                if isinstance(entry, dict) and entry.get("text"):
                    table.remember(
                        str(entry.get("speaker", "")),
                        str(entry.get("role", "")),
                        str(entry["text"]),
                        at=entry.get("at") or 0.0,
                    )

        elif message.type == MessageType.SAID:
            raw_member = message.get("member") or {}
            member = Member.from_dict(raw_member if isinstance(raw_member, dict) else {})
            text = str(message.get("text", ""))
            table.remember(
                member.label,
                member.role,
                text,
                at=message.ts,
                aside=bool(message.get("aside")),
            )
            # Our own line coming back. Answering it would be a conversation
            # with ourselves, and a fast one.
            if table.me is not None and member.id == table.me.id:
                return
            await self._on_said(self, member, text)

        elif message.type == MessageType.ROLLED:
            table.remember(
                str((message.get("member") or {}).get("name", "")),
                "",
                str(
                    message.get("description", "")
                    or message.get("text", "")
                    or message.get("result", "")
                ),
                at=message.ts,
            )

        elif message.type == MessageType.ACTION:
            # `watching` is the DM's copy: something to see, not to answer.
            if not message.get("watching") and self._on_action is not None:
                await self._on_action(self, dict(message.payload))

        elif message.type == MessageType.TRANSLATE_THIS:
            if self._on_translate is not None:
                await self._on_translate(self, dict(message.payload))

        elif message.type == MessageType.ERROR:
            log.warning(
                "host refused: %s (%s)",
                message.get("message"),
                message.get("code"),
            )
