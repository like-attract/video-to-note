from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.video_processor import VideoProcessor
from backend.whisper_asr import WhisperTranscriber


async def run(args: argparse.Namespace) -> None:
    processor = VideoProcessor(PROJECT_ROOT / "workspace")
    task_id = f"smoke-{uuid.uuid4().hex[:8]}"
    info = await processor.get_video_info(args.url)
    subtitle = await processor.fetch_subtitles(args.url, info)
    result = {
        "title": info["title"],
        "duration": info["duration"],
        "source": info["source"],
        "subtitle_segments": len(subtitle.segments) if subtitle else 0,
        "subtitle_source": subtitle.source if subtitle else None,
    }
    if args.keep:
        result["task_id"] = task_id
        result["task_directory"] = str(processor.work_dir / task_id)

    if args.download_audio or args.transcribe:
        audio_path = await processor.download_audio(args.url, task_id)
        result["audio_file"] = audio_path.name
        result["audio_bytes"] = audio_path.stat().st_size
        if args.transcribe:
            transcript = await WhisperTranscriber().transcribe(
                audio_path, args.model, use_gpu=False, initial_prompt=info["title"]
            )
            result["transcript_segments"] = len(transcript["segments"])
            result["transcript_language"] = transcript["language"]
            result["transcript_characters"] = len(transcript["text"])
            segments = transcript["segments"]
            sample_indexes = sorted({0, len(segments) // 3, (2 * len(segments)) // 3, len(segments) - 1})
            result["transcript_samples"] = [
                {
                    "start": round(segments[index].start, 1),
                    "end": round(segments[index].end, 1),
                    "text": segments[index].text[:180],
                }
                for index in sample_indexes
            ]

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.keep:
        await processor.cleanup(task_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the media pipeline without an LLM call")
    parser.add_argument("url")
    parser.add_argument("--download-audio", action="store_true")
    parser.add_argument("--transcribe", action="store_true")
    parser.add_argument("--model", default="small")
    parser.add_argument("--keep", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
