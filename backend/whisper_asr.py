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
    ) -> dict:
        return await asyncio.to_thread(
            self._transcribe_sync, media_path, model_name, use_gpu, initial_prompt
        )

    def _transcribe_sync(
        self,
        media_path: Path,
        model_name: str,
        use_gpu: bool,
        initial_prompt: str | None,
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

        model_to_load = self._preferred_model(model_name)
        cache_key, device = self._ensure_model(
            WhisperModel, model_name, model_to_load, use_gpu
        )

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
        segments = [
            TranscriptSegment(float(item.start), float(item.end), item.text.strip())
            for item in raw_segments
            if item.text.strip()
        ]
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
                    model_class, model_to_load, device, compute_type
                )
                self._actual_models[cache_key] = model_to_load
            except Exception as gpu_error:
                if not use_gpu:
                    self._load_cached_base_or_raise(
                        model_class, cache_key, model_name, device, gpu_error
                    )
                else:
                    device = "cpu"
                    cache_key = (model_name, device)
                    if cache_key not in self._models:
                        try:
                            self._models[cache_key] = self._load_model(
                                model_class, model_to_load, device, "int8"
                            )
                            self._actual_models[cache_key] = model_to_load
                        except Exception as cpu_error:
                            self._load_cached_base_or_raise(
                                model_class, cache_key, model_name, device, cpu_error
                            )
        return cache_key, device

    def _load_model(
        self,
        model_class: Any,
        model_name: str,
        device: str,
        compute_type: str,
    ) -> Any:
        cached_path = self._cached_model_path(model_name)
        if not cached_path and self.download_root:
            # 自建下载（默认 hf-mirror 镜像），避免 huggingface_hub 对镜像的兼容问题
            cached_path = self._download_model_files(model_name)
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
        return model_class(model_reference, **options)

    WHISPER_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")

    def _download_model_files(self, model_name: str) -> Path | None:
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
                snapshot = self._download_model_files_from_endpoint(model_name, candidate)
                if snapshot:
                    return snapshot
            except Exception as exc:
                self._last_download_error = exc
        return None

    def _download_model_files_from_endpoint(
        self, model_name: str, endpoint: str
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
            target = snapshot / filename
            if target.is_file() and target.stat().st_size > 0:
                continue
            if not self._download_one_file(f"{base_url}/{filename}", target, headers):
                if self._last_download_error:
                    raise self._last_download_error
                return None
        if not all((snapshot / name).is_file() for name in self.WHISPER_MODEL_FILES):
            return None
        return snapshot

    def _download_one_file(self, url: str, target: Path, headers: dict[str, str]) -> bool:
        """单文件下载：断点续传（Range）+ 重试三次。"""
        part = target.with_suffix(target.suffix + ".part")
        self._last_download_error = None
        for attempt in range(3):
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
                    mode = "ab" if resume else "wb"
                    with open(part, mode) as out:
                        while True:
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                if part.stat().st_size == 0:
                    part.unlink(missing_ok=True)
                    return False
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
    ) -> None:
        if requested_model == "base" or not self._cached_model_path("base"):
            raise self._model_load_error(requested_model, original_error) from original_error
        self._models[cache_key] = self._load_model(
            model_class, "base", device, "int8"
        )
        self._actual_models[cache_key] = "base"

    def _preferred_model(self, requested_model: str) -> str:
        # 让用户选择的模型先真正尝试加载/下载；只有 _ensure_model 捕获到
        # 下载或加载异常时，才由 _load_cached_base_or_raise 降级到 base。
        return requested_model

    def _cached_model_path(self, model_name: str) -> Path | None:
        direct_path = Path(model_name)
        if direct_path.is_dir():
            return direct_path.resolve()

        roots: list[Path] = []
        if self.download_root:
            roots.append(self.download_root)
        try:
            from huggingface_hub.constants import HF_HUB_CACHE

            roots.append(Path(HF_HUB_CACHE))
        except ImportError:
            pass

        repo_name = f"models--Systran--faster-whisper-{model_name}"
        required_files = {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt"}
        for root in roots:
            snapshots_dir = root / repo_name / "snapshots"
            if not snapshots_dir.is_dir():
                continue
            for snapshot in sorted(
                snapshots_dir.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True
            ):
                if snapshot.is_dir() and all(
                    (snapshot / filename).is_file() for filename in required_files
                ):
                    return snapshot.resolve()
        return None

    def _model_cache_status(self, model_name: str) -> str:
        """模型缓存状态：cached（完整）/ incomplete（部分文件）/ missing（无）。"""
        if self._cached_model_path(model_name):
            return "cached"
        roots: list[Path] = []
        if self.download_root:
            roots.append(self.download_root)
        try:
            from huggingface_hub.constants import HF_HUB_CACHE

            roots.append(Path(HF_HUB_CACHE))
        except ImportError:
            pass
        repo_name = f"models--Systran--faster-whisper-{model_name}"
        for root in roots:
            snapshots_dir = root / repo_name / "snapshots"
            if not snapshots_dir.is_dir():
                continue
            for snapshot in snapshots_dir.iterdir():
                if snapshot.is_dir() and any(
                    (snapshot / filename).is_file()
                    for filename in self.WHISPER_MODEL_FILES
                ):
                    return "incomplete"
        return "missing"

    def _model_load_error(self, model_name: str, error: Exception) -> RuntimeError:
        message = str(error).strip() or error.__class__.__name__
        cache_hint = f"，缓存目录：{self.download_root}" if self.download_root else ""
        return RuntimeError(
            f"Whisper 模型 {model_name} 下载或加载失败{cache_hint}。"
            "下载默认走 hf-mirror.com 镜像；若仍失败，请检查网络/代理，"
            "或通过 HF_ENDPOINT 环境变量指定可用镜像后重试。"
            f"原始错误：{message}"
        )
