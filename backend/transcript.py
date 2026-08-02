from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def segments_to_prompt(segments: Sequence[TranscriptSegment]) -> str:
    return "\n".join(
        f"[{format_timestamp(segment.start)}-{format_timestamp(segment.end)}] {segment.text}"
        for segment in segments
    )


def chunk_segments(
    segments: Sequence[TranscriptSegment], max_characters: int = 14_000
) -> list[list[TranscriptSegment]]:
    if max_characters < 500:
        raise ValueError("max_characters must be at least 500")

    chunks: list[list[TranscriptSegment]] = []
    current: list[TranscriptSegment] = []
    current_size = 0
    for segment in segments:
        line_size = len(segment.text) + 32
        if current and current_size + line_size > max_characters:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(segment)
        current_size += line_size

    if current:
        chunks.append(current)
    return chunks


def transcript_quality(
    segments: Sequence[TranscriptSegment], duration: float
) -> dict[str, float | int | bool]:
    characters = sum(len(segment.text) for segment in segments)
    speech_seconds = sum(max(0, segment.end - segment.start) for segment in segments)
    if duration <= 0:
        return {
            "characters": characters,
            "speech_seconds": round(speech_seconds, 2),
            "speech_coverage": 0.0,
            "characters_per_second": 0.0,
            "insufficient": False,
        }
    coverage = min(1.0, speech_seconds / duration)
    character_rate = characters / duration
    insufficient = duration >= 120 and coverage < 0.25 and character_rate < 1.0
    return {
        "characters": characters,
        "speech_seconds": round(speech_seconds, 2),
        "speech_coverage": round(coverage, 4),
        "characters_per_second": round(character_rate, 4),
        "insufficient": insufficient,
    }


def parse_subtitle_file(path: Path) -> list[TranscriptSegment]:
    payload = path.read_text(encoding="utf-8-sig", errors="replace")
    return parse_subtitle_payload(payload, path.suffix.lower().lstrip("."))


def parse_subtitle_payload(payload: str, extension: str) -> list[TranscriptSegment]:
    extension = extension.lower().lstrip(".")
    if extension in {"json", "json3"}:
        segments = _parse_json_subtitles(payload)
    elif extension in {"vtt", "srt"}:
        segments = _parse_timed_text(payload)
    else:
        raise ValueError(f"Unsupported subtitle format: {extension}")
    return _normalize_segments(segments)


def _parse_json_subtitles(payload: str) -> list[TranscriptSegment]:
    data = json.loads(payload)
    if isinstance(data, dict) and isinstance(data.get("body"), list):
        return [
            TranscriptSegment(
                start=float(item.get("from", 0)),
                end=float(item.get("to", item.get("from", 0))),
                text=str(item.get("content", "")),
            )
            for item in data["body"]
        ]

    events = data.get("events", []) if isinstance(data, dict) else []
    segments: list[TranscriptSegment] = []
    for event in events:
        pieces = event.get("segs") or []
        text = "".join(str(piece.get("utf8", "")) for piece in pieces)
        start = float(event.get("tStartMs", 0)) / 1000
        duration = float(event.get("dDurationMs", 0)) / 1000
        segments.append(TranscriptSegment(start, start + duration, text))
    return segments


_TIMING_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_timed_text(payload: str) -> list[TranscriptSegment]:
    lines = payload.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[TranscriptSegment] = []
    index = 0
    while index < len(lines):
        match = _TIMING_RE.search(lines[index])
        if not match:
            index += 1
            continue
        start = _parse_timestamp(match.group("start"))
        end = _parse_timestamp(match.group("end"))
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = " ".join(text_lines)
        segments.append(TranscriptSegment(start, end, text))
    return segments


def _parse_timestamp(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _normalize_segments(
    segments: Iterable[TranscriptSegment],
) -> list[TranscriptSegment]:
    normalized: list[TranscriptSegment] = []
    for segment in segments:
        text = html.unescape(_TAG_RE.sub("", segment.text))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        end = max(segment.start, segment.end)
        normalized.append(TranscriptSegment(max(0, segment.start), end, text))
    return normalized
