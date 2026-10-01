"""Transcript panel behaviour, with a stand-in for Whisper.

No microphone and no model here: the point is the bookkeeping around them --
that a clip is never orphaned by a slow or failed transcription, that a
hand-corrected line is what gets persisted, and that selecting words really does
produce an entity that then lights up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from canon_keeper.audio.transcribe import TranscriptionResult
from canon_keeper.panels.transcript.view import PREFIX_LENGTH
from canon_keeper.panels.transcript.widget import TranscriptWidget
from canon_keeper_core.repo.entities import KIND_LOCATION, KIND_NPC


@pytest.fixture
def panel(ctx, qtbot) -> TranscriptWidget:
    widget = TranscriptWidget(ctx)
    qtbot.addWidget(widget)
    return widget


def _lines(panel: TranscriptWidget) -> list[str]:
    document = panel._view.document()
    return [
        document.findBlockByNumber(n).text()[PREFIX_LENGTH:]
        for n in range(document.blockCount())
    ]


# ------------------------------------------------------------------ utterances


def test_typed_entry_becomes_an_utterance(panel, ctx, qtbot):
    with qtbot.waitSignal(ctx.bus.utterance_added, timeout=1000):
        panel._typed.setText("The party arrives at Cragmaw Castle.")
        panel._add_typed()

    stored = ctx.repos.utterances.for_session(panel._session.id)
    assert [u.text for u in stored] == ["The party arrives at Cragmaw Castle."]
    assert _lines(panel) == ["The party arrives at Cragmaw Castle."]
    assert panel._typed.text() == "", "the box should clear, ready for the next beat"


def test_blank_typed_entry_is_ignored(panel, ctx):
    panel._typed.setText("   ")
    panel._add_typed()
    assert ctx.repos.utterances.for_session(panel._session.id) == []


def test_editing_a_line_rewrites_the_utterance(panel, ctx):
    panel._typed.setText("crag more castle")
    panel._add_typed()
    utterance_id = ctx.repos.utterances.for_session(panel._session.id)[0].id

    panel._set_line_text(utterance_id, "Cragmaw Castle")

    assert ctx.repos.utterances.get(utterance_id).text == "Cragmaw Castle"
    assert _lines(panel) == ["Cragmaw Castle"]


def test_finished_transcription_fills_the_pending_line(panel, ctx):
    audio = Path("clip.wav")
    utterance = ctx.repos.utterances.add(panel._session.id, "", audio_path=str(audio))
    panel._pending_by_audio[str(audio)] = utterance.id
    panel.reload()
    assert _lines(panel) == ["(transcribing...)"]

    panel._on_transcribed(audio, TranscriptionResult(text="Sildar bleeds out.", duration=4.0))

    assert ctx.repos.utterances.get(utterance.id).text == "Sildar bleeds out."
    assert _lines(panel) == ["Sildar bleeds out."]
    assert str(audio) not in panel._pending_by_audio


def test_failed_transcription_keeps_the_line_and_the_audio(panel, ctx):
    """A failure must leave something you can retype or retry, not vanish."""
    audio = Path("clip.wav")
    utterance = ctx.repos.utterances.add(panel._session.id, "", audio_path=str(audio))
    panel._pending_by_audio[str(audio)] = utterance.id
    panel.reload()

    panel._on_transcription_failed(audio, "model not found")

    assert _lines(panel) == ["(transcription failed)"]
    assert ctx.repos.utterances.get(utterance.id).audio_path == str(audio)
    assert "model not found" in panel._status.text()


def test_reload_restores_the_session_transcript(panel, ctx, qtbot):
    for line in ("First beat.", "Second beat."):
        panel._typed.setText(line)
        panel._add_typed()

    reopened = TranscriptWidget(ctx)
    qtbot.addWidget(reopened)

    assert _lines(reopened) == ["First beat.", "Second beat."]


def test_recording_uses_one_open_session(panel, ctx):
    """Reopening the panel must not start a second session mid-evening."""
    first = panel._session.id
    assert ctx.repos.sessions.ensure_open(ctx.campaign_id).id == first
    assert len(ctx.repos.sessions.list(ctx.campaign_id)) == 1


def test_switching_campaign_swaps_the_transcript(panel, ctx, qtbot):
    panel._typed.setText("Beat in the first campaign.")
    panel._add_typed()

    other = ctx.repos.campaigns.create("Second Campaign")
    ctx.campaign_id = other.id
    ctx.bus.campaign_changed.emit(other.id)

    assert panel._session.campaign_id == other.id
    assert _lines(panel) == [""]


# ------------------------------------------------- selecting words into entities


def test_selection_becomes_a_character(panel, ctx, qtbot):
    with qtbot.waitSignal(ctx.bus.entity_changed, timeout=1000):
        panel._add_entity_from_text("Sildar Hallwinter", KIND_NPC)

    created = ctx.repos.entities.list(ctx.campaign_id, kinds=(KIND_NPC,))
    assert [e.name for e in created] == ["Sildar Hallwinter"]
    assert created[0].data.get("status") == "alive"


def test_selection_becomes_a_place(panel, ctx):
    panel._add_entity_from_text("Cragmaw Castle", KIND_LOCATION)

    created = ctx.repos.entities.list(ctx.campaign_id, kinds=(KIND_LOCATION,))
    assert [e.name for e in created] == ["Cragmaw Castle"]


def test_a_new_entity_lights_up_immediately(panel, ctx):
    """Adding a name must repaint the transcript without a manual refresh."""
    panel._typed.setText("They ride for Cragmaw Castle.")
    panel._add_typed()
    assert not list(panel._view.matcher.finditer("Cragmaw Castle"))

    panel._add_entity_from_text("Cragmaw Castle", KIND_LOCATION)

    matches = list(panel._view.matcher.finditer("They ride for Cragmaw Castle."))
    assert len(matches) == 1
    assert matches[0].kind == KIND_LOCATION


def test_adding_a_name_twice_opens_it_instead_of_duplicating(panel, ctx, qtbot):
    panel._add_entity_from_text("Sildar Hallwinter", KIND_NPC)

    with qtbot.waitSignal(ctx.bus.active_entity_changed, timeout=1000):
        panel._add_entity_from_text("sildar hallwinter", KIND_NPC)

    assert len(ctx.repos.entities.list(ctx.campaign_id, kinds=(KIND_NPC,))) == 1


def test_blank_selection_creates_nothing(panel, ctx):
    panel._add_entity_from_text("   ", KIND_NPC)
    assert ctx.repos.entities.list(ctx.campaign_id) == []


def test_a_new_name_reaches_the_whisper_glossary(panel, ctx):
    """The loop that matters: add a name, and the next clip is primed with it."""
    from canon_keeper.audio.transcribe import build_glossary

    assert build_glossary(ctx.repos, ctx.campaign_id) == ""
    panel._add_entity_from_text("Cragmaw Castle", KIND_LOCATION)
    assert "Cragmaw Castle" in build_glossary(ctx.repos, ctx.campaign_id)


def test_deleting_an_entity_stops_it_highlighting(panel, ctx):
    panel._add_entity_from_text("Cragmaw Castle", KIND_LOCATION)
    entity = ctx.repos.entities.list(ctx.campaign_id, kinds=(KIND_LOCATION,))[0]

    ctx.repos.entities.delete(entity.id)
    ctx.bus.entity_deleted.emit(entity.id)

    assert list(panel._view.matcher.finditer("Cragmaw Castle")) == []


# ----------------------------------------------------------------- the view


def test_clean_strips_punctuation_and_paragraph_marks(panel):
    from canon_keeper.panels.transcript.view import PARAGRAPH_SEPARATOR, TranscriptView

    assert TranscriptView._clean("  Sildar,  ") == "Sildar"
    assert TranscriptView._clean('"Cragmaw Castle."') == "Cragmaw Castle"
    assert TranscriptView._clean(f"Sildar{PARAGRAPH_SEPARATOR}Hallwinter") == (
        "Sildar Hallwinter"
    )


def test_line_text_excludes_the_timestamp(panel, ctx):
    panel._typed.setText("Sildar bleeds out.")
    panel._add_typed()
    utterance_id = ctx.repos.utterances.for_session(panel._session.id)[0].id

    assert panel._view.line_text(utterance_id) == "Sildar bleeds out."


def test_block_order_survives_a_deletion(panel, ctx):
    """Block N maps to utterance N, so a deletion must not shift the mapping."""
    for line in ("First.", "Second.", "Third."):
        panel._typed.setText(line)
        panel._add_typed()
    stored = ctx.repos.utterances.for_session(panel._session.id)

    panel._delete_utterance(stored[1].id)

    assert _lines(panel) == ["First.", "Third."]
    assert panel._view.line_text(stored[2].id) == "Third."
