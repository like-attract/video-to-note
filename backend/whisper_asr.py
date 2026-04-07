import whisper
import torch
from pathlib import Path
from typing import Dict
import asyncio
import platform


class WhisperTranscriber:
    def __init__(self):
        self.models = {}
        self.cuda_available = torch.cuda.is_available()

    def _get_device(self, use_gpu: bool) -> str:
        """根据用户选择和系统支持决定使用 CPU 还是 GPU"""
        if use_gpu and self.cuda_available:
            return "cuda"
        elif use_gpu and not self.cuda_available:
            print("⚠️ GPU 不可用（无 CUDA 或 PyTorch 为 CPU 版本），将使用 CPU 运行")
            return "cpu"
        else:
            return "cpu"

    async def transcribe(self, audio_path: Path, model_name: str = "base", use_gpu: bool = False) -> Dict:
        """转录音频文件"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            self._transcribe_sync,
            audio_path,
            model_name,
            use_gpu
        )
        return result

    def _transcribe_sync(self, audio_path: Path, model_name: str, use_gpu: bool) -> Dict:
        # 获取设备
        device = self._get_device(use_gpu)

        # 构建模型缓存 key（区分 CPU 和 GPU 版本）
        cache_key = f"{model_name}_{device}"

        if cache_key not in self.models:
            print(f"📦 正在加载 Whisper 模型: {model_name} (设备: {device.upper()})")

            # 如果是 CPU 且之前可能有 GPU 缓存问题，强制使用 map_location
            if device == "cpu":
                # 临时修改 torch.load 的默认行为
                original_map_location = torch.serialization._default_map_location
                torch.serialization._default_map_location = lambda storage, loc: storage

                try:
                    self.models[cache_key] = whisper.load_model(model_name, device=device)
                finally:
                    torch.serialization._default_map_location = original_map_location
            else:
                self.models[cache_key] = whisper.load_model(model_name, device=device)

            print(f"✅ Whisper 模型加载完成")

        model = self.models[cache_key]
        result = model.transcribe(str(audio_path), word_timestamps=True)

        return {
            "text": result["text"],
            "segments": result["segments"],
            "language": result["language"]
        }