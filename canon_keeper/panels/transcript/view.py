"""The transcript display: a text view, not a table.

A table cannot do either of the two things this needs. You cannot select three
words inside a cell, and a cell cannot render part of its text in a different
colour. A QTextEdit does both natively, so the transcript is one document with
one block per utterance.

Block N always corresponds to utterance N in ``_utterance_ids``. The document is
rebuilt wholesale on every change rather than patched in place -- a session's
worth of lines is small, and it removes any chance of the mapping drifting out
of step after a deletion.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QMenu, QTextEdit

from canon_keeper.matching import EntityMatcher
from canon_keeper_core.repo.entities import (
    KIND_FACTION,
    KIND_ITEM,
    KIND_LOCATION,
    KIND_NPC,
    KIND_PC,
)

#: "HH:MM:SS" plus two spaces. Fixed width, so the offset from the start of a
#: block to the start of the utterance text is a constant.
TIME_FORMAT = "%H:%M:%S"
PREFIX_LENGTH = 10

#: QTextEdit reports paragraph breaks with this, not a newline.
PARAGRAPH_SEPARATOR = chr(0x2029)

PENDING_MARKERS = frozenset(
    {"(transcribing...)", "(nothing was heard)", "(transcription failed)"}
)

#: What the right-click menu offers to create. Order matters: these are the two
#: things a DM writes down mid-session, so they come first.
ADDABLE_KINDS = (
    (KIND_NPC, "Character"),
    (KIND_LOCATION, "Place"),
    (KIND_FACTION, "Faction"),
    (KIND_ITEM, "Item"),
)

# Mid-tone hues, chosen to stay legible against both a white and a dark page.
_LIGHT_COLOURS = {
    KIND_NPC: "#1a5fb4",
    KIND_PC: "#1a5fb4",
    KIND_LOCATION: "#1c7048",
    KIND_FACTION: "#7a3fa0",
    KIND_ITEM: "#8a6100",
}
_DARK_COLOURS = {
    KIND_NPC: "#7aa2f7",
    KIND_PC: "#7aa2f7",
    KIND_LOCATION: "#7fd1a0",
    KIND_FACTION: "#c9a0ff",
    KIND_ITEM: "#e5c07b",
}


class EntityHighlighter(QSyntaxHighlighter):
    """Colours the timestamp, greys pending lines, and lights up known names.

    Everything it needs is derivable from the block text, so there is no
    per-block state to keep in step with the document.
    """

    def __init__(self, document, matcher: EntityMatcher, is_dark: bool) -> None:
        super().__init__(document)
        self.matcher = matcher
        self._time_format = QTextCharFormat()
        self._pending_format = QTextCharFormat()
        self._kind_formats: dict[str, QTextCharFormat] = {}
        self.set_theme(is_dark)

    def set_theme(self, is_dark: bool) -> None:
        muted = QColor("#8b8b8b" if is_dark else "#767676")
        self._time_format.setForeground(muted)
        self._pending_format.setForeground(muted)
        self._pending_format.setFontItalic(True)

        palette = _DARK_COLOURS if is_dark else _LIGHT_COLOURS
        self._kind_formats = {}
        for kind, colour in palette.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colour))
            fmt.setFontWeight(600)
            self._kind_formats[kind] = fmt

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt naming
        self.setFormat(0, min(PREFIX_LENGTH, len(text)), self._time_format)

        body = text[PREFIX_LENGTH:]
        if body.strip() in PENDING_MARKERS:
            self.setFormat(PREFIX_LENGTH, len(body), self._pending_format)
            return

        for match in self.matcher.finditer(body):
            fmt = self._kind_formats.get(match.kind)
            if fmt is not None:
                self.setFormat(
                    PREFIX_LENGTH + match.start, match.end - match.start, fmt
                )


class TranscriptView(QTextEdit):
    """Read-only transcript with a context menu that acts on the selection."""

    add_entity_requested = Signal(str, str)  # (name, kind)
    open_entity_requested = Signal(int)
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    retry_requested = Signal(int)

    def __init__(self, matcher: EntityMatcher, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setUndoRedoEnabled(False)
        self.setTabChangesFocus(True)

        self._utterance_ids: list[int] = []
        self.matcher = matcher
        self.highlighter = EntityHighlighter(
            self.document(), matcher, self._is_dark_theme()
        )

    def _is_dark_theme(self) -> bool:
        base = self.palette().base().color()
        return base.lightness() < 128

    def set_dark(self, is_dark: bool) -> None:
        """Repaint for a new appearance. Driven by ``bus.theme_changed``."""
        self.highlighter.set_theme(is_dark)
        self.highlighter.rehighlight()

    def set_matcher(self, matcher: EntityMatcher) -> None:
        """Swap the name index and repaint. Called whenever entities change."""
        self.matcher = matcher
        self.highlighter.matcher = matcher
        self.highlighter.rehighlight()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        if event.type() == event.Type.PaletteChange:
            self.highlighter.set_theme(self._is_dark_theme())
            self.highlighter.rehighlight()

    # ----------------------------------------------------------------- content

    def render_utterances(self, utterances, scroll_to_end: bool = True) -> None:
        scrollbar = self.verticalScrollBar()
        previous_scroll = scrollbar.value()

        self.clear()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for index, utterance in enumerate(utterances):
            if index:
                cursor.insertBlock()
            stamp = datetime.fromtimestamp(utterance.t).strftime(TIME_FORMAT)
            text = utterance.text or "(transcribing...)"
            cursor.insertText(f"{stamp}  {text}")

        self._utterance_ids = [utterance.id for utterance in utterances]

        if scroll_to_end:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(previous_scroll, scrollbar.maximum()))

    def line_text(self, utterance_id: int) -> str:
        """The utterance text of a line, without the timestamp."""
        block_number = self._block_number_of(utterance_id)
        if block_number is None:
            return ""
        block = self.document().findBlockByNumber(block_number)
        return block.text()[PREFIX_LENGTH:]

    def _block_number_of(self, utterance_id: int) -> int | None:
        try:
            return self._utterance_ids.index(utterance_id)
        except ValueError:
            return None

    def _utterance_at(self, position) -> int | None:
        cursor = self.cursorForPosition(position)
        number = cursor.blockNumber()
        if 0 <= number < len(self._utterance_ids):
            return self._utterance_ids[number]
        return None

    # -------------------------------------------------------------------- menu

    def _selection_for_menu(self, position) -> str:
        """What the menu should act on.

        An existing selection wins. Otherwise the word under the pointer is
        used, so right-clicking a name offers to add it without dragging first.
        """
        cursor = self.textCursor()
        if cursor.hasSelection():
            return self._clean(cursor.selectedText())

        cursor = self.cursorForPosition(position)
        if cursor.positionInBlock() < PREFIX_LENGTH:
            return ""  # they clicked the timestamp
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        return self._clean(cursor.selectedText())

    @staticmethod
    def _clean(text: str) -> str:
        text = text.replace(PARAGRAPH_SEPARATOR, ' ').strip()
        return text.strip(" \t.,;:!?\"'()[]-")

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        menu = QMenu(self)
        selection = self._selection_for_menu(event.pos())
        utterance_id = self._utterance_at(event.pos())

        if selection:
            known = self.matcher.lookup(selection)
            if known is not None:
                open_action = menu.addAction(f"Go to {known.name}")
                open_action.triggered.connect(
                    lambda _c=False, eid=known.entity_id: self.open_entity_requested.emit(eid)
                )
            else:
                shown = selection if len(selection) <= 40 else selection[:37] + "..."
                submenu = menu.addMenu(f'Add "{shown}" as')
                for kind, label in ADDABLE_KINDS:
                    action = submenu.addAction(label)
                    action.triggered.connect(
                        lambda _c=False, k=kind, name=selection: self.add_entity_requested.emit(
                            name, k
                        )
                    )
            menu.addSeparator()

        if utterance_id is not None:
            edit = menu.addAction("Edit this line...")
            edit.triggered.connect(
                lambda _c=False, uid=utterance_id: self.edit_requested.emit(uid)
            )
            retry = menu.addAction("Transcribe again")
            retry.triggered.connect(
                lambda _c=False, uid=utterance_id: self.retry_requested.emit(uid)
            )
            delete = menu.addAction("Delete this line")
            delete.triggered.connect(
                lambda _c=False, uid=utterance_id: self.delete_requested.emit(uid)
            )
            menu.addSeparator()

        standard = self.createStandardContextMenu()
        for action in standard.actions():
            menu.addAction(action)

        menu.exec(event.globalPos())
        standard.deleteLater()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Double-clicking a known name jumps to it; otherwise select the word."""
        super().mouseDoubleClickEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        selection = self._clean(self.textCursor().selectedText())
        if not selection:
            return
        known = self.matcher.lookup(selection)
        if known is not None:
            self.open_entity_requested.emit(known.entity_id)
