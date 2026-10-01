"""Host, join, and manage who may log in.

The join dialog listens for LAN beacons and lists what it hears, so on a home
network nobody has to read an IP address aloud -- pick the session, then log in.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from canon_keeper import campaigns, credentials
from canon_keeper.net import discovery
from canon_keeper_protocol import enrol
from canon_keeper_protocol.auth import AuthError
from canon_keeper.net.server import DEFAULT_PORT
from canon_keeper_core.repo.entities import KIND_PC
from canon_keeper_core.repo.invites import already_played

_URL_ROLE = 256


def local_addresses() -> list[str]:
    """This machine's LAN addresses, for telling players what to type."""
    return sorted(a for a in discovery.local_addresses() if not a.startswith("127."))


class HostDialog(QDialog):
    """Start hosting the currently open campaign."""

    def __init__(self, campaign_name: str, default_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Host a session")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit(default_name)
        form.addRow("Your name", self._name)

        self._session = QLineEdit(campaign_name)
        form.addRow("Session name", self._session)

        self._port = QSpinBox()
        self._port.setRange(1024, 65535)
        self._port.setValue(DEFAULT_PORT)
        form.addRow("Port", self._port)
        layout.addLayout(form)

        addresses = local_addresses()
        hint = QLabel(
            f"Sharing the campaign {campaign_name!r}. Players on this network will "
            "see the session listed and log in with the accounts you gave them.\n"
            + (f"If they need to type it: {', '.join(addresses)}" if addresses else "")
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, int, str]:
        return (
            self._name.text().strip() or "Dungeon Master",
            self._port.value(),
            self._session.text().strip() or "Canon Keeper session",
        )


_JOIN_NOTE = (
    "Your password is never sent over the network -- the host asks a question "
    "only someone who knows it can answer. Once it works it is kept in this "
    "computer's credential store, so you do not type it again."
)


class JoinDialog(QDialog):
    def __init__(self, last_url: str = "", last_username: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Join a session")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sessions on this network:"))

        self._found = QListWidget()
        self._found.setMaximumHeight(110)
        self._found.itemSelectionChanged.connect(self._use_selected)
        layout.addWidget(self._found)

        form = QFormLayout()
        self._url = QLineEdit(last_url or "ws://192.168.1.10:8765")
        self._url.setToolTip("Filled in for you when you pick a session above.")
        form.addRow("Address", self._url)

        self._username = QLineEdit(last_username)
        form.addRow("Username", self._username)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.returnPressed.connect(self.accept)
        form.addRow("Password", self._password)

        # First time in, there is no account yet: the DM sent a code for a
        # character, and this is where it turns into a login. Empty for
        # everybody who already has one, which is everybody after the first
        # evening.
        self._code = QLineEdit()
        self._code.setPlaceholderText("Paste the whole invite your DM sent")
        self._code.setToolTip(
            "Paste the whole line -- it carries the address as well, and fills "
            "it in above. Then choose any username and password you like: the "
            "DM never sees the password."
        )
        self._code.textChanged.connect(self._on_code_changed)
        form.addRow("Invite code", self._code)
        layout.addLayout(form)

        self._note = QLabel(_JOIN_NOTE)
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._listener = discovery.Listener(self)
        self._listener.sessions_changed.connect(self._on_sessions)
        if not self._listener.start():
            self._found.addItem(
                QListWidgetItem("(could not listen for sessions - type the address)")
            )

        self._load_saved()

    def _load_saved(self) -> None:
        """Fill in the password for a session already joined once."""
        url, username = self._url.text().strip(), self._username.text().strip()
        if not url or not username:
            return
        saved = credentials.load(url, username)
        if saved:
            self._password.setText(saved)

    def _on_sessions(self, sessions) -> None:
        selected = self._found.currentItem()
        selected_url = selected.data(_URL_ROLE) if selected is not None else None

        self._found.clear()
        for session in sessions:
            item = QListWidgetItem(f"{session.name}  -  {session.host}:{session.port}")
            item.setData(_URL_ROLE, session.url)
            self._found.addItem(item)
            if session.url == selected_url:
                self._found.setCurrentItem(item)

        if not sessions:
            self._found.addItem(QListWidgetItem("(looking for sessions...)"))

    def _use_selected(self) -> None:
        item = self._found.currentItem()
        if item is not None and item.data(_URL_ROLE):
            self._url.setText(item.data(_URL_ROLE))
            for remembered in campaigns.list_remote():
                if remembered.url == item.data(_URL_ROLE) and remembered.username:
                    self._username.setText(remembered.username)
                    break
            self._load_saved()

    def _on_code_changed(self, raw: str) -> None:
        """Take an invite apart, and say what it is about to do.

        A whole invite is one line -- ``ws://host:port#CODE`` -- so pasting it
        here fills the address in as well. That is the whole reason it is one
        line: the address is the half people mistype, and a player who has just
        been sent something should not have to work out which part of it goes
        where.

        Enrolment is the one irreversible thing in this dialog: the invite is
        spent whether or not the username was the one they meant.
        """
        address, code = enrol.unwrap(raw)
        if address and code:
            self._url.setText(address)
            # Put back only the code, so the box shows what it is holding
            # rather than a line half of which has moved.
            self._code.setText(code)
            return

        if enrol.clean_code(raw):
            self._note.setText(
                "With an invite code, this makes a <b>new account</b> from the "
                "username and password above and attaches it to the character "
                "your DM invited. Your password is never sent -- not to the "
                "network, and not to your DM."
            )
        else:
            self._note.setText(_JOIN_NOTE)

    def values(self) -> tuple[str, str, str]:
        return (
            self._url.text().strip(),
            self._username.text().strip(),
            self._password.text(),
        )

    def invite_code(self) -> str:
        """The code, tidied, or empty for an ordinary login."""
        return enrol.clean_code(self._code.text())

    def done(self, result: int) -> None:  # noqa: D102 - Qt naming
        self._listener.stop()
        super().done(result)


