"""The session host, bound to one campaign.

Runs inside the DM's app or inside the headless ``canonkeeper-server``; the class
does not know or care which. It owns the roster, rolls the dice, rebroadcasts
chat, and -- the part that matters -- decides what each logged-in player is
allowed to see.

Clients are untrusted. Every outbound entity goes through
:mod:`canon_keeper.net.projection` first, so a player's app is never sent a
secret and asked to hide it.

Logging in is a challenge/response: the password never crosses the wire. See
:mod:`canon_keeper_protocol.auth`.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from canon_keeper import campaigns
from canon_keeper.content import Content
from canon_keeper.net import discovery
from canon_keeper_protocol import auth, enrol, grid, robots, turns
from canon_keeper_protocol.messages import Played
from canon_keeper.repo.chat import (
    DEFAULT_LIMIT,
    DM_ONLY,
    EVERYONE,
    ROLLED,
    SAID,
    SYSTEM,
)
from canon_keeper_protocol.dice import DiceError, roll
from canon_keeper.repo.entities import StaleWrite
from canon_keeper.repo.invites import already_played
from canon_keeper.rules.validation import validate
from canon_keeper.net.projection import (
    project_encounter,
    project_facts,
    EditRefused,
    changed_sheet_fields,
    snapshot_since,
    Viewer,
    apply_player_edit,
    project_entity,
    snapshot,
    visible_entity_ids,
)
from canon_keeper.repo.encounters import DEFAULT_HEIGHT, DEFAULT_WIDTH
from canon_keeper.rules import attack, death, derive
from canon_keeper_protocol.messages import (
    MAX_CHAT_LENGTH,
    MAX_NAME_LENGTH,
    MAX_NOTATION_LENGTH,
    Member,
    MessageType,
    ProtocolError,
    Role,
    SystemKind,
    clean_name,
    decode,
    encode,
    new_member_id,
)

log = logging.getLogger("canonkeeper.net.server")

DEFAULT_PORT = 8765

#: A socket that has not finished logging in by then is closed. Stops a port
#: scanner or a stalled client from occupying a slot indefinitely.
LOGIN_TIMEOUT_MS = 20_000

#: How long a player has, after acting, to do something else before the turn
#: moves on without them. Anything they type stops the clock, so it only ever
#: runs out on somebody who has stopped reading. Fifteen seconds was the first
#: guess and it was too short at a real table: a player who has just watched
#: their attack land needs a moment to decide there is nothing else, and being
#: hurried out of a turn reads as the app losing track rather than waiting.
STILL_YOUR_TURN_MS = 30_000

#: The same, for a monster or a character somebody has handed to autopilot.
#: Much shorter, because there is nobody to ask and nothing to read: it is the
#: gap after the machine stops acting. Each thing it does restarts it, and it
#: does not run at all while the agent is still thinking.
MACHINE_TURN_MS = 6_000


@dataclass
class _Pending:
    """A connection that has said hello but not yet proved who it is."""

    timer: QTimer
    username: str = ""
    nonce: bytes = b""
    account_id: int | None = None
    attempts: int = 0
    #: Versions the client already holds, so the first reply can be a delta
    #: rather than the whole campaign.
    known: dict[int, int] = field(default_factory=dict)


@dataclass
class _Session:
    """A logged-in connection."""

    member: Member
    account_id: int | None
    viewer: Viewer
    #: An autopilot login. Its chat is refused while autopilot is off, which is
    #: the whole of what "off" means -- not a politeness the agent observes.
    is_agent: bool = False
    #: The character this connection is standing in for, when it arrived on a
    #: seat token rather than a password. It is a *player* connection in every
    #: other respect -- same projection, same authority -- because the whole
    #: point is that a machine playing Marla sees what Marla's player sees.
    seat_for: int | None = None
    #: Composing something right now. Broadcast so a table can see that
    #: silence means thinking rather than nothing happening.
    busy: bool = False
    visible: set[int] = field(default_factory=set)
    #: What version of each entity we last sent this connection. The base for
    #: any edit they send back, because a client must not be able to choose
    #: which version it claims to be editing -- nor to omit it and get an
    #: unconditional write.
    sent: dict[int, int] = field(default_factory=dict)

    def remember_sent(self, projected: list[dict] | dict) -> None:
        entries = projected if isinstance(projected, list) else [projected]
        for entity in entries:
            if isinstance(entity.get("id"), int) and isinstance(
                entity.get("version"), int
            ):
                self.sent[entity["id"]] = entity["version"]


class SessionServer(QObject):
    started = Signal(int)  # port
    stopped = Signal()
    failed = Signal(str)
    roster_changed = Signal(list)  # list[Member]
    #: A player's edit was applied. The DM's own panels read the database
    #: directly, so without this their screen shows yesterday's hit points.
    entity_applied = Signal(int)
    #: Something moved a token that was not the DM's own panel -- today only an
    #: agent on autopilot. Same reason as above: the DM's map is read from the
    #: database, so it does not know until it is told.
    encounter_applied = Signal()
    #: A move or a swing happened, for whoever is watching with nobody
    #: connected to send it to. Everyone connected -- including the DM's own
    #: loopback join while hosting -- gets this over the wire instead, from
    #: :meth:`_show`; this is only for the fight run alone, where there is no
    #: wire and the DM's own map would otherwise just teleport tokens around.
    played = Signal(dict)

    def __init__(
        self,
        repos,
        campaign_id: int,
        session_name: str = "Canon Keeper session",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.repos = repos
        self.campaign_id = campaign_id
        self.session_name = session_name

        #: Lets the DM's own app authenticate as itself without a password. It
        #: already holds the campaign file, so demanding a login on the same
        #: machine is friction with nothing behind it. The token is regenerated
        #: per run and never leaves the process except to the local client.
        self.local_token = secrets.token_urlsafe(32)
        #: Live seat tokens: token -> (account id, entity id). One per character
        #: handed to autopilot. Held in memory and never written down, so a
        #: handover cannot outlive the session it was made in -- and there is no
        #: stored credential for a seat to leak from a campaign file.
        self._seats: dict[str, tuple[int, int]] = {}

        #: A campaign's own identity, generated once and kept in its settings.
        #: Entity ids and versions both restart at one in a new campaign, so
        #: without this a client's cache from a different campaign looks
        #: perfectly up to date and is served back to them unchanged.
        self.campaign_key = self._campaign_key()
        #: The evening this is. Chat is filed against it, so each session is
        #: its own log rather than one endless scroll.
        self.session_id = self._open_session()
        #: For checking what a player proposes. A sheet is validated here,
        #: on the host, because the client that sent it is the one thing we
        #: cannot take at its word.
        self.content = Content(repos.settings)

        self._server: QWebSocketServer | None = None
        self._sessions: dict[QWebSocket, _Session] = {}
        # Runtime only, never persisted. Reopening a campaign and finding a
        # machine already running your table is not a state anyone should
        # arrive in by default: autopilot is switched on deliberately, each
        # time, by someone in the room.
        self._autopilot = False
        self._autopilot_by = ""
        #: What the agent reports having spent. Runtime only, like autopilot:
        #: it is "this session's bill", not a total anyone is accruing.
        self._spend: dict = {}
        #: Turns waiting on the player whose character they belong to, by id.
        #: Runtime only: a proposal nobody answered before the session closed
        #: is a proposal about a moment that has passed.
        self._proposed: dict[str, dict] = {}
        #: Whose turn we have asked "anything else?", and the clock that
        #: answers for them if nobody does. Combat is the one part of an
        #: evening where everybody waits on one person, and the commonest
        #: reason for that wait is somebody who has stopped reading.
        self._waiting_on: int | None = None
        #: Who owes a death save right now, if anybody. Runtime only, like
        #: everything else about a turn in progress: a save owed when the
        #: session ended is owed at a moment that has passed.
        self._owed_by: int | None = None
        #: Things the agent was asked to do that the rules refuse, waiting on
        #: the human DM. Runtime only: a bend nobody answered is about a moment
        #: that has passed.
        self._bends: dict[str, dict] = {}
        #: The creature the DM has asked the agent to write one turn for, or
        #: None. A grant rather than a mode: it lets exactly one proposal
        #: through with autopilot off and is spent the moment it is used, so
        #: asking for a translation never becomes the agent running the fight.
        self._translating: int | None = None
        self._turn_clock = QTimer(self)
        self._turn_clock.setSingleShot(True)
        self._turn_clock.timeout.connect(self._nobody_answered)
        self._pending: dict[QWebSocket, _Pending] = {}
        self._beacon = discovery.Beacon(self)

    def _open_session(self) -> int | None:
        try:
            return self.repos.sessions.ensure_open(self.campaign_id).id
        except Exception:  # noqa: BLE001 - a log is not worth failing to host
            log.exception("could not open a session for the chat log")
            return None

    def _record(self, kind: str, text: str, speaker: str = "", role: str = "",
                payload: dict | None = None, audience: str = EVERYONE) -> None:
        """Keep what was said. Never let the log stop the game."""
        try:
            self.repos.chat.add(
                self.campaign_id,
                kind,
                text,
                session_id=self.session_id,
                speaker=speaker,
                role=role,
                payload=payload,
                audience=audience,
            )
        except Exception:  # noqa: BLE001
            log.exception("could not write to the chat log")

    def history(self, limit: int = DEFAULT_LIMIT, for_dm: bool = False) -> list[dict]:
        """What was said before you arrived, filtered for who is asking.

        The log is handed out on every login, so anything private in it is
        private only until the next person connects. A refusal, a request
        waiting for approval, an expired API key -- each of those went to the DM
        alone at the time and used to be read out to whoever logged in next.
        """
        audiences = (EVERYONE, DM_ONLY) if for_dm else (EVERYONE,)
        try:
            return [
                m.to_dict()
                for m in self.repos.chat.recent(self.campaign_id, limit, audiences)
            ]
        except Exception:  # noqa: BLE001
            log.exception("could not read the chat log")
            return []

    def _campaign_key(self) -> str:
        return campaigns.campaign_key(self.repos)

    # ------------------------------------------------------------------ lifecycle

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.isListening()

    @property
    def port(self) -> int:
        return self._server.serverPort() if self.is_running else 0

    @property
    def members(self) -> list[Member]:
        return [s.member for s in self._sessions.values()]

    def start(self, port: int = DEFAULT_PORT, announce: bool = True) -> bool:
        if self.is_running:
            return True

        server = QWebSocketServer(
            self.session_name, QWebSocketServer.SslMode.NonSecureMode, self
        )
        if not server.listen(QHostAddress.SpecialAddress.Any, port):
            reason = server.errorString() or "could not listen"
            log.error("session server failed to start on port %s: %s", port, reason)
            self.failed.emit(f"Could not host on port {port}: {reason}")
            server.deleteLater()
            return False

        server.newConnection.connect(self._on_new_connection)
        self._server = server
        log.info("hosting %r (campaign %s) on port %s", self.session_name, self.campaign_id, self.port)
        # Before anybody can log in, so nobody's first turn of the evening is
        # the one that discovers their character had no owner.
        self._settle_ownership()

        if announce:
            self._beacon.start(self.session_name, self.port)

        self.started.emit(self.port)
        return True

    def stop(self) -> None:
        self._beacon.stop()
        # Nothing to wait for once there is nobody to wait on.
        self._waiting_on = None
        self._turn_clock.stop()

        # Detach before closing. QWebSocketServer owns the sockets it handed us,
        # so closing it destroys them -- and a queued `disconnected` would then
        # fire against a dead C++ object.
        for socket in list(self._sessions) + list(self._pending):
            _silence(socket)
            socket.close()

        self._sessions.clear()
        for pending in self._pending.values():
            pending.timer.stop()
        self._pending.clear()

        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
            log.info("session server stopped")
            self.stopped.emit()

    # ---------------------------------------------------------------- connections

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while (socket := self._server.nextPendingConnection()) is not None:
            socket.textMessageReceived.connect(
                lambda text, s=socket: self._on_text(s, text)
            )
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(LOGIN_TIMEOUT_MS)
            timer.timeout.connect(lambda s=socket: self._drop_silent(s))
            timer.start()
            self._pending[socket] = _Pending(timer=timer)

    def _drop_silent(self, socket: QWebSocket) -> None:
        if socket in self._pending:
            log.info("dropping a connection that never logged in")
            self._pending.pop(socket, None)
            socket.close()

    def _on_disconnected(self, socket: QWebSocket) -> None:
        pending = self._pending.pop(socket, None)
        if pending is not None:
            pending.timer.stop()
        session = self._sessions.pop(socket, None)
        if session is not None and session.busy:
            # Otherwise "Autopilot is writing..." outlives the agent.
            self._broadcast(
                MessageType.BUSY_NOW, member=session.member.to_dict(), on=False
            )
        try:
            socket.deleteLater()
        except RuntimeError:
            # The server was closed first and took its sockets with it.
            pass
        if session is not None:
            log.info("%s left", session.member.label)
            self._broadcast_system(f"{session.member.label} left")
            self._send_roster()
            self.roster_changed.emit(self.members)

    # ------------------------------------------------------------------- messages

    def _on_text(self, socket: QWebSocket, text: str) -> None:
        try:
            message = decode(text)
        except ProtocolError as exc:
            self._send(socket, MessageType.ERROR, code="bad_message", message=str(exc))
            return

        pending = self._pending.get(socket)
        if pending is not None:
            if message.type == MessageType.HELLO:
                self._handle_hello(socket, pending, message)
            elif message.type == MessageType.LOGIN:
                self._handle_login(socket, pending, message)
            elif message.type == MessageType.ENROL:
                self._handle_enrol(socket, pending, message)
            else:
                self._send(
                    socket, MessageType.ERROR, code="expected_hello", message="log in first"
                )
                socket.close()
            return

        session = self._sessions.get(socket)
        if session is None:
            return  # already gone

        if message.type == MessageType.CHAT:
            self._handle_chat(socket, session, message)
        elif message.type == MessageType.ROLL:
            self._handle_roll(socket, session, message)
        elif message.type == MessageType.EDIT:
            self._handle_edit(socket, session, message)
        elif message.type == MessageType.MOVE:
            self._handle_move(socket, session, message)
        elif message.type == MessageType.TURN:
            self._handle_turn(socket, session, message)
        elif message.type == MessageType.INITIATIVE:
            self._handle_initiative(socket, session, message)
        elif message.type == MessageType.FIGHT:
            self._handle_fight(socket, session, message)
        elif message.type == MessageType.ENLIST:
            self._handle_enlist(socket, session, message)
        elif message.type == MessageType.TERRAIN:
            self._handle_terrain(socket, session, message)
        elif message.type == MessageType.TRANSLATE:
            self._handle_translate(socket, session, message)
        elif message.type == MessageType.PROPOSE:
            self._handle_propose(socket, session, message)
        elif message.type == MessageType.ACTED:
            self._handle_acted(socket, session, message)
        elif message.type == MessageType.DONE:
            self._handle_done(socket, session, message)
        elif message.type == MessageType.ALLOW:
            self._handle_allow(socket, session, message)
        elif message.type == MessageType.SWING:
            self._handle_swing(socket, session, message)
        elif message.type == MessageType.DEATH_SAVE:
            self._handle_death_save(socket, session, message)
        elif message.type == MessageType.SIMULATE:
            self._handle_simulate(socket, session, message)
        elif message.type == MessageType.GIVE:
            self._handle_give(socket, session, message)
        elif message.type == MessageType.BUSY:
            self._handle_busy(session, message)
        elif message.type == MessageType.TROUBLE:
            self._handle_trouble(socket, session, message)
        elif message.type == MessageType.SPENT:
            self._handle_spent(socket, session, message)
        elif message.type == MessageType.DECIDE:
            self._handle_decision(socket, session, message)
        else:
            log.debug("ignoring unknown message type %r", message.type)

    # ---------------------------------------------------------------------- login

    def _handle_hello(self, socket: QWebSocket, pending: _Pending, message) -> None:
        # The host's own app: it already owns the campaign file.
        token = str(message.get("token", ""))
        if token and secrets.compare_digest(token, self.local_token):
            pending.known = self._trusted_versions(message)
            self._admit(socket, pending, account=None, name=str(message.get("name", "")))
            return

        # A machine standing in for a character. It is admitted as that player
        # and nothing more: same projection, same authority, no password.
        seat = str(message.get("seat", ""))
        if seat:
            account, entity_id = self._seat_holder(seat)
            if account is None:
                log.info("a seat token was offered and is not live")
                self._send(
                    socket,
                    MessageType.ERROR,
                    code="bad_login",
                    message=auth.explain(),
                )
                QTimer.singleShot(200, socket.close)
                return
            pending.known = self._trusted_versions(message)
            self._admit(
                socket,
                pending,
                account=account,
                name=account.display_name or account.username,
                seat_for=entity_id,
            )
            return

        pending.known = self._trusted_versions(message)

        username = clean_name(message.get("username", ""))
        account = self.repos.accounts.by_username(self.campaign_id, username)

        pending.username = username
        pending.nonce = auth.new_nonce()
        pending.account_id = account.id if account is not None else None

        # An unknown username still gets a challenge, with a salt derived from
        # the name. Otherwise the login screen would answer "does this person
        # play in the campaign?" for anyone who asked.
        salt = account.salt if account is not None else self._decoy_salt(username)

        self._send(
            socket,
            MessageType.CHALLENGE,
            salt=salt.hex(),
            nonce=pending.nonce.hex(),
        )

    def _trusted_versions(self, message) -> dict[int, int]:
        """What the client holds, but only if it is holding *this* campaign.

        A cache from another campaign has the same ids and the same versions, so
        believing it would serve someone a different game's characters.
        """
        if str(message.get("campaign", "")) != self.campaign_key:
            return {}
        return _known_versions(message.get("known"))

    def _decoy_salt(self, username: str) -> bytes:
        import hashlib

        return hashlib.blake2b(
            username.lower().encode("utf-8"),
            key=self.local_token.encode("utf-8")[:64],
            digest_size=auth.SALT_BYTES,
        ).digest()

    def _handle_login(self, socket: QWebSocket, pending: _Pending, message) -> None:
        if not pending.nonce:
            self._send(socket, MessageType.ERROR, code="expected_hello", message="say hello first")
            return

        account = self.repos.accounts.authenticate(
            self.campaign_id, pending.username, pending.nonce, str(message.get("proof", ""))
        )
        if account is None:
            pending.attempts += 1
            log.info("failed login for %r (attempt %d)", pending.username, pending.attempts)
            self._send(
                socket, MessageType.ERROR, code="bad_login", message=auth.explain()
            )
            if pending.attempts >= 3:
                QTimer.singleShot(200, socket.close)
            else:
                # New nonce, so the next attempt cannot replay this one.
                pending.nonce = auth.new_nonce()
                self._send(
                    socket,
                    MessageType.CHALLENGE,
                    salt=(
                        account.salt.hex()
                        if account
                        else self._decoy_salt(pending.username).hex()
                    ),
                    nonce=pending.nonce.hex(),
                )
            return

        self.repos.accounts.touch(account.id)
        self._admit(socket, pending, account=account, name=account.display_name)

    def _handle_enrol(self, socket: QWebSocket, pending: _Pending, message) -> None:
        """Make an account from an invite. Nobody is logged in when this runs.

        The whole of the authorisation is holding a live invite code, which the
        DM decided when they made it. So this checks exactly three things: that
        some live invite opens what was sent, that the username is free, and
        that the account it would attach to does not already exist. It never
        says which of those failed -- see :func:`enrol.explain`.

        Enrolment is not a login. It makes the account and stops; the client
        turns round and logs in with what it just chose, so there is one way
        into a session rather than two.
        """
        if not pending.nonce:
            self._send(
                socket, MessageType.ERROR, code="expected_hello", message="say hello first"
            )
            return

        pending.attempts += 1
        if pending.attempts > enrol.MAX_ATTEMPTS:
            log.info("too many enrolment attempts; closing")
            QTimer.singleShot(200, socket.close)
            return

        username = clean_name(message.get("username", ""))
        try:
            salt = bytes.fromhex(str(message.get("salt", "")))
            sealed = bytes.fromhex(str(message.get("sealed", "")))
        except ValueError:
            self._refuse_enrolment(socket, pending)
            return
        tag = str(message.get("tag", ""))

        # Every live invite is tried, and the loop does not stop at the first
        # match: which invite it was, and how long that took, are not things a
        # guess should be able to measure.
        opened: tuple = ()
        for invite in self.repos.invites.live(self.campaign_id):
            try:
                verifier = enrol.unseal(
                    invite.code, pending.nonce, username, salt, sealed, tag
                )
            except enrol.EnrolError:
                continue
            if not opened:
                opened = (invite, verifier)

        if not opened:
            self._refuse_enrolment(socket, pending)
            return

        invite, verifier = opened
        problem = self._why_not_enrol(username, invite)
        if problem:
            # Said plainly rather than vaguely: whoever is holding a live code
            # is somebody the DM invited, and "that name is taken" is what they
            # need to hear to get in. The vague message is for people who could
            # not open an invite at all.
            self._send(socket, MessageType.ERROR, code="enrol_refused", message=problem)
            return

        # Claimed before anything is made, so two people racing with the same
        # code cannot both end up with an account.
        if not self.repos.invites.claim(invite.id):
            self._refuse_enrolment(socket, pending)
            return

        # An invite on a character somebody already plays is a *hand-over*, not
        # a second player: the same seat, answered by whoever holds the code.
        # That covers both cases a DM has -- a player who lost their password,
        # and somebody new taking the character on -- with one rule and without
        # dropping the private things that seat had been told.
        seat = self._seat_of(invite.entity_id)
        try:
            if seat is None:
                account = self.repos.accounts.create_with_verifier(
                    self.campaign_id,
                    username,
                    salt=salt,
                    verifier=verifier,
                    character_entity_id=invite.entity_id,
                )
            else:
                account = self.repos.accounts.take_over(
                    seat.id, username, salt=salt, verifier=verifier
                )
        except Exception:
            self.repos.invites.hand_back(invite.id)
            log.exception("could not make an account from invite %s", invite.id)
            self._send(
                socket,
                MessageType.ERROR,
                code="enrol_refused",
                message="That account could not be made. Ask your DM.",
            )
            return

        self.repos.invites.take_up(invite.id, account.id)
        entity = self.repos.entities.get(invite.entity_id)
        character = entity.name if entity is not None else ""
        # Ownership follows the seat, so a hand-over hands the character over
        # too rather than leaving it owned by whoever had it last.
        self.repos.entities.set_owner(invite.entity_id, account.id)
        # Anybody logged in on the old credentials is no longer that person.
        if seat is not None:
            self._show_out(seat.id)
        log.info(
            "invite %s taken up: %r plays %r%s",
            invite.id,
            username,
            character,
            " (taken over)" if seat is not None else "",
        )

        self._send(
            socket, MessageType.ENROLLED, username=username, character=character
        )
        # The DM is told, always. An account appearing in their campaign is not
        # something they should have to notice by reading the roster.
        self._tell_dms(
            f"{username} took up the invite for {character}. "
            "They can log in with the password they just chose."
        )
        self.roster_changed.emit(self.members)
        # A fresh nonce: this connection has not logged in yet, and the pad from
        # this one has now been used.
        pending.nonce = auth.new_nonce()

    # ------------------------------------------------------------------ seats
    #
    # A character handed to autopilot needs something to log in with, and after
    # invitations there is nothing to use: nobody can make a login, and the DM
    # does not know their player's password. That is the point of the design,
    # not a gap in it.
    #
    # So the host mints a **seat token** instead. It is scoped to one account
    # and one character, lives in memory for as long as the handover does, and
    # buys exactly what that player's own login buys -- the same projection, the
    # same authority. A machine playing Marla sees what Marla's player sees,
    # because it is on Marla's seat rather than beside it.
    #
    # Not persisted, deliberately. A token in the campaign file would be a
    # stored credential for a seat, and the thing it protects is not worth one:
    # if the host restarts, the handover is minted again.

    def mint_seat(self, entity_id: int) -> str:
        """A token for whoever is standing in for this character. Empty if none.

        Replaces any token already out for that character, so handing a
        character over twice does not leave the first stand-in able to act.
        """
        entity = self.repos.entities.get(entity_id)
        account_id = self._who_plays(entity)
        if entity is None or account_id is None:
            return ""
        self.revoke_seat(entity_id)
        token = secrets.token_urlsafe(32)
        self._seats[token] = (account_id, entity_id)
        log.info("seat token minted for %r", entity.name)
        return token

    def revoke_seat(self, entity_id: int) -> None:
        """Take the seat back, and shut any connection still sitting in it.

        Both halves matter. Dropping the token stops a *new* connection; a
        session already open on it would otherwise keep playing somebody's
        character after they had asked for it back.
        """
        gone = [t for t, (_a, e) in self._seats.items() if e == entity_id]
        for token in gone:
            self._seats.pop(token, None)
        if not gone:
            return
        for socket, session in list(self._sessions.items()):
            if session.seat_for == entity_id:
                self._send(
                    socket,
                    MessageType.SYSTEM,
                    text="That character is back with their player.",
                    kind=SystemKind.NOTICE.value,
                )
                QTimer.singleShot(200, socket.close)

    def _seat_holder(self, token: str):
        """``(account, entity_id)`` for a live token, or ``(None, None)``.

        Constant-time compared against every live token rather than looked up,
        so the answer takes the same time whether or not the token exists.
        """
        found = None
        for known, seat in self._seats.items():
            if secrets.compare_digest(known, token):
                found = seat
        if found is None:
            return None, None
        account_id, entity_id = found
        return self.repos.accounts.get(account_id), entity_id

    def _who_plays(self, entity) -> int | None:
        """The account that plays this character, or None if nobody does.

        Two rows say this and they can disagree: an **account** names the
        character it plays, and an **entity** names the account that owns it.
        They are written at different times by different code, and when they
        drift the entity's half is the one that goes missing -- at which point
        the host cannot tell that character from a monster. No accept bar for
        the player, and autopilot takes their turn.

        So the account is asked first: "which login plays this" is the fact a
        DM actually set. Ownership is the derived half, and
        :meth:`_settle_ownership` puts it back.
        """
        if entity is None:
            return None
        seat = self._seat_of(entity.id)
        if seat is not None:
            return seat.id
        return entity.owner_account_id

    def _settle_ownership(self) -> None:
        """Make the entity half agree with the account half, once, at startup.

        Repairs campaigns where the two came apart -- and they did: a one-shot
        that stopped shipping logins left its characters owned by nobody, and
        an account can be pointed at a character in a dialog that never touched
        the entity.
        """
        for account in self.repos.accounts.list(self.campaign_id):
            if account.character_entity_id is None:
                continue
            entity = self.repos.entities.get(account.character_entity_id)
            if entity is None or entity.owner_account_id == account.id:
                continue
            log.info(
                "%r plays %r but did not own it; putting that right",
                account.username,
                entity.name,
            )
            self.repos.entities.set_owner(entity.id, account.id)

    def _seat_of(self, entity_id: int):
        """The account that plays this character, if anybody does yet."""
        return next(
            (
                account
                for account in self.repos.accounts.list(self.campaign_id)
                if account.character_entity_id == entity_id
            ),
            None,
        )

    def _why_not_enrol(self, username: str, invite) -> str:
        """Why this enrolment cannot go ahead, in words for somebody invited."""
        if len(username) < 2:
            return "That username is too short."
        taken = self.repos.accounts.by_username(self.campaign_id, username)
        seat = self._seat_of(invite.entity_id)
        if taken is not None and (seat is None or taken.id != seat.id):
            # Free to keep your own name when you are coming back to your own
            # character; not free to take somebody else's.
            return f"{username} is already taken in this campaign."
        return ""

    def _refuse_enrolment(self, socket: QWebSocket, pending: _Pending) -> None:
        """One wording, whatever went wrong. See :func:`enrol.explain`."""
        log.info("enrolment refused (attempt %d)", pending.attempts)
        self._send(
            socket, MessageType.ERROR, code="bad_invite", message=enrol.explain()
        )
        if pending.attempts >= enrol.MAX_ATTEMPTS:
            QTimer.singleShot(200, socket.close)
            return
        # A new nonce, so a recorded attempt cannot be replayed and so the pad
        # is never derived twice from the same code and nonce.
        pending.nonce = auth.new_nonce()
        self._send(
            socket,
            MessageType.CHALLENGE,
            salt=self._decoy_salt(pending.username).hex(),
            nonce=pending.nonce.hex(),
        )

    def _show_out(self, account_id: int) -> None:
        """Disconnect anybody still logged in on a seat that changed hands.

        Without this the previous holder keeps a live session -- and a live
        session is authority: they would still be sent that character's private
        things, and could still act as them, until they happened to close the
        app. Handing a seat over has to end the sitting as well as the login.
        """
        for socket, session in list(self._sessions.items()):
            if session.account_id == account_id:
                self._send(
                    socket,
                    MessageType.SYSTEM,
                    text=(
                        "Your DM has invited somebody to this character. "
                        "This login is no longer the one that plays it."
                    ),
                    kind=SystemKind.NOTICE.value,
                )
                QTimer.singleShot(200, socket.close)

    def invite_for(self, entity_id: int) -> str:
        """Make an invite for a character and return the code. The DM's button.

        Allowed on a character somebody already plays. That is the password
        reset: a player whose password is gone gets a new code rather than a
        DM who knows what they typed. It is also how a character changes hands
        between people. Either way the seat is handed over, not duplicated --
        the old login stops working the moment the code is used, and until then
        it still does.

        Returns an empty string only when there is no such character.
        """
        entity = self.repos.entities.get(entity_id)
        if entity is None:
            return ""
        invite = self.repos.invites.create(self.campaign_id, entity_id)
        log.info("invite made for %r", entity.name)
        return invite.code

    def _admit(
        self,
        socket: QWebSocket,
        pending: _Pending,
        account,
        name: str,
        seat_for: int | None = None,
    ) -> None:
        pending.timer.stop()
        self._pending.pop(socket, None)

        if account is None:  # the host's own app
            viewer = Viewer.dungeon_master()
            member = Member(
                id=new_member_id(),
                name=clean_name(name or "Dungeon Master"),
                role=Role.DM.value,
            )
            account_id = None
        else:
            viewer = Viewer(
                # An agent standing in for the DM answers from the canon, so it
                # sees what they see. What it may *do* is a separate question,
                # settled by the autopilot switch rather than by the projection.
                account_id=account.id,
                is_dm=account.sees_everything,
                owned_entity_ids=self.repos.entities.owned_ids(account.id),
            )
            character = ""
            if account.character_entity_id is not None:
                entity = self.repos.entities.get(account.character_entity_id)
                character = entity.name if entity else ""
            if account.is_agent:
                role = Role.AGENT.value
            elif account.is_dm:
                role = Role.DM.value
            else:
                role = Role.PLAYER.value
            member = Member(
                id=new_member_id(),
                name=clean_name(account.display_name or account.username),
                role=role,
                character=character,
            )
            account_id = account.id

        session = _Session(
            member=member,
            account_id=account_id,
            viewer=viewer,
            is_agent=account is not None and account.is_agent,
            seat_for=seat_for,
        )
        session.visible = visible_entity_ids(self.repos, self.campaign_id, viewer)
        self._sessions[socket] = session
        log.info("%s logged in as %s", member.label, member.role)

        campaign = self.repos.campaigns.get(self.campaign_id)
        self._send(
            socket,
            MessageType.WELCOME,
            you=member.to_dict(),
            session=self.session_name,
            campaign=campaign.name if campaign else "",
            campaign_key=self.campaign_key,
            members=[m.to_dict() for m in self.members],
        )
        # What was said before they arrived, so a session picks up where the
        # last one stopped rather than from an empty panel.
        self._send(
            socket,
            MessageType.HISTORY,
            messages=self.history(for_dm=session.viewer.is_dm),
        )
        self._send_snapshot(socket, session, known=pending.known)
        self._send(socket, MessageType.PANEL_NAMES, names=self.panel_names)
        if session.viewer.is_dm:
            self._send(socket, MessageType.PROPOSALS, proposals=self.proposals)
            self._send(socket, MessageType.FACTS, facts=self.facts)
        # Sent to everyone, filtered per person. Joining halfway through a fight
        # should show you the fight, not an empty grid until something moves.
        self._send(
            socket, MessageType.ENCOUNTER, encounter=self._encounter_for(session)
        )
        # Everyone, not only the agent: a table deserves to know whether it is
        # being answered by a person.
        self._send(socket, MessageType.AUTOPILOT, on=self._autopilot, by=self._autopilot_by)
        self._broadcast_system(f"{member.label} joined", exclude=socket)
        self._send_roster()
        self.roster_changed.emit(self.members)

    # ---------------------------------------------------------------------- state

    def _send_snapshot(
        self, socket: QWebSocket, session: _Session, known: dict[int, int] | None = None
    ) -> None:
        """Everything they may see, or only what has changed since they looked."""
        if known:
            entities, gone = snapshot_since(
                self.repos, self.campaign_id, session.viewer, known
            )
            session.remember_sent(entities)
            # A delta means they still hold everything else at the version they
            # told us about, and we just agreed with them.
            for entity_id, version in known.items():
                session.sent.setdefault(entity_id, version)
            log.info(
                "%s reconnected holding %d entities; sending %d changed, %d gone",
                session.member.label,
                len(known),
                len(entities),
                len(gone),
            )
            self._send(
                socket,
                MessageType.SNAPSHOT,
                entities=entities,
                gone=gone,
                partial=True,
            )
            return

        everything = snapshot(self.repos, self.campaign_id, session.viewer)
        session.remember_sent(everything)
        self._send(
            socket, MessageType.SNAPSHOT, entities=everything, partial=False
        )

    @property
    def panel_names(self) -> dict:
        """What the DM calls each panel, for the rest of the table."""
        prefix = "panel_name.party."
        rows = self.repos.conn.execute(
            "SELECT key, value_json FROM setting WHERE key LIKE ?", (prefix + "%",)
        ).fetchall()
        names = {}
        for row in rows:
            try:
                value = json.loads(row["value_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, str) and value.strip():
                names[row["key"][len(prefix) :]] = value
        return names

    def publish_panel_names(self) -> None:
        """Push the DM's names to everyone connected."""
        self._broadcast(MessageType.PANEL_NAMES, names=self.panel_names)

    def refuse_conflicting(self, entity_id: int) -> int:
        """Refuse proposals made against an older version of this character.

        The DM has since changed the sheet, so the proposal was written against
        something that no longer exists. Approving it would apply a decision
        made about a different character; asking the DM to work out whether it
        still makes sense is worse. Refuse, and let the player ask again.
        """
        entity = self.repos.entities.get(entity_id)
        if entity is None:
            return 0

        refused = 0
        for proposal in self.repos.proposals.open_for_entity(entity_id):
            if proposal.base_version == entity.version:
                continue
            self.repos.proposals.decide(
                proposal.id, "rejected", "the DM changed the sheet in the meantime"
            )
            refused += 1

        if refused:
            self._broadcast_system(
                f"{entity.name} changed, so "
                f"{refused} pending request{'s' if refused > 1 else ''} "
                "no longer applies. Ask again if you still want it."
            )
            self.publish_proposals()
        return refused

    def publish_entity(self, entity_id: int) -> None:
        """Push an entity to everyone allowed to see it, after a DM change.

        Recomputes visibility per connection, so revoking a share arrives as a
        removal rather than leaving a stale copy on a player's screen.
        """
        entity = self.repos.entities.get(entity_id)

        for socket, session in self._sessions.items():
            allowed = visible_entity_ids(self.repos, self.campaign_id, session.viewer)
            was_visible = entity_id in session.visible
            session.visible = allowed

            if entity is None or entity_id not in allowed:
                session.sent.pop(entity_id, None)
                if was_visible:
                    self._send(socket, MessageType.ENTITY_GONE, id=entity_id)
                continue

            projected = project_entity(entity, session.viewer, allowed)
            session.remember_sent(projected)
            self._send(socket, MessageType.ENTITY, entity=projected)

    def publish_all(self) -> None:
        """Resend everything to everyone. Used after a bulk change of shares."""
        for socket, session in self._sessions.items():
            session.visible = visible_entity_ids(self.repos, self.campaign_id, session.viewer)
            self._send_snapshot(socket, session)

    # ------------------------------------------------------------------- actions

    def _handle_chat(self, socket: QWebSocket, session: _Session, message) -> None:
        text = str(message.get("text", "")).strip()[:MAX_CHAT_LENGTH]
        if not text:
            return
        if session.is_agent and not self._autopilot:
            # The entire meaning of autopilot being off. The agent stays
            # connected and keeps receiving, so switching back on is instant --
            # it simply cannot speak, and that is enforced here rather than
            # trusted to it.
            self._send(
                socket,
                MessageType.ERROR,
                code="autopilot_off",
                message="Autopilot is off. The DM is answering.",
            )
            return

        if self._is_an_aside(session):
            self._say_aside(session, text)
            return

        # Somebody who is talking is somebody who has not stopped reading, so
        # the clock waiting on them stops. Saying what you do next is doing
        # something, even before it has been worked out into a turn.
        #
        # Not when a machine is playing them, though: nothing would restart it,
        # and a player chatting through their own handed-over turn would hold
        # the table indefinitely.
        if (
            self._waiting_on is not None
            and self._is_theirs(session, self._waiting_on)
            and not self._machine_plays(
                self.repos.encounters.combatant(self._waiting_on)
            )
        ):
            self._stop_the_clock()

        self._record(SAID, text, speaker=session.member.label, role=session.member.role)
        self._broadcast(MessageType.SAID, member=session.member.to_dict(), text=text)

    def _is_an_aside(self, session: _Session) -> bool:
        """Whether this line is direction rather than speech.

        While autopilot is on there is one voice at the table, and it is the
        agent's. A DM typing then is *directing* -- "there is something behind
        the door" -- and if their words also went out the party would hear two
        DMs, one of whom keeps being contradicted by the other.

        So it goes to the back room: the DM, any co-DM, and the agent, which
        answers it as part of the conversation. Wanting to speak to the table
        directly is what the switch is for.
        """
        return (
            self._autopilot
            and session.viewer.is_dm
            and not session.is_agent
        )

    def _say_aside(self, session: _Session, text: str) -> None:
        """A DM's line while autopilot is on. Heard by the agent, not the party."""
        self._record(
            SAID,
            text,
            speaker=session.member.label,
            role=session.member.role,
            audience=DM_ONLY,
        )
        log.info("%s directed autopilot: %s", session.member.label, text)
        frame = encode(
            MessageType.SAID,
            member=session.member.to_dict(),
            text=text,
            # So the DM's own screen can show that it did not go out. A line
            # that looks public and was not is worse than one held back.
            aside=True,
        )
        for socket, other in self._sessions.items():
            if other.viewer.is_dm:
                socket.sendTextMessage(frame)

    def _handle_busy(self, session: _Session, message) -> None:
        """Someone is composing. Told to everyone, because the point of it is
        that a table with nothing on screen assumes nothing is happening."""
        on = bool(message.get("on"))
        if session.busy == on:
            return
        session.busy = on
        self._broadcast(
            MessageType.BUSY_NOW, member=session.member.to_dict(), on=on
        )

    def _handle_trouble(self, socket: QWebSocket, session: _Session, message) -> None:
        """The agent could not answer, and the DM should hear why.

        Told privately rather than announced: a table does not need to watch a
        machine apologise, and the DM is the only one who can do anything about
        an expired key.
        """
        if not session.is_agent:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="Only an agent reports trouble answering.",
            )
            return
        text = str(message.get("message", "")).strip()[:MAX_CHAT_LENGTH]
        if text:
            log.warning("the agent could not answer: %s", text)
            self._tell_dms(f"Autopilot could not answer: {text}")

    def _handle_spent(self, socket: QWebSocket, session: _Session, message) -> None:
        """What the agent has cost so far.

        Only an agent may report it -- a player claiming a spend figure would
        be putting a number on the DM's screen that nothing generated. And only
        DMs are told: it is their bill.
        """
        if not session.is_agent:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="Only an agent reports what it has spent.",
            )
            return

        self._spend = {
            "tokens_in": int(message.get("tokens_in") or 0),
            "tokens_out": int(message.get("tokens_out") or 0),
            "cached": int(message.get("cached") or 0),
            "dollars": float(message.get("dollars") or 0.0),
            "turns": int(message.get("turns") or 0),
            "model": str(message.get("model") or ""),
        }
        self.publish_spend()

    @property
    def spend(self) -> dict:
        return dict(self._spend)

    def publish_spend(self) -> None:
        frame = encode(MessageType.SPEND, **self._spend)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm and not session.is_agent:
                socket.sendTextMessage(frame)

    def _handle_roll(self, socket: QWebSocket, session: _Session, message) -> None:
        notation = str(message.get("notation", "")).strip()[:MAX_NOTATION_LENGTH]
        try:
            result = roll(notation)
        except DiceError as exc:
            self._send(socket, MessageType.ERROR, code="bad_dice", message=str(exc))
            return
        self._record(
            ROLLED,
            result.describe(),
            speaker=session.member.label,
            role=session.member.role,
            payload={"total": result.total, "rolls": result.rolls},
        )
        self._broadcast(
            MessageType.ROLLED,
            member=session.member.to_dict(),
            notation=result.notation,
            rolls=result.rolls,
            kept=result.kept,
            modifier=result.modifier,
            total=result.total,
            description=result.describe(),
        )

    def _handle_edit(self, socket: QWebSocket, session: _Session, message) -> None:
        """A player asking for a change. Nothing here is applied.

        Everything a player sends is a request the DM answers, hit points
        included. The host writes nothing on a player's say-so.
        """
        entity_id = message.get("id")
        changes = message.get("changes")
        if not isinstance(entity_id, int) or not isinstance(changes, dict):
            return

        if not session.viewer.owns(entity_id):
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="You can only ask about your own characters.",
            )
            return

        entity = self.repos.entities.get(entity_id)
        if entity is None:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="That character no longer exists.",
            )
            return

        # The version they are working against is the one we last sent them.
        # Taking it from the message would let a client pick a convenient one,
        # or omit it and slip past the check entirely.
        base = session.sent.get(entity_id, entity.version)
        if base != entity.version:
            log.info(
                "refused a stale request for %s from %s",
                entity_id,
                session.member.label,
            )
            self._send(
                socket,
                MessageType.ERROR,
                code="stale",
                message=(
                    "Your DM changed this character while you were editing it, "
                    "so your request was not sent. Here it is as it stands now."
                ),
            )
            self.publish_entity(entity_id)
            return

        wanted = self._collect_request(entity, changes)
        if not wanted:
            return  # nothing actually differs; not worth asking about

        problem = self._why_not(entity, wanted)
        if problem:
            # Refused outright rather than queued: the DM should not be asked
            # to approve something that is not a legal sheet.
            self._send(socket, MessageType.ERROR, code="illegal", message=problem)
            return

        self._propose(session, entity_id, wanted, base)
        self._send(
            socket,
            MessageType.SYSTEM,
            text=f"Sent to your DM: {describe_changes(wanted)}",
        )

    def _collect_request(self, entity, changes: dict) -> dict:
        """What they are actually asking to change, and nothing else."""
        proposed = (changes.get("data") or {}).get("sheet")
        wanted: dict = {}
        if isinstance(proposed, dict):
            wanted.update(
                changed_sheet_fields(entity.data.get("sheet") or {}, proposed)
            )

        summary = changes.get("summary")
        if isinstance(summary, str) and summary.strip() != entity.summary:
            wanted["summary"] = summary.strip()
        return wanted

    def _why_not(self, entity, wanted: dict) -> str:
        """A reason the request cannot stand, or empty if it is fine.

        Checked here because the client that sent it is the one thing we cannot
        take at its word, and because the DM should not be handed nonsense to
        approve.
        """
        sheet = dict(entity.data.get("sheet") or {})
        if not sheet:
            return ""
        sheet.update({k: v for k, v in wanted.items() if k != "summary"})
        report = validate(sheet, self.content)
        return "" if report.ok else f"That is not a legal sheet: {report.summary()}"


    # -------------------------------------------------------------- the fight

    def _encounter_for(self, session: _Session) -> dict:
        """The running fight as this one person may see it, or ``{}`` for none.

        Only the *running* fight is ever sent. A DM preparing next week's ambush
        in another encounter is doing exactly the thing the party must not see,
        and "which fight is on screen" is not a distinction worth trusting to
        the panel.
        """
        encounter = self.repos.encounters.running(self.campaign_id)
        if encounter is None:
            return {}
        return project_encounter(
            encounter,
            self._with_stand_ins(self.repos.encounters.combatants(encounter.id)),
            session.viewer,
            visible_entity_ids(self.repos, self.campaign_id, session.viewer),
            self.repos.encounters.obstacles(encounter.id),
            self._turn_budget(encounter),
            self.repos.encounters.teams(encounter.id),
        )

    def _turn_budget(self, encounter) -> dict:
        """What the creature whose turn it is has left, in squares and feet."""
        if not encounter.has_begun or encounter.turn_combatant_id is None:
            return {}
        combatant = self.repos.encounters.combatant(encounter.turn_combatant_id)
        entity = self._entity_of(encounter.turn_combatant_id)
        if combatant is None or entity is None:
            return {}
        sheet = (entity.data or {}).get("sheet") or {}
        speed = derive.speed_in_squares(sheet, self.content)
        return {
            "combatant": combatant.id,
            "speed": speed,
            "moved": int(encounter.moved_squares or 0),
            "left": max(0, speed - int(encounter.moved_squares or 0)),
            "acted": bool(encounter.action_used),
        }

    def _show(self, kind: str, combatant, target=None, **detail) -> None:
        """Tell everyone to show the same thing, at the same moment.

        Sent as an event rather than left to be worked out from two states: a
        client diffing "it was there, now it is here" would draw its own idea
        of the walk, at its own moment, and four people would watch four
        different fights.

        Filtered like the map itself. A token you were never sent does not
        acquire an animation -- that would be the one thing projection exists
        to prevent, arriving as a moving dot.

        With nobody connected at all -- not even our own loopback join, which
        means we are not hosting -- there is no wire for this to travel, and
        the DM's own map would otherwise just teleport tokens around. Emitted
        locally in that one case only: hosting always joins ourselves as a
        session, so this and the wire never fire for the same move.
        """
        if combatant is None:
            return
        if not self._sessions:
            payload = dict(detail)
            payload["kind"] = kind
            payload["combatant"] = combatant.id
            if target is not None:
                payload["target"] = target.id
            self.played.emit(payload)
        for socket, session in self._sessions.items():
            visible = visible_entity_ids(self.repos, self.campaign_id, session.viewer)
            if not self._can_see(combatant, session, visible):
                continue
            payload = dict(detail)
            payload["kind"] = kind
            payload["combatant"] = combatant.id
            if target is not None and self._can_see(target, session, visible):
                payload["target"] = target.id
            self._send(socket, MessageType.PLAY, **payload)

    def _can_see(self, combatant, session: _Session, visible: set[int]) -> bool:
        if session.viewer.is_dm:
            return True
        return combatant.entity_id is not None and (
            combatant.entity_id in visible or session.viewer.owns(combatant.entity_id)
        )

    def _tidy_the_fight(self) -> None:
        """Bring the fight's own bookkeeping back in step before it is sent.

        Two things are stored on the combatant that are really facts about the
        creature: which side it is on, and whether it is lying down. Both are
        kept here so that SQL can answer "who is standing in that square" and
        "who is an enemy" without reaching across to the entity table, and both
        are re-derived here so that a DM editing hit points in the Characters
        panel cannot leave a ghost on its feet.
        """
        encounter = self._the_running_fight()
        if encounter is None:
            return
        self.repos.encounters.sort_into_teams(encounter.id)
        for combatant in self.repos.encounters.combatants(encounter.id):
            entity = self._entity_of(combatant.id)
            hp = (entity.data or {}).get("hp") if entity is not None else None
            if isinstance(hp, int):
                self.repos.encounters.set_down(combatant.id, hp == 0)

    def publish_encounter(self) -> None:
        """Push the fight to everyone, filtered per person.

        Not one frame broadcast: two people at the same table are shown
        different tokens, so there is no shared frame to send.
        """
        self._tidy_the_fight()
        for socket, session in self._sessions.items():
            self._send(
                socket, MessageType.ENCOUNTER, encounter=self._encounter_for(session)
            )

    def _may_run_the_table(self, session: _Session) -> bool:
        """Who may move a token, pass the turn, or set an initiative.

        One question for all three, because they are one authority: whoever is
        running the fight. The DM always is. An agent is gated on autopilot
        exactly as its chat is, and for the same reason -- "off" has to be
        something the host enforces, not something the agent is trusted to
        observe. A player cannot do any of it yet; see the known gaps in
        ARCHITECTURE.md.

        Putting a turn to a player is *not* one of the three, and asks
        :meth:`_may_propose` instead. Keep it that way: these three change the
        fight, and an offer does not.
        """
        if session.is_agent:
            return self._autopilot
        return session.viewer.is_dm

    def _may_act_for(self, session: _Session, combatant) -> bool:
        """Whether this connection may take *this* creature's turn, right now.

        Everything a stand-in is allowed to do is in the four conditions below,
        and it is allowed nothing else. It is a player connection: it cannot
        move anybody else, pass the turn, set an initiative or touch the fight.
        It can take the turn of the one character it was handed, while it is
        still handed over, while it is still that character's turn.

        Narrow on purpose. A seat exists so a machine can sit in one chair, and
        the moment it can reach past that chair it stops being a seat and
        becomes an account with a strange name.
        """
        if self._may_run_the_table(session):
            return True
        if combatant is None:
            return False

        # Your own character, on its own turn. Narrow in exactly the way the
        # seat below is, and for the same reason: this is the door a player's
        # map takes their turn through, and it must not become a door onto
        # anybody else's. It cannot pass the turn, set an initiative or touch
        # the fight -- it can move and swing, as themselves, while it is
        # theirs to do.
        #
        # Not for a seat, which is minted against the owner's account and would
        # otherwise inherit this the moment its handover ended -- the one thing
        # the seat rules below exist to prevent.
        encounter = self._the_running_fight()
        if (
            session.seat_for is None
            and encounter is not None
            and encounter.turn_combatant_id == combatant.id
        ):
            theirs = self._entity_of(combatant.id)
            if (
                theirs is not None
                and session.account_id is not None
                and theirs.owner_account_id == session.account_id
                and not combatant.simulated
            ):
                return True

        if session.seat_for is None:
            return False
        if not combatant.simulated:
            return False
        entity = self._entity_of(combatant.id)
        if entity is None or entity.id != session.seat_for:
            return False
        encounter = self._the_running_fight()
        return encounter is not None and encounter.turn_combatant_id == combatant.id

    def _refuse_the_fight(self, socket: QWebSocket) -> None:
        self._send(
            socket,
            MessageType.ERROR,
            code="refused",
            message="Only whoever is running the fight can do that.",
        )

    def _the_running_fight(self):
        return self.repos.encounters.running(self.campaign_id)

    def _handle_turn(self, socket: QWebSocket, session: _Session, message) -> None:
        """Start the fight, pass the turn, or stop the clock.

        The same three buttons the DM's panel has, reachable over the wire, so
        an agent running a combat is doing the thing the DM does rather than a
        parallel thing that happens to look like it.
        """
        if not self._may_run_the_table(session):
            self._refuse_the_fight(socket)
            return

        encounter = self._the_running_fight()
        if encounter is None:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="There is no fight being run.",
            )
            return

        action = str(message.get("action", "")).lower()
        if action not in ("begin", "next", "end"):
            self._send(
                socket,
                MessageType.ERROR,
                code="bad_message",
                message="A turn is begin, next or end.",
            )
            return

        # Autopilot may end its own turns and nobody else's. It is told to call
        # next_turn once it has resolved whoever was up, and a model that calls
        # it one turn early took a person's turn away with nothing on screen to
        # say why. A person's turn ends when they say Done, when their own
        # clock runs out, or when the DM moves it on.
        if session.is_agent and action == "next":
            up = self._the_running_fight()
            combatant = (
                self.repos.encounters.combatant(up.turn_combatant_id)
                if up is not None and up.turn_combatant_id is not None
                else None
            )
            if combatant is not None and not self._machine_plays(combatant):
                entity = self._entity_of(combatant.id)
                whose = entity.name if entity is not None else "somebody"
                self._send_refusal(
                    socket,
                    f"It is {whose}'s turn, and they play for themselves. Wait "
                    "for them rather than passing it on.",
                )
                self._tell_the_agent(
                    f"You tried to end {whose}'s turn. It is not yours to end -- "
                    "they answer for themselves, and the turn passes when they "
                    "are done. Carry on with what you were saying."
                )
                return

        self.run_turn(action)
        log.info("%s: turn %r", session.member.label, action)

    def run_turn(self, action: str) -> str:
        """Begin, next or end. Returns a reason it could not happen, or empty.

        Public because the DM's own Combat panel calls it directly -- their app
        *is* the host, and passing the turn now rolls a death save, which is not
        a thing a panel may do for itself.
        """
        encounter = self._the_running_fight()
        if encounter is None:
            return "There is no fight being run."

        # The turn is moving whatever we were waiting for -- including a death
        # save that was owed and never rolled. It belonged to that turn.
        self._stop_the_clock()
        self._owed_by = None

        if action == "begin":
            self.repos.encounters.begin(encounter.id)
            self._owe_a_save_if_dying()
        elif action == "next":
            self._advance_turn()
        elif action == "end":
            self.repos.encounters.end(encounter.id)
        else:
            return "A turn is begin, next or end."

        self._announce_turn(action)
        self.publish_encounter()
        self.encounter_applied.emit()
        return ""

    def _announce_turn(self, action: str) -> None:
        """Say it in the chat as well, and keep it.

        Whose turn it was is part of what happened at that table, and a fight
        run by an agent is exactly the case where somebody will want to read
        back what it did.
        """
        encounter = self._the_running_fight()
        if action == "end":
            self._broadcast_system("The fight is over.")
            return
        if encounter is None:
            return
        combatant = (
            self.repos.encounters.combatant(encounter.turn_combatant_id)
            if encounter.turn_combatant_id
            else None
        )
        entity = (
            self.repos.entities.get(combatant.entity_id)
            if combatant is not None and combatant.entity_id is not None
            else None
        )
        whose = entity.name if entity is not None else "someone"
        if action == "begin":
            self._broadcast_system(f"Roll initiative. {whose} is up first.")
        else:
            self._broadcast_system(f"Round {encounter.round}: {whose} is up.")

    def _handle_fight(self, socket: QWebSocket, session: _Session, message) -> None:
        """Start a fight. The one the DM's New fight button makes, over the wire.

        Deliberately the same repository call: an agent setting up a combat must
        not be able to produce an encounter the app could not have produced
        itself.
        """
        if not self._may_run_the_table(session):
            self._refuse_the_fight(socket)
            return

        encounter = self.repos.encounters.create(
            self.campaign_id,
            name=str(message.get("name", ""))[:MAX_NAME_LENGTH],
            width=int(message.get("width") or DEFAULT_WIDTH),
            height=int(message.get("height") or DEFAULT_HEIGHT),
        )
        log.info("%s started a fight: %r", session.member.label, encounter.name)
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_enlist(self, socket: QWebSocket, session: _Session, message) -> None:
        """Put a creature into the fight being run, optionally on a square."""
        if not self._may_run_the_table(session):
            self._refuse_the_fight(socket)
            return

        encounter = self._the_running_fight()
        entity_id = message.get("entity")
        if encounter is None or not isinstance(entity_id, int):
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="There is no fight being run.",
            )
            return
        if self.repos.entities.get(entity_id) is None:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="There is no such creature.",
            )
            return

        x, y = message.get("x"), message.get("y")
        initiative = message.get("initiative")
        if isinstance(x, int) and isinstance(y, int) and not self.repos.encounters.free_for(
            encounter.id, x, y
        ):
            # Said rather than quietly dropped. This door wrote the square
            # without asking, so autopilot could stand a goblin outside the
            # room -- and would then describe it as standing there, which is
            # the one thing it must never do.
            self._send_refusal(
                socket,
                f"{grid.label(x, y)} is off the map, taken, or in the way. "
                "Nothing is standing there.",
            )
            return
        combatant = self.repos.encounters.add(
            encounter.id,
            entity_id=entity_id,
            initiative=int(initiative) if isinstance(initiative, int) else None,
            x=int(x) if isinstance(x, int) else None,
            y=int(y) if isinstance(y, int) else None,
        )
        if combatant is None:
            # Already in the fight. Not an error worth a message: asking twice
            # is the normal way a model gets to the state it wanted.
            return
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_terrain(self, socket: QWebSocket, session: _Session, message) -> None:
        """Put something in the way, or take it out."""
        if not self._may_run_the_table(session):
            self._refuse_the_fight(socket)
            return

        encounter = self._the_running_fight()
        x, y = message.get("x"), message.get("y")
        if encounter is None or not isinstance(x, int) or not isinstance(y, int):
            return

        wanted = bool(message.get("on", True))
        here = (x, y) in self.repos.encounters.obstacles(encounter.id)
        if here == wanted:
            return
        self.repos.encounters.toggle_obstacle(encounter.id, x, y)
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_initiative(self, socket: QWebSocket, session: _Session, message) -> None:
        """Set one combatant's initiative, or clear it with a null."""
        if not self._may_run_the_table(session):
            self._refuse_the_fight(socket)
            return

        combatant_id = message.get("combatant")
        if not isinstance(combatant_id, int):
            return
        value = message.get("value")
        value = int(value) if isinstance(value, int) else None

        combatant = self.repos.encounters.combatant(combatant_id)
        encounter = self._the_running_fight()
        if combatant is None or encounter is None or combatant.encounter_id != encounter.id:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="That is not in the fight being run.",
            )
            return

        self.repos.encounters.set_initiative(combatant_id, value)
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_move(self, socket: QWebSocket, session: _Session, message) -> None:
        """A request to move a token. ``x``/``y`` of null takes it off the map."""
        combatant_id = message.get("combatant")
        if not isinstance(combatant_id, int):
            return
        x = message.get("x")
        y = message.get("y")
        x = int(x) if isinstance(x, int) else None
        y = int(y) if isinstance(y, int) else None

        combatant = self.repos.encounters.combatant(combatant_id)
        encounter = self._the_running_fight()
        # Asked after the combatant is known, because a stand-in may move one
        # creature and only one; see :meth:`_may_act_for`.
        if not self._may_act_for(session, combatant):
            self._refuse_the_fight(socket)
            return
        if combatant is None or encounter is None or combatant.encounter_id != encounter.id:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="That is not in the fight being run.",
            )
            return

        if x is not None and y is not None and not encounter.holds(x, y):
            # Checked before the speed rule, because a square off the edge of
            # the map is not a rule anybody can waive -- there is no such
            # square -- and it must not be put to the DM as though it were.
            self._send_refusal(socket, f"{x},{y} is off the map.")
            return

        # Once the fight is running, moving a creature *is* that creature
        # walking, whoever asked: round what is in the way, charged against
        # what the turn has left, and swung at by anybody whose reach it
        # leaves. Before it starts there is no turn to spend and nobody to
        # walk past, so a drag is what it looks like -- arranging the board.
        entity = self._entity_of(combatant_id)
        walking = session.is_agent or encounter.has_begun
        if walking and entity is not None and x is not None and y is not None:
            nowhere = self._no_way_through(combatant, entity, x, y)
            if nowhere:
                # Also not bendable: a creature walled in by bodies and rock
                # has nowhere to walk to, which is the same tier as a square
                # already taken, not a rule the DM can wave through.
                self._send_refusal(socket, nowhere)
                return

        # The rules of a walk bind whoever is walking. This used to be asked
        # only of the agent, because for a long time the agent was the only
        # thing that walked -- everybody else either arranged the board or
        # could not move at all. A player taking their own turn on their own
        # map is neither of those, and without this they walked nine squares
        # on a speed of six.
        if walking and entity is not None:
            reason = self._breaks_a_rule(
                encounter, combatant, entity, [x, y] if x is not None else None
            )
            if reason and session.is_agent:
                # An agent's is put to the DM, who may well say yes. Nobody
                # else has that conversation: a player is not asking for a
                # ruling, they are taking their turn.
                self._ask_the_dm_to_bend(
                    {"kind": "move", "combatant": combatant_id, "x": x, "y": y,
                     "what": f"move {entity.name} to {x},{y}"},
                    what=f"move {entity.name} to {x},{y}",
                    why=reason,
                )
                self._tell_the_agent(
                    f"{reason} It is with your DM, who may allow it. Do not "
                    "narrate it as though it happened."
                )
                return
            if reason:
                self._send_refusal(socket, reason)
                return

        if not self._do_move(combatant_id, x, y, spending=walking):
            trouble = self._walk_went_wrong(
                combatant_id, x, y, entity.name if entity is not None else "They"
            )
            if trouble:
                self._send_refusal(socket, trouble)
            return
        log.info("%s moved combatant %s to %s,%s", session.member.label, combatant_id, x, y)
        if session.is_agent:
            # Something happened, so the clock that ends the turn restarts. A
            # move then a swing is one turn, not two turns and a gap.
            self._ask_if_done(combatant_id)

    def _do_move(
        self,
        combatant_id: int,
        x: int | None,
        y: int | None,
        spending: bool = False,
    ) -> bool:
        """Move a token, and count it against the turn if it *is* a turn.

        ``spending`` is the difference between a creature walking and a DM
        arranging the board. Both end with a token on a different square; only
        one of them uses up somebody's movement.
        """
        before = self.repos.encounters.combatant(combatant_id)
        # Worked out before anything moves, because it is the way round the map
        # as it stands now -- and used for all three of the things a walk is:
        # who gets a swing at it, how far it costs, and what is drawn.
        route: list[tuple[int, int]] = []
        if before is not None and before.on_map and x is not None and y is not None:
            route = self.route_for(before, x, y) or grid.steps_between(
                (before.x, before.y), (x, y)
            )

        walking = (
            spending
            and before is not None
            and before.on_map
            and x is not None
            and y is not None
        )
        if walking:
            # Asked before anything is drawn, because a square that cannot be
            # stood on is a fact about the map and not something a walk finds
            # out on arrival. ``place`` has always refused it -- but it is the
            # last thing to run, by which point the walk has been sent to every
            # screen and swung at along the way. Two goblins running for the
            # same corner left the second one drawn standing in the first one's
            # square, having never moved at all and with nothing sent after to
            # put it back.
            if not self.repos.encounters.free_for(
                before.encounter_id, x, y, ignoring=combatant_id
            ):
                return False
            # Sent *before* the swings, so every screen already has the walk to
            # time them against: a swing provoked five squares along has to
            # land five squares along, not the moment the creature sets off.
            self._show(
                Played.MOVE.value, before, path=[list(square) for square in route]
            )
            reached = self._opportunity_attacks(before, x, y, route)
            # The swings happen as they leave, so a creature dropped by one
            # never arrives. It falls on the square the blow caught it on --
            # which is where it was standing, and where anybody coming to help
            # it will look.
            still_up = self.repos.encounters.combatant(combatant_id)
            if still_up is None or not still_up.on_map or still_up.down:
                self._fell_on_the_way(combatant_id, before, route, reached)
                return False

        if not self.repos.encounters.place(combatant_id, x, y):
            return False

        encounter = self._the_running_fight()
        if x is not None and before is not None and before.on_map and route:
            if not walking:
                # Shown as a walk rather than a jump, and worked out here so
                # every screen walks the same line -- round the same rock, too.
                self._show(
                    Played.MOVE.value,
                    before,
                    path=[list(square) for square in route],
                )
            if (
                spending
                and encounter is not None
                and encounter.turn_combatant_id == combatant_id
            ):
                # Charged along the way walked. Going round something costs
                # what going round it costs.
                self.repos.encounters.spend_movement(encounter.id, len(route) - 1)

        self.publish_encounter()
        # The DM's own map reads the database, so it does not know yet.
        self.encounter_applied.emit()
        return True

    def _walk_went_wrong(self, combatant_id: int, x, y, who: str) -> str:
        """Why a walk did not end where it was asked to, in a sentence.

        A walk fails for two quite different reasons, and every door that
        reported one reported both the same way: "that square is taken, or
        something is in the way". True of the ordinary case, and a red herring
        for the other one -- the walker cut down partway along, which
        :meth:`_fell_on_the_way` has already described in the right words. So
        the table was told somebody was standing on an empty square, at the
        loudest moment the fight had.

        Empty when there is nothing left to say.
        """
        fallen = self.repos.encounters.combatant(combatant_id)
        if fallen is not None and fallen.down:
            return ""
        return (
            f"{who} could not get to {grid.label(x, y)} -- it is taken, or "
            "something is in the way."
        )

    def _still_up(self, combatant_id: int) -> bool:
        """Whether this creature is in any state to do anything at all.

        Asked before the *action* half of a turn, because the move half can end
        with the mover unconscious. A proposal to walk over and swing is
        carried out in two steps, and nothing between them noticed that the
        creature holding the axe had been cut down on the first one -- the
        turn had already passed to somebody else by then, which is exactly why
        the one-action rule did not catch it either.
        """
        combatant = self.repos.encounters.combatant(combatant_id)
        return combatant is not None and not combatant.down

    def _fell_on_the_way(self, combatant_id: int, before, route, reached: int) -> None:
        """A walk that ended in the walker going down partway along it.

        Three things all had to be put right here, and all three were the same
        omission: this used to return without telling anybody. The creature
        stayed drawn on its feet at the square it set off from, because nothing
        was published; the walk that had already been described ran its full
        length, because nothing corrected it; and the turn went on belonging to
        somebody unconscious, because nothing passed it.

        So: they are put on the square they got to, the walk is re-sent as the
        shorter one that actually happened -- a second walk replaces the first
        rather than drawing beside it -- and the turn moves on, because there
        is nothing an unconscious creature is going to do with the rest of it.
        """
        walked = route[: max(1, reached) + 1] if route else []
        if walked and len(walked) > 1:
            self.repos.encounters.place(combatant_id, *walked[-1])
            self._show(
                Played.MOVE.value, before, path=[list(square) for square in walked]
            )

        self.publish_encounter()
        self.encounter_applied.emit()

        encounter = self._the_running_fight()
        if encounter is not None and encounter.turn_combatant_id == combatant_id:
            self._stop_the_clock()
            self._advance_turn()
            self._announce_turn("next")
            self.publish_encounter()
            self.encounter_applied.emit()

    def _are_enemies(self, one, other) -> bool:
        """Whether these two combatants are on opposite sides of this fight.

        Sides are a thing the fight records rather than a thing this guesses.
        Every fight is made with two, everybody is sorted onto one of them by
        the old rule -- player characters together, everything else against
        them -- and from then on it is the DM's to change. The captured guard
        fighting beside the party is a move, not an argument with the app.

        Combatants with no side are on nobody's: a fight from before teams
        existed provokes nothing until it is next published, which sorts it.
        """
        if one is None or other is None:
            return False
        if one.team_id is None or other.team_id is None:
            return False
        return one.team_id != other.team_id

    def _opportunity_attacks(self, mover, to_x: int, to_y: int, route=None) -> int:
        """Everybody whose reach this creature walks out of gets a swing.

        Evaluated one square of the walk at a time -- the *route*, the way
        actually taken round whatever was in the way, which is the same walk
        the move is animated along -- rather than only the start and the end.
        Going the long way round a pillar means leaving somebody's reach
        somewhere the straight line never went, and they get their swing for
        the walk that happened. Start-and-end alone
        missed the creature who was never adjacent when it *started* but
        crossed straight through an ogre's reach on the way to somewhere
        else -- entered and left in the same move, with nothing at either
        end to show for it. Squeezing along the *edge* of a reach and back
        out the same side, by contrast, is still not really leaving it: a
        rule that fired on every wobble would punish moving at all. The
        version people play at a table is "did you leave, at any point
        during the walk", and checking each step is that.

        Returns how far along the walk the creature got: the whole of it
        normally, and the square a swing dropped it on when one did.
        """
        encounter = self._the_running_fight()
        if encounter is None or mover.id is None:
            return 0
        moving = self._entity_of(mover.id)
        if moving is None:
            return 0
        # Sides are read off the fight, so they have to have been worked out
        # before the first step anybody takes -- not at the next publish.
        self.repos.encounters.sort_into_teams(encounter.id)
        mover = self.repos.encounters.combatant(mover.id) or mover
        path = route or grid.steps_between((mover.x, mover.y), (to_x, to_y))

        resting = self._resting()
        for other in self.repos.encounters.combatants(encounter.id):
            if other.id == mover.id or not other.on_map or other.id is None:
                continue
            if other.id in resting or other.down:
                continue  # the fallen do not swing at passers-by
            if other.reaction_round >= encounter.round:
                continue  # one reaction a round, and it is spent
            if not self._are_enemies(mover, other):
                continue
            watcher = self._entity_of(other.id)
            if watcher is None:
                continue
            sheet = (watcher.data or {}).get("sheet") or {}
            weapon = attack.melee_weapon(sheet, self.content)
            if weapon is None:
                continue

            # The first step of the walk where this watcher's reach is behind
            # rather than ahead -- the exact square the walk leaves it from,
            # found rather than assumed to be the last one. `break` the moment
            # it is found: this watcher has one reaction regardless of whether
            # the rest of the walk crosses back through its reach again.
            left_at = None
            for step, (here, there) in enumerate(zip(path, path[1:]), start=1):
                was = attack.squares_between((other.x, other.y), here)
                now = attack.squares_between((other.x, other.y), there)
                if attack.threatens(sheet, self.content, was) and not attack.threatens(
                    sheet, self.content, now
                ):
                    left_at = step
                    break
            if left_at is None:
                continue

            self.repos.encounters.use_reaction(other.id, encounter.round)
            self._broadcast_system(
                f"{moving.name} leaves {watcher.name}'s reach: "
                "an opportunity attack."
            )
            self._swing(
                {"combatant": other.id, "target": mover.id, "weapon": weapon.name},
                checked=True,
                at_step=left_at,
            )
            # Dropped on the way out. Nobody else gets a swing at somebody who
            # is already on the floor, and the walk ends where they fell.
            after = self._entity_of(mover.id)
            if after is not None and (after.data or {}).get("hp") == 0:
                return left_at

        return max(0, len(path) - 1)

    # ------------------------------------------------------------ taking a turn
    #
    # A player says "I get behind the orc and hit it with my axe". The agent
    # turns that into squares and a weapon; the host checks it; the player sees
    # exactly what is about to happen and says yes. Three parties, and none of
    # them can skip the others:
    #
    # * The **agent** may only propose. It never moves anybody.
    # * The **host** decides whether the proposal is even legal, and does all
    #   the rolling. The dice were never the client's.
    # * The **player** owns their own turn. Nothing touches their character
    #   until they accept, and refusing costs one click.
    #
    # That last one is why this is not simply the agent moving tokens. Handing
    # a machine the power to walk your character into a fire is a different
    # product from one that offers to.

    def _may_propose(self, session: _Session, combatant_id) -> bool:
        """Who may put a turn to a player.

        Not :meth:`_may_run_the_table`, deliberately. That one answers for
        moving a token, passing the turn and setting an initiative all at once,
        and widening it so a DM could ask for one translation would hand the
        agent the other three for free -- a rule loosened at one door and
        thereby at doors nobody was thinking about.

        A proposal is the one thing that is safe to grant on its own, because
        it is an offer: the player still has to accept it, and refusing costs a
        click. So the grant lives here and nowhere else.
        """
        if not session.is_agent:
            return session.viewer.is_dm
        if self._autopilot:
            return True
        if self._translating is None or combatant_id != self._translating:
            return False
        # Still *their* turn. Not the turn rule over again -- that one is
        # `_breaks_a_rule`'s, and is the DM's to waive -- but the edge of the
        # grant: the DM asked for one creature's turn, and once the turn has
        # moved on there is no longer a turn of theirs to write. Checked here
        # rather than cleared when the turn passes, because the turn passes
        # from several places and a grant that had to be swept up by each of
        # them would eventually be missed by one.
        encounter = self._the_running_fight()
        return encounter is not None and encounter.turn_combatant_id == combatant_id

    def _handle_translate(self, socket: QWebSocket, session: _Session, message) -> None:
        """The DM asks for a line of plain English as a turn.

        The DM's door only, and it does not touch autopilot: the point of it is
        the evening where somebody is running their own table and wants one
        sentence worked out, not one where they have handed the table over.

        Nothing is refused quietly. Every way this can fail is something the DM
        just clicked on, so each one says what happened.
        """
        if session.is_agent or not session.viewer.is_dm:
            self._refuse_the_fight(socket)
            return

        said = str(message.get("text", "")).strip()[:MAX_CHAT_LENGTH]
        if not said:
            self._send_refusal(socket, "There is nothing there to turn into a turn.")
            return

        encounter = self._the_running_fight()
        combatant = (
            self.repos.encounters.combatant(encounter.turn_combatant_id)
            if encounter is not None and encounter.turn_combatant_id is not None
            else None
        )
        entity = (
            self.repos.entities.get(combatant.entity_id)
            if combatant is not None and combatant.entity_id is not None
            else None
        )
        if encounter is None or combatant is None or entity is None:
            self._send_refusal(socket, "Nobody's turn is up in a fight right now.")
            return

        # The speaker has to be the one whose turn it is, and this is the only
        # place that can say so: the client knows a name, and a name is the
        # half of "who plays this character" that drifts. See `_who_plays`.
        speaking = self._account_of_member(str(message.get("member", "")))
        theirs = self._who_plays(entity)
        if theirs is None:
            self._send_refusal(
                socket,
                "Nobody plays that character, so there is nobody to put a turn "
                "to. Move it yourself.",
            )
            return
        if speaking is None or speaking != theirs:
            # Said now rather than letting the agent find out. The mark sits
            # beside a line that may have scrolled past several turns ago, and
            # the honest answer is about the turn, not about the sentence.
            self._send_refusal(socket, f"It is {entity.name}'s turn, not theirs.")
            return

        # The grant is written before the agent is told, because the agent may
        # answer faster than this method returns.
        self._translating = combatant.id
        if not self._tell_the_agent(
            MessageType.TRANSLATE_THIS,
            combatant=combatant.id,
            who=entity.name,
            said=said,
        ):
            self._translating = None
            self._send_refusal(
                socket,
                "The agent is not connected, so there is nothing to read that. "
                "Start it from the table panel.",
            )

    def _handle_propose(self, socket: QWebSocket, session: _Session, message) -> None:
        if not self._may_propose(session, message.get("combatant")):
            self._refuse_the_fight(socket)
            return
        # Spent on use, whatever becomes of the proposal. A grant that outlived
        # the one turn it was given for would be autopilot by another name.
        self._translating = None

        encounter = self._the_running_fight()
        combatant_id = message.get("combatant")
        combatant = (
            self.repos.encounters.combatant(combatant_id)
            if isinstance(combatant_id, int)
            else None
        )
        if encounter is None or combatant is None or combatant.encounter_id != encounter.id:
            self._send_refusal(socket, "That is not in the fight being run.")
            return

        entity = (
            self.repos.entities.get(combatant.entity_id)
            if combatant.entity_id is not None
            else None
        )
        if entity is None:
            self._send_refusal(socket, "That token is not a creature.")
            return
        if self._who_plays(entity) is None:
            self._send_refusal(
                socket,
                "Nobody plays that character, so there is nobody to ask. Move it "
                "yourself.",
            )
            return

        fields = {
            "move": message.get("move"),
            "target": message.get("target"),
            "weapon": message.get("weapon", ""),
            "text": message.get("text", ""),
        }
        problem, action, bendable = self._shape_the_action(
            encounter, combatant, entity, fields
        )
        if problem and bendable and session.is_agent:
            # A rule, and the DM is the one who decides whether it applies
            # tonight. Parked rather than refused.
            self._ask_the_dm_to_bend(
                {"kind": "propose", "combatant": combatant.id, "fields": fields,
                 "what": str(fields["text"] or "that")},
                what=str(fields["text"] or f"a turn for {entity.name}"),
                why=problem,
            )
            self._tell_the_agent(
                f"{problem} It is with your DM, who may allow it. Do not "
                "narrate it as though it happened."
            )
            return
        if problem:
            self._send_refusal(socket, problem)
            return

        self._offer(action, entity)

    def is_played_here(self, combatant_id: int) -> bool:
        """Whether somebody is playing this creature *and is at the table now*.

        Both halves matter, and the second is the one worth stating. A character
        whose player has gone home is the DM's to run: putting a turn to an empty
        chair would hold the fight until the clock gave up, which is a worse
        answer than the DM simply playing them. So an offer is only made to
        somebody who could actually answer it.

        Asked by the DM's own panel before a staged turn goes anywhere, so that
        "carry it out" and "put it to them" is one decision made in one place.
        The panel cannot answer it: which login plays a character is what
        :meth:`_who_plays` exists to settle, and a second opinion worked out
        from the entity alone is how those two halves drift apart.
        """
        entity = self._entity_of(combatant_id)
        if entity is None:
            return False
        theirs = self._who_plays(entity)
        if theirs is None:
            return False
        return any(
            session.account_id == theirs for session in self._sessions.values()
        )

    def offer_turn(
        self, combatant_id: int, move=None, target=None, weapon: str = "", steps=None
    ) -> str:
        """Put a turn to whoever plays this character. Returns a reason, or "".

        The DM's half of a symmetric deal. Either side may propose; the one being
        asked answers once. Staging it *was* the DM's consent, so an accepted
        offer is carried out without coming back to them -- coming back would be
        asking the same person the same question twice.

        The sentence the player reads is built here from the fields that were
        just checked, rather than written separately, so it cannot describe a
        turn other than the one it carries.
        """
        combatant = self.repos.encounters.combatant(combatant_id)
        entity = self._entity_of(combatant_id)
        encounter = self._the_running_fight()
        if encounter is None or combatant is None or entity is None:
            return "That is not in the fight being run."

        # A proposal still carries the shorthand -- one move, one attack, in that
        # order -- so a staged turn that does more than that cannot be put to
        # anybody yet. Said plainly rather than quietly truncated: sending them
        # half of what the DM lined up would be describing a turn nobody chose.
        walk = turns.normalise(steps, move=move, target=target, weapon=weapon)
        walks = [s for s in walk if s["kind"] == turns.MOVE]
        swings = [s for s in walk if s["kind"] == turns.ATTACK]
        if len(walks) > 1 or len(swings) > 1 or any(
            s["kind"] == turns.DASH for s in walk
        ):
            return (
                "A turn with more than one move or attack cannot be put to a "
                "player yet. Carry it out yourself, or line up a simpler one."
            )
        move = (walks[0]["x"], walks[0]["y"]) if walks else None
        target = swings[0]["target"] if swings else None
        weapon = swings[0].get("weapon", "") if swings else ""

        target_name = ""
        if target is not None:
            hit = self._entity_of(int(target))
            target_name = hit.name if hit is not None else "someone"

        fields = {
            "move": list(move) if move is not None else None,
            "target": int(target) if target is not None else None,
            "weapon": weapon,
            "text": turns.describe(
                move=move, target_name=target_name, weapon=weapon
            ),
        }
        problem, action, _bendable = self._shape_the_action(
            encounter, combatant, entity, fields
        )
        if problem:
            return problem
        self._offer(action, entity)
        return ""

    def _do_propose(self, combatant_id: int, fields: dict, bending: bool = False) -> None:
        """Build and offer a turn, skipping the rules the DM has waived."""
        encounter = self._the_running_fight()
        combatant = self.repos.encounters.combatant(combatant_id)
        entity = self._entity_of(combatant_id)
        if encounter is None or combatant is None or entity is None:
            return
        problem, action, _bendable = self._shape_the_action(
            encounter, combatant, entity, fields, bending=bending
        )
        if problem:
            self._tell_dms(f"That still will not work: {problem}")
            return
        self._offer(action, entity)

    def _offer(self, action: dict, entity) -> None:
        # One at a time per character. A second proposal replaces the first
        # rather than queueing: the table has moved on, and a player answering
        # a stale one would act on a map that no longer looks like that.
        self._withdraw_for(action["combatant"])
        self._proposed[action["id"]] = action
        # Something is being put to them, so they are not being waited on.
        if self._waiting_on == action["combatant"]:
            self._stop_the_clock()

        log.info("proposed for %s: %s", entity.name, action["text"])
        theirs = self._who_plays(entity)
        self._send_to_account(theirs, MessageType.ACTION, **action)
        # The DM watches it happen. They are running the table even while a
        # machine is talking, and a turn being offered is part of that.
        frame = encode(MessageType.ACTION, **action, watching=True)
        for other_socket, other in self._sessions.items():
            if other.viewer.is_dm and other.account_id != theirs:
                other_socket.sendTextMessage(frame)

    def _shape_the_action(self, encounter, combatant, entity, fields, bending=False):
        """Turn a proposal into something legal, or say what is wrong with it.

        Checked here rather than trusted, because the thing that wrote it is a
        language model and the thing that reads it is a person about to press
        Accept.

        Returns ``(problem, action, bendable)``. **Bendable** marks a refusal
        that is a *rule* rather than an impossibility -- speed, not a square off
        the edge of the map. Only those are worth putting to the DM, because
        only those are theirs to waive.
        """
        move = fields.get("move")
        square = None
        if isinstance(move, (list, tuple)) and len(move) == 2:
            x, y = move
            if not isinstance(x, int) or not isinstance(y, int):
                return "That is not a square.", None, False
            if not encounter.holds(x, y):
                return f"{x},{y} is off the map.", None, False
            standing = self.repos.encounters.at(encounter.id, x, y)
            if standing is not None and standing.id != combatant.id:
                return f"Somebody is already at {x},{y}.", None, False
            if (x, y) in self.repos.encounters.obstacles(encounter.id):
                return f"There is something in the way at {x},{y}.", None, False
            square = [x, y]

        # After the impossibilities, because a rule is only worth putting to
        # somebody once the thing being asked for actually exists.
        broken = (
            "" if bending else self._breaks_a_rule(encounter, combatant, entity, square)
        )
        if broken:
            return broken, None, True

        target_id = fields.get("target")
        target_name = ""
        if target_id is not None:
            if not isinstance(target_id, int):
                return "That is not a target.", None, False
            target = self.repos.encounters.combatant(target_id)
            if target is None or target.encounter_id != encounter.id:
                return "That target is not in the fight.", None, False
            if target.id == combatant.id:
                return "Nobody attacks themselves.", None, False
            hit = (
                self.repos.entities.get(target.entity_id)
                if target.entity_id is not None
                else None
            )
            target_name = hit.name if hit else "someone"

        return "", {
            "id": secrets.token_hex(6),
            "combatant": combatant.id,
            "who": entity.name,
            "move": square,
            "target": target_id if isinstance(target_id, int) else None,
            "target_name": target_name,
            "weapon": str(fields.get("weapon", ""))[:MAX_NAME_LENGTH],
            "text": str(fields.get("text", ""))[:MAX_CHAT_LENGTH],
            # Carried with it, so accepting a waived turn does not run into the
            # same rule a second time on the way out.
            "bending": bool(bending),
        }, False

    def _breaks_a_rule(self, encounter, combatant, entity, square, acting=False) -> str:
        """A rule the DM could waive tonight, or empty if nothing is broken.

        Only *rules* belong here. A square off the edge of the map, or one
        somebody is standing in, is not a rule -- it is a square that does not
        exist or is already taken -- and no amount of authority makes it
        otherwise. Those are refused outright, and never put to anybody.

        ``acting`` asks about the *action*, ``square`` about the *movement*.
        They are separate halves of a turn and must be asked about separately:
        a creature that has swung may still walk, and one that has walked its
        whole speed may still swing.
        """
        if encounter.has_begun and encounter.turn_combatant_id != combatant.id:
            return f"It is not {entity.name}'s turn."
        if acting:
            return self.already_acted(combatant, entity)
        if square is not None:
            return self._too_far(combatant, entity, square[0], square[1])
        return ""

    def _too_far(self, combatant, entity, x: int, y: int) -> str:
        """Whether one turn's movement reaches that square, or why it does not.

        A turn is a move up to your speed, so the app has to know what your
        speed is -- without this a proposal could walk somebody from one corner
        of the room to the other and the host would carry it out. Diagonals
        count as one square, which is the Player's Handbook's own
        simplification and how every table plays it.

        Coming *onto* the map is not moving across it: a creature with no
        square yet is arriving, and there is nowhere to measure from.

        Measured along the way actually walked, not across the room as the
        crow flies. Going the long way round a pillar costs the long way: a
        distance that ignored what the creature has to walk round would let a
        move through a doorway at the far end of a wall look like three squares
        when it is nine.
        """
        if not combatant.on_map:
            return ""

        route = self.route_for(combatant, x, y)
        steps = (
            len(route) - 1
            if route
            else attack.squares_between((combatant.x, combatant.y), (x, y))
        )
        left = self.movement_left(entity, combatant)
        if steps <= left:
            return ""
        spent = self._spent_this_turn(combatant)
        already = f" -- {spent} already used" if spent else ""
        return (
            f"{entity.name} has {left} squares left this turn "
            f"({left * 5} feet{already}), and {x},{y} is {steps} away."
        )

    def _in_the_way(self, encounter, mover, destination=None) -> set[tuple[int, int]]:
        """Squares this creature cannot walk through: bodies and terrain alike.

        Neither can be shared, so neither is a route. The fallen are not in it
        -- ``at`` already excludes them -- because stepping over a body is a
        thing people do, and a corpse that closed a corridor would turn the end
        of every fight into an obstacle course.

        The destination is left out on purpose. Somebody standing on the square
        being walked *to* is refused by ``place``, in its own words; counting it
        here would instead report the square as unreachable, which is a
        different and less useful thing to be told.
        """
        blocked = set(self.repos.encounters.obstacles(encounter.id))
        for other in self.repos.encounters.combatants(encounter.id):
            if other.id == mover.id or not other.on_map or other.down:
                continue
            blocked.add((other.x, other.y))
        blocked.discard(destination)
        return blocked

    def route_for(self, combatant, x: int, y: int) -> list[tuple[int, int]]:
        """The way this creature would actually walk to that square.

        Around whatever is in the way rather than through it, and the shortest
        such way, because its length is what the turn's movement is charged.
        Empty when there is no way at all.

        One route, worked out here and sent, and the same function a client
        uses to draw the walk before it is asked for. A client that found its
        own way round the same rock would preview a walk that never happened.
        """
        encounter = self._the_running_fight()
        if encounter is None or not combatant.on_map:
            return []
        return grid.route_between(
            (combatant.x, combatant.y),
            (x, y),
            self._in_the_way(encounter, combatant, destination=(x, y)),
            within=encounter.bounds,
        )

    def _no_way_through(self, combatant, entity, x: int, y: int) -> str:
        """Whether anything at all can be walked, or why nothing can."""
        if not combatant.on_map:
            return ""
        if self.route_for(combatant, x, y):
            return ""
        return f"There is no way through to {x},{y} for {entity.name}."

    def _spent_this_turn(self, combatant) -> int:
        encounter = self._the_running_fight()
        if encounter is None or encounter.turn_combatant_id != combatant.id:
            return 0
        return int(encounter.moved_squares or 0)

    def movement_left(self, entity, combatant=None) -> int:
        """Squares this creature can still cover before the turn passes."""
        sheet = (entity.data or {}).get("sheet") or {}
        speed = derive.speed_in_squares(sheet, self.content)
        encounter = self._the_running_fight()
        if encounter is None or not encounter.has_begun:
            return speed
        if combatant is None:
            combatant = self.repos.encounters.combatant(
                encounter.turn_combatant_id or -1
            )
        if combatant is None or combatant.entity_id != entity.id:
            return speed
        # Dash: the action was spent on moving again, so the allowance is your
        # speed twice. Read from what the action *became* rather than from the
        # flag saying it is gone -- a swing spends the same flag and buys no
        # squares at all.
        allowance = speed * 2 if encounter.dashed else speed
        return max(0, allowance - int(encounter.moved_squares or 0))

    def already_acted(self, combatant, entity) -> str:
        """Whether this creature's action is already spent, and so says.

        A turn is a move up to your speed and *one* action. The app has counted
        the action since there was a turn budget at all, and until now nothing
        read the count -- so a second swing went through, which made the whole
        budget decorative. Told as a sentence rather than a shrug, the same way
        being out of reach is, because it is a rule and rules are worth stating.
        """
        encounter = self._the_running_fight()
        if encounter is None or combatant is None or entity is None:
            return ""
        if encounter.turn_combatant_id != combatant.id:
            # Not their turn: an opportunity attack, or a DM resolving
            # something out of order. Neither spends this turn's action, so
            # neither is bound by it.
            return ""
        if not encounter.action_used:
            return ""
        # Extra Attack: one action, more than one swing. The flag says the action
        # is gone, which used to end the matter -- and is why a level-five fighter
        # still got one swing. What decides is how many attacks that action has
        # produced against how many it is worth.
        sheet = (entity.data or {}).get("sheet") or {}
        allowed = derive.attacks_per_action(sheet, self.content)
        if encounter.attacks_made < allowed:
            return ""
        if allowed > 1:
            return (
                f"{entity.name} has taken all {allowed} attacks this turn."
            )
        return f"{entity.name} has already acted this turn."

    def _send_refusal(self, socket: QWebSocket, message: str) -> None:
        self._send(socket, MessageType.ERROR, code="refused", message=message)

    def _withdraw_for(self, combatant_id: int) -> None:
        """Take back any turn still waiting for this character."""
        for action_id, action in list(self._proposed.items()):
            if action["combatant"] == combatant_id:
                self._proposed.pop(action_id, None)
                self._broadcast(MessageType.ACTION_GONE, id=action_id)

    def _handle_acted(self, socket: QWebSocket, session: _Session, message) -> None:
        """The player's answer to a turn that was put to them."""
        action = self._proposed.get(str(message.get("id", "")))
        if action is None:
            return  # answered already, or overtaken. Not worth a complaint.

        entity = self._entity_of(action["combatant"])
        # The player whose character it is, or the human DM answering for
        # somebody who has stepped out. Never the agent: it wrote the proposal,
        # and a proposal something can accept on your behalf is not a proposal.
        allowed = entity is not None and (
            session.account_id == self._who_plays(entity)
            or (session.viewer.is_dm and not session.is_agent)
        )
        if not allowed:
            self._send_refusal(
                socket,
                "That turn is not yours to answer."
                if not session.is_agent
                else "You proposed it. It is theirs to accept.",
            )
            return

        self._proposed.pop(action["id"], None)
        self._broadcast(MessageType.ACTION_GONE, id=action["id"])

        if not bool(message.get("accept")):
            note = str(message.get("note", "")).strip()[:MAX_CHAT_LENGTH]
            # A refusal with instructions is the player still taking their
            # turn, so it goes to the table as an ordinary line and the agent
            # hears it and offers something else.
            self._record(
                SAID,
                note or f"{action['who']} does something else.",
                speaker=session.member.label,
                role=session.member.role,
            )
            self._broadcast(
                MessageType.SAID,
                member=session.member.to_dict(),
                text=note or f"Not that -- {action['who']} does something else.",
            )
            return

        self._carry_out(action, session)

    def _handle_give(self, socket: QWebSocket, session: _Session, message) -> None:
        """Put something in somebody's inventory.

        Nothing did this before. A DM would say "there is a longsword in the
        chest", and the item existed in the fiction and nowhere else -- a player
        who wanted it recorded had to type it in themselves, which is the sort
        of clerical job the app is meant to be doing.
        """
        if not self._may_run_the_table(session):
            self._refuse_the_fight(socket)
            return

        entity_id = message.get("entity")
        item = str(message.get("item", "")).strip()[:MAX_CHAT_LENGTH]
        entity = self.repos.entities.get(entity_id) if isinstance(entity_id, int) else None
        if entity is None or not item:
            self._send_refusal(socket, "Say what, and to whom.")
            return

        self.give(entity, item)

    def give(self, entity, item: str) -> None:
        """Add a line to an entity's inventory, and say so.

        Appended rather than parsed: inventory is free text somebody writes
        their own way -- "3 torches", "the innkeeper's key (bent)" -- and a
        structured list would be this app deciding how their kit is written
        down. A line is the smallest thing that is still theirs.
        """
        data = dict(entity.data or {})
        held = str(data.get("inventory") or "").rstrip()
        data["inventory"] = f"{held}\n{item}" if held else item
        entity.data = data
        self.repos.entities.update(entity)

        log.info("gave %s to %s", item, entity.name)
        self._broadcast_system(f"{entity.name} picks up: {item}")
        self.publish_entity(entity.id)
        self.entity_applied.emit(entity.id)

    def _handle_simulate(self, socket: QWebSocket, session: _Session, message) -> None:
        """Hand a player's character to autopilot for this fight, or take it back.

        The DM's to decide, and the player's for their own character: somebody
        stepping out should be able to say "play mine" without finding the DM,
        and a DM should be able to fill an empty chair without waiting for
        somebody who is not there.

        Not the agent's. It does not get to decide which characters it plays.
        """
        combatant_id = message.get("combatant")
        entity = self._entity_of(combatant_id) if isinstance(combatant_id, int) else None
        if entity is None:
            return

        theirs = self._who_plays(entity) == session.account_id
        if session.is_agent or not (theirs or session.viewer.is_dm):
            self._send_refusal(socket, "That is not yours to hand over.")
            return

        on = bool(message.get("on"))
        self.repos.encounters.set_simulated(combatant_id, on)
        # The seat exists for exactly as long as the handover does.
        if on:
            self.mint_seat(entity.id)
        else:
            self.revoke_seat(entity.id)
        self._broadcast_system(
            f"{self.stand_in_name(entity.id)} is playing {entity.name}." if on
            else f"{entity.name} is back with their player."
        )
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_swing(self, socket: QWebSocket, session: _Session, message) -> None:
        """Roll an attack. The half of a monster's turn that was missing.

        The agent could move a goblin and could talk about it hitting somebody,
        and had no way to actually swing -- so it narrated outcomes instead of
        asking for them, which is the one thing it is told never to do. This is
        the door.
        """
        encounter = self._the_running_fight()
        combatant = self.repos.encounters.combatant(message.get("combatant"))
        target = self.repos.encounters.combatant(message.get("target"))
        entity = self._entity_of(message.get("combatant"))
        if not self._may_act_for(session, combatant):
            self._refuse_the_fight(socket)
            return
        # A fight is laid out before it is started -- tokens placed, initiative
        # rolled -- and starting it is the moment the rules come on. Placing a
        # creature during that is arranging; swinging at one is not. A human DM
        # may still do it, because a DM doing something surprising on purpose
        # is a ruling rather than a bug.
        if (session.is_agent or session.seat_for is not None) and (
            encounter is None or not encounter.has_begun
        ):
            self._send_refusal(
                socket,
                "The fight has not started yet. Nothing swings until it does.",
            )
            return

        if (
            encounter is None
            or combatant is None
            or target is None
            or entity is None
            or combatant.encounter_id != encounter.id
            or target.encounter_id != encounter.id
        ):
            self._send_refusal(socket, "That is not in the fight being run.")
            return
        if target.id == combatant.id:
            self._send_refusal(socket, "Nobody attacks themselves.")
            return

        wanted = str(message.get("weapon", ""))[:MAX_NAME_LENGTH]
        # One action a turn, asked here as well as in `take_turn`. This door
        # *spent* the action and never checked it, so anything coming over the
        # wire could swing as many times as it liked -- which for a long while
        # was only the agent, and is now a player's map as well.
        reason = self._breaks_a_rule(encounter, combatant, entity, None, acting=True)
        if not reason:
            sheet = (entity.data or {}).get("sheet") or {}
            try:
                weapon = attack.find_weapon(sheet, self.content, wanted)
            except attack.NoAttack as exc:
                # Not a rule anybody can waive: it is not on the sheet.
                self._send_refusal(socket, str(exc))
                return
            reason = self.out_of_reach(combatant, target, weapon)

        if reason and session.is_agent:
            self._ask_the_dm_to_bend(
                {"kind": "swing", "combatant": combatant.id, "target": target.id,
                 "weapon": wanted},
                what=f"have {entity.name} attack",
                why=reason[0].upper() + reason[1:] + ".",
            )
            self._tell_the_agent(
                f"{reason}. It is with your DM, who may allow it. Do not narrate "
                "the outcome."
            )
            return
        if reason:
            self._send_refusal(socket, reason)
            return

        self._do_swing(combatant.id, target.id, wanted)
        if session.is_agent:
            self._ask_if_done(combatant.id)

    def _do_swing(self, combatant_id: int, target_id: int, weapon: str) -> None:
        self._swing(
            {"combatant": combatant_id, "target": target_id, "weapon": weapon},
            checked=True,
        )
        encounter = self._the_running_fight()
        if encounter is not None and encounter.turn_combatant_id == combatant_id:
            self.repos.encounters.use_action(encounter.id)
        self.publish_encounter()
        self.encounter_applied.emit()

    # ------------------------------------------------------ bending the rules
    #
    # A DM can always overrule the rules -- that is most of what being a DM is.
    # So when they tell autopilot to do something the rules refuse ("have the
    # goblin charge the wizard", from nine squares away), the answer is not a
    # flat no and it is certainly not the agent quietly doing it. It is put
    # back to the DM: this is what you asked for, here is the rule it breaks,
    # shall I?
    #
    # Only *rules* bend. A square off the edge of the map or one somebody is
    # standing in is not a rule, it is a square that does not exist or is
    # taken, and no amount of authority makes it otherwise.

    def _ask_the_dm_to_bend(self, request: dict, what: str, why: str) -> str:
        """Park an agent's request and put it to the DM. Returns its id."""
        bend_id = secrets.token_hex(6)
        self._bends[bend_id] = request
        log.info("asking the DM to allow: %s (%s)", what, why)
        frame = encode(MessageType.BEND, id=bend_id, what=what, why=why)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm and not session.is_agent:
                socket.sendTextMessage(frame)
        return bend_id

    def _handle_allow(self, socket: QWebSocket, session: _Session, message) -> None:
        """The DM's answer. Theirs alone -- the agent cannot bless its own ask."""
        if not session.viewer.is_dm or session.is_agent:
            self._send_refusal(socket, "Only the DM decides that.")
            return

        request = self._bends.pop(str(message.get("id", "")), None)
        if request is None:
            return
        self._broadcast(MessageType.BEND_GONE, id=str(message.get("id", "")))

        if not bool(message.get("allow")):
            self._tell_the_agent(
                f"Your DM said no to that: {request.get('what', 'it')}. "
                "Do something the rules allow instead."
            )
            return

        log.info("the DM allowed %s", request.get("what"))
        self._broadcast_system(f"The DM allows it: {request.get('what')}.")
        if request.get("kind") == "move":
            self._do_move(request["combatant"], request["x"], request["y"])
        elif request.get("kind") == "propose":
            self._do_propose(request["combatant"], request["fields"], bending=True)
        elif request.get("kind") == "swing":
            self._do_swing(
                request["combatant"], request["target"], request.get("weapon", "")
            )

    def _tell_the_agent(self, text: str) -> None:
        """Say something to the agent, and to nobody else.

        It arrives as an ordinary system line, which is what the agent's own
        transcript is built from -- so the next thing it writes knows about it.
        """
        frame = encode(MessageType.SYSTEM, text=text, kind=SystemKind.NOTICE.value)
        for socket, session in self._sessions.items():
            if session.is_agent:
                socket.sendTextMessage(frame)

    # ------------------------------------------------- anything else, or done?
    #
    # A turn is a move and an action, and this app only models one action -- so
    # once it is taken there is usually nothing left to decide. Usually is not
    # always, though, so the player is asked rather than cut off, and the clock
    # is what stops "asked" from meaning "everybody waits indefinitely".
    #
    # It runs on the host. A client-side timer would be a promise the app makes
    # to four other people and keeps only while one person's laptop is awake.

    def _resting(self) -> frozenset[int]:
        """Who the turn should pass by: the dead, and the unconscious.

        Worked out here rather than in the repository because it needs hit
        points, which live on the entity. See
        :func:`canon_keeper.rules.death.resting`.
        """
        encounter = self._the_running_fight()
        if encounter is None:
            return frozenset()
        return death.resting(
            self.repos.encounters.combatants(encounter.id),
            lambda combatant: self._entity_of(combatant.id),
        )

    def _advance_turn(self) -> None:
        """Pass the turn, and keep passing it until it lands on somebody awake.

        A dying character is handed the turn on purpose -- the death save
        happens at the start of it -- but the save is the whole turn, so the
        answer arrives and the turn moves straight on. Two of them in a row is
        two rolls and one announcement each, which is what a table would do.
        """
        encounter = self._the_running_fight()
        if encounter is None:
            return
        # Bounded by the size of the order: every step either lands on somebody
        # who acts or resolves one death save, and there are only so many of
        # either before the round has to end.
        for _ in range(len(self.repos.encounters.combatants(encounter.id)) + 1):
            self.repos.encounters.advance(encounter.id, passing_over=self._resting())
            encounter = self._the_running_fight()
            if encounter is None or encounter.turn_combatant_id is None:
                return
            combatant = self.repos.encounters.combatant(encounter.turn_combatant_id)
            if combatant is None or not self._is_dying(combatant):
                return
            if self._ask_for_a_death_save(combatant):
                return  # theirs to roll; the turn waits on them
            if self._roll_death_save(combatant):
                return  # a natural twenty: awake, and the turn is theirs

    def _is_dying(self, combatant) -> bool:
        entity = self._entity_of(combatant.id) if combatant is not None else None
        if entity is None:
            return False
        hp = (entity.data or {}).get("hp")
        if not isinstance(hp, int):
            return False
        return death.condition(
            hp, entity.kind, combatant.death_successes, combatant.death_failures
        ) == death.DYING

    def _owe_a_save_if_dying(self) -> None:
        """Ask for a save if the turn has just landed on somebody at zero.

        Called wherever the turn arrives somewhere other than by walking the
        order -- starting a fight on a character who was already down, which
        `_advance_turn` never sees. The save happens at the start of *each* of
        their turns, and the first one is a turn like any other.
        """
        encounter = self._the_running_fight()
        if encounter is None or encounter.turn_combatant_id is None:
            return
        combatant = self.repos.encounters.combatant(encounter.turn_combatant_id)
        if combatant is None or not self._is_dying(combatant):
            return
        if not self._ask_for_a_death_save(combatant):
            self._roll_death_save(combatant)

    def _ask_for_a_death_save(self, combatant) -> bool:
        """Put the save to whoever plays them. True if it is now theirs to roll.

        The rule asks, not autopilot. A death save is the one roll nobody
        chooses to make and the loudest moment the game has, and the host
        taking it quietly on their behalf -- which is what happened before --
        left the player watching a number change in a list.

        So it goes in the chat, from the host, where it becomes the roll they
        press. Everybody sees it asked: at a table the whole room knows a death
        save is owed, and that is most of what makes it one.

        **Forced, not optional.** They may take their time and they may ignore
        it, and when the clock runs out the host rolls it anyway -- see
        :meth:`_nobody_answered`. There is no answer that makes it not happen,
        because there is no such answer at a table either.

        False when there is nobody to ask -- an unowned character, or one a
        machine is playing. Then the host rolls it straight away, as it always
        did: the rule happens either way, and only who presses the button is in
        question.
        """
        entity = self._entity_of(combatant.id)
        if entity is None or entity.owner_account_id is None:
            return False
        if self._machine_plays(combatant):
            return False
        if not any(
            session.account_id == entity.owner_account_id
            for session in self._sessions.values()
        ):
            return False  # nobody is there to be asked

        self._owed_by = combatant.id
        self._broadcast_system(
            f"{entity.name} is dying, and owes a death saving throw "
            f"-- {death.DEATH_SAVE_DC} or better on a d20, no modifier."
        )
        # Autopilot waits for this rather than working around it. There is
        # nothing here for it to formalise: a death save is not a turn somebody
        # chose to take, and the one thing it could do -- narrate the outcome
        # -- is the one thing it must never do.
        if self._autopilot:
            self._tell_the_agent(
                f"{entity.name} is dying and owes a death saving throw. It is "
                "theirs to roll and the host resolves it. Take no turn for "
                "them, propose nothing, and do not say how it comes out."
            )
        self._waiting_on = combatant.id
        self._turn_clock.start(STILL_YOUR_TURN_MS)
        self._send_to_account(
            entity.owner_account_id,
            MessageType.YOUR_TURN,
            combatant=combatant.id,
            who=entity.name,
            seconds=STILL_YOUR_TURN_MS // 1000,
            waiting=True,
        )
        return True

    def _handle_death_save(self, socket: QWebSocket, session: _Session, message) -> None:
        """The save being rolled by the person who owes it.

        Carries nothing and is trusted for nothing. The host has already
        decided whose save is owed; this only says "now". A login that asks
        for somebody else's, or for one that is not owed, is refused -- the
        alternative is a client that can roll death saves into existence.
        """
        owed = self._owed_by
        combatant = self.repos.encounters.combatant(owed) if owed else None
        entity = self._entity_of(owed) if owed else None
        encounter = self._the_running_fight()
        if (
            combatant is None
            or entity is None
            or encounter is None
            or encounter.turn_combatant_id != combatant.id
            or entity.owner_account_id != session.account_id
            or not self._is_dying(combatant)
        ):
            self._send_refusal(socket, "There is no death save owed by you.")
            return

        self._owed_by = None
        self._stop_the_clock()
        if not self._roll_death_save(combatant):
            # Still down. The save was the whole of the turn, so it moves on.
            self._advance_turn()
            self._announce_turn("next")
        self.publish_encounter()
        self.encounter_applied.emit()

    def _roll_death_save(self, combatant) -> bool:
        """One death save, on the host's dice. True if they are back up.

        Everybody hears it. A death save resolved quietly, with only the count
        in the initiative order to show for it, would be the loudest moment in
        the game happening off-screen.
        """
        entity = self._entity_of(combatant.id)
        if entity is None:
            return False

        result = death.save(roll)
        who = entity.name

        if result.revived:
            self.repos.encounters.clear_death_saves(combatant.id)
            self._heal_to(entity, 1)
            self._broadcast_system(f"{who} rolls a death save: {result.described}.")
            return True

        self.repos.encounters.record_death_save(
            combatant.id,
            successes=result.successes_added,
            failures=result.failures_added,
        )
        after = self.repos.encounters.combatant(combatant.id)
        successes = after.death_successes if after else 0
        failures = after.death_failures if after else 0

        self._broadcast_system(
            f"{who} rolls a death save: {result.described} "
            f"({successes} of {death.SAVES_NEEDED} made, "
            f"{failures} of {death.SAVES_NEEDED} failed)."
        )
        if failures >= death.SAVES_NEEDED:
            self._broadcast_system(f"{who} is dead.")
            data = dict(entity.data or {})
            data["status"] = "dead"
            entity.data = data
            self.repos.entities.update(entity)
            self.publish_entity(entity.id)
            self.entity_applied.emit(entity.id)
        elif successes >= death.SAVES_NEEDED:
            self._broadcast_system(f"{who} is stable, and out of the fight.")
        self.publish_encounter()
        return False

    def _heal_to(self, entity, hit_points: int) -> None:
        """Put somebody back above zero, in both the places hit points live."""
        data = dict(entity.data or {})
        data["hp"] = hit_points
        data["status"] = "alive"
        sheet = data.get("sheet")
        if isinstance(sheet, dict):
            sheet["hp_current"] = hit_points
        entity.data = data
        self.repos.entities.update(entity)
        encounter = self._the_running_fight()
        if encounter is not None:
            for combatant in self.repos.encounters.combatants(encounter.id):
                if combatant.entity_id == entity.id:
                    self.repos.encounters.set_down(combatant.id, False)
        self.publish_entity(entity.id)
        self.entity_applied.emit(entity.id)

    def _machine_plays(self, combatant) -> bool:
        """Whether nobody is going to press anything for this one.

        Every monster, and any character somebody has handed over. It is the
        difference between a turn that is waiting on a person and one that is
        waiting on nothing at all.
        """
        if combatant is None:
            return False
        entity = self._entity_of(combatant.id)
        if entity is None:
            return False
        # A stand-in sitting in the seat *is* somebody to ask. It says what the
        # character does, autopilot works out the rules, and it answers the
        # proposal -- the same three steps a person's turn takes, at the same
        # pace. Treating that as "nobody will press anything" would hurry the
        # turn along on the short clock and cut the exchange in half.
        if self._seat_is_taken(entity.id):
            return False
        if combatant.simulated:
            return True
        return self._who_plays(entity) is None

    def stand_in_name(self, entity_id: int) -> str:
        """What this character's stand-in is called.

        The same every time for the same character, so it is a name people can
        use rather than a label that changes when a process restarts.
        """
        return robots.name_for_character(self.campaign_key, entity_id)

    def _with_stand_ins(self, combatants: list) -> list:
        """Mark the ones a stand-in is actually connected for.

        Handed over is a wish; this is whether it came true. Autopilot needs
        the difference: it proposes a turn to a seat that can answer, and takes
        it outright when nobody can. Without this a character whose stand-in
        never started would be offered a turn nobody could accept, and the
        fight would stop there.
        """
        for combatant in combatants:
            combatant.stand_in = (
                combatant.entity_id is not None
                and self._seat_is_taken(combatant.entity_id)
            )
            combatant.stand_in_name = (
                self.stand_in_name(combatant.entity_id)
                if combatant.entity_id is not None and combatant.simulated
                else ""
            )
        return combatants

    def _seat_is_taken(self, entity_id: int) -> bool:
        """Whether something is currently connected on this character's seat."""
        return any(
            session.seat_for == entity_id for session in self._sessions.values()
        )

    def _an_agent_is_thinking(self) -> bool:
        return any(
            session.is_agent and session.busy for session in self._sessions.values()
        )

    def _ask_if_done(self, combatant_id: int) -> None:
        entity = self._entity_of(combatant_id)
        encounter = self._the_running_fight()
        combatant = self.repos.encounters.combatant(combatant_id)
        if entity is None or combatant is None:
            return
        # Only while it is still theirs. An action resolved after the DM moved
        # the turn on is not an invitation to take another one.
        if encounter is None or encounter.turn_combatant_id != combatant_id:
            return

        self._waiting_on = combatant_id
        if self._machine_plays(combatant):
            # There is nobody to ask, so nothing is asked -- the turn simply
            # passes once whatever is playing them goes quiet. Without this a
            # character handed to autopilot took its turn and then held the
            # whole table, because the only thing that ever ended a turn was a
            # person pressing Done.
            self._turn_clock.start(MACHINE_TURN_MS)
            return

        self._turn_clock.start(STILL_YOUR_TURN_MS)
        self._send_to_account(
            entity.owner_account_id,
            MessageType.YOUR_TURN,
            combatant=combatant_id,
            who=entity.name,
            seconds=STILL_YOUR_TURN_MS // 1000,
            waiting=True,
        )

    def _stop_the_clock(self) -> None:
        """Somebody answered, or the turn moved on. Stop asking."""
        if self._waiting_on is None:
            return
        combatant_id, self._waiting_on = self._waiting_on, None
        self._turn_clock.stop()
        entity = self._entity_of(combatant_id)
        if entity is not None and entity.owner_account_id is not None:
            self._send_to_account(
                entity.owner_account_id,
                MessageType.YOUR_TURN,
                combatant=combatant_id,
                who=entity.name,
                seconds=0,
                waiting=False,
            )

    def _nobody_answered(self) -> None:
        """Nothing happened for long enough. Move on, and say so."""
        combatant_id, self._waiting_on = self._waiting_on, None
        encounter = self._the_running_fight()
        if combatant_id is None or encounter is None:
            return
        if encounter.turn_combatant_id != combatant_id:
            return  # the turn moved on by itself; nothing to do

        combatant = self.repos.encounters.combatant(combatant_id)
        if self._owed_by == combatant_id and combatant is not None:
            # This is what "forced" means. They were asked, they did not
            # answer, and a death save nobody rolls is a character who neither
            # dies nor recovers -- which is worse than either. The host rolls
            # it, exactly as it did before anybody was asked at all.
            self._owed_by = None
            entity = self._entity_of(combatant_id)
            self._broadcast_system(
                f"{entity.name if entity else 'They'} does not answer; "
                "the save is rolled for them."
            )
            if not self._roll_death_save(combatant):
                self._advance_turn()
                self._announce_turn("next")
            self.publish_encounter()
            self.encounter_applied.emit()
            return

        if self._machine_plays(combatant) and self._an_agent_is_thinking():
            # A model call takes seconds, and a turn taken away mid-thought
            # comes back as an action on somebody else's. Thinking counts as
            # something happening.
            self._waiting_on = combatant_id
            self._turn_clock.start(MACHINE_TURN_MS)
            return

        entity = self._entity_of(combatant_id)
        who = entity.name if entity is not None else "somebody"
        log.info("nobody answered for %s; passing the turn on", who)
        self._stop_the_clock()
        self._advance_turn()
        self._broadcast_system(f"{who} takes no further action.")
        self._announce_turn("next")
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_done(self, socket: QWebSocket, session: _Session, message) -> None:
        """"That is my turn." Only for the character whose turn it is."""
        encounter = self._the_running_fight()
        if encounter is None or encounter.turn_combatant_id is None:
            return
        entity = self._entity_of(encounter.turn_combatant_id)
        # Their own seat, whether a person is sitting in it or a machine is.
        if entity is None or self._who_plays(entity) != session.account_id:
            self._send_refusal(socket, "It is not your turn to end.")
            return

        self._stop_the_clock()
        self._advance_turn()
        self._announce_turn("next")
        self.publish_encounter()
        self.encounter_applied.emit()

    def take_turn(self, combatant_id: int, move=None, target=None, weapon: str = "") -> str:
        """The DM taking a turn by hand, with no agent anywhere near it.

        The shorthand: a whole move and then a whole attack. Kept because every
        caller that existed before turns became a sequence uses it, and because
        most turns really are one of each. :meth:`take_turn_steps` is the same
        thing with the order spelled out.
        """
        return self.take_turn_steps(
            combatant_id,
            turns.normalise(move=move, target=target, weapon=weapon),
        )[0]

    def take_turn_steps(self, combatant_id: int, steps) -> tuple[str, int]:
        """A turn as an ordered sequence. Returns (reason it stopped, steps done).

        Called by the DM's own panel rather than sent over the wire, because the
        DM's app *is* the host. It still goes through here rather than writing to
        the database, for the half that is not a database write: the dice, the
        armour class, and the hit points that come off the other creature.

        **The count is not decoration.** A turn can stop halfway -- three squares
        and then a swing that is out of reach -- and the three squares have
        happened. Whoever is holding the turn needs to know how much of it to
        stop holding, or committing it again would walk those squares twice.

        Two things are re-read between steps, and both have bitten before. The
        creature has *moved*, so the next step must be measured from where it is
        now rather than from where the turn started. And it may have been cut
        down on the way -- an opportunity attack lands mid-walk -- in which case
        the rest of the turn is not refused, it simply has nobody left to take
        it.
        """
        encounter = self._the_running_fight()
        combatant = self.repos.encounters.combatant(combatant_id)
        entity = self._entity_of(combatant_id)
        if encounter is None or combatant is None or entity is None:
            return "That is not in the fight being run.", 0

        done = 0
        for step in turns.normalise(steps):
            if step["kind"] == turns.MOVE:
                problem = self._walk_one_step(encounter, combatant, entity, step)
            elif step["kind"] == turns.DASH:
                problem = self._dash_one_step(encounter, combatant, entity)
            else:
                problem = self._swing_one_step(combatant, entity, step)
            if problem:
                self._settle_the_turn()
                return problem, done
            done += 1

            # Both, and for the same reason. The creature has moved; the turn has
            # spent something. Re-reading one and not the other is how a swing
            # bought a Dash: the flag said used in the database and still said
            # free in the copy this loop was holding.
            combatant = self.repos.encounters.combatant(combatant_id)
            encounter = self._the_running_fight() or encounter
            if combatant is None or combatant.down:
                # Not a refusal. The turn ended because whoever was taking it
                # went down in the middle of it, which is already announced and
                # has already passed the turn on.
                break

        self._settle_the_turn()
        return "", done

    def _walk_one_step(self, encounter, combatant, entity, step) -> str:
        x, y = int(step["x"]), int(step["y"])
        if not encounter.holds(x, y):
            return f"{x},{y} is off the map."
        # The same allowance a player is held to. Dragging a token remains
        # free -- that gesture means "put it there", not "walk there" --
        # but a turn taken *as a turn* obeys the rules whoever takes it, or
        # the budget on screen is decorative.
        # Asked first: a creature walled in has nowhere to walk at all,
        # and being told the distance instead would be answering a
        # question it did not ask.
        nowhere = self._no_way_through(combatant, entity, x, y)
        if nowhere:
            return nowhere
        short = self._too_far(combatant, entity, x, y)
        if short:
            return short
        if not self._do_move(combatant.id, x, y, spending=True):
            return self._walk_went_wrong(combatant.id, x, y, entity.name)
        return ""

    def _dash_one_step(self, encounter, combatant, entity) -> str:
        """Spend the action on your speed again.

        Not routed through :meth:`already_acted`: that one answers "may this
        creature *attack* again", and with Extra Attack the answer can be yes
        while the action itself is long gone. Dashing needs the action, so it asks
        about the action.
        """
        if encounter.turn_combatant_id != combatant.id:
            return f"It is not {entity.name}'s turn."
        if encounter.action_used:
            return f"{entity.name} has already used their action this turn."
        self.repos.encounters.dash(encounter.id)
        self._broadcast_system(f"{entity.name} dashes.")
        return ""

    def _swing_one_step(self, combatant, entity, step) -> str:
        spent = self.already_acted(combatant, entity)
        if spent:
            return spent
        # What comes back matters here: a swing that could not land stops the
        # turn, the same way a walk that could not happen does.
        return self._swing(
            {
                "combatant": combatant.id,
                "target": int(step["target"]),
                "weapon": step.get("weapon", ""),
            }
        )

    def _settle_the_turn(self) -> None:
        """Publish once, whatever happened. Half a turn is still a change.

        In one place because a turn that stopped partway is exactly the case
        where forgetting to publish leaves every screen showing a creature that
        has already moved standing where it was.
        """
        self.publish_encounter()
        self.encounter_applied.emit()

    def _entity_of(self, combatant_id: int):
        combatant = self.repos.encounters.combatant(combatant_id)
        if combatant is None or combatant.entity_id is None:
            return None
        return self.repos.entities.get(combatant.entity_id)

    def _is_theirs(self, session: _Session, combatant_id: int) -> bool:
        entity = self._entity_of(combatant_id)
        return entity is not None and entity.owner_account_id == session.account_id

    def _carry_out(self, action: dict, session: _Session) -> None:
        """Do it. The move first, then the swing, then say what happened."""
        moved = False
        if action.get("move"):
            x, y = action["move"]
            # Checked again on the way out, not only when it was proposed: the
            # map moves while a player is deciding, and the square they are
            # measuring from may not be the one they were standing on.
            standing = self.repos.encounters.combatant(action["combatant"])
            entity = self._entity_of(action["combatant"])
            reason = ""
            if standing is not None and entity is not None:
                # The speed limit is a rule and "bending" waives it. Having
                # nowhere to walk is not a rule -- it is a fact about the map,
                # the same tier as a square already taken -- so bending does
                # not touch it.
                reason = self._no_way_through(standing, entity, x, y)
                if not reason and not action.get("bending"):
                    reason = self._too_far(standing, entity, x, y)
            moved = not reason and self._do_move(
                action["combatant"], x, y, spending=True
            )
            if not moved:
                trouble = reason or self._walk_went_wrong(
                    action["combatant"], x, y, action["who"]
                )
                if trouble:
                    self._tell(session.account_id, trouble)

        if moved:
            self._broadcast_system(
                f"{action['who']} moves to {action['move'][0]},{action['move'][1]}."
            )

        # Cut down on the way over, there is no second half to the turn. The
        # one-action rule does not catch this on its own: falling passes the
        # turn, and an action is only spent by whoever's turn it is.
        if action.get("target") is not None and self._still_up(action["combatant"]):
            # A bend is the DM saying the rule does not apply this once, so it
            # skips the check the same way the reach check is skipped.
            spent = (
                ""
                if action.get("bending")
                else self.already_acted(
                    self.repos.encounters.combatant(action["combatant"]),
                    self._entity_of(action["combatant"]),
                )
            )
            if spent:
                self._tell(session.account_id, spent)
            else:
                self._swing(action)

        self.publish_encounter()
        self.encounter_applied.emit()
        # They have acted. Anything else, or shall we move on?
        self._ask_if_done(action["combatant"])

    def out_of_reach(self, combatant, target_combatant, weapon) -> str:
        """Whether the swing lands short, and by how much.

        A rule like any other, so it is worth a sentence rather than a shrug --
        and worth putting to the DM when an agent runs into it, since "just let
        him reach" is a thing a DM says.
        """
        if combatant is None or target_combatant is None:
            return ""
        if not combatant.on_map or not target_combatant.on_map:
            return ""
        gap = attack.squares_between(
            (combatant.x, combatant.y), (target_combatant.x, target_combatant.y)
        )
        if attack.within_reach(weapon, gap):
            return ""
        return f"that is {gap * 5} feet away -- too far for a {weapon.name}"

    def _swing(self, action: dict, checked: bool = False, at_step: int = 0) -> str:
        """One weapon attack, rolled on the host and applied to the target.

        Returns why it could not be swung, or empty when it was. **The reason is
        returned as well as announced**, because a swing that cannot land is the
        same kind of answer as a walk that cannot happen, and for a while it was
        not treated as one: a refused move stopped a turn while an out-of-reach
        swing only said so and let the turn carry on. In a turn made of steps
        that difference is the difference between stopping and quietly going on
        to the next thing.

        Callers that only want it announced may still ignore what comes back --
        an opportunity attack has no turn to stop.

        ``at_step`` is how far along the target's walk the swing lands, for an
        opportunity attack. Sent so it can be *drawn* at that point rather than
        at the moment the walk begins: a creature swings as somebody leaves its
        reach, not as they set off from the other side of the room, and a lunge
        that fired up front looked like it knew where they were going.
        """
        attacker = self._entity_of(action["combatant"])
        target_combatant = self.repos.encounters.combatant(action["target"])
        target = (
            self.repos.entities.get(target_combatant.entity_id)
            if target_combatant is not None and target_combatant.entity_id is not None
            else None
        )
        if attacker is None or target is None or target_combatant is None:
            return "There is nobody there to swing at."

        sheet = (attacker.data or {}).get("sheet") or {}
        try:
            weapon = attack.find_weapon(sheet, self.content, action.get("weapon", ""))
        except attack.NoAttack as exc:
            self._broadcast_system(f"{attacker.name} cannot attack: {exc}")
            return str(exc)

        mine = self.repos.encounters.combatant(action["combatant"])
        if not checked:
            short = self.out_of_reach(mine, target_combatant, weapon)
            if short:
                self._broadcast_system(f"{attacker.name} swings at {target.name}: {short}.")
                return short

        result = attack.resolve(
            sheet,
            self.content,
            weapon,
            attack.armour_class(target.data or {}, self.content),
            roll,
        )
        encounter = self._the_running_fight()
        if encounter is not None and encounter.turn_combatant_id == action["combatant"]:
            self.repos.encounters.use_action(encounter.id)
            # And which swing out of the action this was, so a second one can be
            # told from a second action.
            self.repos.encounters.count_attack(encounter.id)
        said = result.describe(attacker.name, target.name)
        # Shown before it is said, so the number floating off the target and
        # the line in the chat are the same event rather than two.
        self._show(
            Played.ATTACK.value,
            mine if mine is not None else self.repos.encounters.combatant(action["combatant"]),
            target=target_combatant,
            hit=result.hit,
            damage=result.damage,
            **({"after": int(at_step)} if at_step else {}),
        )
        self._record(ROLLED, said, speaker=attacker.name, role=Role.DM.value)
        self._broadcast(
            MessageType.ROLLED,
            member=Member(id="", name=attacker.name, role=Role.DM.value).to_dict(),
            notation=f"1d20{result.bonus:+d}",
            rolls=[result.roll],
            kept=[result.roll],
            modifier=result.bonus,
            total=result.total,
            description=said,
        )
        if result.hit and result.damage:
            self._take_damage(target, result.damage)
        # Swung. A miss is not a refusal -- the d20 decided, which is the whole
        # point of rolling it.
        return ""

    def _take_damage(self, target, damage: int) -> None:
        """Apply it, and tell everyone who can see the creature.

        Written straight to the entity: this is the host applying its own
        decision, not a client's request. Hit points live in two places on a
        creature -- the shared field and the sheet -- so both move together or
        the DM and the players read different numbers.
        """
        data = dict(target.data or {})
        maximum = data.get("max_hp")
        before = data.get("hp")
        if not isinstance(before, int):
            before = maximum if isinstance(maximum, int) else None
        if not isinstance(before, int):
            return

        after = max(0, before - int(damage))
        data["hp"] = after
        sheet = data.get("sheet")
        if isinstance(sheet, dict):
            sheet["hp_current"] = after
        if after == 0:
            data["status"] = "down"
        target.data = data
        self.repos.entities.update(target)

        self._broadcast_system(
            f"{target.name} is down." if after == 0
            else f"{target.name}: {after}"
                 + (f"/{maximum}" if isinstance(maximum, int) else "")
                 + " hit points."
        )
        if after == 0 and before > 0:
            self._goes_down(target)
        elif after == 0:
            # Already down and hit again. A failed death save, which is the
            # rule that makes standing over a fallen character mean something.
            self._hit_while_down(target)
        self.publish_entity(target.id)
        self.entity_applied.emit(target.id)

    def _goes_down(self, entity) -> None:
        """Off their feet, and seen to fall -- but not off the map.

        They stay on the square they fell on. Taking the token away made the
        most interesting square on the board the one square showing nothing,
        and it hid the thing a party most needs to see: where their friend is
        lying and how far away that is. A body does not hold the square against
        anybody, so nobody has to walk around it.

        For a player character this is the start of dying rather than the end of
        it: the death save count starts here, at zero, because a character
        healed and dropped a second time in the same fight is dying afresh and
        not two thirds of the way through something.
        """
        encounter = self._the_running_fight()
        if encounter is None:
            return
        for combatant in self.repos.encounters.combatants(encounter.id):
            if combatant.entity_id == entity.id:
                self.repos.encounters.clear_death_saves(combatant.id)
                self.repos.encounters.set_down(combatant.id, True)
                if combatant.on_map:
                    self._show(Played.DOWN.value, combatant)
                self.publish_encounter()
                return

    def _hit_while_down(self, entity) -> None:
        """Damage to somebody already at zero: one failed death save.

        Only for a character who is actually dying. Hitting a corpse is not a
        rule, it is a mood.
        """
        encounter = self._the_running_fight()
        if encounter is None:
            return
        combatant = next(
            (
                c
                for c in self.repos.encounters.combatants(encounter.id)
                if c.entity_id == entity.id
            ),
            None,
        )
        if combatant is None or not self._is_dying(combatant):
            return
        self.repos.encounters.record_death_save(combatant.id, failures=1)
        after = self.repos.encounters.combatant(combatant.id)
        failures = after.death_failures if after else 0
        self._broadcast_system(
            f"{entity.name} is hit while down: a failed death save "
            f"({failures} of {death.SAVES_NEEDED})."
        )
        if failures >= death.SAVES_NEEDED:
            self._broadcast_system(f"{entity.name} is dead.")
        self.publish_encounter()

    # ---------------------------------------------------------------- proposals

    def _propose(self, session: _Session, entity_id: int, build: dict, version: int) -> None:
        """Queue a change to what a character *is*, and tell the table."""
        proposal = self.repos.proposals.propose(
            self.campaign_id, entity_id, session.account_id, build, version
        )
        entity = self.repos.entities.get(entity_id)
        described = describe_changes(build)
        log.info("%s proposed %s for %s", session.member.label, described, entity_id)

        # Told to the DM, not the table. A player asking for something is
        # between the two of them, and announcing every hit point change to
        # everyone would drown the chat.
        self._tell_dms(
            f"{session.member.label} asks to change "
            f"{entity.name if entity else 'a character'}: {described}"
        )
        self.publish_proposals()
        return proposal

    @property
    def proposals(self) -> list[dict]:
        """Open proposals, described for the DM's list."""
        out = []
        for proposal in self.repos.proposals.open_for(self.campaign_id):
            entity = self.repos.entities.get(proposal.entity_id)
            account = (
                self.repos.accounts.get(proposal.account_id)
                if proposal.account_id
                else None
            )
            out.append(
                {
                    "id": proposal.id,
                    "entity_id": proposal.entity_id,
                    "character": entity.name if entity else "?",
                    "who": account.display_name or account.username if account else "?",
                    "changes": proposal.changes,
                    "description": describe_changes(proposal.changes),
                    "stale": bool(entity and entity.version != proposal.base_version),
                }
            )
        return out

    # ------------------------------------------------------------- autopilot

    @property
    def autopilot(self) -> bool:
        return self._autopilot

    def set_autopilot(self, on: bool, by: str = "") -> None:
        """Hand the table to the agent, or take it back.

        Taking it back is immediate and needs no cooperation from the agent: it
        stays connected, and its next line is refused. There is no drain, no
        handshake and nothing to wait for, because the DM interrupting a machine
        mid-sentence is the point.
        """
        on = bool(on)
        if on == self._autopilot:
            return
        self._autopilot = on
        self._autopilot_by = by if on else ""
        log.info("autopilot %s%s", "on" if on else "off", f" by {by}" if by else "")

        self._broadcast(MessageType.AUTOPILOT, on=on, by=self._autopilot_by)
        # Said out loud in the chat as well, and kept in the log. Who was
        # answering is part of what happened at that table.
        self._broadcast_system(
            "Autopilot on -- an agent is answering for the DM."
            if on
            else "Autopilot off -- the DM is answering again."
        )
        self._record(
            SYSTEM,
            "autopilot on" if on else "autopilot off",
            speaker=by or "the DM",
        )

    @property
    def has_agent(self) -> bool:
        """Whether this campaign has an agent login at all."""
        return any(
            account.is_agent
            for account in self.repos.accounts.list(self.campaign_id)
        )

    @property
    def facts(self) -> list[dict]:
        """The canon log as it goes on the wire. Empty for anyone but a DM."""
        return project_facts(self.repos, self.campaign_id, Viewer.dungeon_master())

    def publish_facts(self) -> None:
        """Tell DM-role connections the canon moved.

        Only they get it, so this walks the sessions rather than broadcasting:
        a broadcast with a DM check inside is one edit away from being a leak.
        """
        frame = encode(MessageType.FACTS, facts=self.facts)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm:
                socket.sendTextMessage(frame)

    def publish_proposals(self) -> None:
        """Only the DM needs the queue; players see the chat line."""
        frame = encode(MessageType.PROPOSALS, proposals=self.proposals)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm:
                socket.sendTextMessage(frame)

    def _tell_dms(self, text: str) -> None:
        """Say something to whoever is running the game.

        Kept in the log, but marked as theirs. It used to be recorded as an
        ordinary line, which meant "Autopilot could not answer: Error code 401"
        was read out to the next player who logged in.
        """
        self._record(SYSTEM, text, audience=DM_ONLY)
        frame = encode(MessageType.SYSTEM, text=text, kind=SystemKind.NOTICE.value)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm:
                socket.sendTextMessage(frame)

    def _tell(self, account_id: int | None, text: str) -> None:
        """Say something to one person rather than the whole table."""
        if account_id is None:
            return
        frame = encode(MessageType.SYSTEM, text=text, kind=SystemKind.NOTICE.value)
        for socket, session in self._sessions.items():
            if session.account_id == account_id:
                socket.sendTextMessage(frame)

    def _account_of_member(self, member_id: str) -> int | None:
        """The login behind a roster entry, or None if nobody is it now.

        A member id is what a client can see and name; an account is what the
        host checks things against. Somebody who has left has neither, which is
        why this can come back empty for a line still sitting in the log.
        """
        if not member_id:
            return None
        for session in self._sessions.values():
            if session.member is not None and session.member.id == member_id:
                return session.account_id
        return None

    def _tell_the_agent(self, message_type, **payload) -> bool:
        """One message to the agent, and whether there was one to receive it.

        The answer matters to the caller: a DM who clicked something and got
        silence would reasonably conclude the feature is broken, when in fact
        nothing is running to do the work.
        """
        frame = encode(message_type, **payload)
        reached = False
        for socket, session in self._sessions.items():
            if session.is_agent:
                socket.sendTextMessage(frame)
                reached = True
        return reached

    def _send_to_account(self, account_id: int | None, message_type, **payload) -> None:
        """One message to every connection that login has open."""
        if account_id is None:
            return
        frame = encode(message_type, **payload)
        for socket, session in self._sessions.items():
            if session.account_id == account_id:
                socket.sendTextMessage(frame)

    def decide(self, proposal_id: int, approve: bool, note: str = "") -> bool:
        """Apply or refuse a request. Called by the DM's app."""
        proposal = self.repos.proposals.get(proposal_id)
        if proposal is None or not proposal.is_open:
            return False

        if not approve:
            self.repos.proposals.decide(proposal_id, "rejected", note)
            entity = self.repos.entities.get(proposal.entity_id)
            described = describe_changes(proposal.changes)
            said = f"Your DM said no to {described}"
            if entity is not None:
                said += f" for {entity.name}"
            if note:
                said += f" -- {note}"
            # Told privately: a refusal is between the two of them, and the
            # table does not need to watch.
            self._tell(proposal.account_id, said)
            # And told *what is true*, not only that they were wrong. Without
            # this their screen keeps showing the change they asked for, which
            # reads exactly like it was accepted.
            self._send_to_account(
                proposal.account_id,
                MessageType.REFUSED,
                id=proposal.entity_id,
                reason=note,
            )
            self.publish_entity(proposal.entity_id)
            self.publish_proposals()
            return True

        entity = self.repos.entities.get(proposal.entity_id)
        if entity is None:
            self.repos.proposals.decide(proposal_id, "stale", "the character is gone")
            self.publish_proposals()
            return False

        changes = dict(proposal.changes)
        if "summary" in changes:
            entity.summary = str(changes.pop("summary"))
        if changes:
            sheet = dict(entity.data.get("sheet") or {})
            sheet.update(changes)
            entity.data["sheet"] = sheet
        self.repos.entities.update(entity)

        self.repos.proposals.decide(proposal_id, "approved")
        self._broadcast_system(
            f"{entity.name}: {describe_changes(proposal.changes)} approved"
        )
        self.publish_entity(proposal.entity_id)
        self.publish_proposals()
        self.entity_applied.emit(proposal.entity_id)
        return True

    def _handle_decision(self, socket: QWebSocket, session: _Session, message) -> None:
        if not session.viewer.is_dm:
            self._send(
                socket, MessageType.ERROR, code="refused",
                message="only the DM decides these",
            )
            return
        proposal_id = message.get("proposal")
        if isinstance(proposal_id, int):
            self.decide(
                proposal_id,
                bool(message.get("approve")),
                str(message.get("note", ""))[:200],
            )

    # --------------------------------------------------------------------- output

    @staticmethod
    def _send(socket: QWebSocket, message_type, **payload) -> None:
        socket.sendTextMessage(encode(message_type, **payload))

    def _broadcast(self, message_type, exclude: QWebSocket | None = None, **payload) -> None:
        frame = encode(message_type, **payload)
        for socket in self._sessions:
            if socket is not exclude:
                socket.sendTextMessage(frame)

    def _broadcast_system(self, text: str, exclude: QWebSocket | None = None) -> None:
        """Housekeeping the whole table hears: joins, leaves, autopilot.

        Marked as chatter so a reader can hide it. Anything a person actually
        needs to act on goes through _tell or _tell_dms instead.
        """
        self._record(SYSTEM, text)
        self._broadcast(
            MessageType.SYSTEM,
            exclude=exclude,
            text=text,
            kind=SystemKind.CHATTER.value,
        )

    def _send_roster(self) -> None:
        self._broadcast(MessageType.ROSTER, members=[m.to_dict() for m in self.members])


def _known_versions(raw) -> dict[int, int]:
    """What the client says it holds, taken as a hint and never trusted.

    A wrong version costs at most a resend, so the only real risk is a client
    sending something enormous; the size is capped for that reason alone.
    """
    if not isinstance(raw, dict):
        return {}
    known: dict[int, int] = {}
    for key, value in list(raw.items())[:5000]:
        try:
            known[int(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return known


def describe_changes(changes: dict) -> str:
    """'level 5 to 6' rather than a dump of JSON."""
    parts = []
    for key, value in sorted(changes.items()):
        caption = key.replace("_index", "").replace("_", " ")
        if isinstance(value, dict):
            parts.append(caption)
        else:
            parts.append(f"{caption} to {value}")
    return ", ".join(parts) or "something"


def _silence(socket: QWebSocket) -> None:
    """Drop our signal connections to a socket that is about to go away."""
    for signal in (socket.textMessageReceived, socket.disconnected):
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            # Already disconnected, or the C++ object is gone. Either is fine.
            pass
