"""Record a beat, see it transcribed, turn the names in it into entities.

The row appears the moment you stop recording, marked as pending, and fills in
when the model finishes. Nothing blocks the GUI thread, so a slow transcription
never freezes the app mid-session.

Names you already know are highlighted as they appear. Select words you do not
know yet and the right-click menu will make them a character or a place -- which
also feeds them back into the Whisper glossary, so the next time you say the
name it is transcribed correctly.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioDevice
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from canon_keeper import config
from canon_keeper.audio import capture, transcribe
from canon_keeper.matching import EntityMatcher
from canon_keeper.panels.transcript.view import TranscriptView
from canon_keeper.plugin import REACH_EVERYWHERE, AppContext, ReservedKey
from canon_keeper.repo.entities import Entity

RECORD_SHORTCUT = "F9"


class TranscriptWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._pending_by_audio: dict[str, int] = {}

        self._recorder = capture.Recorder(self)
        self._recorder.level_changed.connect(self._on_level)
        self._recorder.failed.connect(self._on_recorder_failed)

        self._pool = QThreadPool(self)
        # The model is not safe to drive from several threads, and serialising
        # also stops a burst of clips thrashing the CPU mid-session.
        self._pool.setMaxThreadCount(1)
        self._transcriber: transcribe.FasterWhisperTranscriber | None = None

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(200)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        self._session = ctx.repos.sessions.ensure_open(ctx.campaign_id)

        self._build_ui()

        ctx.bus.campaign_changed.connect(self._on_campaign_changed)
        # Any entity edit anywhere changes what should light up in here.
        ctx.bus.entity_changed.connect(lambda _id: self.refresh_matcher())
        ctx.bus.entity_deleted.connect(lambda _id: self.refresh_matcher())
        # The name colours are chosen per appearance, so they must be rebuilt
        # when the user switches between light and dark.
        ctx.bus.theme_changed.connect(self._view_set_dark)

        self.reload()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        bar = QHBoxLayout()

        self._record_button = QPushButton(f"Record  ({RECORD_SHORTCUT})")
        self._record_button.setCheckable(True)
        self._record_button.setMinimumWidth(170)
        self._record_button.clicked.connect(self._toggle_recording)
        bar.addWidget(self._record_button)

        self._level = QProgressBar()
        self._level.setRange(0, 100)
        self._level.setTextVisible(False)
        self._level.setFixedWidth(90)
        self._level.setToolTip("Input level")
        bar.addWidget(self._level)

        self._device_combo = QComboBox()
        self._device_combo.setToolTip("Microphone")
        self._device_combo.setMinimumWidth(180)
        bar.addWidget(self._device_combo, 1)

        self._model_combo = QComboBox()
        self._model_combo.setToolTip(
            "Whisper model. Larger is more accurate and slower; the first use of "
            "each downloads it."
        )
        for size in transcribe.MODEL_SIZES:
            self._model_combo.addItem(size)
        saved_model = self._ctx.repos.settings.get("whisper_model", transcribe.DEFAULT_MODEL)
        index = self._model_combo.findText(saved_model)
        self._model_combo.setCurrentIndex(index if index >= 0 else 0)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        bar.addWidget(self._model_combo)

        outer.addLayout(bar)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)

        self._view = TranscriptView(self._build_matcher(), self)
        self._view.setToolTip(
            "Select a name, then right-click to add it as a character or place."
        )
        self._view.add_entity_requested.connect(self._add_entity_from_text)
        self._view.open_entity_requested.connect(self._open_entity)
        self._view.edit_requested.connect(self._edit_line)
        self._view.delete_requested.connect(self._delete_utterance)
        self._view.retry_requested.connect(self._retry)
        outer.addWidget(self._view, 1)

        typed = QHBoxLayout()
        self._typed = QLineEdit()
        self._typed.setPlaceholderText("...or type a beat and press Enter")
        self._typed.returnPressed.connect(self._add_typed)
        typed.addWidget(self._typed, 1)
        add_button = QPushButton("Add")
        add_button.clicked.connect(self._add_typed)
        typed.addWidget(add_button)
        outer.addLayout(typed)

        self._shortcut = QShortcut(QKeySequence(RECORD_SHORTCUT), self)
        self._shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut.activated.connect(self._record_button.click)

        self._populate_devices()
        self._check_availability()

    def reserved_keys(self) -> list[ReservedKey]:
        """Push-to-talk, and it reaches the whole machine on purpose.

        The point of it is to record what you are saying *about* whatever you are
        looking at, so a key that only worked while this panel had the focus would
        be a key for talking about this panel. It costs F9 for everything else
        running, which is the trade, and saying so here is what makes the trade
        visible instead of surprising.
        """
        return [
            ReservedKey(
                RECORD_SHORTCUT,
                "start and stop recording (push-to-talk)",
                reach=REACH_EVERYWHERE,
            )
        ]

    def _populate_devices(self) -> None:
        self._device_combo.clear()
        self._device_combo.addItem("Default microphone", None)
        saved = self._ctx.repos.settings.get("audio_input_id", None)
        for device in capture.available_inputs():
            device_id = bytes(device.id()).decode("utf-8", "replace")
            self._device_combo.addItem(device.description(), device_id)
            if saved and device_id == saved:
                self._device_combo.setCurrentIndex(self._device_combo.count() - 1)
        self._device_combo.currentIndexChanged.connect(
            lambda _i: self._ctx.repos.settings.set(
                "audio_input_id", self._device_combo.currentData()
            )
        )

    def _check_availability(self) -> None:
        """Recording without transcription is a tape recorder, so gate on it."""
        if transcribe.is_available():
            self._status.setText("")
            return
        self._record_button.setEnabled(False)
        self._model_combo.setEnabled(False)
        self._status.setText(transcribe.INSTALL_HINT.replace("\n\n", "  ").replace("\n", "  "))
        self._status.setToolTip(transcribe.INSTALL_HINT)

    def _selected_device(self) -> QAudioDevice | None:
        wanted = self._device_combo.currentData()
        if wanted is None:
            return None
        for device in capture.available_inputs():
            if bytes(device.id()).decode("utf-8", "replace") == wanted:
                return device
        return None

    # -------------------------------------------------------------- highlights

    def _build_matcher(self) -> EntityMatcher:
        return EntityMatcher.from_repos(self._ctx.repos, self._ctx.campaign_id)

    def _view_set_dark(self, is_dark: bool) -> None:
        self._view.set_dark(is_dark)

    def refresh_matcher(self) -> None:
        """Rebuild the name index and repaint the transcript."""
        self._view.set_matcher(self._build_matcher())

    def _add_entity_from_text(self, name: str, kind: str) -> None:
        """Turn selected words into a character or a place."""
        name = name.strip()
        if not name:
            return

        existing = self._view.matcher.lookup(name)
        if existing is not None:
            self._open_entity(existing.entity_id)
            return

        entity = self._ctx.repos.entities.create(
            Entity(
                id=None,
                campaign_id=self._ctx.campaign_id,
                kind=kind,
                name=name,
                data={"status": "alive"} if kind in ("npc", "pc") else {},
            )
        )
        # entity_changed rebuilds the matcher here and refreshes the other
        # panels, so the name lights up immediately and the next transcription
        # is primed with it.
        self._ctx.bus.entity_changed.emit(entity.id)
        self._ctx.bus.active_entity_changed.emit(entity.id)
        self._ctx.bus.status_message.emit(f"Added {name}")

    def _open_entity(self, entity_id: int) -> None:
        entity = self._ctx.repos.entities.get(entity_id)
        if entity is None:
            return
        if entity.kind == "location":
            self._ctx.bus.active_location_changed.emit(entity_id)
        else:
            self._ctx.bus.active_entity_changed.emit(entity_id)
        self._ctx.bus.status_message.emit(entity.name)

    # -------------------------------------------------------------- recording

    def _toggle_recording(self) -> None:
        if self._record_button.isChecked():
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        if not self._recorder.start(self._selected_device()):
            self._record_button.setChecked(False)
            return
        self._record_button.setText("Stop  0:00")
        self._elapsed_timer.start()
        self._ctx.bus.status_message.emit("Recording...")

    def _stop_recording(self) -> None:
        self._elapsed_timer.stop()
        self._record_button.setText(f"Record  ({RECORD_SHORTCUT})")
        self._level.setValue(0)

        destination = self._audio_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}.wav"
        path = self._recorder.stop(destination)
        if path is None:
            self._ctx.bus.status_message.emit("Nothing was recorded.")
            return

        # The row is created now, with the audio attached, so a failed or slow
        # transcription can never orphan a recording.
        utterance = self._ctx.repos.utterances.add(
            self._session.id, "", audio_path=str(path)
        )
        self._pending_by_audio[str(path)] = utterance.id
        self.reload()
        self._queue_transcription(path)

    def _queue_transcription(self, path: Path) -> None:
        transcriber = self._ensure_transcriber()
        if transcriber is None:
            return
        prompt = transcribe.build_glossary(self._ctx.repos, self._ctx.campaign_id)
        task = transcribe.TranscriptionTask(transcriber, path, prompt)
        task.signals.started.connect(self._on_transcription_started)
        task.signals.finished.connect(self._on_transcribed)
        task.signals.failed.connect(self._on_transcription_failed)
        self._pool.start(task)

    def _ensure_transcriber(self) -> transcribe.FasterWhisperTranscriber | None:
        if not transcribe.is_available():
            self._check_availability()
            return None
        if self._transcriber is None:
            self._transcriber = transcribe.FasterWhisperTranscriber(
                self._model_combo.currentText()
            )
        return self._transcriber

    def _update_elapsed(self) -> None:
        seconds = int(self._recorder.elapsed)
        self._record_button.setText(f"Stop  {seconds // 60}:{seconds % 60:02d}")

    def _on_level(self, level: float) -> None:
        self._level.setValue(int(level * 100))

    def _on_recorder_failed(self, message: str) -> None:
        self._record_button.setChecked(False)
        self._record_button.setText(f"Record  ({RECORD_SHORTCUT})")
        self._elapsed_timer.stop()
        QMessageBox.warning(self, "Microphone", message)

    def _on_model_changed(self, model: str) -> None:
        self._ctx.repos.settings.set("whisper_model", model)
        # Drop the loaded model; the next clip loads the newly chosen one.
        self._transcriber = None
        self._ctx.bus.status_message.emit(f"Whisper model set to {model}")

    # ----------------------------------------------------------- transcription

    def _on_transcription_started(self, path: object) -> None:
        loaded = self._transcriber is not None and self._transcriber.is_loaded
        self._status.setText(
            "Transcribing..."
            if loaded
            else f"Loading the {self._model_combo.currentText()} model "
            "(the first run downloads it)..."
        )

    def _on_transcribed(self, path: object, result: object) -> None:
        self._status.setText("")
        utterance_id = self._pending_by_audio.pop(str(path), None)
        if utterance_id is None:
            return

        self._ctx.repos.utterances.update_text(utterance_id, result.text.strip())
        self.reload()
        self._ctx.bus.utterance_added.emit(utterance_id)
        self._ctx.bus.status_message.emit(f"Transcribed {result.duration:.0f}s of audio")

    def _on_transcription_failed(self, path: object, message: str) -> None:
        self._status.setText(f"Transcription failed: {message}")
        utterance_id = self._pending_by_audio.pop(str(path), None)
        if utterance_id is not None:
            # The audio survives, so the line can be retried or typed by hand.
            self._ctx.repos.utterances.update_text(utterance_id, "(transcription failed)")
            self.reload()

    # --------------------------------------------------------------- the lines

    def reload(self, scroll_to_end: bool = True) -> None:
        self._view.render_utterances(
            self._ctx.repos.utterances.for_session(self._session.id),
            scroll_to_end=scroll_to_end,
        )

    def _edit_line(self, utterance_id: int) -> None:
        """A hand-corrected transcription is what everything downstream trusts."""
        utterance = self._ctx.repos.utterances.get(utterance_id)
        if utterance is None:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Edit line", "What you said:", utterance.text
        )
        if not ok:
            return
        self._set_line_text(utterance_id, text.strip())

    def _set_line_text(self, utterance_id: int, text: str) -> None:
        self._ctx.repos.utterances.update_text(utterance_id, text)
        self.reload(scroll_to_end=False)
        self._ctx.bus.utterance_added.emit(utterance_id)

    def _retry(self, utterance_id: int) -> None:
        utterance = self._ctx.repos.utterances.get(utterance_id)
        if utterance is None or not utterance.audio_path:
            self._ctx.bus.status_message.emit("That line has no audio to re-transcribe.")
            return
        path = Path(utterance.audio_path)
        if not path.exists():
            self._ctx.bus.status_message.emit(f"Audio file is missing: {path.name}")
            return
        self._ctx.repos.utterances.update_text(utterance_id, "")
        self.reload(scroll_to_end=False)
        self._pending_by_audio[str(path)] = utterance_id
        self._queue_transcription(path)

    def _delete_utterance(self, utterance_id: int) -> None:
        self._ctx.repos.utterances.delete(utterance_id)
        self.reload(scroll_to_end=False)

    # ------------------------------------------------------------------ typed

    def _add_typed(self) -> None:
        text = self._typed.text().strip()
        if not text:
            return
        utterance = self._ctx.repos.utterances.add(self._session.id, text, t=time.time())
        self._typed.clear()
        self.reload()
        self._ctx.bus.utterance_added.emit(utterance.id)

    # --------------------------------------------------------------- campaign

    def _on_campaign_changed(self, campaign_id: int) -> None:
        if self._recorder.is_recording:
            self._recorder.cancel()
            self._record_button.setChecked(False)
            self._record_button.setText(f"Record  ({RECORD_SHORTCUT})")
            self._elapsed_timer.stop()
        self._session = self._ctx.repos.sessions.ensure_open(campaign_id)
        self.refresh_matcher()
        self.reload()

    def _audio_dir(self) -> Path:
        return config.data_dir() / "audio" / f"session_{self._session.id}"
