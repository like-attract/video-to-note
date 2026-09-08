import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

from backend import paraformer_asr
from backend.paraformer_asr import ParaformerTranscriber
from backend.whisper_asr import TranscriptionCancelledError


def tiny_min_sizes() -> dict[str, int]:
    """测试用体积下限：只需区分「空/损坏文件」与「非空文件」。"""
    return {name: 1 for name in paraformer_asr.MIN_FILE_BYTES}


def _make_file(path: Path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)


def test_cache_status_three_states(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paraformer_asr, "MIN_FILE_BYTES", tiny_min_sizes())
    transcriber = ParaformerTranscriber(tmp_path / "cache")
    assert transcriber.cache_status() == "missing"

    asr_dir = tmp_path / "cache" / "sherpa" / "asr"
    for name in paraformer_asr.MODEL_SPECS["asr"]["files"]:
        _make_file(asr_dir / name)
    assert transcriber.cache_status() == "incomplete"

    _make_file(tmp_path / "cache" / "sherpa" / "vad" / "silero_vad.onnx")
    # 标点整体缺失可降级，不算 incomplete
    assert transcriber.cache_status() == "cached"

    # 标点存在但不完整 → incomplete
    _make_file(tmp_path / "cache" / "sherpa" / "punc" / "model.onnx", size=0)
    assert transcriber.cache_status() == "incomplete"


class _FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeStream:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def accept_waveform(self, _rate: int, _samples) -> None:  # noqa: ANN001
        return None

    @property
    def result(self) -> _FakeResult:
        return _FakeResult(self._texts.pop(0))


class _FakeRecognizer:
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)

    def create_stream(self) -> _FakeStream:
        return _FakeStream(self._texts)

    def decode_stream(self, _stream) -> None:  # noqa: ANN001
        return None


class _FakePunct:
    def add_punctuation(self, text: str) -> str:
        return text + "。"


def _fake_models(recognizer: _FakeRecognizer, punct=None, note=None) -> dict:  # noqa: ANN001
    return {"recognizer": recognizer, "vad_config": object(), "punct": punct, "note": note}


def _fake_audio(seconds: float = 3.0) -> np.ndarray:
    return np.zeros(int(paraformer_asr.SAMPLE_RATE * seconds), dtype=np.float32)


def _patch_audio(monkeypatch: pytest.MonkeyPatch, audio: np.ndarray) -> None:
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        types.SimpleNamespace(decode_audio=lambda _path, sampling_rate: audio),
    )


def test_transcribe_sync_assembles_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcriber = ParaformerTranscriber(tmp_path / "cache")
    monkeypatch.setattr(
        transcriber,
        "_ensure_models",
        lambda cancel_event: _fake_models(
            _FakeRecognizer(["第一句", "第二句"]), _FakePunct()
        ),
    )
    audio = _fake_audio(3.0)
    _patch_audio(monkeypatch, audio)
    rate = paraformer_asr.SAMPLE_RATE
    pieces = [(0, audio[:rate]), (int(rate * 1.5), audio[int(rate * 1.5) : int(rate * 2.5)])]
    monkeypatch.setattr(transcriber, "_vad_pieces", lambda *_args, **_kw: pieces)

    result = transcriber._transcribe_sync(Path("a.m4a"), "paraformer-zh", False, None)

    assert [(s.start, s.end, s.text) for s in result["segments"]] == [
        (0.0, 1.0, "第一句。"),
        (1.5, 2.5, "第二句。"),
    ]
    assert result["text"] == "第一句。第二句。"
    assert result["language"] == "zh"
    assert result["device"] == "cpu"
    assert result["duration"] == 3.0
    assert result["fallback_note"] is None
    assert result["model"] == "paraformer-zh"


def test_transcribe_sync_reports_punc_and_gpu_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcriber = ParaformerTranscriber(tmp_path / "cache")
    monkeypatch.setattr(
        transcriber,
        "_ensure_models",
        lambda cancel_event: _fake_models(
            _FakeRecognizer(["句子"]), note="标点模型不可用（x），本次转写结果没有标点。"
        ),
    )
    audio = _fake_audio(1.0)
    _patch_audio(monkeypatch, audio)
    monkeypatch.setattr(
        transcriber,
        "_vad_pieces",
        lambda *_args, **_kw: [(0, audio)],
    )

    result = transcriber._transcribe_sync(Path("a.m4a"), "paraformer-zh", True, None)

    assert result["fallback_note"] is not None
    assert "标点模型不可用" in result["fallback_note"]
    assert "仅支持 CPU" in result["fallback_note"]


def test_transcribe_sync_aborts_between_pieces_when_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcriber = ParaformerTranscriber(tmp_path / "cache")
    monkeypatch.setattr(
        transcriber,
        "_ensure_models",
        lambda cancel_event: _fake_models(_FakeRecognizer(["第一句", "第二句"])),
    )
    audio = _fake_audio(2.0)
    _patch_audio(monkeypatch, audio)

    cancel_event = threading.Event()

    def fake_pieces(*_args, **_kw):  # noqa: ANN002, ANN003
        yield 0, audio[: paraformer_asr.SAMPLE_RATE]
        cancel_event.set()
        yield paraformer_asr.SAMPLE_RATE, audio[: paraformer_asr.SAMPLE_RATE]

    monkeypatch.setattr(transcriber, "_vad_pieces", lambda *_a, **_kw: fake_pieces())

    with pytest.raises(TranscriptionCancelledError):
        transcriber._transcribe_sync(
            Path("a.m4a"), "paraformer-zh", False, cancel_event
        )
