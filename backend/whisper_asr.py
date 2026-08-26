from __future__ import annotations

import asyncio
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .transcript import TranscriptSegment

# 缓存文件完整性的下限校验：历史上中断的下载可能遗留只有几十字节的
# 损坏 model.bin（文件存在且非空，但 CTranslate2 无法加载），仅靠
# "文件存在"判断会把损坏缓存当成已缓存，且下载器会跳过不修复。
# 下限取真实体积的约 1/2 以下（最小的 tiny model.bin 也有 ~75MB）。
MIN_MODEL_FILE_BYTES = {
    "model.bin": 30 * 1024 * 1024,
    "tokenizer.json": 100 * 1024,
    "vocabulary.txt": 100 * 1024,
}

# CTranslate2 加载损坏模型时的典型报错特征（小写匹配）。
CORRUPT_MODEL_ERROR_MARKERS = ("is incomplete", "failed to read a buffer")


class TranscriptionCancelledError(Exception):
    """转写过程中检测到用户取消请求（协作式中止信号）。

    由 async transcribe() 转换为 asyncio.CancelledError，走任务编排里
    统一的取消处理路径（任务标记为已取消）。
    """


class WhisperTranscriber:
    """Lazy faster-whisper wrapper with CPU-safe defaults."""

    def __init__(self, download_root: Path | None = None) -> None:
        self._models: dict[tuple[str, str], Any] = {}
        self._actual_models: dict[tuple[str, str], str] = {}
        self._last_download_error: Exception | None = None
        self._model_lock = threading.Lock()
        self.download_root = download_root.resolve() if download_root else None
        if self.download_root:
            self.download_root.mkdir(parents=True, exist_ok=True)

    async def transcribe(
        self,
        media_path: Path,
        model_name: str = "base",
        use_gpu: bool = False,
        initial_prompt: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        """转写音视频文件。

        cancel_event：每任务取消事件；转写过程中在段与段之间检查，检测到
        已设置时抛 TranscriptionCancelledError 并转为 asyncio.CancelledError。
        传入 None（如冒烟脚本）时不检查取消。
        """
        try:
            return await asyncio.to_thread(
                self._transcribe_sync,
                media_path,
                model_name,
                use_gpu,
                initial_prompt,
                cancel_event,
            )
        except TranscriptionCancelledError as exc:
            raise asyncio.CancelledError("转写已被用户取消") from exc

    def _transcribe_sync(
        self,
        media_path: Path,
        model_name: str,
        use_gpu: bool,
        initial_prompt: str | None,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        # Hugging Face 直连在国内网络常超时，默认走 hf-mirror.com 镜像；
        # 可通过环境变量 HF_ENDPOINT 覆盖（切回官方源或自建镜像）。
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Run pip install -r backend/requirements.txt"
            ) from exc

        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelledError()
        model_to_load = self._preferred_model(model_name)
        cache_key, device = self._ensure_model(
            WhisperModel, model_name, model_to_load, use_gpu, cancel_event
        )
        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelledError()

        model = self._models[cache_key]
        raw_segments, info = model.transcribe(
            str(media_path),
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            repetition_penalty=1.1,
            no_repeat_ngram_size=3,
            hallucination_silence_threshold=2.0,
            initial_prompt=initial_prompt,
            word_timestamps=False,
        )
        # 惰性生成器逐段消费：每取一段前检查取消事件，用户取消时在段与段
        # 之间停下，而不是等整段音频转写完。单段 C 层解码中途无法打断。
        segments: list[TranscriptSegment] = []
        for item in raw_segments:
            if cancel_event is not None and cancel_event.is_set():
                raise TranscriptionCancelledError()
            if item.text.strip():
                segments.append(
                    TranscriptSegment(
                        float(item.start), float(item.end), item.text.strip()
                    )
                )
        return {
            "text": " ".join(item.text for item in segments),
            "segments": segments,
            "language": info.language,
            "language_probability": info.language_probability,
            "device": device,
            "model": self._actual_models.get(cache_key, model_name),
            "requested_model": model_name,
            "duration": float(info.duration or 0),
            "duration_after_vad": float(info.duration_after_vad or 0),
        }

    def _ensure_model(
        self,
        model_class: Any,
        model_name: str,
        model_to_load: str,
        use_gpu: bool,
        cancel_event: threading.Event | None = None,
    ) -> tuple[tuple[str, str], str]:
        """Load each shared model once, including first-download and GPU fallback."""
        device = "cuda" if use_gpu else "cpu"
        compute_type = "float16" if use_gpu else "int8"
        cache_key = (model_name, device)
        with self._model_lock:
            if cache_key in self._models:
                return cache_key, device
            try:
                self._models[cache_key] = self._load_model(
                    model_class, model_to_load, device, compute_type, cancel_event
                )
                self._actual_models[cache_key] = model_to_load
            except Exception as gpu_error:
                if not use_gpu:
                    self._load_cached_base_or_raise(
                        model_class, cache_key, model_name, device, gpu_error, cancel_event
                    )
                else:
                    device = "cpu"
                    cache_key = (model_name, device)
                    if cache_key not in self._models:
                        try:
                            self._models[cache_key] = self._load_model(
                                model_class, model_to_load, device, "int8", cancel_event
                            )
                            self._actual_models[cache_key] = model_to_load
                        except Exception as cpu_error:
                            self._load_cached_base_or_raise(
                                model_class, cache_key, model_name, device, cpu_error, cancel_event
                            )
        return cache_key, device

    def _load_model(
        self,
        model_class: Any,
        model_name: str,
        device: str,
        compute_type: str,
        cancel_event: threading.Event | None = None,
    ) -> Any:
        cached_path = self._cached_model_path(model_name)
        if not cached_path and self.download_root:
            # 自建下载（默认 hf-mirror 镜像），避免 huggingface_hub 对镜像的兼容问题
            cached_path = self._download_model_files(model_name, cancel_event)
            # 不要在自建下载失败后再让 huggingface_hub 重复请求同一个地址；
            # 那会把真正的网络/证书错误包装成“找不到 snapshot”。
            if not cached_path and self._last_download_error:
                raise self._last_download_error
        model_reference = str(cached_path) if cached_path else model_name
        options: dict[str, Any] = {
            "device": device,
            "compute_type": compute_type,
        }
        if self.download_root and not cached_path:
            options["download_root"] = str(self.download_root)
        try:
            return model_class(model_reference, **options)
        except Exception as exc:
            # 缓存里的模型文件可能已损坏（历史中断下载遗留，体积校验也可能
            # 漏掉的截断）。识别典型报错后删除损坏文件并自动重下一次，
            # 避免“已缓存却永远加载失败”的死循环。
            if (
                cached_path
                and self._looks_like_corrupt_model_error(exc)
                and self._is_managed_model_dir(Path(cached_path))
            ):
                self._discard_corrupt_model(Path(cached_path))
                refreshed = (
                    self._download_model_files(model_name, cancel_event)
                    if self.download_root
                    else None
                )
                if refreshed:
                    return model_class(str(refreshed), **options)
            raise

    WHISPER_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")

    def _download_model_files(
        self, model_name: str, cancel_event: threading.Event | None = None
    ) -> Path | None:
        """下载 Whisper 模型到标准缓存目录结构。

        新版 huggingface_hub 校验重定向响应的 X-Repo-Commit 头，hf-mirror
        等镜像不返回该头导致原生下载失败，因此这里自己实现下载：
        走 HF_ENDPOINT（默认 hf-mirror.com），支持断点续传与重试。
        """
        self._last_download_error = None
        configured_endpoint = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
        endpoint = configured_endpoint.rstrip("/")
        endpoints = [endpoint]
        # 镜像证书或代理链路异常时，官方源通常仍可用。仅对默认镜像追加，
        # 不改变用户通过 HF_ENDPOINT 指定的其他自建 endpoint。
        if endpoint == "https://hf-mirror.com":
            endpoints.append("https://huggingface.co")

        for candidate in endpoints:
            try:
                snapshot = self._download_model_files_from_endpoint(
                    model_name, candidate, cancel_event
                )
                if snapshot:
                    return snapshot
            except Exception as exc:
                self._last_download_error = exc
        return None

    def _download_model_files_from_endpoint(
        self, model_name: str, endpoint: str, cancel_event: threading.Event | None = None
    ) -> Path | None:
        """从单个 Hugging Face endpoint 下载完整快照。"""
        repo = f"Systran/faster-whisper-{model_name}"
        base_url = f"{endpoint}/{repo}/resolve/main"
        headers = {"User-Agent": "VideoToNo/1.0", "Accept-Encoding": "identity"}

        # 从 307 重定向响应头取 commit hash（目录命名用）
        commit = "main"
        try:
            request = urllib.request.Request(
                f"{base_url}/config.json", headers=headers, method="HEAD"
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                commit = response.headers.get("X-Repo-Commit") or commit
        except Exception:
            pass

        snapshot = (
            self.download_root
            / f"models--Systran--faster-whisper-{model_name}"
            / "snapshots"
            / commit
        )
        snapshot.mkdir(parents=True, exist_ok=True)
        for filename in self.WHISPER_MODEL_FILES:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("任务已取消")
            target = snapshot / filename
            # 已有文件必须通过完整性下限校验；损坏/截断的遗留文件会被重新下载
            if self._file_complete(target):
                continue
            if not self._download_one_file(
                f"{base_url}/{filename}", target, headers, cancel_event
            ):
                if self._last_download_error:
                    raise self._last_download_error
                return None
        if not self._snapshot_complete(snapshot):
            return None
        return snapshot

    def _download_one_file(
        self,
        url: str,
        target: Path,
        headers: dict[str, str],
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """单文件下载：断点续传（Range）+ 重试三次 + Content-Length 完整性校验。"""
        part = target.with_suffix(target.suffix + ".part")
        self._last_download_error = None
        for attempt in range(3):
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("任务已取消")
            try:
                resume = part.stat().st_size if part.is_file() else 0
                request_headers = dict(headers)
                if resume:
                    request_headers["Range"] = f"bytes={resume}-"
                request = urllib.request.Request(url, headers=request_headers)
                with urllib.request.urlopen(request, timeout=60) as response:
                    if response.status == 200 and resume:
                        # 服务端忽略 Range，从头重下
                        part.unlink(missing_ok=True)
                        resume = 0
                    expected_total: int | None = None
                    content_length = response.headers.get("Content-Length")
                    if content_length and content_length.isdigit():
                        expected_total = int(content_length) + resume
                    mode = "ab" if resume else "wb"
                    with open(part, mode) as out:
                        while True:
                            if cancel_event is not None and cancel_event.is_set():
                                raise asyncio.CancelledError("任务已取消")
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                actual_size = part.stat().st_size
                if actual_size == 0:
                    part.unlink(missing_ok=True)
                    return False
                if expected_total is not None and actual_size != expected_total:
                    # 连接中断但未抛异常时会得到截断文件：保留 .part 以便断点续传，
                    # 绝不能把不完整文件改名成正式文件（历史上损坏缓存的来源之一）。
                    self._last_download_error = RuntimeError(
                        f"下载不完整：已接收 {actual_size} 字节，预期 {expected_total} 字节"
                    )
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if target.exists():
                    target.unlink()
                part.rename(target)
                return True
            except Exception as exc:
                self._last_download_error = exc
                time.sleep(1.5 * (attempt + 1))
        return False

    def _load_cached_base_or_raise(
        self,
        model_class: Any,
        cache_key: tuple[str, str],
        requested_model: str,
        device: str,
        original_error: Exception,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if requested_model == "base" or not self._cached_model_path("base"):
            raise self._model_load_error(requested_model, original_error) from original_error
        self._models[cache_key] = self._load_model(
            model_class, "base", device, "int8", cancel_event
        )
        self._actual_models[cache_key] = "base"

    def _preferred_model(self, requested_model: str) -> str:
        # 让用户选择的模型先真正尝试加载/下载；只有 _ensure_model 捕获到
        # 下载或加载异常时，才由 _load_cached_base_or_raise 降级到 base。
        return requested_model

    def _snapshot_dirs(self, model_name: str) -> list[Path]:
        """该模型可能存放文件的所有目录：手动导入目录优先，其次各 revision 快照。"""
        dirs: list[Path] = []
        repo_name = f"models--Systran--faster-whisper-{model_name}"
        roots: list[Path] = []
        if self.download_root:
            # 手动导入约定目录：manual/{model}/ 或 manual/faster-whisper-{model}/
            for alias in (model_name, f"faster-whisper-{model_name}"):
                dirs.append(self.download_root / "manual" / alias)
            roots.append(self.download_root)
        try:
            from huggingface_hub.constants import HF_HUB_CACHE

            roots.append(Path(HF_HUB_CACHE))
        except ImportError:
            pass
        for root in roots:
            snapshots_dir = root / repo_name / "snapshots"
            if not snapshots_dir.is_dir():
                continue
            dirs.extend(
                sorted(
                    (path for path in snapshots_dir.iterdir() if path.is_dir()),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
            )
        return dirs

    def _cached_model_path(self, model_name: str) -> Path | None:
        direct_path = Path(model_name)
        if direct_path.is_dir():
            return direct_path.resolve()

        for snapshot in self._snapshot_dirs(model_name):
            if self._snapshot_complete(snapshot):
                return snapshot.resolve()
        return None

    def _model_cache_status(self, model_name: str) -> str:
        """模型缓存状态：cached（完整）/ incomplete（部分或损坏文件）/ missing（无）。"""
        if self._cached_model_path(model_name):
            return "cached"
        for snapshot in self._snapshot_dirs(model_name):
            if snapshot.is_dir() and any(
                (snapshot / filename).is_file()
                for filename in self.WHISPER_MODEL_FILES
            ):
                return "incomplete"
        return "missing"

    @classmethod
    def _snapshot_complete(cls, snapshot: Path) -> bool:
        return all(cls._file_complete(snapshot / name) for name in cls.WHISPER_MODEL_FILES)

    @staticmethod
    def _file_complete(path: Path) -> bool:
        """文件存在且通过体积下限校验（拦截损坏/截断的遗留文件）。"""
        if not path.is_file():
            return False
        minimum = MIN_MODEL_FILE_BYTES.get(path.name, 1)
        return path.stat().st_size >= minimum

    @staticmethod
    def _looks_like_corrupt_model_error(error: Exception) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in CORRUPT_MODEL_ERROR_MARKERS)

    def _is_managed_model_dir(self, path: Path) -> bool:
        """目录是否由本工具的缓存管理；用户手填的外部路径绝不自动删改。"""
        if self.download_root:
            try:
                path.relative_to(self.download_root)
                return True
            except ValueError:
                pass
        try:
            from huggingface_hub.constants import HF_HUB_CACHE

            path.relative_to(Path(HF_HUB_CACHE))
            return True
        except (ImportError, ValueError):
            return False

    def _discard_corrupt_model(self, snapshot: Path) -> None:
        """删除损坏的模型文件（加载报错的 model.bin 及未完成分片、体积异常的小文件）。"""
        (snapshot / "model.bin").unlink(missing_ok=True)
        (snapshot / "model.bin.part").unlink(missing_ok=True)
        for name in self.WHISPER_MODEL_FILES:
            path = snapshot / name
            minimum = MIN_MODEL_FILE_BYTES.get(name, 1)
            if path.is_file() and path.stat().st_size < minimum:
                path.unlink(missing_ok=True)

    def _model_load_error(self, model_name: str, error: Exception) -> RuntimeError:
        message = str(error).strip() or error.__class__.__name__
        cache_hint = f"，缓存目录：{self.download_root}" if self.download_root else ""
        manual_hint = (
            f"也可手动下载 4 个模型文件放入缓存目录的 manual/{model_name} 子文件夹"
            "（界面「手动导入模型」按钮可直达）后重试。"
            if self.download_root
            else ""
        )
        return RuntimeError(
            f"Whisper 模型 {model_name} 下载或加载失败{cache_hint}。{manual_hint}"
            "下载默认走 hf-mirror.com 镜像；若仍失败，请检查网络/代理，"
            "或通过 HF_ENDPOINT 环境变量指定可用镜像后重试。"
            f"原始错误：{message}"
        )
