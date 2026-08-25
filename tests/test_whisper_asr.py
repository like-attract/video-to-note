from pathlib import Path

import pytest

from backend import whisper_asr
from backend.whisper_asr import TranscriptionCancelledError, WhisperTranscriber


def tiny_min_sizes() -> dict[str, int]:
    """测试用体积下限：只需区分“空/损坏文件”与“非空文件”。"""
    return {name: 1 for name in whisper_asr.MIN_MODEL_FILE_BYTES}


def make_snapshot_files(snapshot: Path, model_bin_size: int = 64) -> None:
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.bin").write_bytes(b"0" * model_bin_size)
    (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
    (snapshot / "vocabulary.txt").write_text("a", encoding="utf-8")


def test_model_loader_uses_project_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    class FakeModel:
        def __init__(self, model_name: str, **kwargs):
            captured.update(model_name=model_name, **kwargs)

    transcriber = WhisperTranscriber(tmp_path / "models")
    monkeypatch.setattr(transcriber, "_download_model_files", lambda name: None)
    transcriber._load_model(FakeModel, "small", "cpu", "int8")

    assert captured["model_name"] == "small"
    assert captured["download_root"] == str((tmp_path / "models").resolve())


def test_load_model_prefers_local_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缓存目录已有完整模型时直接本地加载，不触发下载。"""
    monkeypatch.setattr(whisper_asr, "MIN_MODEL_FILE_BYTES", tiny_min_sizes())
    snapshot = (
        tmp_path
        / "models--Systran--faster-whisper-base"
        / "snapshots"
        / "revision"
    )
    make_snapshot_files(snapshot)

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


def test_undersized_model_bin_is_not_treated_as_cached(tmp_path: Path) -> None:
    """历史中断下载可能遗留几十字节的 model.bin：必须视为不完整而非已缓存。"""
    snapshot = (
        tmp_path
        / "models--Systran--faster-whisper-medium"
        / "snapshots"
        / "revision"
    )
    make_snapshot_files(snapshot, model_bin_size=187)

    transcriber = WhisperTranscriber(tmp_path)

    assert transcriber._cached_model_path("medium") is None
    assert transcriber._model_cache_status("medium") == "incomplete"


def test_model_download_error_is_actionable(tmp_path: Path) -> None:
    transcriber = WhisperTranscriber(tmp_path / "models")
    original = RuntimeError("Server disconnected without sending a response.")

    error = transcriber._model_load_error("small", original)

    assert "Whisper 模型 small 下载或加载失败" in str(error)
    assert "代理" in str(error)
    assert "Server disconnected" in str(error)


def test_finds_complete_model_in_huggingface_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(whisper_asr, "MIN_MODEL_FILE_BYTES", tiny_min_sizes())
    transcriber = WhisperTranscriber(tmp_path)
    snapshot = (
        tmp_path
        / "models--Systran--faster-whisper-base"
        / "snapshots"
        / "revision"
    )
    make_snapshot_files(snapshot)

    assert transcriber._cached_model_path("base") == snapshot.resolve()
    assert transcriber._preferred_model("small") == "small"
    assert transcriber._preferred_model("base") == "base"


def test_manual_model_dir_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """手动导入目录 manual/{model}/ 内 4 文件齐全时按已缓存处理。"""
    monkeypatch.setattr(whisper_asr, "MIN_MODEL_FILE_BYTES", tiny_min_sizes())
    transcriber = WhisperTranscriber(tmp_path)
    manual_dir = tmp_path / "manual" / "medium"
    make_snapshot_files(manual_dir)

    assert transcriber._cached_model_path("medium") == manual_dir.resolve()
    assert transcriber._model_cache_status("medium") == "cached"

    # 缺文件时为 incomplete，且别名目录 faster-whisper-{model} 也能识别
    (manual_dir / "model.bin").unlink()
    assert transcriber._model_cache_status("medium") == "incomplete"

    alias_dir = tmp_path / "manual" / "faster-whisper-small"
    make_snapshot_files(alias_dir)
    assert transcriber._cached_model_path("small") == alias_dir.resolve()


def test_download_replaces_undersized_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """快照内已有损坏 model.bin 时，下载器必须重新下载而不是跳过。"""
    transcriber = WhisperTranscriber(tmp_path)
    snapshot = (
        tmp_path
        / "models--Systran--faster-whisper-medium"
        / "snapshots"
        / "revision"
    )
    make_snapshot_files(snapshot, model_bin_size=187)

    downloaded: list[str] = []

    def fake_download_one(url: str, target: Path, headers: dict[str, str]) -> bool:
        downloaded.append(target.name)
        target.write_bytes(b"0" * 64)
        return True

    monkeypatch.setattr(transcriber, "_download_one_file", fake_download_one)
    monkeypatch.setattr(
        whisper_asr, "MIN_MODEL_FILE_BYTES", {**tiny_min_sizes(), "model.bin": 32}
    )

    assert transcriber._download_model_files_from_endpoint(
        "medium", "https://hf-mirror.com"
    ) == snapshot.resolve()
    assert "model.bin" in downloaded


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"", headers: dict | None = None) -> None:
        self.status = status
        self.body = body
        self.headers = {key: str(value) for key, value in (headers or {}).items()}

    def read(self, size: int) -> bytes:
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None


def test_download_replaces_undersized_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """快照内已有损坏 model.bin 时，下载器必须重新下载而不是跳过。"""
    transcriber = WhisperTranscriber(tmp_path)
    snapshot = (
        tmp_path
        / "models--Systran--faster-whisper-medium"
        / "snapshots"
        / "revision"
    )
    make_snapshot_files(snapshot, model_bin_size=187)

    downloaded: list[str] = []

    def fake_download_one(url: str, target: Path, headers: dict[str, str]) -> bool:
        downloaded.append(target.name)
        target.write_bytes(b"0" * 2048)
        return True

    def fake_urlopen(request, timeout):  # noqa: ANN001
        return FakeResponse(headers={"X-Repo-Commit": "revision"})

    monkeypatch.setattr(transcriber, "_download_one_file", fake_download_one)
    monkeypatch.setattr("backend.whisper_asr.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        whisper_asr, "MIN_MODEL_FILE_BYTES", {**tiny_min_sizes(), "model.bin": 1024}
    )

    assert transcriber._download_model_files_from_endpoint(
        "medium", "https://hf-mirror.com"
    ) == snapshot.resolve()
    assert "model.bin" in downloaded


def test_download_one_file_rejects_truncated_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连接中断但未抛错时不能把截断文件改名成正式文件（保留 .part 续传）。"""
    body = b"x" * 500

    def fake_urlopen(request, timeout):  # noqa: ANN001
        return FakeResponse(200, body, {"Content-Length": "1000"})

    monkeypatch.setattr("backend.whisper_asr.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(whisper_asr.time, "sleep", lambda _seconds: None)
    transcriber = WhisperTranscriber(tmp_path)
    target = tmp_path / "model.bin"

    assert transcriber._download_one_file("https://example.com/model.bin", target, {}) is False
    assert not target.exists()
    assert (tmp_path / "model.bin.part").stat().st_size == 500


def test_download_one_file_accepts_complete_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"x" * 1000

    def fake_urlopen(request, timeout):  # noqa: ANN001
        return FakeResponse(200, body, {"Content-Length": "1000"})

    monkeypatch.setattr("backend.whisper_asr.urllib.request.urlopen", fake_urlopen)
    transcriber = WhisperTranscriber(tmp_path)
    target = tmp_path / "model.bin"

    assert transcriber._download_one_file("https://example.com/model.bin", target, {}) is True
    assert target.stat().st_size == 1000
    assert not target.with_suffix(target.suffix + ".part").exists()


def test_load_model_self_heals_corrupt_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """加载报“is incomplete”时自动删除损坏 model.bin 并重下重载一次。"""
    monkeypatch.setattr(whisper_asr, "MIN_MODEL_FILE_BYTES", tiny_min_sizes())
    snapshot = (
        tmp_path
        / "models--Systran--faster-whisper-medium"
        / "snapshots"
        / "revision"
    )
    make_snapshot_files(snapshot)

    transcriber = WhisperTranscriber(tmp_path)

    class FakeModel:
        def __init__(self, model_name: str, **kwargs):
            if Path(model_name) == snapshot.resolve() and (snapshot / "model.bin").exists():
                raise RuntimeError(
                    "File model.bin is incomplete: failed to read a buffer of size 1 at position 0"
                )

    def fake_download(name: str) -> Path | None:
        # 自愈路径：模拟重新下载成功（损坏文件已被删除）
        assert not (snapshot / "model.bin").exists()
        return snapshot

    monkeypatch.setattr(transcriber, "_download_model_files", fake_download)

    assert transcriber._load_model(FakeModel, "medium", "cpu", "int8") is not None


def test_load_model_keeps_user_provided_dir_on_failure(tmp_path: Path) -> None:
    """用户手填的外部模型目录加载失败时不做任何删除。"""
    external = tmp_path / "my-model"
    make_snapshot_files(external)

    transcriber = WhisperTranscriber(tmp_path / "models")

    class FakeModel:
        def __init__(self, model_name: str, **kwargs):
            raise RuntimeError("File model.bin is incomplete: failed to read")

    with pytest.raises(RuntimeError, match="is incomplete"):
        transcriber._load_model(FakeModel, str(external), "cpu", "int8")
    assert (external / "model.bin").is_file()


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


# ---- 转写取消：段粒度协作式中止 ----

class _FakeSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    language = "zh"
    language_probability = 0.9
    duration = 100.0
    duration_after_vad = 90.0


class _FakeWhisperModel:
    def __init__(self, segments_factory) -> None:
        self._segments_factory = segments_factory

    def transcribe(self, *_args, **_kwargs):
        return self._segments_factory(), _FakeInfo()


def _transcriber_with_fake_model(
    tmp_path: Path, segments_factory
) -> WhisperTranscriber:
    transcriber = WhisperTranscriber(tmp_path / "models")
    transcriber._models[("base", "cpu")] = _FakeWhisperModel(segments_factory)
    transcriber._actual_models[("base", "cpu")] = "base"
    return transcriber


def test_transcribe_consumes_all_segments_without_cancel(tmp_path: Path) -> None:
    def segments():
        for i in range(10):
            yield _FakeSegment(float(i), float(i + 1), f"段{i}")

    transcriber = _transcriber_with_fake_model(tmp_path, segments)
    result = transcriber._transcribe_sync(
        tmp_path / "audio.mp3", "base", False, None, None
    )
    assert result["language"] == "zh"
    assert [s.text for s in result["segments"]] == [f"段{i}" for i in range(10)]


def test_transcribe_aborts_between_segments_when_cancelled(
    tmp_path: Path,
) -> None:
    import threading

    cancel_event = threading.Event()
    consumed: list[int] = []

    def segments():
        for i in range(10):
            if i == 2:
                cancel_event.set()
            consumed.append(i)
            yield _FakeSegment(float(i), float(i + 1), f"段{i}")

    transcriber = _transcriber_with_fake_model(tmp_path, segments)
    with pytest.raises(TranscriptionCancelledError):
        transcriber._transcribe_sync(
            tmp_path / "audio.mp3", "base", False, None, cancel_event
        )
    # 第 2 段被取出后、尚未加入结果列表前抛错：段 0/1 已消费
    assert consumed == [0, 1, 2]


@pytest.mark.asyncio
async def test_transcribe_converts_cancel_to_cancelled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading

    def segments():
        yield _FakeSegment(0.0, 1.0, "段0")

    transcriber = _transcriber_with_fake_model(tmp_path, segments)
    monkeypatch.setattr(
        transcriber,
        "_ensure_model",
        lambda model_class, model_name, model_to_load, use_gpu: ("base", "cpu"),
    )
    cancel_event = threading.Event()
    cancel_event.set()
    with pytest.raises(asyncio.CancelledError):
        await transcriber.transcribe(tmp_path / "audio.mp3", cancel_event=cancel_event)
