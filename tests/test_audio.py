"""Audio format conversion and the Whisper glossary.

The conversion helpers are pure functions on bytes, so they are worth pinning:
a silent bug here produces audio that sounds fine but transcribes as noise.
"""

from __future__ import annotations

import array
import wave

import pytest

from PySide6.QtMultimedia import QAudioFormat

from canon_keeper.audio import capture, transcribe
from canon_keeper_core.repo.entities import KIND_LOCATION, KIND_NPC, Entity


def _format(sample_format, channels=1, rate=16000) -> QAudioFormat:
    fmt = QAudioFormat()
    fmt.setSampleRate(rate)
    fmt.setChannelCount(channels)
    fmt.setSampleFormat(sample_format)
    return fmt


def test_int16_passes_through_untouched():
    samples = array.array("h", [0, 1000, -1000, 32767, -32768])
    raw = samples.tobytes()
    assert capture._to_int16_mono(raw, _format(QAudioFormat.SampleFormat.Int16)) == raw


def test_float_is_scaled_and_clamped():
    floats = array.array("f", [0.0, 0.5, -0.5, 2.0, -2.0])
    out = array.array("h")
    out.frombytes(capture._to_int16_mono(floats.tobytes(), _format(QAudioFormat.SampleFormat.Float)))

    assert out[0] == 0
    assert abs(out[1] - 16383) <= 1
    assert abs(out[2] + 16383) <= 1
    # Out-of-range input must clamp rather than wrap into loud noise.
    assert out[3] == 32767
    assert out[4] == -32768


def test_stereo_is_averaged_not_truncated():
    # Left silent, right loud: dropping a channel would lose the signal entirely.
    stereo = array.array("h", [0, 1000, 0, 2000])
    out = array.array("h")
    out.frombytes(
        capture._to_int16_mono(stereo.tobytes(), _format(QAudioFormat.SampleFormat.Int16, channels=2))
    )
    assert list(out) == [500, 1000]


def test_odd_trailing_bytes_do_not_raise():
    """Qt hands over whatever is in the buffer; a partial frame must not crash."""
    raw = array.array("h", [1, 2, 3]).tobytes() + b"\x01"
    out = capture._to_int16_mono(raw, _format(QAudioFormat.SampleFormat.Int16))
    assert len(out) == 6


def test_peak_level_is_normalised():
    assert capture.peak_level(b"") == 0.0
    assert capture.peak_level(array.array("h", [0, 0]).tobytes()) == 0.0
    assert capture.peak_level(array.array("h", [16384]).tobytes()) == 0.5
    assert capture.peak_level(array.array("h", [-32768]).tobytes()) == 1.0


def test_recorder_writes_a_playable_wav(tmp_path):
    """Drive the buffer directly: CI has no microphone, but the writer is ours."""
    recorder = capture.Recorder()
    recorder._format = _format(QAudioFormat.SampleFormat.Int16)
    recorder._buffer = bytearray(array.array("h", [0, 500, -500] * 100).tobytes())

    class _FakeSource:
        def stop(self): ...
        def deleteLater(self): ...

    recorder._source = _FakeSource()

    path = recorder.stop(tmp_path / "clip.wav")
    assert path is not None and path.exists()

    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert handle.getnframes() == 300


def test_glossary_lists_campaign_proper_nouns(repos):
    campaign = repos.campaigns.ensure_default()
    repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_LOCATION,
            name="Cragmaw Castle",
            aliases=["the Castle"],
        )
    )
    repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Sildar Hallwinter")
    )

    prompt = transcribe.build_glossary(repos, campaign.id)
    assert "Cragmaw Castle" in prompt
    assert "Sildar Hallwinter" in prompt
    assert "the Castle" in prompt


def test_glossary_skips_placeholder_names(repos):
    campaign = repos.campaigns.ensure_default()
    repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="New character")
    )
    # Priming Whisper with "New character" would make it hear that phrase.
    assert transcribe.build_glossary(repos, campaign.id) == ""


def test_glossary_is_capped(repos):
    campaign = repos.campaigns.ensure_default()
    for index in range(300):
        repos.entities.create(
            Entity(
                id=None,
                campaign_id=campaign.id,
                kind=KIND_NPC,
                name=f"Ambassador Quellorion the {index}th of Vandrenwick",
            )
        )
    prompt = transcribe.build_glossary(repos, campaign.id)
    assert len(prompt) <= transcribe.MAX_GLOSSARY_CHARS + 1
    assert prompt.endswith(".")


def test_missing_cuda_is_recognised():
    assert transcribe._is_missing_cuda(
        RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
    )
    assert transcribe._is_missing_cuda(RuntimeError("libcudnn_ops.so.9: cannot open"))
    assert not transcribe._is_missing_cuda(RuntimeError("audio file not found"))


def test_gpu_failure_falls_back_to_cpu(monkeypatch, tmp_path):
    """A machine with an NVIDIA card but no CUDA runtime must still transcribe.

    device='auto' selects CUDA there, and ctranslate2 ships neither cuBLAS nor
    cuDNN, so the failure only appears at the first inference.
    """
    attempts: list[str] = []

    class _FakeModel:
        def __init__(self, device: str):
            self._device = device

        def transcribe(self, *_args, **_kwargs):
            attempts.append(self._device)
            if self._device != "cpu":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

            class _Segment:
                text = " Sildar bleeds out."

            class _Info:
                language = "en"
                duration = 3.0

            return iter([_Segment()]), _Info()

    transcriber = transcribe.FasterWhisperTranscriber("tiny", device="auto")
    monkeypatch.setattr(
        transcriber, "load", lambda: _FakeModel(transcriber.device), raising=False
    )

    result = transcriber.transcribe(tmp_path / "clip.wav")

    assert attempts == ["auto", "cpu"], "it must retry once on the CPU, and only once"
    assert transcriber.device == "cpu"
    assert result.text == "Sildar bleeds out."


def test_non_cuda_errors_are_not_retried(monkeypatch, tmp_path):
    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("audio file is corrupt")

    transcriber = transcribe.FasterWhisperTranscriber("tiny", device="auto")
    monkeypatch.setattr(transcriber, "load", _boom, raising=False)

    with pytest.raises(RuntimeError, match="corrupt"):
        transcriber.transcribe(tmp_path / "clip.wav")
    assert len(calls) == 1
