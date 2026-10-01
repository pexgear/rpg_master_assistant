"""Host or join a session, then chat and roll dice in it.

Hosting starts a server and then connects to it over loopback like anybody else,
so the DM's own messages travel the same path a player's do.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from PySide6.QtCore import (
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from canon_keeper import agent_runner, campaigns, credentials, stand_ins
from canon_keeper.audio.dictation import Dictation
from canon_keeper.net import discovery, funnel
from canon_keeper.net.client import SessionClient
from canon_keeper_protocol import turns
from canon_keeper_protocol.messages import Member, Role, SystemKind
from canon_keeper.net.server import DEFAULT_PORT, SessionServer
from canon_keeper.panels.table.agent_settings import (
    MODEL_SETTING,
    TRANSLATE_SETTING,
    AgentSettingsDialog,
)
from canon_keeper.panels.table.approvals import ApprovalsDialog
from canon_keeper.panels.table import rolls
from canon_keeper.panels.table.dialogs import AccountsDialog, HostDialog, JoinDialog
from canon_keeper.panels.table.dice_overlay import RollDialog
from canon_keeper.plugin import AppContext


class _FunnelSignals(QObject):
    done = Signal(object)  # funnel.Result


class _FunnelTask(QRunnable):
    """Runs one tailscale command off the GUI thread.

    The CLI usually answers in under a second, but the first run of Funnel on a
    tailnet can sit there while certificates are provisioned -- long enough to
    look like a hang if it were done inline.
    """

    def __init__(self, action, *args) -> None:
        super().__init__()
        self.signals = _FunnelSignals()
        self._action = action
        self._args = args

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            result = self._action(*self._args)
        except Exception as exc:  # noqa: BLE001 - report, never take the app down
            result = funnel.Result(False, message=str(exc))
        self.signals.done.emit(result)


QUICK_DICE = ("d20", "d12", "d10", "d8", "d6", "d4")

#: What each role is called on the roster. Written out rather than branched on,
#: because a two-way "DM or player" is how the agent came to be listed as a
#: player -- which it is not. It answers *for* the DM, and is named as such
#: while still saying which of the two you are talking to.
ROLE_LABELS = {
    Role.DM.value: "DM",
    Role.AGENT.value: "DM (autopilot)",
    Role.PLAYER.value: "player",
}


class _Line(NamedTuple):
    """One line in the log, kept whether or not it is on screen.

    Named rather than a bare tuple because it has already grown once, and the
    growing is what breaks things: every reader unpacked three values
    positionally, so adding a fourth broke five tests that had no interest in
    it. A field nobody asked for is now invisible to them.
    """

    kind: str
    text: str
    when: float
    #: The roster id of whoever said it, where a person said it. Kept because a
    #: line is only worth offering to translate if the host can be told whose
    #: it was -- and a *name* would be the wrong thing to tell it.
    said_by: str = ""


class TableWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._server: SessionServer | None = None
        #: The referee for an evening run with nobody connected: the same host,
        #: never started. Built on the first turn, and put away the moment a
        #: real one exists, so there is never a second set of dice.
        self._alone: SessionServer | None = None
        #: True while the join this app was launched with is still unresolved.
        #: The shell is waiting on it before showing the window.
        self._launching = False
        #: Credentials to save once the host actually accepts them. Saving
        #: before that would store a password we have no reason to believe.
        self._pending_credentials: tuple[str, str, str] | None = None
        self._funnel_url = ""
        #: Whether a publish has been asked for and not yet taken down. Kept
        #: apart from the address because the address only exists once
        #: Tailscale has answered, and the funnel is up either way.
        self._publishing = False
        #: Set while relaying a player's edit to the DM's own panels, so the
        #: refresh does not look like a change *by* the DM.
        self._relaying_player_edit = False
        #: The same, for a token an agent moved: the host has already sent that
        #: out, so telling our own panels must not send it again.
        self._relaying_agent_move = False
        #: The agent we started, if we started one. An agent someone else
        #: is running is not ours to stop.
        self._agent_process = None
        #: One process per character handed to autopilot. Kept matching the
        #: handed-over characters whenever the fight changes.
        self._stand_ins = stand_ins.StandIns()
        #: Who is composing right now, by label.
        self._busy: set[str] = set()
        #: Every line, shown or not, so the filter can be turned back on.
        self._entries: list[_Line] = []
        #: The fight as the host last described it, or empty. Only used to ask
        #: whether one is running and whose turn it is.
        self._fight: dict = {}
        #: Rolls the DM has asked for, by the token in their link. Rebuilt
        #: whenever the log is, so a filtered-out line leaves no live link.
        self._roll_prompts: dict[int, object] = {}
        #: Lines the DM has been offered a translation of, by the token in their
        #: mark: ``token -> (member id, what they said)``. Rebuilt with the log,
        #: for the same reason the rolls are.
        self._translations: dict[int, tuple[str, str]] = {}
        #: The die currently open, so the host's answer can land in it.
        self._roll_dialog: RollDialog | None = None
        #: SRD rules, for working out what a character adds to a roll. Built on
        #: first use: most sessions never need it.
        self._content = None
        #: The turn we have already announced as ours, so it is announced once.
        self._up_now = None
        #: A turn worked out for us and waiting on our word. While it is set,
        #: the entry answers it instead of saying something new.
        self._offered: dict | None = None
        #: A rule autopilot wants waived, waiting on this DM.
        self._bending: dict | None = None
        #: The display half of the host's "anything else?" clock.
        self._seconds_left = 0
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)
        # An agent that dies a millisecond after starting used to do so in
        # silence, leaving the button on and the table waiting for a machine
        # that was not there.
        self._agent_watchdog = QTimer(self)
        self._agent_watchdog.setInterval(1000)
        self._agent_watchdog.timeout.connect(self._check_agent)

        # Speaking instead of typing. The text lands in the box rather than
        # being sent, because a transcription is a first draft.
        self._dictation = Dictation(self)
        self._dictation.text_ready.connect(self._on_dictated)
        self._dictation.status.connect(self._on_dictation_status)
        self._dictation.failed.connect(lambda message: self._append("error", message))
        self._dictation_timer = QTimer(self)
        self._dictation_timer.setInterval(200)
        self._dictation_timer.timeout.connect(self._update_dictation_button)
        self._proposals: list[dict] = []
        self._approvals_dialog = None
        self._funnel_pool = QThreadPool(self)
        self._funnel_pool.setMaxThreadCount(1)
        self._client = SessionClient(self, state=ctx.shared)

        self._client.connected.connect(self._on_connected)
        self._client.disconnected.connect(self._on_disconnected)
        self._client.failed.connect(self._on_failed)
        self._client.roster_changed.connect(self._on_roster)
        self._client.said.connect(self._on_said)
        self._client.rolled.connect(self._on_rolled)
        self._client.system.connect(self._append_system)
        # The DM's panel names arrive with the campaign; hand them to the
        # shell, which re-titles the docks.
        self._client.panel_names_received.connect(self._on_panel_names)
        self._client.proposals_received.connect(self._on_proposals)
        self._client.history_received.connect(self._on_history)
        self._client.autopilot_changed.connect(self._on_autopilot_changed)
        self._client.busy_changed.connect(self._on_busy_changed)
        self._client.spend_changed.connect(self._on_spend_changed)
        # A refusal has to reach the panel showing the character, not just the
        # chat: their screen still has the change they asked for on it.
        self._client.edit_refused.connect(ctx.bus.edit_refused)
        # A turn worked out for us. The Combat panel draws it and asks; this
        # panel only carries it, because the socket lives here.
        self._client.action_proposed.connect(ctx.bus.action_proposed)
        self._client.action_withdrawn.connect(ctx.bus.action_withdrawn)
        self._client.action_proposed.connect(self._on_turn_offered)
        self._client.action_withdrawn.connect(self._on_turn_withdrawn)
        self._client.still_your_turn.connect(self._on_still_your_turn)
        self._client.play.connect(ctx.bus.play)
        self._client.bend_requested.connect(self._on_bend_asked)
        self._client.bend_withdrawn.connect(self._on_bend_withdrawn)
        self._client.encounter_received.connect(self._on_encounter_received)
        ctx.bus.action_answered.connect(self._client.send_answer)

        self._build_ui()
        # A share changed while hosting: push it to whoever may now see it.
        ctx.bus.share_changed.connect(self._on_share_changed)
        ctx.bus.player_edit_requested.connect(self._on_player_edit)
        ctx.bus.panel_names_changed.connect(self._on_panel_names_changed)
        ctx.bus.entity_changed.connect(self._on_share_changed)
        # Deletion too, or a character removed mid-session stays on every
        # player's screen until they reconnect. publish_entity turns a missing
        # entity into a removal, so the same handler covers it.
        ctx.bus.entity_deleted.connect(self._on_share_changed)
        # The canon log, for DM-role connections only -- an agent on autopilot
        # answers from it, so a fact committed mid-scene has to reach it.
        ctx.bus.fact_committed.connect(self._on_fact_committed)
        # The DM moved a token, or the turn passed. Everyone at the table is
        # looking at the same fight, so it goes out immediately. A player's map
        # does not come through here at all -- it reads ctx.shared, which the
        # client fills -- so there is no path from receiving a fight back to
        # publishing one.
        ctx.bus.encounter_changed.connect(self._on_encounter_changed)
        # The DM taking a turn by hand. It needs the host, and the host lives
        # here; the Combat panel has no socket, exactly like every other panel.
        ctx.bus.turn_taken.connect(self._on_turn_taken)
        # Passing the turn also goes to the host, because it can roll a death
        # save. Offline it falls back to walking the order here.
        ctx.bus.turn_requested.connect(self._on_turn_requested)
        # A player handing their own character over. It goes to the host, which
        # decides whether it is theirs to hand.
        ctx.bus.simulate_requested.connect(self._client.send_simulate)
        # The DM's Combat panel writes the flag straight to the database, so
        # the host never hears about it -- this is how a stand-in starts for a
        # handover the DM made themselves.
        ctx.bus.encounter_changed.connect(self._mind_the_stand_ins)
        ctx.bus.theme_changed.connect(lambda _dark: self._refresh_colours())
        # Our own character arriving is what turns "Perception check" from text
        # into something clickable, and it arrives after the first lines do.
        self._had_character = False
        if ctx.shared is not None:
            ctx.shared.changed.connect(self._on_shared_changed)
        self._refresh_colours()
        # Before the first `_update_state`, so a machine left published shows up
        # as such on the screen the DM opens rather than after their first click.
        self._mind_a_leftover_funnel()
        self._update_state()

        # Launched by joining a session: connect straight away rather than
        # asking for the same credentials a second time.
        if ctx.pending_join is not None:
            wanted = ctx.pending_join
            ctx.pending_join = None
            # The shell is holding the window back until this resolves, so the
            # answer has to come exactly once, whichever way it goes.
            self._launching = True
            if wanted.invite:
                # First time in: there is no account yet, so make one from what
                # they typed in the chooser rather than asking for it twice.
                self._append_system(f"Making your account on {wanted.url}...")
                self._client.enrol(
                    wanted.url, wanted.invite, wanted.username, wanted.password
                )
            else:
                self._append_system(f"Connecting to {wanted.url}...")
                self._client.join(wanted.url, wanted.username, wanted.password)

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        bar = QHBoxLayout()
        self._host_button = QPushButton("Go online")
        self._host_button.setToolTip(
            "Start a server for this campaign and publish it, so players can "
            "join from your network or from anywhere."
        )
        self._host_button.clicked.connect(self._host)
        bar.addWidget(self._host_button)

        self._join_button = QPushButton("Join session")
        self._join_button.clicked.connect(self._join)
        bar.addWidget(self._join_button)

        self._leave_button = QPushButton("Leave")
        self._leave_button.clicked.connect(self._leave)
        bar.addWidget(self._leave_button)

        self._autopilot_button = QPushButton("Autopilot")
        self._autopilot_button.setCheckable(True)
        self._autopilot_button.setToolTip(
            "Let the agent answer for you. Press it again to take the table "
            "back -- it goes quiet immediately, mid-sentence if need be."
        )
        self._autopilot_button.clicked.connect(self._toggle_autopilot)
        bar.addWidget(self._autopilot_button)

        self._agent_button = QPushButton("Agent...")
        self._agent_button.setToolTip(
            "The key and model autopilot answers with, and how to change them."
        )
        self._agent_button.clicked.connect(self._show_agent_settings)
        bar.addWidget(self._agent_button)

        self._approvals_button = QPushButton("Waiting for you")
        self._approvals_button.setToolTip(
            "Changes players have asked for: levels, classes, ability scores"
        )
        self._approvals_button.clicked.connect(self._show_approvals)
        self._approvals_button.setVisible(False)
        bar.addWidget(self._approvals_button)

        self._players_button = QPushButton("Players...")
        self._players_button.setToolTip("Who may log in, and which character they play")
        self._players_button.clicked.connect(self._manage_accounts)
        self._players_button.setVisible(self._ctx.role == Role.DM.value)
        bar.addWidget(self._players_button)

        self._invite_button = QPushButton("Copy invite")
        self._invite_button.setToolTip("Copy the address players should connect to")
        self._invite_button.clicked.connect(self._copy_invite)
        bar.addWidget(self._invite_button)
        bar.addStretch(1)
        outer.addLayout(bar)

        # Who is composing, and -- for the DM -- what the agent has cost. Both
        # answer the same question: is anything actually happening?
        self._busy_label = QLabel("")
        self._busy_label.setWordWrap(True)
        self._busy_label.setVisible(False)
        outer.addWidget(self._busy_label)

        self._spend_label = QLabel("")
        self._spend_label.setVisible(False)
        self._spend_label.setToolTip(
            "What autopilot has cost this session, as the agent reports it."
        )
        outer.addWidget(self._spend_label)

        self._status = QLabel("Not connected.")
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self._status)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        # A browser rather than a plain edit, for one reason: the rolls the DM
        # asks for are links in the text. Nothing here ever opens a real URL --
        # setOpenLinks(False) makes every click come to us instead.
        self._log = QTextBrowser()
        self._log.setReadOnly(True)
        self._log.setOpenLinks(False)
        self._log.setOpenExternalLinks(False)
        self._log.anchorClicked.connect(self._on_anchor)
        splitter.addWidget(self._log)

        self._roster = QListWidget()
        self._roster.setMaximumWidth(190)
        self._roster.setToolTip("Who is connected")
        splitter.addWidget(self._roster)
        splitter.setStretchFactor(0, 1)
        outer.addWidget(splitter, 1)

        dice_row = QHBoxLayout()
        dice_row.setSpacing(4)
        for die in QUICK_DICE:
            button = QPushButton(die)
            button.setMaximumWidth(48)
            button.clicked.connect(lambda _c=False, d=die: self._roll(d))
            dice_row.addWidget(button)
        dice_row.addStretch(1)
        outer.addLayout(dice_row)

        # A turn worked out for you, sitting exactly where Send is. It replaces
        # sending rather than adding a second place to press yes: you said what
        # you wanted here, so here is where you agree that is what you meant.
        #
        # And it holds the box until you answer. A question about what your
        # character is about to do, left open while you carry on chatting, is a
        # question nobody ever gets back to.
        self._offer_bar = QWidget()
        offer = QHBoxLayout(self._offer_bar)
        offer.setContentsMargins(0, 0, 0, 0)
        self._offer_label = QLabel("")
        self._offer_label.setWordWrap(True)
        offer.addWidget(self._offer_label, 1)

        self._do_it = QPushButton("Do it")
        self._do_it.setToolTip("Take this turn. The table's dice roll it.")
        self._do_it.clicked.connect(self._accept_turn)
        offer.addWidget(self._do_it)

        self._say_more = QPushButton("Say more...")
        self._say_more.setToolTip(
            "Not quite. Type what you meant and it will come back changed."
        )
        self._say_more.clicked.connect(self._unlock_to_say_more)
        offer.addWidget(self._say_more)

        self._not_at_all = QPushButton("Refuse")
        self._not_at_all.setToolTip("Do none of it")
        self._not_at_all.clicked.connect(self._refuse_turn)
        offer.addWidget(self._not_at_all)

        self._offer_bar.setVisible(False)
        outer.addWidget(self._offer_bar)

        # You have acted, and the turn is still yours. Asked rather than cut
        # off -- but with a clock, because "asked" must not mean everybody else
        # waits indefinitely on somebody who has stopped reading.
        self._turn_bar = QWidget()
        still = QHBoxLayout(self._turn_bar)
        still.setContentsMargins(0, 0, 0, 0)
        self._turn_label = QLabel("")
        self._turn_label.setWordWrap(True)
        still.addWidget(self._turn_label, 1)
        self._done_button = QPushButton("Done")
        self._done_button.setToolTip("End your turn now")
        self._done_button.clicked.connect(self._finish_turn)
        still.addWidget(self._done_button)
        self._turn_bar.setVisible(False)
        outer.addWidget(self._turn_bar)

        # Autopilot has been asked to do something the rules refuse. A DM can
        # always overrule the rules -- that is most of what being a DM is -- so
        # the answer is not a flat no, and it is certainly not the machine
        # quietly doing it. It is this.
        self._bend_bar = QWidget()
        bend = QHBoxLayout(self._bend_bar)
        bend.setContentsMargins(0, 0, 0, 0)
        self._bend_label = QLabel("")
        self._bend_label.setWordWrap(True)
        bend.addWidget(self._bend_label, 1)
        self._allow_button = QPushButton("Allow it")
        self._allow_button.setToolTip("The rules bend tonight")
        self._allow_button.clicked.connect(lambda: self._answer_bend(True))
        bend.addWidget(self._allow_button)
        self._forbid_button = QPushButton("No")
        self._forbid_button.clicked.connect(lambda: self._answer_bend(False))
        bend.addWidget(self._forbid_button)
        self._bend_bar.setVisible(False)
        outer.addWidget(self._bend_bar)

        entry = QHBoxLayout()
        self._entry = QLineEdit()
        self._entry.setPlaceholderText("Say something, or /roll 2d6+3")
        self._entry.returnPressed.connect(self._send)
        entry.addWidget(self._entry, 1)

        self._show_chatter = QCheckBox("Show log")
        self._show_chatter.setToolTip(
            "The app talking about itself: who arrived and left, autopilot "
            "switching, and anything else that is not the game. Hidden by "
            "default. Things addressed to you are always shown."
        )
        self._show_chatter.toggled.connect(self._on_log_toggled)
        outer.addWidget(self._show_chatter)

        self._say_button = QPushButton("Speak")
        self._say_button.setCheckable(True)
        self._say_button.setMaximumWidth(96)
        self._say_button.clicked.connect(self._toggle_dictation)
        if Dictation.is_available():
            self._say_button.setToolTip(
                "Talk instead of typing. What you said appears here for you to "
                "correct before you send it."
            )
        else:
            self._say_button.setEnabled(False)
            self._say_button.setToolTip(Dictation.unavailable_hint())
        entry.addWidget(self._say_button)

        send = QPushButton("Send")
        send.clicked.connect(self._send)
        entry.addWidget(send)
        outer.addLayout(entry)

    def _refresh_colours(self) -> None:
        dark = self.palette().base().color().lightness() < 128
        self._colours = {
            "system": QColor("#8b8b8b" if dark else "#767676"),
            "roll": QColor("#e5c07b" if dark else "#8a6100"),
            "dm": QColor("#7aa2f7" if dark else "#1a5fb4"),
            "player": QColor("#7fd1a0" if dark else "#1c7048"),
            "error": QColor("#ff6b6b" if dark else "#b00020"),
            # Your turn. Loud on purpose: it is the one line that is asking you
            # to do something right now.
            "turn": QColor("#ffd166" if dark else "#9a6b00"),
        }
        # The agent speaks for the DM, so it reads as the DM -- a little paler,
        # since the roster already says which it is.
        self._colours["agent"] = QColor("#9db8e8" if dark else "#4a7fc4")
        if getattr(self, "_show_chatter", None) is not None and self._show_chatter.styleSheet():
            self._flag_log()

    # -------------------------------------------------------------- dictation

    def _toggle_dictation(self) -> None:
        if self._say_button.isChecked():
            if not self._dictation.start():
                self._say_button.setChecked(False)
                return
            self._dictation_timer.start()
            self._update_dictation_button()
        else:
            self._dictation_timer.stop()
            self._say_button.setText("Speak")
            self._dictation.stop(self._glossary())

    def _update_dictation_button(self) -> None:
        seconds = int(self._dictation.elapsed)
        self._say_button.setText(f"Stop {seconds // 60}:{seconds % 60:02d}")

    def _on_dictated(self, text: str) -> None:
        """Put the words in the box. Sending stays a deliberate act.

        Appended rather than replacing, so a second sentence adds to the first
        instead of throwing it away.
        """
        existing = self._entry.text().strip()
        self._entry.setText(f"{existing} {text}".strip() if existing else text)
        self._entry.setFocus()
        self._entry.end(False)

    def _on_dictation_status(self, text: str) -> None:
        if text:
            self._ctx.bus.status_message.emit(text)

    def _glossary(self) -> str:
        """Proper nouns to prime the transcriber with.

        The DM has the campaign to draw on. A player has only what the host
        chose to send them, which is exactly the set of names they are likely
        to say out loud.
        """
        if self._ctx.role == Role.DM.value:
            from canon_keeper.audio.transcribe import build_glossary

            return build_glossary(self._ctx.repos, self._ctx.campaign_id)

        if self._ctx.shared is None:
            return ""
        names = [
            entity.get("name", "")
            for entity in self._ctx.shared.all()
            if entity.get("name")
        ]
        if not names:
            return ""
        return "The following names may be mentioned: " + ", ".join(names) + "."

    # ---------------------------------------------------------------- actions

    def _default_name(self) -> str:
        return self._ctx.repos.settings.get("session_name", "") or "Dungeon Master"

    def _on_player_edit(self, entity_id: int, changes: dict) -> None:
        if not self._client.send_edit(entity_id, changes):
            self._append("error", "Not connected, so that change was not saved.")

    def _on_proposals(self, proposals: list) -> None:
        """The DM's queue changed. Make it visible without being a nuisance."""
        self._proposals = proposals
        waiting = len(proposals)
        self._approvals_button.setVisible(waiting > 0)
        self._approvals_button.setText(
            f"Waiting for you ({waiting})" if waiting else "Waiting for you"
        )
        if self._approvals_dialog is not None:
            self._approvals_dialog.set_proposals(proposals)

    def _show_approvals(self) -> None:
        dialog = ApprovalsDialog(self._proposals, self)
        dialog.decided.connect(self._decide_proposal)
        self._approvals_dialog = dialog
        dialog.exec()
        self._approvals_dialog = None

    def _decide_proposal(self, proposal_id: int, approve: bool, note: str = "") -> None:
        # Through the client, so the host applies it -- even when the host is us.
        self._client.send_decision(proposal_id, approve, note)

    def _on_panel_names(self, names: dict) -> None:
        if self._ctx.names is not None:
            self._ctx.names.apply_party_names(names)

    def _on_panel_names_changed(self) -> None:
        # The DM renamed something while hosting: tell the table.
        if self._server is not None and self._server.is_running:
            self._server.publish_panel_names()

    def _on_share_changed(self, entity_id: int) -> None:
        if self._server is None or not self._server.is_running:
            return
        if not self._relaying_player_edit:
            # A change of *yours* refuses anything a player proposed against the
            # older sheet. A player's own edit must not: it would cancel the
            # level-up they asked for the moment they took damage.
            self._server.refuse_conflicting(entity_id)
        self._server.publish_entity(entity_id)

    def _toggle_autopilot(self) -> None:
        if self._server is None or not self._server.is_running:
            self._autopilot_button.setChecked(False)
            return

        if not self._autopilot_button.isChecked():
            self._server.set_autopilot(False, by=self._my_name())
            # The agent is stopped after the switch, not before: turning it off
            # is what silences it, and that has already happened by here.
            self._stop_agent()
            self._update_state()
            return

        if not self._ensure_agent_running():
            self._autopilot_button.setChecked(False)
            self._update_state()
            return

        self._server.set_autopilot(True, by=self._my_name())
        self._update_state()

    def _my_name(self) -> str:
        me = self._client.me
        return me.name if me else "the DM"

    def _ensure_agent_running(self) -> bool:
        """Make sure something is there to answer. Returns False if not.

        An agent someone started themselves -- on this machine or a spare box --
        is left alone. Only when nothing is connected does the app start one,
        because pressing a button should not require a terminal.
        """
        if self._server is None:
            return False
        if any(m.role == Role.AGENT.value for m in self._server.members):
            return True
        if self._agent_process is not None and self._agent_process.poll() is None:
            return True  # started, still connecting

        problem = agent_runner.missing_requirement()
        if problem:
            QMessageBox.information(self, "The agent cannot run", problem)
            return False

        key = agent_runner.api_key()
        if not key:
            key = self._ask_for_api_key()
            if not key:
                return False

        try:
            username, password = agent_runner.ensure_account(
                self._ctx.repos, self._ctx.campaign_id
            )
            self._agent_process = agent_runner.start(
                f"ws://127.0.0.1:{self._server.port}",
                username,
                password,
                key,
                self._agent_model(),
                agent_runner.workspace_id(),
            )
        except agent_runner.AgentUnavailable as exc:
            QMessageBox.warning(self, "Cannot start the agent", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - surfaced, never fatal
            self._ctx.log.exception("could not start the agent")
            QMessageBox.warning(self, "Cannot start the agent", str(exc))
            return False

        self._agent_watchdog.start()
        self._append("system", "Starting the agent...")
        return True

    def _check_agent(self) -> None:
        """Notice when the agent stops, and say why."""
        agent = self._agent_process
        if agent is None:
            self._agent_watchdog.stop()
            return
        if agent.poll() is None:
            return  # still running

        self._agent_watchdog.stop()
        self._agent_process = None
        reason = agent.why_it_stopped()
        self._ctx.log.warning("the agent stopped (%s): %s", agent.returncode, reason)
        self._append("error", f"The agent stopped. {reason}")

        # Autopilot without an agent is a table waiting on nothing.
        if self._server is not None and self._server.is_running:
            self._server.set_autopilot(False, by=self._my_name())
        self._autopilot_button.setChecked(False)
        self._update_state()

    def _ask_for_api_key(self) -> str:
        """Open the agent settings so a first-time key can be typed.

        Returns the key to use now. On a machine with no credential store the
        dialog saves nothing, so what it was given is returned directly rather
        than read back from a store that did not keep it.
        """
        dialog = AgentSettingsDialog(self._ctx, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        return dialog.key

    def _show_agent_settings(self) -> None:
        """Reachable whether or not a key is set -- otherwise a mistyped one is
        permanent, and the only symptom is an agent that never answers."""
        AgentSettingsDialog(self._ctx, self).exec()

    def _agent_model(self) -> str:
        return self._ctx.repos.settings.get(MODEL_SETTING, "")

    def _stop_agent(self) -> None:
        self._agent_watchdog.stop()
        if self._agent_process is None:
            return
        # Only ours. An agent someone else started is not the app's to kill.
        agent_runner.stop(self._agent_process)
        self._agent_process = None

    def _on_busy_changed(self, member, on: bool) -> None:
        """Show that someone is composing.

        Held as a set rather than a flag: two people can be typing at once, and
        one of them finishing must not clear the other.
        """
        label = getattr(member, "label", "") or "Someone"
        if on:
            self._busy.add(label)
        else:
            self._busy.discard(label)

        if not self._busy:
            self._busy_label.setVisible(False)
            return

        names = sorted(self._busy)
        if len(names) == 1:
            text = f"{names[0]} is writing..."
        else:
            text = f"{', '.join(names[:-1])} and {names[-1]} are writing..."
        self._busy_label.setText(text)
        self._busy_label.setVisible(True)

    def _on_spend_changed(self, spend: dict) -> None:
        """The agent's running bill, on the DM's screen only."""
        dollars = float(spend.get("dollars") or 0.0)
        turns = int(spend.get("turns") or 0)
        if not turns:
            self._spend_label.setVisible(False)
            return

        went_in = int(spend.get("tokens_in") or 0)
        came_out = int(spend.get("tokens_out") or 0)
        cost = f"${dollars:.2f}" if dollars >= 0.005 else "under a cent"
        if dollars <= 0:
            # An unpriced model: report the tokens rather than invent a figure.
            cost = "cost unknown for this model"
        self._spend_label.setText(
            f"Autopilot: {turns} answer{'s' if turns != 1 else ''}, "
            f"{went_in:,} in / {came_out:,} out, {cost}"
        )
        self._spend_label.setVisible(True)

    def _on_autopilot_changed(self, on: bool, by: str) -> None:
        """Someone flipped the switch -- reflect it without echoing it back."""
        self._autopilot_button.blockSignals(True)
        self._autopilot_button.setChecked(on)
        self._autopilot_button.blockSignals(False)
        self._autopilot_button.setText("Autopilot on" if on else "Autopilot")
        self._update_state()

    def _on_fact_committed(self, _fact_id: int) -> None:
        if self._server is not None and self._server.is_running:
            self._server.publish_facts()

    def _on_encounter_received(self, fight: dict) -> None:
        """Say so, once, when the turn comes round to your character.

        A map three panels away has already said it, in a colour, on a token
        the size of a fingernail. The chat is where people are looking, and
        missing your turn is the one thing this app can actually prevent.
        """
        # Kept whether or not it is anybody's turn here, because the DM's copy
        # of the log needs to know a fight is running at all -- the offer to
        # translate a line only exists during one.
        self._fight = fight if isinstance(fight, dict) else {}

        mine = self._own_character()
        if not fight or mine is None:
            self._up_now = None
            return

        turn = fight.get("turn")
        acting = next(
            (
                c
                for c in fight.get("combatants") or []
                if c.get("id") == turn and c.get("entity") == mine.get("id")
            ),
            None,
        )
        if acting is None:
            self._up_now = None
            return
        if self._up_now == turn:
            return  # already said, and saying it twice is worse than not at all
        self._up_now = turn
        self._append(
            "turn",
            f"It is your turn -- {mine.get('name', 'you')}. Say what you do.",
        )

    def _on_encounter_changed(self) -> None:
        if self._relaying_agent_move:
            # The host has already sent this one out; publishing it a second
            # time would be a frame to every player saying nothing new.
            return
        if self._server is not None and self._server.is_running:
            self._server.publish_encounter()

    def _referee(self):
        """Whoever is holding the dice. Always somebody, online or not.

        A fight is rolls and hit points before it is a network, and plenty of
        evenings are run with the laptop on the table and nobody connected to
        it. So the same host code runs either way: hosting is *other people
        being able to reach* the referee, not the referee existing.

        Built on the first turn rather than at start-up, because it opens a
        session in the log, and a campaign you only came in to rename should
        not get an evening it never had.
        """
        if self._server is not None:
            return self._server
        if self._alone is None:
            self._alone = SessionServer(
                self._ctx.repos, self._ctx.campaign_id, parent=self
            )
            # Nothing is sent anywhere -- there is nowhere to send it -- but the
            # DM's own map still has to hear that the fight moved, and how: a
            # walk or a swing is an animation, not just a database row that
            # changed underneath it.
            self._alone.encounter_applied.connect(self._on_agent_moved)
            self._alone.played.connect(self._ctx.bus.play)
        return self._alone

    def _on_turn_taken(self, turn: dict) -> None:
        """A turn taken from a map, from whichever end of the wire that map is.

        A player's app has no referee of its own -- their campaign lives on
        somebody else's machine -- so theirs is asked for, in the two messages
        it is made of. The host applies the very same rules either way: it is
        the one holding the dice, and which door the turn came through is not
        one of the things it takes into account.
        """
        combatant = int(turn.get("combatant") or 0)
        if self._ctx.role != "dm":
            if turn.get("move"):
                x, y = turn["move"]
                self._client.send_move(combatant, int(x), int(y))
            if turn.get("target") is not None:
                self._client.send_swing(
                    combatant, int(turn["target"]), str(turn.get("weapon", ""))
                )
            return

        referee = self._referee()
        steps = turns.normalise(
            turn.get("steps"),
            move=turn.get("move"),
            target=turn.get("target"),
            weapon=str(turn.get("weapon", "")),
        )
        # A turn for somebody else's character is an offer, not an act. The DM
        # staging it was their consent; the player answers once and an accepted
        # turn is carried out without coming back to the DM, because coming back
        # would be asking the same person the same question twice.
        #
        # The referee decides, not this panel: which login plays a character is
        # `_who_plays`'s to settle, and a second opinion worked out here is how
        # the two halves of that fact drift apart.
        done = 0
        if referee.is_played_here(combatant):
            problem = referee.offer_turn(combatant, steps=steps)
            if not problem:
                self._ctx.bus.status_message.emit("Put to them to accept.")
                # An offer is the whole turn or none of it: nothing has been
                # spent, and it is now theirs to answer.
                done = len(steps)
        else:
            problem, done = referee.take_turn_steps(combatant, steps)
        if problem:
            self._ctx.bus.status_message.emit(problem)
        # Said either way, because whoever staged it is holding it until they
        # hear. `done` is how much of it has already been paid for, so what is
        # still staged is only the part that has not happened.
        self._ctx.bus.turn_settled.emit(not problem, problem, done)

    def _on_turn_requested(self, action: str) -> None:
        """The DM's Combat panel passing the turn.

        Through the referee either way, because a dying character's death save
        is rolled as the turn passes and dice are the referee's -- and a fight
        run alone must behave like a fight run for four people, or the rules
        quietly become two sets of rules.
        """
        problem = self._referee().run_turn(action)
        if problem:
            self._ctx.bus.status_message.emit(problem)

    def _mind_the_stand_ins(self) -> None:
        """Keep the running stand-ins matching the handed-over characters.

        Cheap enough to call on every change: it compares two sets and only
        does anything when they differ.
        """
        self._stand_ins.look(self._server)

    def _on_agent_moved(self) -> None:
        """A token moved on someone else's say-so. Tell the DM's own panels."""
        self._relaying_agent_move = True
        try:
            self._ctx.bus.encounter_changed.emit()
        finally:
            self._relaying_agent_move = False

    def _on_player_edit_applied(self, entity_id: int) -> None:
        """Tell the DM's own panels to re-read what a player just changed."""
        self._relaying_player_edit = True
        try:
            self._ctx.bus.entity_changed.emit(entity_id)
        finally:
            self._relaying_player_edit = False

    def _start_funnel(self) -> None:
        """Publish the session. Part of going online, not a second decision.

        Hosting on a LAN and hosting for people who are not on it are the same
        intent -- "my players can join" -- so pressing one button does both. The
        publish is the half that can fail for reasons outside the app, and when
        it does the session stays up on the network regardless.
        """
        if self._server is None or not self._server.is_running:
            return
        self._append_system("Asking Tailscale to publish this session...")
        # Both set before the command runs, not after it answers: from here on
        # there may be a funnel to take down, whatever comes back -- including
        # on some later run of the app, which is what the marker is for.
        self._publishing = True
        funnel.remember(self._server.port)
        self._run_funnel(funnel.start, self._server.port, self._on_funnel_started)

    @property
    def _anything_published(self) -> bool:
        """Whether there may be a funnel of ours on the public internet.

        The *address* is not the test. ``funnel --bg`` takes effect in the
        daemon, so a publish that never reported back is published all the
        same. Two callers -- going offline, and closing the app -- and each had
        its own copy of this condition, both wrong in the same way.
        """
        return bool(self._funnel_url) or self._publishing

    def _mind_a_leftover_funnel(self) -> None:
        """Say when a previous run left the machine published.

        Not taken down on sight. A public address is the DM's to keep or drop,
        and an app that silently undid something it found would be deciding
        that for them -- so this makes it visible and makes the button that
        ends a session end this too.
        """
        if self._ctx.role != Role.DM.value:
            return
        port = funnel.left_over()
        if port is None:
            return
        self._publishing = True
        self._append_system(
            f"This machine may still be published to the internet from last "
            f"time, pointing at port {port}. Nothing is hosting it now. "
            "Press Go offline to take it down."
        )

    def _stop_funnel(self) -> None:
        self._funnel_url = ""
        self._publishing = False
        funnel.forget()
        self._proposals: list[dict] = []
        self._approvals_dialog = None
        self._run_funnel(funnel.stop, None, self._on_funnel_stopped)

    def _run_funnel(self, action, argument, on_done) -> None:
        task = (
            _FunnelTask(action, argument) if argument is not None else _FunnelTask(action)
        )
        task.signals.done.connect(on_done)
        self._funnel_pool.start(task)

    def _on_funnel_started(self, result) -> None:
        if not result.ok:
            if not result.attempted:
                # It failed before the command ran -- no Tailscale, or Funnel
                # not switched on -- so nothing is out there and the note
                # saying otherwise would be a false alarm on the next launch.
                self._publishing = False
                funnel.forget()
            self._report_funnel_problem(result)
            self._update_state()
            return

        self._funnel_url = result.websocket_url
        self._append_system(f"Published. Players outside your network: {self._funnel_url}")
        self._append_system("Use Copy invite to send that to them.")
        self._update_state()

    def _report_funnel_problem(self, result) -> None:
        """Say what went wrong, and where it can be fixed if there is a link.

        Tailscale's own wording names the setting to change, so it is shown
        unaltered rather than paraphrased.
        """
        box = QMessageBox(self)
        box.setWindowTitle("Players outside your network")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            f"{result.message}\n\nYou are online either way: anyone on your "
            "network can join right now."
        )

        if result.enable_url:
            open_button = box.addButton(
                "Open Tailscale settings", QMessageBox.ButtonRole.AcceptRole
            )
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_button:
                QDesktopServices.openUrl(QUrl(result.enable_url))
                self._append_system(
                    "Turn Funnel on in the page that just opened, then leave "
                    "and go online again."
                )
                return
        else:
            box.setStandardButtons(QMessageBox.StandardButton.Close)
            box.exec()

        # Not an error: the session is up, and reachable by everyone the DM
        # can actually see. Only the far half did not happen.
        self._append_system("Not published to the internet. Your network still works.")

    def _on_funnel_stopped(self, result) -> None:
        if result.ok:
            self._append_system("No longer shared on the internet.")
        else:
            self._append("error", result.message or "Could not stop sharing.")
        self._update_state()

    def current_address(self) -> str:
        """Whatever address a player should actually be given, or empty.

        The funnel's if there is one, because a session that is on the internet
        is one somebody may be joining from outside the room.
        """
        if self._funnel_url:
            return self._funnel_url
        if self._server is not None and self._server.is_running:
            addresses = discovery.local_addresses()
            host = next(
                (a for a in sorted(addresses) if not a.startswith("127.")), "127.0.0.1"
            )
            return f"ws://{host}:{self._server.port}"
        return ""

    def _copy_invite(self) -> None:
        invite = self.current_address()
        if not invite:
            return
        QApplication.clipboard().setText(invite)
        self._ctx.bus.status_message.emit(f"Copied {invite}")

    def _manage_accounts(self) -> None:
        # The dialog builds whole invites -- address and code in one string --
        # so it has to be told where this session actually is.
        AccountsDialog(self._ctx, self, address=self.current_address()).exec()

    def _host(self) -> None:
        campaign = self._ctx.repos.campaigns.get(self._ctx.campaign_id)
        campaign_name = campaign.name if campaign else "this campaign"

        if not self._ctx.repos.accounts.players(self._ctx.campaign_id):
            proceed = QMessageBox.question(
                self,
                "Host a session",
                "Nobody has a login for this campaign yet, so no player could "
                "join. Host anyway?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
                self._manage_accounts()
                return

        dialog = HostDialog(campaign_name, self._default_name(), self)
        if not dialog.exec():
            return
        name, port, session_name = dialog.values()

        server = SessionServer(
            self._ctx.repos, self._ctx.campaign_id, session_name, parent=self
        )
        server.failed.connect(self._on_failed)
        server.entity_applied.connect(self._on_player_edit_applied)
        server.encounter_applied.connect(self._on_agent_moved)
        # A character handed over, or handed back, is a change to the
        # fight -- so this is where a stand-in starts and stops.
        server.encounter_applied.connect(self._mind_the_stand_ins)
        # Normally the wire carries this, through our own loopback join below
        # -- but that join has not completed the instant hosting starts, and
        # this covers the narrow window before it has.
        server.played.connect(self._ctx.bus.play)
        if not server.start(port):
            server.deleteLater()
            return

        self._server = server
        if self._alone is not None:
            # One referee at a time. The stand-in one held no state worth
            # keeping -- the fight is in the database, not in it.
            self._alone.deleteLater()
            self._alone = None
        self._ctx.repos.settings.set("session_name", name)
        self._append_system(f"Hosting {session_name!r} on port {server.port}.")
        # Join our own server like anyone else, so there is one path through the
        # code -- with a token, since we already own the campaign file.
        self._client.join_as_host(f"ws://127.0.0.1:{server.port}", server.local_token, name)
        self._update_state()
        # Publishing is part of going online rather than a second decision --
        # "my players can join" does not become a different wish when one of
        # them is not on the sofa. It happens in the background, and failing
        # does not take the session down with it.
        self._start_funnel()

    def _join(self) -> None:
        dialog = JoinDialog(
            self._ctx.repos.settings.get("session_last_url", ""),
            self._ctx.repos.settings.get("session_username", ""),
            self,
        )
        if not dialog.exec():
            return
        url, username, password = dialog.values()
        if not username or not password:
            self._append("error", "A username and password are needed to join.")
            return

        # Held until the host accepts it: saving a password we have no reason
        # to believe is how a bad one gets remembered.
        self._pending_credentials = (url, username, password)

        self._ctx.repos.settings.set("session_last_url", url)
        self._ctx.repos.settings.set("session_username", username)

        code = dialog.invite_code()
        if code:
            self._append_system(f"Making your account on {url}...")
            self._client.enrol(url, code, username, password)
        else:
            self._append_system(f"Connecting to {url}...")
            self._client.join(url, username, password)
        self._update_state()

    def _leave(self) -> None:
        self._client.leave()
        if self._anything_published:
            # Closing the session must also take it off the public internet,
            # or the tunnel outlives the thing it was pointing at.
            self._stop_funnel()
        if self._server is not None:
            self._server.stop()
            self._server.deleteLater()
            self._server = None
            self._append_system("Session closed.")
        self._update_state()

    def _send(self) -> None:
        text = self._entry.text().strip()
        if not text:
            return
        if text.startswith("/"):
            self._command(text)
            return
        if self._client.send_chat(text):
            self._entry.clear()
            # Saying more is one message. After it, the box waits for the
            # answer again rather than leaving the turn half-open.
            if self._offered is not None:
                self._lock_for_the_offer()

    # ------------------------------------------------------------- your turn
    #
    # A turn on offer holds the chat box. There are exactly three ways out --
    # do it, say more, or refuse -- and no fourth one where you carry on
    # chatting and never come back to it. Combat is the one part of an evening
    # where everybody is waiting on one person, and the app should not be the
    # reason that wait is longer.

    def _on_turn_offered(self, action: dict) -> None:
        """The agent has worked out what you meant. Ask, where Send is."""
        if action.get("watching"):
            # The DM's copy: they see what is on offer and do not answer it.
            self._append("system", f"Offered: {action.get('text', '')}")
            return
        self._offered = action
        self._offer_label.setText(f"<b>{action.get('text', 'Your turn.')}</b>")
        self._lock_for_the_offer()

    def _holding_for_an_answer(self) -> bool:
        return self._offered is not None and self._offer_bar.isVisible()

    def _lock_for_the_offer(self) -> None:
        self._offer_bar.setVisible(True)
        self._entry.setPlaceholderText(
            "Do it, say more, or refuse -- it is your turn"
        )
        self._update_state()

    def _unlock_to_say_more(self) -> None:
        """Let them type one more thing. The offer stands until it is replaced.

        The map keeps showing what was proposed while they explain, because the
        thing being corrected is what they are looking at.
        """
        if self._offered is None:
            return
        self._offer_bar.setVisible(False)
        self._entry.setPlaceholderText("What did you mean? It will come back changed.")
        self._update_state()
        self._entry.setFocus()

    def _on_turn_withdrawn(self, action_id: str) -> None:
        if self._offered is not None and self._offered.get("id") == action_id:
            self._clear_offer()

    def _clear_offer(self) -> None:
        self._offered = None
        self._offer_bar.setVisible(False)
        self._entry.setPlaceholderText("Say something, or /roll 2d6+3")
        self._update_state()

    def _on_still_your_turn(self, waiting: bool, seconds: int) -> None:
        """Acted, and the turn is still yours: anything else, or done?

        The countdown shown here is only a display of the host's. It is the
        host that decides when the turn moves on, because a promise made to
        four other people cannot depend on one person's laptop staying awake.
        """
        self._countdown.stop()
        if not waiting:
            self._turn_bar.setVisible(False)
            return
        self._seconds_left = max(0, int(seconds))
        self._show_countdown()
        self._turn_bar.setVisible(True)
        self._countdown.start()

    def _show_countdown(self) -> None:
        self._turn_label.setText(
            "<b>Still your turn.</b> Say what else you do, or "
            f"<b>Done</b> -- moving on in {self._seconds_left}s."
        )

    def _tick(self) -> None:
        self._seconds_left -= 1
        if self._seconds_left <= 0:
            # The host has the real clock; this only stops the number going
            # negative while its word arrives.
            self._countdown.stop()
            self._turn_label.setText("<b>Still your turn.</b> Moving on...")
            return
        self._show_countdown()

    # --------------------------------------------------- bending the rules

    def _on_bend_asked(self, bend: dict) -> None:
        """Autopilot wants to do something the rules refuse. Your call."""
        self._bending = bend
        self._bend_label.setText(
            f"<b>Autopilot wants to {bend.get('what', 'do that')}.</b> "
            f"{bend.get('why', '')} Allow it?"
        )
        self._bend_bar.setVisible(True)
        # Said in the log as well, because the bar goes away and the decision
        # is part of what happened at that table.
        self._append("system", f"Autopilot asks: {bend.get('what', '')}")

    def _on_bend_withdrawn(self, bend_id: str) -> None:
        if self._bending is not None and self._bending.get("id") == bend_id:
            self._bending = None
            self._bend_bar.setVisible(False)

    def _answer_bend(self, allow: bool) -> None:
        if self._bending is None:
            return
        self._client.send_allow(self._bending["id"], allow)
        self._bending = None
        self._bend_bar.setVisible(False)

    def _finish_turn(self) -> None:
        self._countdown.stop()
        self._turn_bar.setVisible(False)
        self._client.send_done()

    def _accept_turn(self) -> None:
        if self._offered is None:
            return
        self._ctx.bus.action_answered.emit(self._offered["id"], True, "")
        self._clear_offer()

    def _refuse_turn(self) -> None:
        """None of it. The turn is given back and the box is yours again."""
        if self._offered is None:
            return
        self._ctx.bus.action_answered.emit(self._offered["id"], False, "")
        self._clear_offer()

    def _command(self, text: str) -> None:
        head, _, rest = text[1:].partition(" ")
        head = head.lower()
        if head in ("roll", "r"):
            if self._roll(rest.strip()):
                self._entry.clear()
        else:
            self._append("error", f"Unknown command /{head}. Try /roll 2d6+3.")

    def _roll(self, notation: str) -> bool:
        if not notation:
            self._append("error", "What should I roll? Try /roll 2d6+3.")
            return False
        # Rolled by the host, not here, so the result is not ours to fake.
        return self._client.send_roll(notation)

    # ----------------------------------------------------------------- events

    def _on_connected(self) -> None:
        # Emitted on WELCOME rather than on the socket opening, so this is the
        # first moment the login is known to have been accepted.
        self._settle_launch(True)
        # Only now do we know the credentials were good, so only now save them.
        if self._pending_credentials is not None:
            url, username, password = self._pending_credentials
            self._pending_credentials = None
            campaigns.remember_remote(url, self._client.me.name if self._client.me else "", username)
            if credentials.save(url, username, password):
                self._append_system("Password saved for this session.")
        self._update_state()

    def _on_disconnected(self) -> None:
        self._roster.clear()
        self._update_state()

    def _on_failed(self, message: str) -> None:
        self._append("error", message)
        self._settle_launch(False, message)
        self._update_state()

    def _settle_launch(self, ok: bool, reason: str = "") -> None:
        """Answer the shell's question about the join we were launched with.

        Once only. The client retries on its own, so without the flag a session
        that dropped an hour into the evening would look to the shell like the
        launch having failed.
        """
        if not self._launching:
            return
        self._launching = False
        self._ctx.bus.session_ready.emit(ok, reason)

    def _on_roster(self, members: list[Member]) -> None:
        self._roster.clear()
        for member in members:
            label = member.label
            if member.character and member.character != member.name:
                label = f"{member.character}  ({member.name})"
            item = QListWidgetItem(
                f"{label}  -  {ROLE_LABELS.get(member.role, member.role)}"
            )
            item.setForeground(self._colours.get(member.role, self._colours["player"]))
            self._roster.addItem(item)

    def _on_said(self, member: Member, text: str, aside: bool = False) -> None:
        if aside:
            # While autopilot is on there is one voice at the table and it is
            # the agent's. This went to the agent, not the party, and saying so
            # is the difference between directing and being ignored.
            self._append(member.role, f"{member.label} (to autopilot): {text}")
            return
        self._append(member.role, f"{member.label}: {text}", said_by=member.id)

    def _on_rolled(self, member: Member, payload: dict) -> None:
        self._append("roll", f"{member.label} rolled {payload.get('description', '')}")
        # If we asked for this one, the die that is still tumbling stops here.
        me = self._client.me
        if self._roll_dialog is not None and me is not None and member.id == me.id:
            self._roll_dialog.settle(payload)

    # ------------------------------------------------------------------ output

    def _on_history(self, messages: list) -> None:
        """Replay what was said before we arrived.

        The log is cleared first: this is the authoritative account, and our own
        "Connecting..." lines are not part of it.
        """
        self._log.clear()
        self._entries.clear()
        self._roll_prompts.clear()
        for entry in messages:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind", "system")
            speaker = entry.get("speaker", "")
            text = str(entry.get("text", ""))
            if kind == "said" and speaker:
                text = f"{speaker}: {text}"
            elif kind == "rolled" and speaker:
                text = f"{speaker} rolled {text}"
            elif kind == "system":
                # Old joins and leaves are the same housekeeping as live ones,
                # and hiding one while showing the other would be odd.
                kind = "chatter"
            self._append(
                entry.get("role") or kind,
                text,
                when=entry.get("at"),
            )
        if messages:
            self._append("system", "--- you are here ---")

    def _append(
        self,
        kind: str,
        text: str,
        when: float | None = None,
        said_by: str = "",
    ) -> None:
        """Record a line, and show it if it is not being filtered out."""
        self._entries.append(
            _Line(kind, text, when or datetime.now().timestamp(), said_by)
        )
        if kind == "error":
            # Some errors answer a button the reader just pressed, and the log
            # is the wrong place to learn that: it is filtered, and they are
            # looking at the button. The status bar is immediate and transient,
            # which is exactly the right weight for it.
            self._ctx.bus.status_message.emit(text)
        if self._is_hidden(kind):
            if kind == "error":
                # And a mark on the filter, so a problem is not both hidden and
                # unannounced.
                self._flag_log()
            return
        self._draw(kind, text, when, said_by)

    def _flag_log(self) -> None:
        colour = self._colours.get("error", QColor("#b00020")).name()
        self._show_chatter.setStyleSheet(f"color: {colour}; font-weight: 600;")
        self._show_chatter.setText("Show log  (something went wrong)")

    def _unflag_log(self) -> None:
        self._show_chatter.setStyleSheet("")
        self._show_chatter.setText("Show log")

    def _on_log_toggled(self, shown: bool) -> None:
        if shown:
            # Seen. The mark has done its job.
            self._unflag_log()
        self._redraw()

    #: What lives in the log rather than in the game: the app talking about
    #: itself. A refusal, a roll, an agent that could not answer are *notices*
    #: and are never in here -- they are the reason someone is reading.
    LOG_KINDS = ("chatter", "error")

    def _is_hidden(self, kind: str) -> bool:
        return kind in self.LOG_KINDS and not self._show_chatter.isChecked()

    def _redraw(self) -> None:
        """Rebuild the whole log, which is what makes the filter reversible."""
        self._log.clear()
        self._roll_prompts.clear()
        self._translations.clear()
        for line in self._entries:
            if not self._is_hidden(line.kind):
                self._draw(line.kind, line.text, line.when, line.said_by)

    def _draw(
        self,
        kind: str,
        text: str,
        when: float | None = None,
        said_by: str = "",
    ) -> None:
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        stamp = QTextCharFormat()
        stamp.setForeground(self._colours["system"])
        body = QTextCharFormat()
        body.setForeground(self._colours.get(kind, self._colours["system"]))
        if kind in ("roll", "error", "turn"):
            body.setFontWeight(600)

        if not self._log.document().isEmpty():
            cursor.insertBlock()
        moment = datetime.fromtimestamp(when) if when else datetime.now()
        cursor.insertText(f"{moment:%H:%M}  ", stamp)
        self._insert_body(cursor, kind, text, body)
        self._offer_translation(cursor, kind, text, said_by, body)
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

    # ---------------------------------------------------------- rolls in chat
    #
    # "Make a DC 14 Perception check" is an instruction, and every player then
    # does the same arithmetic by hand. The words become a link, clicking it
    # opens a die, and the die asks the host -- so the convenience is in the
    # looking-up, never in the number.

    #: Whose words are read for rolls. The DM's, and the agent's when it is
    #: standing in for them. Not a player's: someone typing "stealth check" in
    #: character is not asking the table to roll.
    ASKS_FOR_ROLLS = (Role.DM.value, Role.AGENT.value)

    def _insert_body(self, cursor, kind: str, text: str, body) -> None:
        prompts = self._prompts_in(kind, text)
        if not prompts:
            cursor.insertText(text, body)
            return

        at = 0
        for prompt in prompts:
            if prompt.start > at:
                cursor.insertText(text[at : prompt.start], body)
            token = len(self._roll_prompts) + 1
            self._roll_prompts[token] = prompt
            cursor.insertText(
                text[prompt.start : prompt.end], self._link_format(body, token)
            )
            at = prompt.end
        cursor.insertText(text[at:], body)

    def _prompts_in(self, kind: str, text: str) -> list:
        """The rolls asked for in one line, if this reader can answer them.

        No character, no links. A DM watching their own table has nothing to
        roll with, and offering them a die for every prompt they wrote would be
        noise on the one screen that is already busiest.
        """
        if kind not in self.ASKS_FOR_ROLLS:
            return []
        if self._own_character() is None:
            return []
        return rolls.find(text)

    def _link_format(self, body, token: int) -> QTextCharFormat:
        link = QTextCharFormat(body)
        link.setAnchor(True)
        link.setAnchorHref(f"roll:{token}")
        link.setForeground(self._colours.get("roll", self._colours["system"]))
        link.setFontUnderline(True)
        link.setToolTip("Click to roll this")
        return link

    # ------------------------------------------------- a line, as a turn
    #
    # "I get behind the orc and hit it with my axe" is a turn, and somebody has
    # to work out which square that is. The agent already can -- `propose_turn`
    # is the whole of it -- but only ever decided to on its own, after a pause,
    # with autopilot on. This is the other way round: the DM reads the line,
    # points at it, and asks. Nothing happens to the character either way until
    # the player accepts, which is what makes an offer safe to hand out.

    #: The mark itself. A glyph rather than an icon so it inherits the log's
    #: font and colour and needs nothing shipped alongside it.
    TRANSLATE_MARK = "  ⚔"

    def _offer_translation(
        self, cursor, kind: str, text: str, said_by: str, body
    ) -> None:
        """Put a mark beside a line that could be taken as somebody's turn."""
        if not self._may_translate(kind, said_by):
            return
        token = len(self._translations) + 1
        self._translations[token] = (said_by, self._spoken_part(text))
        mark = QTextCharFormat(body)
        mark.setAnchor(True)
        mark.setAnchorHref(f"translate:{token}")
        mark.setForeground(self._colours.get("turn", self._colours["system"]))
        mark.setToolTip("Ask the agent to write this as their turn")
        cursor.insertText(self.TRANSLATE_MARK, mark)

    def _may_translate(self, kind: str, said_by: str) -> bool:
        """Whether this line, on this screen, is worth offering.

        Four things have to be true, and the fourth is the one worth saying:
        the speaker has to be the character whose turn it is. Offering it
        beside everybody's line would mean most clicks came back refused, and a
        mark that usually does not work is worse than no mark.

        Matched on the name the host put in the roster against the name the
        host put on the entity. Both come from the host and a mismatch only
        ever costs a mark that is not drawn -- the click itself is checked
        against the *account* that plays the character, which is the half of it
        a client cannot see.
        """
        if kind != Role.PLAYER.value or not said_by:
            return False
        if not self._translates_turns():
            return False
        me = self._client.me
        if me is None or me.role != Role.DM.value:
            return False
        acting = self._whose_turn()
        if not acting:
            return False
        speaker = next(
            (m for m in self._client.members if m.id == said_by), None
        )
        return speaker is not None and bool(speaker.character) and (
            speaker.character == acting
        )

    def _translates_turns(self) -> bool:
        return self._ctx.repos.settings.get(TRANSLATE_SETTING, "") == "on"

    def _whose_turn(self) -> str:
        """The name of the creature whose turn it is, or empty if none is."""
        turn = self._fight.get("turn")
        if not self._fight.get("running") or turn is None:
            return ""
        acting = next(
            (
                c
                for c in self._fight.get("combatants") or []
                if c.get("id") == turn
            ),
            None,
        )
        if acting is None or self._ctx.shared is None:
            return ""
        entity = self._ctx.shared.get(acting.get("entity"))
        return str((entity or {}).get("name", ""))

    @staticmethod
    def _spoken_part(text: str) -> str:
        """The line without the "Brok: " the log puts in front of it.

        Sent rather than the whole thing because the agent is being asked what
        somebody meant, and a name it already knows, glued to the front by the
        panel that displays it, is not part of what they said.
        """
        _, _, rest = text.partition(": ")
        return (rest or text).strip()

    def _on_translation_asked(self, token: int) -> None:
        said_by, text = self._translations.get(token, ("", ""))
        if not said_by or not text:
            return
        if self._client.send_translate(said_by, text):
            # Said, because the answer arrives as a turn on somebody else's
            # screen. Without this the DM's own click has no visible effect at
            # all on the one screen they are looking at.
            self._append("system", "Asked the agent to write that as a turn.")

    def _on_shared_changed(self) -> None:
        """Gaining -- or losing -- a character changes what the log offers.

        Lines already on screen were drawn before we knew, so the log is
        rebuilt. Only on the change, not on every entity that arrives: a
        snapshot of forty characters would otherwise redraw the log forty
        times.
        """
        has_one = self._own_character() is not None
        if has_one != self._had_character:
            self._had_character = has_one
            self._redraw()

    def _own_character(self) -> dict | None:
        """The character this app is playing, or None for a DM or a spectator."""
        if self._ctx.shared is None:
            return None
        return self._ctx.shared.own_character()

    def _on_anchor(self, url: QUrl) -> None:
        scheme = url.scheme()
        if scheme not in ("roll", "translate"):
            return
        try:
            token = int(url.path() or url.toString().split(":", 1)[1])
        except (TypeError, ValueError):
            return
        if scheme == "translate":
            self._on_translation_asked(token)
            return
        prompt = self._roll_prompts.get(token)
        if prompt is not None:
            self._open_die(prompt)

    def _open_die(self, prompt) -> None:
        character = self._own_character() or {}
        sheet = (character.get("data") or {}).get("sheet") or {}
        bonus = rolls.bonus_for(prompt, sheet, self._rules_content())
        notation = rolls.notation_for(prompt, bonus)

        if prompt.kind == rolls.DICE:
            note = f"{prompt.notation} as asked for."
        elif bonus:
            note = f"{character.get('name', 'You')} adds {bonus:+d}."
        else:
            note = (
                f"{character.get('name', 'You')} has no bonus recorded for this, "
                "so it is a plain d20."
            )

        if prompt.kind == rolls.DEATH:
            note = "No modifier: a death save is a bare d20."

        dialog = RollDialog(prompt.label, notation, note, prompt.dc, self)
        # A death save is not an ordinary roll that happens to be a d20. It is
        # recorded against the character -- three of them either way decide it
        # -- so it goes to the host as the thing it is, and the host checks
        # that this login is in fact the one that owes it.
        if prompt.kind == rolls.DEATH:
            dialog.roll_requested.connect(lambda _n: self._client.send_death_save())
        else:
            dialog.roll_requested.connect(self._roll)
        self._roll_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._roll_dialog = None

    def _rules_content(self):
        if self._content is None:
            from canon_keeper_core.content import Content

            self._content = Content(self._ctx.repos.settings)
        return self._content

    def _append_system(self, text: str, kind: str = "") -> None:
        """A line from the host. ``kind`` distinguishes chatter from a notice."""
        self._append("chatter" if kind == SystemKind.CHATTER.value else "system", text)

    def _update_state(self) -> None:
        connected = self._client.is_connected
        hosting = self._server is not None and self._server.is_running
        # Published for anything that has to hand somebody an address without
        # being able to see the server -- an invite made from a right-click in
        # another panel, most of all. Panels do not import each other, so this
        # is how one panel's fact reaches the rest.
        self._ctx.session_address = self.current_address()

        is_dm = self._ctx.role == Role.DM.value
        self._host_button.setEnabled(is_dm and not connected and not hosting)
        self._host_button.setVisible(is_dm)
        # Only the host can hand over what it is hosting.
        self._autopilot_button.setVisible(is_dm)
        self._autopilot_button.setEnabled(hosting)
        # Reachable without hosting: setting the key up front is a reasonable
        # thing to do before a session rather than during one.
        self._agent_button.setVisible(is_dm)
        self._invite_button.setVisible(is_dm)
        self._invite_button.setEnabled(hosting)
        self._join_button.setVisible(not is_dm)
        self._join_button.setEnabled(not connected and not hosting)
        # A funnel left over from a previous run counts: it is the one thing on
        # this screen that is genuinely still on, and Go offline is what ends
        # it. Without this the button is greyed out beside a line saying the
        # machine is published, which is the app arguing with itself.
        self._leave_button.setEnabled(connected or hosting or self._anything_published)
        # Held while a turn is waiting on you: there are three ways to answer
        # it and carrying on chatting is not one of them.
        self._entry.setEnabled(connected and not self._holding_for_an_answer())

        if hosting and self._server is not None:
            if self._funnel_url:
                self._status.setText(
                    f"Hosting on port {self._server.port}, and published at "
                    f"{self._funnel_url} for players anywhere."
                )
            else:
                self._status.setText(
                    f"Hosting on port {self._server.port}. Players on this network "
                    "will see it listed and log in with the accounts you gave them."
                )
        elif connected:
            me = self._client.me
            self._status.setText(f"Connected as {me.name}." if me else "Connected.")
        elif self._anything_published:
            self._status.setText(
                "Not hosting, but this machine may still be published to the "
                "internet from a previous session. Go offline takes it down."
            )
        else:
            self._status.setText("Not connected.")

    def shutdown(self) -> None:
        """Close the socket, stop hosting, and unpublish. Called when the app exits."""
        # Before the socket goes: an agent left running against a dead session
        # is a process nobody knows to kill.
        self._stop_agent()
        # Before the socket goes: a stand-in left running against a dead
        # session is a process nobody knows to kill.
        self._stand_ins.stop_all()
        self._dictation_timer.stop()
        self._dictation.cancel()
        self._client.leave()
        # Asked, not confirmed. `tailscale funnel --bg` takes effect in the
        # daemon rather than in this process, so a publish still in flight when
        # the app closes leaves the session on the public internet with nothing
        # here holding its address -- and the next launch, which starts with an
        # empty one, has no idea it is there.
        if self._anything_published:
            funnel.stop()
            funnel.forget()
            self._funnel_url = ""
            self._publishing = False
        if self._server is not None:
            self._server.stop()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.shutdown()
        super().closeEvent(event)


__all__ = ["TableWidget", "DEFAULT_PORT", "discovery"]