class AccountsDialog(QDialog):
    """Who may log in, and which character each of them plays."""

    def __init__(self, ctx, parent=None, address: str = "") -> None:
        super().__init__(parent)
        self._ctx = ctx
        #: Where this session is, so an invite can carry it. Empty when the DM
        #: is not hosting yet, which makes the code the whole invite.
        self._address = address
        self.setWindowTitle("Players")
        self.resize(520, 380)

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "Everybody gets in by invitation. Pick a character below and press "
            "<b>Invite a player...</b> for a code to send them - they choose "
            "their own username and password, and you never see it. Sending a "
            "new code for a character they already play hands it over, which is "
            "also how somebody who has lost their password gets back in."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(lambda *_: self._load_selected())
        layout.addWidget(self._list, 1)

        form = QFormLayout()
        self._username = QLineEdit()
        self._username.setReadOnly(True)
        self._username.setPlaceholderText("(chosen by whoever takes the invite)")
        form.addRow("Username", self._username)

        self._character = QComboBox()
        self._character.setToolTip(
            "The character this login plays: they own it, see its whole sheet, "
            "and the table sees its name in chat."
        )
        form.addRow("Plays", self._character)

        self._is_dm = QCheckBox("Give this login the DM's full view")
        form.addRow("", self._is_dm)
        layout.addLayout(form)

        row = QHBoxLayout()
        add = QPushButton("Change character")
        add.setToolTip("Move the selected login onto a different character.")
        add.clicked.connect(self._save)
        row.addWidget(add)

        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)

        invite_row = QHBoxLayout()
        invite = QPushButton("Invite a player...")
        invite.setToolTip(
            "Make a code for the character chosen above. Whoever you send it "
            "to picks their own username and password -- you never see it."
        )
        invite.clicked.connect(self._invite)
        invite_row.addWidget(invite)
        self._invite_note = QLabel("")
        self._invite_note.setWordWrap(True)
        invite_row.addWidget(self._invite_note, 1)
        layout.addLayout(invite_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._reload()

    # ------------------------------------------------------------------ helpers

    def _reload(self) -> None:
        self._character.clear()
        self._character.addItem("(no character)", None)
        for pc in self._ctx.repos.entities.list(self._ctx.campaign_id, kinds=(KIND_PC,)):
            self._character.addItem(pc.name, pc.id)

        self._list.clear()
        # Characters with a code out and nobody yet. Listed with the logins
        # rather than somewhere else, because "who can get in" is one question
        # and an unanswered invite is half an answer to it.
        for pc in self._ctx.repos.entities.list(self._ctx.campaign_id, kinds=(KIND_PC,)):
            waiting = self._ctx.repos.invites.waiting_for(pc.id)
            if waiting is None:
                continue
            item = QListWidgetItem(f"{pc.name}   [invited - {waiting.code}]")
            item.setData(_URL_ROLE, None)
            item.setForeground(self.palette().placeholderText())
            self._list.addItem(item)

        for account in self._ctx.repos.accounts.list(self._ctx.campaign_id):
            character = ""
            if account.character_entity_id is not None:
                entity = self._ctx.repos.entities.get(account.character_entity_id)
                character = f"  -  {entity.name}" if entity else ""
            label = f"{account.username}{character}"
            if account.is_dm:
                label += "   [DM]"
            item = QListWidgetItem(label)
            item.setData(_URL_ROLE, account.id)
            self._list.addItem(item)

    def _selected_account(self):
        item = self._list.currentItem()
        if item is None or item.data(_URL_ROLE) is None:
            return None  # a waiting invite, which is not a login yet
        return self._ctx.repos.accounts.get(item.data(_URL_ROLE))

    def _load_selected(self) -> None:
        account = self._selected_account()
        if account is None:
            # A waiting invite rather than a login. Clear the name so the form
            # is not showing somebody the buttons would not act on.
            self._username.clear()
            return
        self._username.setText(account.username)
        index = self._character.findData(account.character_entity_id)
        self._character.setCurrentIndex(index if index >= 0 else 0)
        self._is_dm.setChecked(account.is_dm)

    # ------------------------------------------------------------------ actions

    def _invite(self) -> None:
        """A code for the chosen character, and the last one stops working.

        No password is typed here by anybody. That is the point: a DM who sets
        a player's password knows it, and then "do not reuse your password" is
        advice they are not in a position to give.
        """
        repos = self._ctx.repos
        entity_id = self._character.currentData()
        if entity_id is None:
            QMessageBox.information(
                self,
                "Invite a player",
                "Choose which character they will play first.",
            )
            return

        entity = repos.entities.get(entity_id)
        name = entity.name if entity is not None else "that character"
        handover = already_played(repos.accounts, self._ctx.campaign_id, entity_id)
        if handover and QMessageBox.question(
            self,
            "Invite a player",
            f"{name} already has a player.\n\nA new code hands the character "
            "over: whoever uses it chooses a new username and password, and the "
            "login playing them now stops working. That is also how somebody "
            "who has lost their password gets back in.\n\nMake the code?",
        ) != QMessageBox.StandardButton.Yes:
            return

        replacing = repos.invites.waiting_for(entity_id) is not None
        invite = repos.invites.create(self._ctx.campaign_id, entity_id)
        # One string, holding where to connect as well as the code. Two things
        # to copy is two things to get wrong, and the address is the half people
        # mistype.
        whole = enrol.wrap(self._address, invite.code)
        QApplication.clipboard().setText(whole)
        self._invite_note.setText(
            f"<b>{whole}</b> copied - it plays {name}, and it is good for 24 "
            "hours. Send the whole line: it is the address and the code."
            + (
                "" if self._address else
                " <b>You are not hosting yet</b>, so it carries only the code -"
                " make another once you are online, or send the address too."
            )
            + (" The code you made before this one no longer works." if replacing else "")
            + (
                " The login playing them now keeps working until this code is "
                "used." if handover else ""
            )
        )
        self._reload()

    def _save(self) -> None:
        """Change which character an existing login plays. It cannot make one.

        Nobody's password is typed here any more. A DM who set a player's
        password knew it, which made "do not reuse this one" advice they were
        not in a position to give -- and the first thing a new table needs is a
        way in that does not go through somebody else's memory. Everybody
        arrives by invitation; see :meth:`_invite`.
        """
        account = self._selected_account()
        if account is None:
            QMessageBox.information(
                self,
                "Players",
                "Pick a login to change, or use Invite a player... to make one.",
            )
            return

        repos = self._ctx.repos
        chosen = self._character.currentData()
        try:
            repos.accounts.set_character(account.id, chosen)
            if chosen is not None:
                # Playing a character and owning it are the same intent here.
                repos.entities.set_owner(chosen, account.id)
        except (ValueError, AuthError) as exc:
            QMessageBox.warning(self, "Players", str(exc))
            return

        self._reload()

    def _remove(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        confirm = QMessageBox.question(
            self, "Players", f"Remove the login {account.username!r}?"
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._ctx.repos.accounts.delete(account.id)
            self._reload()
