"""The session client.

The DM's app uses this too, pointed at its own loopback server. One path through
the code means the host cannot accidentally see something a player would not.

Reconnects on its own with a backoff, because a laptop lid closing mid-session is
the normal case, not the exceptional one.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

from canon_keeper.net import cache
from canon_keeper_protocol import auth, enrol
from canon_keeper_protocol.messages import (
    SystemKind,
    MAX_HOST_FRAME_BYTES,
    Member,
    MessageType,
    ProtocolError,
    Role,
    decode,
    encode,
)
from canon_keeper.net.state import SharedState

log = logging.getLogger("canonkeeper.net.client")

_BACKOFF_MS = (1000, 2000, 4000, 8000, 15000)

#: QWebSocket has no connect timeout of its own, so an unreachable host falls
#: back to the OS TCP timeout -- around 40 seconds on Windows, during which the
#: app looks like it has simply hung. A firewall silently dropping the SYN is
#: the common cause, and that deserves a fast, specific answer.
CONNECT_TIMEOUT_MS = 8000


class SessionClient(QObject):
    connected = Signal()
    disconnected = Signal()
    failed = Signal(str)  # human-readable, safe to show
    #: (username, character) -- an invite was taken up and the account
    #: exists. Not a login: the client logs in next, with what it chose.
    enrolled = Signal(str, str)
    welcomed = Signal(object)  # Member (you)
    roster_changed = Signal(list)  # list[Member]
    #: (Member, text, aside). ``aside`` is a DM speaking while autopilot is on:
    #: it reached the back room and not the party, and their own screen has to
    #: say so -- a line that looks public and was not is worse than one held
    #: back.
    said = Signal(object, str, bool)
    rolled = Signal(object, dict)  # (Member, roll payload)
    #: (text, kind) -- see SystemKind. Chatter can be hidden; a notice is
    #: addressed to the person reading it and must not be.
    system = Signal(str, str)
    #: A request of ours was turned down: (entity id, reason).
    edit_refused = Signal(int, str)
    #: Emitted once the host's filtered view of the campaign has arrived.
    state_replaced = Signal()
    #: What the DM calls each panel, as a {panel_id: name} mapping.
    panel_names_received = Signal(dict)
    #: Build changes waiting on the DM. Only the DM's client sees these.
    proposals_received = Signal(list)
    #: The canon log. Only ever arrives on a DM-role connection; the host
    #: sends a player nothing at all rather than a redacted version.
    facts_received = Signal(list)
    #: Whether an agent is currently answering for the DM, and who said so.
    #: Sent to everyone, not only the agent: a table deserves to know when it
    #: is talking to a machine.
    autopilot_changed = Signal(bool, str)
    #: Someone is composing: (who, whether they still are).
    busy_changed = Signal(object, bool)
    #: What the agent has cost. DM connections only.
    spend_changed = Signal(dict)
    #: What was said before we arrived, oldest first.
    history_received = Signal(list)
    #: The fight, as the host filtered it for us. Empty when there is none.
    encounter_received = Signal(dict)
    #: A turn put to us: what the agent worked out we meant, waiting on yes.
    #: Sent to the player whose character it is, and to the DM to watch.
    action_proposed = Signal(dict)
    #: (id) -- that turn is no longer waiting: answered, or overtaken.
    action_withdrawn = Signal(str)
    #: (waiting, seconds) -- you have acted and the turn is still yours. The
    #: host is counting; when it runs out the turn moves on without you.
    still_your_turn = Signal(bool, int)
    #: The agent was asked to do something the rules refuse, and it is the DM's
    #: to allow or not. DM connections only.
    bend_requested = Signal(dict)
    bend_withdrawn = Signal(str)
    #: Something to show on the map: a walk, a swing, a creature going down.
    #: The host describes it so every screen shows the same thing.
    play = Signal(dict)

    def __init__(self, parent: QObject | None = None, state: SharedState | None = None) -> None:
        super().__init__(parent)
        self._socket = QWebSocket()
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.textMessageReceived.connect(self._on_text)
        if hasattr(self._socket, "errorOccurred"):
            self._socket.errorOccurred.connect(self._on_error)

        self._url = ""
        self._username = ""
        self._password = ""
        #: Set only while making an account from an invite. Never stored, and
        #: cleared the moment the account exists.
        self._invite_code = ""
        self._token = ""
        self._name = ""
        self._role = Role.PLAYER.value
        self._me: Member | None = None
        self._members: list[Member] = []
        #: The host's filtered view of the campaign, for player mode. Shared
        #: with the panels via AppContext, so they need no socket of their own.
        self.state = state if state is not None else SharedState(self)

        #: Where this session's cache lives, once we know who we are talking to.
        self._cache_key: tuple[str, str] | None = None
        #: Which campaign the cache belongs to. Sent with the versions we
        #: hold, so the host can tell whether to believe them.
        self._campaign_key = ""
        self._wanted = False  # whether the user wants to be connected
        self._attempt = 0
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._reconnect)

        self._connect_timeout = QTimer(self)
        self._connect_timeout.setSingleShot(True)
        self._connect_timeout.timeout.connect(self._on_connect_timeout)

    # ------------------------------------------------------------------ lifecycle

    @property
    def is_connected(self) -> bool:
        return self._socket.state().name == "ConnectedState"

    @property
    def me(self) -> Member | None:
        return self._me

    @property
    def members(self) -> list[Member]:
        return list(self._members)

    def join(self, url: str, username: str, password: str, name: str = "") -> None:
        """Log in with an account. The password never leaves this process."""
        self.leave()
        self._url = url
        self._username = username
        self._password = password
        self._token = ""
        self._name = name or username
        self._cache_key = (url, username)

        # Show what we had before the socket is even open, and tell the host
        # about it so it can reply with only what changed.
        held, self._campaign_key = cache.load(url, username)
        if held:
            self.state.replace_all(list(held.values()))

        self._wanted = True
        self._attempt = 0
        self._open()

    def enrol(self, url: str, code: str, username: str, password: str) -> None:
        """Make an account from an invite, then log in with it.

        Two round trips rather than one, on purpose. Enrolment makes the
        account and stops; logging in is the ordinary path every session takes.
        A host that admitted somebody straight off an enrolment would have two
        doors into a session, and the second one would be the one nobody looks
        at again.
        """
        self.leave()
        self._url = url
        self._invite_code = enrol.clean_code(code)
        self._username = username
        self._password = password
        self._token = ""
        self._name = username
        self._cache_key = (url, username)
        self._campaign_key = ""
        self._wanted = True
        self._attempt = 0
        self._open()

    def join_as_host(self, url: str, token: str, name: str) -> None:
        """Connect the DM's own app to the server it just started.

        No password: the app already holds the campaign file, so asking for one
        on the same machine would be friction with nothing behind it.
        """
        self.leave()
        self._url = url
        self._token = token
        self._username = ""
        self._password = ""
        self._name = name
        self._role = Role.DM.value
        self._wanted = True
        self._attempt = 0
        self._open()

    def leave(self) -> None:
        self._wanted = False
        self._retry.stop()
        self._connect_timeout.stop()
        # An invite half-used is not one to retry with on the next connection.
        self._invite_code = ""
        self._me = None
        self._members = []
        # The cache is deliberately left in place: it is what makes the next
        # connection cheap, and the host removes anything we may no longer see.
        self._cache_key = None
        self.state.clear()
        if self._socket.state().name != "UnconnectedState":
            self._socket.close()

    def _open(self) -> None:
        log.info("connecting to %s", self._url)
        self._connect_timeout.start(CONNECT_TIMEOUT_MS)
        self._socket.open(QUrl(self._url))

    def _on_connect_timeout(self) -> None:
        url = QUrl(self._url)
        where = url.host() or self._url
        port = url.port(0)
        log.warning("no response from %s within %sms", self._url, CONNECT_TIMEOUT_MS)
        self._socket.abort()
        if self._attempt <= 1:
            self.failed.emit(
                f"No answer from {where}. Check the host is running, and that its "
                f"firewall allows incoming connections on port {port}."
            )

    def _reconnect(self) -> None:
        if self._wanted:
            self._open()

    # ------------------------------------------------------------------- sending

    def send_chat(self, text: str) -> bool:
        return self._send(MessageType.CHAT, text=text)

    def send_roll(self, notation: str) -> bool:
        return self._send(MessageType.ROLL, notation=notation)

    def send_edit(self, entity_id: int, changes: dict) -> bool:
        """Ask the host to change one of your characters.

        No version travels with this. The host knows what it last sent us and
        checks against that, so a client cannot choose which version it claims
        to be editing, nor omit one and be written unconditionally.
        """
        return self._send(MessageType.EDIT, id=entity_id, changes=changes)

    def send_move(self, combatant_id: int, x: int | None, y: int | None) -> bool:
        """Ask the host to move a token. ``None, None`` takes it off the map.

        A request like any other: the host decides whether this login may move
        anything, and the map only changes when the answer comes back.
        """
        return self._send(MessageType.MOVE, combatant=combatant_id, x=x, y=y)

    def send_death_save(self) -> bool:
        """Roll the death save you owe. Says nothing about which one it is.

        The host knows whose save is owed; this only says that the person who
        owes it has pressed the button. Naming it here would be a client
        choosing when a death save happens.
        """
        return self._send(MessageType.DEATH_SAVE)

    def send_swing(self, combatant_id: int, target_id: int, weapon: str = "") -> bool:
        """Ask the host to roll an attack. It rolls; nothing here does.

        The other half of a turn taken from the map, and a request like the
        move above: whether this login may swing for that creature is the
        host's to answer, not ours to assume.
        """
        return self._send(
            MessageType.SWING, combatant=combatant_id, target=target_id, weapon=weapon
        )

    def send_answer(self, action_id: str, accept: bool, note: str = "") -> bool:
        """Accept a turn that was put to us, or refuse it and say what instead."""
        return self._send(
            MessageType.ACTED, id=action_id, accept=bool(accept), note=note
        )

    def send_translate(self, member_id: str, text: str) -> bool:
        """Ask the agent to write what somebody said as a turn, for the DM.

        Names the speaker rather than their token: which creature they play,
        and whether it is that creature's turn, are the host's to answer.
        """
        return self._send(MessageType.TRANSLATE, member=member_id, text=text)

    def send_allow(self, bend_id: str, allow: bool) -> bool:
        """The DM waiving a rule for the agent, or declining to."""
        return self._send(MessageType.ALLOW, id=bend_id, allow=bool(allow))

    def send_simulate(self, combatant_id: int, on: bool) -> bool:
        """Hand your character to autopilot for this fight, or take it back."""
        return self._send(
            MessageType.SIMULATE, combatant=combatant_id, on=bool(on)
        )

    def send_done(self) -> bool:
        """End your turn now rather than waiting for the clock to do it."""
        return self._send(MessageType.DONE)

    def send_turn(self, action: str) -> bool:
        """Start the fight, pass the turn, or end it. ``begin``/``next``/``end``."""
        return self._send(MessageType.TURN, action=action)

    def send_initiative(self, combatant_id: int, value: int | None) -> bool:
        return self._send(
            MessageType.INITIATIVE, combatant=combatant_id, value=value
        )

    def send_decision(self, proposal_id: int, approve: bool, note: str = "") -> bool:
        """The DM answering a request, with an optional reason for refusing."""
        return self._send(
            MessageType.DECIDE, proposal=proposal_id, approve=approve, note=note
        )

    def _send(self, message_type, **payload) -> bool:
        if not self.is_connected:
            self.failed.emit("Not connected.")
            return False
        self._socket.sendTextMessage(encode(message_type, **payload))
        return True

    # ------------------------------------------------------------------- signals

    def _on_connected(self) -> None:
        self._connect_timeout.stop()
        self._attempt = 0
        known = cache.versions(
            {e["id"]: e for e in self.state.all() if isinstance(e.get("id"), int)}
        )
        if self._token:
            self._socket.sendTextMessage(
                encode(
                    MessageType.HELLO,
                    token=self._token,
                    name=self._name,
                    known=known,
                    campaign=self._campaign_key,
                )
            )
        else:
            self._socket.sendTextMessage(
                encode(
                    MessageType.HELLO,
                    username=self._username,
                    known=known,
                    campaign=self._campaign_key,
                )
            )

    def _on_disconnected(self) -> None:
        self._connect_timeout.stop()
        was_in = self._me is not None
        self._me = None
        self._members = []
        self.disconnected.emit()
        if was_in:
            self.roster_changed.emit([])
        if self._wanted:
            delay = _BACKOFF_MS[min(self._attempt, len(_BACKOFF_MS) - 1)]
            self._attempt += 1
            log.info("reconnecting in %sms", delay)
            self._retry.start(delay)

    def _on_error(self, _error) -> None:
        message = self._socket.errorString() or "connection failed"
        # Only surface the first failure of a run; a reconnect loop that shouts
        # every few seconds is worse than silence.
        if self._attempt <= 1:
            self.failed.emit(message)

    def _on_text(self, raw: str) -> None:
        try:
            # A campaign snapshot is legitimately large, and this host is one we
            # chose and logged into.
            message = decode(raw, max_bytes=MAX_HOST_FRAME_BYTES)
        except ProtocolError as exc:
            log.warning("unreadable frame from server: %s", exc)
            return

        if message.type == MessageType.CHALLENGE:
            self._answer_challenge(message)

        elif message.type == MessageType.ENROLLED:
            self._on_enrolled(message)

        elif message.type == MessageType.SNAPSHOT:
            entities = message.get("entities")
            entities = entities if isinstance(entities, list) else []
            if message.get("partial"):
                # A delta: keep what we had, apply what changed, drop what the
                # host says we may no longer see.
                for entity in entities:
                    self.state.upsert(entity)
                for entity_id in message.get("gone") or ():
                    if isinstance(entity_id, int):
                        self.state.remove(entity_id)
            else:
                self.state.replace_all(entities)
            self._remember()
            self.state_replaced.emit()

        elif message.type == MessageType.PANEL_NAMES:
            names = message.get("names")
            self.panel_names_received.emit(names if isinstance(names, dict) else {})

        elif message.type == MessageType.HISTORY:
            messages = message.get("messages")
            self.history_received.emit(messages if isinstance(messages, list) else [])

        elif message.type == MessageType.PROPOSALS:
            proposals = message.get("proposals")
            self.proposals_received.emit(
                proposals if isinstance(proposals, list) else []
            )

        elif message.type == MessageType.AUTOPILOT:
            self.autopilot_changed.emit(
                bool(message.get("on")), str(message.get("by") or "")
            )

        elif message.type == MessageType.BUSY_NOW:
            raw = message.get("member") or {}
            self.busy_changed.emit(
                Member.from_dict(raw if isinstance(raw, dict) else {}),
                bool(message.get("on")),
            )

        elif message.type == MessageType.SPEND:
            self.spend_changed.emit(dict(message.payload))

        elif message.type == MessageType.ENCOUNTER:
            encounter = message.get("encounter")
            encounter = encounter if isinstance(encounter, dict) else {}
            self.state.set_encounter(encounter or None)
            self.encounter_received.emit(encounter)

        elif message.type == MessageType.ACTION:
            self.action_proposed.emit(dict(message.payload))

        elif message.type == MessageType.ACTION_GONE:
            self.action_withdrawn.emit(str(message.get("id", "")))

        elif message.type == MessageType.PLAY:
            self.play.emit(dict(message.payload))

        elif message.type == MessageType.BEND:
            self.bend_requested.emit(dict(message.payload))

        elif message.type == MessageType.BEND_GONE:
            self.bend_withdrawn.emit(str(message.get("id", "")))

        elif message.type == MessageType.YOUR_TURN:
            self.still_your_turn.emit(
                bool(message.get("waiting")), int(message.get("seconds") or 0)
            )

        elif message.type == MessageType.FACTS:
            facts = message.get("facts")
            self.facts_received.emit(facts if isinstance(facts, list) else [])

        elif message.type == MessageType.ENTITY:
            self.state.upsert(message.get("entity") or {})
            self._remember()

        elif message.type == MessageType.ENTITY_GONE:
            entity_id = message.get("id")
            if isinstance(entity_id, int):
                self.state.remove(entity_id)
                self._remember()

        elif message.type == MessageType.WELCOME:
            key = str(message.get("campaign_key", ""))
            if key and key != self._campaign_key:
                # A different campaign from the one we cached. Ids and versions
                # both restart, so what we are holding is another game's.
                self.state.clear()
                self._campaign_key = key
            self._me = Member.from_dict(message.get("you", {}))
            self._members = [Member.from_dict(m) for m in message.get("members", [])]
            self.welcomed.emit(self._me)
            self.roster_changed.emit(self.members)
            self.connected.emit()

        elif message.type == MessageType.ROSTER:
            self._members = [Member.from_dict(m) for m in message.get("members", [])]
            self.roster_changed.emit(self.members)

        elif message.type == MessageType.SAID:
            self.said.emit(
                Member.from_dict(message.get("member", {})),
                str(message.get("text", "")),
                bool(message.get("aside")),
            )

        elif message.type == MessageType.ROLLED:
            self.rolled.emit(Member.from_dict(message.get("member", {})), message.payload)

        elif message.type == MessageType.SYSTEM:
            self.system.emit(
                str(message.get("text", "")),
                str(message.get("kind", "") or SystemKind.NOTICE.value),
            )

        elif message.type == MessageType.REFUSED:
            entity_id = message.get("id")
            if isinstance(entity_id, int):
                self.edit_refused.emit(entity_id, str(message.get("reason", "")))

        elif message.type == MessageType.ERROR:
            text = str(message.get("message", "The host refused the connection."))
            if message.get("code") in ("bad_login", "refused"):
                # Bad credentials will never come right on their own, so stop
                # rather than hammering the host every second.
                self._wanted = False
                self._retry.stop()
            self.failed.emit(text)

    def _remember(self) -> None:
        """Keep the cache in step with what we hold."""
        if self._cache_key is None:
            return
        url, username = self._cache_key
        cache.save(
            url,
            username,
            {e["id"]: e for e in self.state.all() if isinstance(e.get("id"), int)},
            campaign_key=self._campaign_key,
        )

    def _answer_challenge(self, message) -> None:
        """Prove we know the password without sending it.

        Or, when we are here to make an account rather than to use one, seal the
        new verifier under the invite code and send that instead. Both answer
        the same challenge, because both need the host's nonce and neither may
        put the thing it is proving on the wire.
        """
        try:
            salt = bytes.fromhex(str(message.get("salt", "")))
            nonce = bytes.fromhex(str(message.get("nonce", "")))
        except ValueError:
            self.failed.emit("The host sent a login challenge we could not read.")
            return
        if not nonce:
            return

        if self._invite_code:
            self._answer_with_enrolment(nonce)
            return
        if not salt:
            return
        verifier = auth.derive_verifier(self._password, salt)
        self._socket.sendTextMessage(
            encode(MessageType.LOGIN, proof=auth.proof(verifier, nonce))
        )

    def _on_enrolled(self, message) -> None:
        """The account exists. Drop the code and come back in the front door.

        The invite is spent either way, so holding on to it could only make a
        second attempt fail confusingly. What we keep is the username and
        password the person just chose -- which is exactly what they will type
        every session from now on.
        """
        username = str(message.get("username", "")) or self._username
        character = str(message.get("character", ""))
        self._invite_code = ""
        log.info("enrolled as %r", username)
        self.enrolled.emit(username, character)
        self.join(self._url, username, self._password, name=username)

    def _answer_with_enrolment(self, nonce: bytes) -> None:
        """Make the password material here, and send only what is sealed.

        The salt is ours, not the host's: it is a new account and there is
        nothing on the host to be consistent with yet. The password stays in
        this process exactly as it does at login.
        """
        try:
            salt, verifier = auth.make_credentials(self._password)
        except auth.AuthError as exc:
            self.failed.emit(str(exc))
            self.leave()
            return
        self._socket.sendTextMessage(
            encode(
                MessageType.ENROL,
                **enrol.seal(
                    self._invite_code, nonce, self._username, salt, verifier
                ),
            )
        )
