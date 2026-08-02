from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .transcript import TranscriptSegment


class WhisperTranscriber:
    """Lazy faster-whisper wrapper with CPU-safe defaults."""

    def __init__(self, download_root: Path | None = None) -> None:
        self._models: dict[tuple[str, str], Any] = {}
        self._actual_models: dict[tuple[str, str], str] = {}
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
        # Hugging Face's Xet backend is fragile on some Windows networks. The
        # regular HTTP downloader is slower but much more predictable here.
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Run pip install -r backend/requirements.txt"
            ) from exc

        device = "cuda" if use_gpu else "cpu"
        compute_type = "float16" if use_gpu else "int8"
        cache_key = (model_name, device)
        model_to_load = self._preferred_model(model_name)
        if cache_key not in self._models:
            try:
                self._models[cache_key] = self._load_model(
                    WhisperModel, model_to_load, device, compute_type
                )
                self._actual_models[cache_key] = model_to_load
            except Exception as gpu_error:
                if not use_gpu:
                    self._load_cached_base_or_raise(
                        WhisperModel, cache_key, model_name, device, gpu_error
                    )
                else:
                    device = "cpu"
                    compute_type = "int8"
                    cache_key = (model_name, device)
                    if cache_key not in self._models:
                        try:
                            self._models[cache_key] = self._load_model(
                                WhisperModel, model_to_load, device, compute_type
                            )
                            self._actual_models[cache_key] = model_to_load
                        except Exception as cpu_error:
                            self._load_cached_base_or_raise(
                                WhisperModel, cache_key, model_name, device, cpu_error
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

    def _load_model(
        self,
        model_class: Any,
        model_name: str,
        device: str,
        compute_type: str,
    ) -> Any:
        cached_path = self._cached_model_path(model_name)
        model_reference = str(cached_path) if cached_path else model_name
        options: dict[str, Any] = {
            "device": device,
            "compute_type": compute_type,
        }
        if self.download_root and not cached_path:
            options["download_root"] = str(self.download_root)
        return model_class(model_reference, **options)

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
        if (
            requested_model != "base"
            and not self._cached_model_path(requested_model)
            and self._cached_model_path("base")
        ):
            return "base"
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

    def _model_load_error(self, model_name: str, error: Exception) -> RuntimeError:
        message = str(error).strip() or error.__class__.__name__
        cache_hint = f"，缓存目录：{self.download_root}" if self.download_root else ""
        return RuntimeError(
            f"Whisper 模型 {model_name} 下载或加载失败{cache_hint}。"
            "请检查网络、代理和磁盘权限；首次使用该模型需要从 Hugging Face 下载。"
            f"原始错误：{message}"
        )
