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
    assert transcriber._preferred_model("small") == "small"
    assert transcriber._preferred_model("base") == "base"


def test_model_download_falls_back_to_official_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcriber = WhisperTranscriber(tmp_path)
    calls: list[str] = []
    expected = tmp_path / "snapshot"

    def fake_download(model_name: str, endpoint: str) -> Path | None:
        calls.append(endpoint)
        if endpoint == "https://hf-mirror.com":
            raise RuntimeError("certificate verify failed")
        return expected

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setattr(transcriber, "_download_model_files_from_endpoint", fake_download)

    assert transcriber._download_model_files("base") == expected
    assert calls == ["https://hf-mirror.com", "https://huggingface.co"]


def test_load_model_does_not_repeat_hub_download_after_custom_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcriber = WhisperTranscriber(tmp_path)
    original = RuntimeError("certificate verify failed")
    transcriber._last_download_error = original
    monkeypatch.setattr(transcriber, "_cached_model_path", lambda name: None)
    monkeypatch.setattr(transcriber, "_download_model_files", lambda name: None)

    class FailIfCalled:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("Hub fallback should not be attempted")

    with pytest.raises(RuntimeError, match="certificate verify failed"):
        transcriber._load_model(FailIfCalled, "base", "cpu", "int8")
