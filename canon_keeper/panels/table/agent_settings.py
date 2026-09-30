"""Where the agent's key and model are set.

Autopilot needs a key you supply, and the first version asked for it with a bare
one-line prompt. That was enough to get started and wrong to keep: a key typed
into a box with no explanation, no way to see what you typed, and -- the actual
bug -- no way to change it afterwards. A mistyped key was permanent, and the
only symptom was an agent that never answered.

So: one dialog, reachable whether or not a key is already set, that says what
the key is for, where it goes, and roughly what it costs.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from canon_keeper import agent_runner, credentials

CONSOLE_URL = "https://platform.claude.com/settings/keys"
WORKSPACES_URL = "https://platform.claude.com/settings/workspaces"

#: Setting key for the model. Per campaign rather than per machine: a one-shot
#: and a long campaign are not obviously the same choice.
MODEL_SETTING = "agent.model"

#: Setting key for the translate offer. Off by default: it puts a small mark
#: beside other people's lines in the log, and a feature that quietly decorates
#: the chat of a DM who never asked for it is one they have to go and turn off.
TRANSLATE_SETTING = "agent.translates_turns"

#: What to offer, plainest first. The list is short on purpose -- this is a
#: choice between "good" and "cheap", not a catalogue.
MODELS = (
    ("claude-opus-5", "Opus 5 — the best of them"),
    ("claude-sonnet-5", "Sonnet 5 — cheaper, still good"),
    ("claude-haiku-4-5", "Haiku 4.5 — fastest and cheapest"),
)


class AgentSettingsDialog(QDialog):
    """The key, the model, and what happens to both."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle("Agent")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        blurb = QLabel(
            "Autopilot answers using Anthropic's models, with a key you supply. "
            "A session's worth of answering costs on the order of a pound or two."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        form = QFormLayout()

        self._key = QLineEdit(agent_runner.api_key())
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText("sk-ant-...")

        self._show = QCheckBox("Show")
        self._show.toggled.connect(
            lambda on: self._key.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.addWidget(self._key, 1)
        key_row.addWidget(self._show)
        form.addRow("Key", key_row)

        # Some keys -- identity-linked ones -- are refused outright without the
        # id of the workspace they belong to. Nothing here can detect that in
        # advance; the API says so plainly, and this is where the answer goes.
        self._workspace = QLineEdit(agent_runner.workspace_id())
        self._workspace.setPlaceholderText("wrkspc_...  (usually not needed)")
        self._workspace.setToolTip(
            "Console > Settings > Workspaces. Only a key with access to several "
            "workspaces needs to name one; a key scoped to a single workspace "
            "does not."
        )
        workspaces = QPushButton("Find it")
        workspaces.setMaximumWidth(80)
        workspaces.clicked.connect(
            lambda: QDesktopServices.openUrl(WORKSPACES_URL)  # type: ignore[arg-type]
        )
        workspace_row = QHBoxLayout()
        workspace_row.addWidget(self._workspace, 1)
        workspace_row.addWidget(workspaces)
        form.addRow("Workspace id", workspace_row)

        self._model = QComboBox()
        for value, label in MODELS:
            self._model.addItem(label, value)
        current = ctx.repos.settings.get(MODEL_SETTING, MODELS[0][0])
        index = self._model.findData(current)
        self._model.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Model", self._model)

        layout.addLayout(form)

        self._translate = QCheckBox("Offer to turn what a player says into a turn")
        self._translate.setChecked(
            ctx.repos.settings.get(TRANSLATE_SETTING, "") == "on"
        )
        self._translate.setToolTip(
            "While a fight is running, a mark appears beside what the player "
            "whose turn it is has said. Clicking it asks the agent to write "
            "that as a move and an attack, and puts it to them to accept.\n\n"
            "It costs a model call each time you click it, and nothing when "
            "you do not. Autopilot does not have to be on."
        )
        layout.addWidget(self._translate)

        where = QLabel(self._where_it_goes())
        where.setWordWrap(True)
        where.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(where)

        # Said here because the API error that sends people to this field does
        # not mention the simpler answer.
        note = QLabel(
            "A workspace id is only needed by a key that can reach several "
            "workspaces. If yours is refused without one, the simplest fix is a "
            "new key scoped to a single workspace — then leave this blank."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        get_one = QPushButton("Get a key...")
        get_one.clicked.connect(
            lambda: QDesktopServices.openUrl(CONSOLE_URL)  # type: ignore[arg-type]
        )
        layout.addWidget(get_one)

        self._forget = QPushButton("Forget this key")
        self._forget.clicked.connect(self._on_forget)
        self._forget.setEnabled(bool(agent_runner.api_key()))
        layout.addWidget(self._forget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _where_it_goes() -> str:
        if credentials.is_available():
            return (
                "The key goes to your operating system's credential store, never "
                "to the campaign file — so copying a campaign to another machine "
                "does not carry your key with it.\n\n"
                "An ANTHROPIC_API_KEY in your environment is used in preference "
                "to this one."
            )
        return (
            "This machine has no usable credential store, so the key cannot be "
            "kept. It will be used for this run only. To avoid retyping it, set "
            "ANTHROPIC_API_KEY in your environment instead."
        )

    @property
    def key(self) -> str:
        return self._key.text().strip()

    @property
    def workspace(self) -> str:
        return self._workspace.text().strip()

    @property
    def model(self) -> str:
        return self._model.currentData() or MODELS[0][0]

    @property
    def translates_turns(self) -> bool:
        return self._translate.isChecked()

    # ------------------------------------------------------------------ actions

    def _on_forget(self) -> None:
        credentials.forget("anthropic://api", "key")
        credentials.forget("anthropic://api", "workspace")
        self._key.clear()
        self._workspace.clear()
        self._forget.setEnabled(False)

    def _on_save(self) -> None:
        key = self.key
        # Checked rather than trusted: the usual mistake is pasting the key name
        # from the console instead of the key, and the only symptom otherwise is
        # an agent that never answers.
        if key and not key.startswith("sk-"):
            answer = QMessageBox.question(
                self,
                "That does not look like a key",
                "Anthropic keys start with 'sk-'. Save it anyway?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save:
                return

        self._ctx.repos.settings.set(MODEL_SETTING, self.model)
        self._ctx.repos.settings.set(
            TRANSLATE_SETTING, "on" if self.translates_turns else "off"
        )
        if key:
            agent_runner.remember_api_key(key)
        # Saved even when blank, so clearing it actually clears it.
        agent_runner.remember_workspace_id(self.workspace)
        self.accept()
