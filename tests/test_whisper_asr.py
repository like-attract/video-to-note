from pathlib import Path

import pytest

from backend.whisper_asr import WhisperTranscriber


def test_model_loader_uses_project_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeModel:
        def __init__(self, model_name: str, **kwargs):
            captured.update(model_name=model_name, **kwargs)

    transcriber = WhisperTranscriber(tmp_path / "models")
    monkeypatch.setattr(transcriber, "_download_model_files", lambda name: None)
    transcriber._load_model(FakeModel, "small", "cpu", "int8")

    assert captured["model_name"] == "small"
    assert captured["download_root"] == str((tmp_path / "models").resolve())


def test_load_model_prefers_local_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存目录已有完整模型时直接本地加载，不触发下载。"""
    snapshot = (
        tmp_path
        / "models--Systran--faster-whisper-base"
        / "snapshots"
        / "revision"
    )
    snapshot.mkdir(parents=True)
    for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (snapshot / filename).touch()

    captured: dict = {}

    class FakeModel:
        def __init__(self, model_name: str, **kwargs):
            captured["model_name"] = str(model_name)

    transcriber = WhisperTranscriber(tmp_path)
    download_calls: list[str] = []
    monkeypatch.setattr(
        transcriber, "_download_model_files", lambda name: download_calls.append(name) or None
    )
    transcriber._load_model(FakeModel, "base", "cpu", "int8")

    assert captured["model_name"] == str(snapshot.resolve())
    assert download_calls == []


def test_model_download_error_is_actionable(tmp_path: Path) -> None:
    transcriber = WhisperTranscriber(tmp_path / "models")
    original = RuntimeError("Server disconnected without sending a response.")

    error = transcriber._model_load_error("small", original)

    assert "Whisper 模型 small 下载或加载失败" in str(error)
    assert "代理" in str(error)
    assert "Server disconnected" in str(error)


def test_finds_complete_model_in_huggingface_cache(tmp_path: Path) -> None:
    transcriber = WhisperTranscriber(tmp_path)
    snapshot = (
        tmp_path
        / "models--Systran--faster-whisper-base"
        / "snapshots"
        / "revision"
    )
    snapshot.mkdir(parents=True)
    for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (snapshot / filename).touch()

    assert transcriber._cached_model_path("base") == snapshot.resolve()
    assert transcriber._preferred_model("small") == "base"
    assert transcriber._preferred_model("base") == "base"
