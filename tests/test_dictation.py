"""Speaking into the chat instead of typing."""

from __future__ import annotations

import pytest

from canon_keeper.audio import transcribe
from canon_keeper.audio.dictation import Dictation
from canon_keeper.panels.table.widget import TableWidget
from canon_keeper_core.repo.entities import KIND_LOCATION, KIND_NPC, Entity


@pytest.fixture
def table(ctx, qtbot) -> TableWidget:
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    return widget


# -------------------------------------------------------------------- the button


def test_the_chat_offers_a_speak_button(table):
    assert table._say_button.text() == "Speak"


def test_without_whisper_the_button_says_why(ctx, qtbot, monkeypatch):
    monkeypatch.setattr(transcribe, "is_available", lambda: False)
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)

    assert widget._say_button.isEnabled() is False
    assert "pip install" in widget._say_button.toolTip()


def test_a_failure_to_record_untoggles_the_button(table, monkeypatch):
    """Otherwise it sits there saying Stop while recording nothing."""
    monkeypatch.setattr(table._dictation, "start", lambda device=None: False)
    table._say_button.setChecked(True)

    table._toggle_dictation()

    assert table._say_button.isChecked() is False


# ------------------------------------------------------------- what comes back


def test_the_words_go_into_the_box_and_are_not_sent(table):
    """A transcription is a first draft; only the speaker can approve it."""
    sent = []
    table._client.send_chat = lambda text: sent.append(text) or True

    table._dictation.text_ready.emit("I check the door for traps")

    assert table._entry.text() == "I check the door for traps"
    assert sent == [], "nothing should have been sent"


def test_a_second_sentence_adds_to_the_first(table):
    table._entry.setText("I draw my sword")
    table._dictation.text_ready.emit("and step forward")

    assert table._entry.text() == "I draw my sword and step forward"


def test_dictated_text_can_be_edited_then_sent(table):
    sent = []
    table._client.send_chat = lambda text: sent.append(text) or True

    table._dictation.text_ready.emit("I check the door for trapps")
    table._entry.setText("I check the door for traps")
    table._send()

    assert sent == ["I check the door for traps"]


def test_a_dictated_command_still_works_as_a_command(table):
    """You should be able to say 'slash roll two d six' and have it roll."""
    rolled = []
    table._client.send_roll = lambda notation: rolled.append(notation) or True

    table._dictation.text_ready.emit("/roll 2d6+3")
    table._send()

    assert rolled == ["2d6+3"]


def test_a_failure_is_reported(table):
    """Errors are diagnostics: recorded in the log, and marked on the filter."""
    table._dictation.failed.emit("the microphone is busy")

    assert any("microphone is busy" in line.text for line in table._entries)
    assert table._show_chatter.styleSheet(), "the filter should be marked"


# ---------------------------------------------------------------- the glossary


def test_the_dm_primes_the_transcriber_from_the_campaign(ctx, table):
    ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_NPC, name="Sildar Hallwinter")
    )
    ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_LOCATION, name="Cragmaw Castle")
    )

    glossary = table._glossary()

    assert "Sildar Hallwinter" in glossary
    assert "Cragmaw Castle" in glossary


def test_a_player_primes_it_from_what_the_host_sent(ctx, qtbot):
    """They have no campaign to read -- only what was shared, which is exactly
    the set of names they are likely to say out loud."""
    ctx.role = "player"
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    ctx.shared.replace_all(
        [{"id": 1, "kind": "npc", "name": "Sildar Hallwinter", "data": {}}]
    )

    assert "Sildar Hallwinter" in widget._glossary()


def test_no_names_means_no_glossary(ctx, qtbot):
    ctx.role = "player"
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)

    assert widget._glossary() == ""


# ------------------------------------------------------------------- the clip


def test_the_recording_is_not_kept(tmp_path, monkeypatch):
    """A dictated line is wanted as text; the audio is a means, not a record."""
    dictation = Dictation()
    clip = tmp_path / "said.wav"
    clip.write_bytes(b"not really audio")

    class _Result:
        text = "I check the door"

    dictation._on_finished(clip, _Result())

    assert not clip.exists()


def test_a_failed_transcription_also_cleans_up(tmp_path):
    dictation = Dictation()
    clip = tmp_path / "said.wav"
    clip.write_bytes(b"not really audio")

    dictation._on_failed(clip, "the model is missing")

    assert not clip.exists()


def test_changing_the_model_drops_the_loaded_one(monkeypatch):
    dictation = Dictation(model="tiny")
    dictation._transcriber = object()

    dictation.set_model("small")

    assert dictation._transcriber is None
    assert dictation._model == "small"


def test_setting_the_same_model_keeps_it_loaded(monkeypatch):
    dictation = Dictation(model="tiny")
    loaded = object()
    dictation._transcriber = loaded

    dictation.set_model("tiny")

    assert dictation._transcriber is loaded
