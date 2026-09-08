"""sherpa-onnx Paraformer CPU 转写引擎（仅中文）。

与 WhisperTranscriber 保持同一返回契约（segments/text/duration/device/…），
调用方按 model_name 路由，两种引擎产物可以互换。组成部分：

- asr：Paraformer-large int8（约 230MB，非自回归，CPU 上明显快于 whisper）
- punc：ct-transformer 标点（约 292MB；下载失败只降级为"无标点"，不判任务失败）
- vad：silero v4（约 2.2MB），按静音切段，段粒度支持协作式取消

模型全部走 hf-mirror（与 whisper 下载器同一 endpoint 约定），
下载复用 backend.download 的断点续传实现。
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from .download import download_to_file
from .whisper_asr import TranscriptionCancelledError
from .transcript import TranscriptSegment

HF_ENDPOINT = "https://hf-mirror.com"
HEADERS = {"User-Agent": "VideoToNo/1.0", "Accept-Encoding": "identity"}

# 每个组件的 HF 仓库与必需文件（sherpa-onnx 转换版把词表内嵌进 onnx 元数据，
# 所以标点只需要 model.onnx；ASR 需要 tokens.txt）。
MODEL_SPECS = {
    "asr": {
        "repo": "csukuangfj/sherpa-onnx-paraformer-zh-2023-09-14",
        "files": ("model.int8.onnx", "tokens.txt"),
    },
    "punc": {
        "repo": "csukuangfj/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12",
        "files": ("model.onnx",),
    },
    "vad": {
        "repo": "csukuangfj/vad",
        "files": ("silero_vad.onnx",),
    },
}

# 体积下限拦截截断/损坏的缓存文件（真实体积的约 1/2 以下）。
# 注意 tokens.txt 真实只有 ~74KB，别按词表量级估。
MIN_FILE_BYTES = {
    "model.int8.onnx": 100 * 1024 * 1024,
    "tokens.txt": 10 * 1024,
    "model.onnx": 100 * 1024 * 1024,
    "silero_vad.onnx": 512 * 1024,
}

SAMPLE_RATE = 16000
# Paraformer 按段推理；超过该秒数的 VAD 段切成等长小片，避免内存与精度问题
MAX_PIECE_SECONDS = 25
VAD_WINDOW = 512


class ParaformerTranscriber:
    """Lazy sherpa-onnx wrapper with CPU-only defaults."""

    def __init__(self, download_root: Path | None = None) -> None:
        self._models: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self.download_root = download_root.resolve() / "sherpa" if download_root else None
        if self.download_root:
            self.download_root.mkdir(parents=True, exist_ok=True)

    async def transcribe(
        self,
        media_path: Path,
        model_name: str = "paraformer-zh",
        use_gpu: bool = False,
        initial_prompt: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        try:
            return await asyncio.to_thread(
                self._transcribe_sync, media_path, model_name, use_gpu, cancel_event
            )
        except TranscriptionCancelledError as exc:
            raise asyncio.CancelledError("转写已被用户取消") from exc

    # ---- 模型缓存与加载 ----

    def _component_dir(self, component: str) -> Path:
        if not self.download_root:
            raise RuntimeError("Paraformer 引擎需要配置模型缓存目录")
        return self.download_root / component

    def _file_complete(self, path: Path) -> bool:
        if not path.is_file():
            return False
        minimum = MIN_FILE_BYTES.get(path.name, 1)
        return path.stat().st_size >= minimum

    def cache_status(self) -> str:
        """cached / incomplete / missing。标点可降级：整体缺失不算 incomplete。"""
        any_file = False
        all_complete = True
        for component, spec in MODEL_SPECS.items():
            directory = self._component_dir(component)
            exists = any((directory / name).is_file() for name in spec["files"])
            complete = all(self._file_complete(directory / name) for name in spec["files"])
            if complete:
                any_file = True
                continue
            if component == "punc" and not exists:
                continue
            all_complete = False
            any_file = any_file or exists
        if all_complete:
            return "cached"
        return "incomplete" if any_file else "missing"

    def _download_component(self, component: str, cancel_event: threading.Event | None) -> Path:
        spec = MODEL_SPECS[component]
        directory = self._component_dir(component)
        directory.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for filename in spec["files"]:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("任务已取消")
            target = directory / filename
            if self._file_complete(target):
                continue
            url = f"{HF_ENDPOINT}/{spec['repo']}/resolve/main/{filename}"
            ok, error = download_to_file(url, target, HEADERS, cancel_event)
            if not ok:
                last_error = error
        if any(
            not self._file_complete(directory / name) for name in MODEL_SPECS[component]["files"]
        ):
            raise RuntimeError(
                f"Paraformer {component} 模型下载失败：{last_error}。"
                f"默认走 hf-mirror.com 镜像，请检查网络/代理或通过 HF_ENDPOINT 指定可用镜像。"
            )
        return directory

    def _ensure_models(self, cancel_event: threading.Event | None) -> dict[str, Any]:
        if self._models is not None:
            return self._models
        with self._lock:
            if self._models is not None:
                return self._models
            try:
                import sherpa_onnx
            except ImportError as exc:
                raise RuntimeError("sherpa-onnx is not installed") from exc

            asr_dir = self._download_component("asr", cancel_event)
            vad_dir = self._download_component("vad", cancel_event)
            recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=str(asr_dir / "model.int8.onnx"),
                tokens=str(asr_dir / "tokens.txt"),
                num_threads=2,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                decoding_method="greedy_search",
            )
            vad_config = sherpa_onnx.VadModelConfig()
            # threshold 等参数挂在 silero_vad 子对象上，不在顶层
            vad_config.silero_vad.model = str(vad_dir / "silero_vad.onnx")
            vad_config.silero_vad.threshold = 0.5
            vad_config.silero_vad.min_silence_duration = 0.5
            vad_config.silero_vad.min_speech_duration = 0.25
            # 连续讲话不超过 10s 强制断句，否则字幕时间戳粒度太粗
            vad_config.silero_vad.max_speech_duration = 10.0
            vad_config.silero_vad.window_size = VAD_WINDOW
            vad_config.sample_rate = SAMPLE_RATE

            punct = None
            punc_note: str | None = None
            try:
                punc_dir = self._download_component("punc", cancel_event)
                punct = sherpa_onnx.OfflinePunctuation(
                    sherpa_onnx.OfflinePunctuationConfig(
                        model=sherpa_onnx.OfflinePunctuationModelConfig(
                            ct_transformer=str(punc_dir / "model.onnx"),
                            num_threads=2,
                            provider="cpu",
                            debug=False,
                        )
                    )
                )
            except Exception as exc:
                punc_note = f"标点模型不可用（{exc}），本次转写结果没有标点。"

            self._models = {
                "recognizer": recognizer,
                "vad_config": vad_config,
                "punct": punct,
                "note": punc_note,
            }
            return self._models

    # ---- 转写 ----

    def _transcribe_sync(
        self,
        media_path: Path,
        model_name: str,
        use_gpu: bool,
        cancel_event: threading.Event | None,
    ) -> dict:
        if cancel_event is not None and cancel_event.is_set():
            raise TranscriptionCancelledError()
        started = time.monotonic()
        models = self._ensure_models(cancel_event)
        load_seconds = round(time.monotonic() - started, 1)
        recognizer = models["recognizer"]
        punct = models["punct"]

        import faster_whisper

        audio = faster_whisper.decode_audio(str(media_path), sampling_rate=SAMPLE_RATE)
        duration = len(audio) / SAMPLE_RATE

        transcribe_started = time.monotonic()
        segments: list[TranscriptSegment] = []
        for piece_start_sample, piece in self._vad_pieces(
            audio, models["vad_config"], cancel_event
        ):
            if cancel_event is not None and cancel_event.is_set():
                raise TranscriptionCancelledError()
            stream = recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, piece)
            recognizer.decode_stream(stream)
            text = stream.result.text.strip()
            if not text:
                continue
            if punct is not None:
                text = punct.add_punctuation(text).strip()
            if text:
                segments.append(
                    TranscriptSegment(
                        round(piece_start_sample / SAMPLE_RATE, 2),
                        round((piece_start_sample + len(piece)) / SAMPLE_RATE, 2),
                        text,
                    )
                )

        notes: list[str] = []
        if models.get("note"):
            notes.append(models["note"])
        if use_gpu:
            notes.append("Paraformer 引擎当前仅支持 CPU 转写。")

        return {
            "text": "".join(item.text for item in segments),
            "segments": segments,
            "language": "zh",
            "language_probability": 1.0,
            "device": "cpu",
            "model": "paraformer-zh",
            "requested_model": model_name,
            "duration": duration,
            "duration_after_vad": sum(
                (seg.end - seg.start) for seg in segments
            ),
            "fallback_note": " ".join(notes) if notes else None,
            "model_load_seconds": load_seconds,
            "transcribe_seconds": round(time.monotonic() - transcribe_started, 1),
        }

    def _vad_pieces(
        self,
        audio: np.ndarray,
        vad_config: Any,
        cancel_event: threading.Event | None,
    ) -> list[tuple[int, np.ndarray]]:
        """VAD 切段；超长段再按 MAX_PIECE_SECONDS 切小片。返回 (起始采样点, 音频片)。"""
        import sherpa_onnx

        detector = sherpa_onnx.VoiceActivityDetector(
            vad_config,
            buffer_size_in_seconds=120,
        )
        raw_segments: list[tuple[int, np.ndarray]] = []
        for offset in range(0, len(audio), VAD_WINDOW):
            if cancel_event is not None and cancel_event.is_set():
                raise TranscriptionCancelledError()
            detector.accept_waveform(audio[offset : offset + VAD_WINDOW])
            while not detector.empty():
                segment = detector.front
                raw_segments.append((int(segment.start), np.asarray(segment.samples)))
                detector.pop()
        if detector.is_speech_detected():
            # 收尾：补静音让最后一段闭合
            silence = np.zeros(VAD_WINDOW, dtype=np.float32)
            for _ in range(64):
                detector.accept_waveform(silence)
                if not detector.is_speech_detected():
                    break
            while not detector.empty():
                segment = detector.front
                raw_segments.append((int(segment.start), np.asarray(segment.samples)))
                detector.pop()

        pieces: list[tuple[int, np.ndarray]] = []
        max_samples = MAX_PIECE_SECONDS * SAMPLE_RATE
        for start, samples in raw_segments:
            if len(samples) <= max_samples:
                pieces.append((start, samples))
                continue
            for offset in range(0, len(samples), max_samples):
                piece = samples[offset : offset + max_samples]
                if len(piece) >= SAMPLE_RATE // 2:  # 丢弃不足 0.5s 的尾巴碎片
                    pieces.append((start + offset, piece))
        return pieces
