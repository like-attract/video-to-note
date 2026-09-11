import sys
import types
import urllib.error
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
    monkeypatch.setattr(transcriber, "_download_model_files", lambda name, cancel_event=None: None)
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
        transcriber, "_download_model_files", lambda name, cancel_event=None: download_calls.append(name) or None
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


def test_manual_model_dir_accepts_either_vocabulary_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户照下载页存下 vocabulary.json 也算导入完成：large-v3 官方仓根本没有 .txt。"""
    monkeypatch.setattr(whisper_asr, "MIN_MODEL_FILE_BYTES", tiny_min_sizes())
    transcriber = WhisperTranscriber(tmp_path)
    manual_dir = tmp_path / "manual" / "large-v3"
    make_snapshot_files(manual_dir)
    (manual_dir / "vocabulary.txt").rename(manual_dir / "vocabulary.json")

    assert transcriber._cached_model_path("large-v3") == manual_dir.resolve()
    assert transcriber._model_cache_status("large-v3") == "cached"

    # 两个名字都没有才是 incomplete：CTranslate2 缺词表会直接加载失败，不能放行
    (manual_dir / "vocabulary.json").unlink()
    assert transcriber._cached_model_path("large-v3") is None
    assert transcriber._model_cache_status("large-v3") == "incomplete"


def test_manual_import_file_list_offers_both_vocabulary_names() -> None:
    """导入引导清单：词表槽位列出两个可接受的名字，本仓库实际用的那个排前面。"""
    assert WhisperTranscriber.manual_import_files("large-v3") == [
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt 或 vocabulary.json",
    ]
    assert WhisperTranscriber.manual_import_files("turbo")[3].startswith("vocabulary.json")


def test_turbo_and_belle_repos_map_to_ct2_layouts() -> None:
    """turbo 的官方 CT2 不存在（Systran 仓 404），映射社区仓；CT2 转换仓用 vocabulary.json。"""
    assert whisper_asr.WHISPER_MODEL_REPOS["turbo"] == "deepdml/faster-whisper-large-v3-turbo-ct2"
    assert whisper_asr.WHISPER_MODEL_REPOS["belle-turbo-zh"] == (
        "wolfofbackstreet/faster-whisper-belle-whisper-large-v3-turbo-zh-ct2-int8"
    )
    # 未知模型名回落 Systran 命名约定
    assert WhisperTranscriber._model_repo("base") == "Systran/faster-whisper-base"
    assert WhisperTranscriber._model_repo("custom") == "Systran/faster-whisper-custom"

    for name in ("turbo", "belle-turbo-zh"):
        required = WhisperTranscriber._required_files(name)
        assert "vocabulary.json" in required
        assert "vocabulary.txt" not in required
        # faster-whisper 靠 preprocessor_config.json 读 feature_size（128 mel）
        assert "preprocessor_config.json" in required
    assert "vocabulary.txt" in WhisperTranscriber._required_files("base")


def test_belle_turbo_zh_snapshot_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """belle-turbo-zh 缓存目录按泛化命名（models--{org}--{repo}）+ vocabulary.json 识别。"""
    monkeypatch.setattr(whisper_asr, "MIN_MODEL_FILE_BYTES", tiny_min_sizes())
    snapshot = (
        tmp_path
        / "models--wolfofbackstreet--faster-whisper-belle-whisper-large-v3-turbo-zh-ct2-int8"
        / "snapshots"
        / "revision"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.bin").write_bytes(b"0" * 64)
    (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
    (snapshot / "vocabulary.json").write_text("{}", encoding="utf-8")
    (snapshot / "preprocessor_config.json").write_text("{}", encoding="utf-8")

    transcriber = WhisperTranscriber(tmp_path)
    assert transcriber._cached_model_path("belle-turbo-zh") == snapshot.resolve()
    assert transcriber._model_cache_status("belle-turbo-zh") == "cached"

    # 缺 vocabulary.json / preprocessor_config.json 时是 incomplete 而不是 cached
    (snapshot / "vocabulary.json").unlink()
    assert transcriber._model_cache_status("belle-turbo-zh") == "incomplete"
    (snapshot / "vocabulary.json").write_text("{}", encoding="utf-8")
    (snapshot / "preprocessor_config.json").unlink()
    assert transcriber._model_cache_status("belle-turbo-zh") == "incomplete"


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

    def fake_download_one(url: str, target: Path, headers: dict[str, str], cancel_event=None) -> bool:
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


def test_download_falls_back_to_other_vocabulary_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仓库用的词表名和清单不一致时（large-v3 只有 .json）：404 后换名重试，不能整单失败。"""
    transcriber = WhisperTranscriber(tmp_path)
    attempted: list[str] = []

    def fake_download_one(url: str, target: Path, headers: dict[str, str], cancel_event=None) -> bool:
        attempted.append(target.name)
        if target.name == "vocabulary.txt":
            transcriber._last_download_error = urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return False
        target.write_bytes(b"0" * 64)
        return True

    def fake_urlopen(request, timeout):  # noqa: ANN001
        return FakeResponse(headers={"X-Repo-Commit": "revision"})

    monkeypatch.setattr(transcriber, "_download_one_file", fake_download_one)
    monkeypatch.setattr("backend.whisper_asr.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(whisper_asr, "MIN_MODEL_FILE_BYTES", tiny_min_sizes())

    snapshot = transcriber._download_model_files_from_endpoint(
        "large-v3", "https://hf-mirror.com"
    )

    assert snapshot is not None
    assert attempted[-2:] == ["vocabulary.txt", "vocabulary.json"]
    # 换名成功后不能留下那个 404，否则下次失败会拿它冒充原因
    assert transcriber._last_download_error is None


def test_download_one_file_gives_up_immediately_on_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 是确定性结果：重试只会白等，一次就返回让调用方换名或换 endpoint。"""
    requests: list[str] = []
    slept: list[float] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001
        requests.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("backend.download.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("backend.download.time.sleep", slept.append)
    transcriber = WhisperTranscriber(tmp_path)

    assert transcriber._download_one_file(
        "https://example.com/vocabulary.txt", tmp_path / "vocabulary.txt", {}
    ) is False
    assert len(requests) == 1
    assert slept == []


def test_download_one_file_rejects_truncated_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连接中断但未抛错时不能把截断文件改名成正式文件（保留 .part 续传）。"""
    body = b"x" * 500

    def fake_urlopen(request, timeout):  # noqa: ANN001
        return FakeResponse(200, body, {"Content-Length": "1000"})

    monkeypatch.setattr("backend.download.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("backend.download.time.sleep", lambda _seconds: None)
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

    monkeypatch.setattr("backend.download.urllib.request.urlopen", fake_urlopen)
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

    def fake_download(name: str, cancel_event=None) -> Path | None:
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

    def fake_download(model_name: str, endpoint: str, cancel_event=None) -> Path | None:
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
    monkeypatch.setattr(transcriber, "_download_model_files", lambda name, cancel_event=None: None)

    class FailIfCalled:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("Hub fallback should not be attempted")

    with pytest.raises(RuntimeError, match="certificate verify failed"):
        transcriber._load_model(FailIfCalled, "base", "cpu", "int8")


# ---- 转写取消：段粒度协作式中止 ----

def test_run_model_beam_size_depends_on_device() -> None:
    """CPU 转写用 beam=1（速度优先），GPU 保持 beam=5。"""
    calls: list[dict] = []

    class FakeModel:
        def transcribe(self, _media_path: str, **kwargs):
            calls.append(kwargs)

    WhisperTranscriber._run_model(FakeModel(), Path("a.mp3"), None, "cpu")
    WhisperTranscriber._run_model(FakeModel(), Path("a.mp3"), None, "cuda")

    assert [call["beam_size"] for call in calls] == [1, 5]


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


def test_download_one_file_aborts_when_cancelled(tmp_path: Path) -> None:
    import asyncio
    import threading

    transcriber = WhisperTranscriber(tmp_path / "models")
    cancel_event = threading.Event()
    cancel_event.set()
    with pytest.raises(asyncio.CancelledError):
        transcriber._download_one_file(
            "https://example.com/model.bin", tmp_path / "model.bin", {}, cancel_event
        )


# ---- GPU 能用但缺运行库：推理阶段才抛错，也要退到 CPU ----

@pytest.mark.asyncio
async def test_gpu_inference_failure_falls_back_to_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归：测试同学机器上 "Library cublas64_12.dll is not found" 直接把任务打死在第 4 步。

    驱动在、缺 cuBLAS/cuDNN 运行库时，模型构造会成功，错误要到真正计算才抛，
    所以兜底不能只盖住构造阶段。
    """
    constructed: list[str] = []

    class FakeModel:
        def __init__(self, model_name: str, **kwargs):
            self.device = kwargs["device"]
            constructed.append(self.device)

        def transcribe(self, *_args, **_kwargs):
            if self.device == "cuda":
                raise RuntimeError(
                    "Library cublas64_12.dll is not found or cannot be loaded"
                )
            return (
                [_FakeSegment(0.0, 3.0, "口播内容")],
                _FakeInfo(),
            )

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel)
    )
    transcriber = WhisperTranscriber(tmp_path / "models")
    monkeypatch.setattr(
        transcriber, "_download_model_files", lambda name, cancel_event=None: None
    )
    media = tmp_path / "audio.m4a"
    media.write_bytes(b"x")

    result = await transcriber.transcribe(media, "base", use_gpu=True)

    assert result["device"] == "cpu"
    assert [segment.text for segment in result["segments"]] == ["口播内容"]
    assert "GPU 转写不可用" in result["fallback_note"]
    assert "cublas64_12.dll" in result["fallback_note"]
    assert "CUDA 12" in result["fallback_note"]
    assert result["model_load_seconds"] >= 0
    assert result["transcribe_seconds"] >= 0

    # 坏掉的 cuda 模型不能留在缓存里让后续任务反复撞
    await transcriber.transcribe(media, "base", use_gpu=True)
    assert constructed == ["cuda", "cpu"]
    assert ("base", "cuda") not in transcriber._models


@pytest.mark.asyncio
async def test_gpu_construction_failure_is_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构造阶段就失败的 GPU，同样要记住：否则每个任务都重付一次注定失败的初始化。"""
    constructed: list[str] = []

    class FakeModel:
        def __init__(self, model_name: str, **kwargs):
            self.device = kwargs["device"]
            constructed.append(self.device)
            if self.device == "cuda":
                raise RuntimeError("CUDA driver version is insufficient")

        def transcribe(self, *_args, **_kwargs):
            return [_FakeSegment(0.0, 3.0, "口播内容")], _FakeInfo()

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel)
    )
    transcriber = WhisperTranscriber(tmp_path / "models")
    monkeypatch.setattr(
        transcriber, "_download_model_files", lambda name, cancel_event=None: None
    )
    media = tmp_path / "audio.m4a"
    media.write_bytes(b"x")

    first = await transcriber.transcribe(media, "base", use_gpu=True)
    assert first["device"] == "cpu"
    assert "本机 GPU 不可用" in first["fallback_note"]

    second = await transcriber.transcribe(media, "base", use_gpu=True)
    assert second["device"] == "cpu"
    assert second["fallback_note"] is None
    # 第二次直接命中已缓存的 CPU 模型：既不再试 cuda，也不再重新加载
    assert constructed == ["cuda", "cpu"]

