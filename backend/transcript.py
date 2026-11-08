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


def format_srt_timestamp(seconds: float) -> str:
    """SubRip 时间码 ``HH:MM:SS,mmm``：毫秒三位补零，小时从 00 起。

    先换算成整数毫秒再拆位，999.7 秒这种小数才不会写出 ``00:16:39,1000``。
    """
    total_milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def segments_to_srt(segments: Sequence[TranscriptSegment]) -> str:
    """渲染成 SubRip 字幕文本，供 ``/api/task/{id}/transcript?output_format=srt`` 使用。

    条目与传进来的 ``segments`` 一一对应、**不并段**：SRT 与 ``output_format=json`` 要能
    逐条核对（Issue #4 里承诺的契约）。碎行合并是提示词那侧的事（见
    :func:`merge_segment_lines`），字幕要的是和播放器里对得上的原始粒度。
    行尾用 CRLF：这是 SubRip 的通行写法，剪映 / Premiere 对纯 LF 的容忍度不一。
    """
    blocks = [
        "{}\r\n{} --> {}\r\n{}\r\n".format(
            index,
            format_srt_timestamp(segment.start),
            format_srt_timestamp(segment.end),
            segment.text,
        )
        for index, segment in enumerate(segments, start=1)
    ]
    return "\r\n".join(blocks)


def merge_segment_lines(
    segments: Sequence[TranscriptSegment], target_characters: int = 48
) -> list[TranscriptSegment]:
    """把相邻的碎行拼成接近 ``target_characters`` 字的长行，时间戳取整组区间。

    只为提示词服务：语音转写常切成平均几个字的碎行，逐行加时间戳会让时间戳占掉大部分
    输入预算，模型读到的也不像话。**不要拿它改写盘用的 ``transcript.md``**——那份产物的
    逐句粒度是字幕用途的一部分。
    """
    merged: list[TranscriptSegment] = []
    group: list[TranscriptSegment] = []
    used = 0
    for segment in segments:
        group.append(segment)
        used += len(segment.text) + 1
        if used >= target_characters:
            merged.append(_merge_group(group))
            group, used = [], 0
    if group:
        merged.append(_merge_group(group))
    return merged


def _merge_group(group: Sequence[TranscriptSegment]) -> TranscriptSegment:
    if len(group) == 1:
        return group[0]
    return TranscriptSegment(
        group[0].start, group[-1].end, " ".join(segment.text for segment in group)
    )


def chunk_segments(
    segments: Sequence[TranscriptSegment],
    max_characters: int = 14_000,
    *,
    include_timestamp_overhead: bool = True,
) -> list[list[TranscriptSegment]]:
    """按字符预算切块，保持段边界不被切断。

    ``include_timestamp_overhead=False`` 时预算只算转录净字数，用于"每块都要直接写成
    终稿"的管线：那里每块的目标是固定的口播量，不该被时间戳开销稀释。
    """
    if max_characters < 500:
        raise ValueError("max_characters must be at least 500")

    overhead = 32 if include_timestamp_overhead else 0
    chunks: list[list[TranscriptSegment]] = []
    current: list[TranscriptSegment] = []
    current_size = 0
    for segment in segments:
        line_size = len(segment.text) + overhead
        if current and current_size + line_size > max_characters:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(segment)
        current_size += line_size

    if current:
        chunks.append(current)
    return chunks


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[A-Za-z]+")


def _segment_looks_noisy(text: str) -> bool:
    """单段是否像解码退化后的乱码/重复，而不是正常口播。

    典型形状是 Whisper 系的重复循环：`X, C, A, X, X. X.X, XC, XK...`。
    这里只认“几乎没有中文 + 大量单字母/X”的强噪声，避免把正常的英文术语段
    误判成坏转录。
    """
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 6:
        return False
    letters = _LATIN_LETTER_RE.findall(compact)
    cjk = _CJK_RE.findall(compact)
    tokens = _WORD_RE.findall(text)
    cjk_ratio = len(cjk) / len(compact)
    latin_ratio = len(letters) / len(compact)
    x_ratio = sum(1 for letter in letters if letter.lower() == "x") / max(1, len(letters))
    single_ratio = sum(1 for token in tokens if len(token) == 1) / max(1, len(tokens))
    if cjk_ratio < 0.05 and latin_ratio >= 0.35 and (
        x_ratio >= 0.15 or (single_ratio >= 0.35 and len(tokens) >= 6)
    ):
        return True
    unique_ratio = len(set(compact)) / len(compact)
    return len(compact) >= 40 and unique_ratio < 0.15


def _suspicious_transcript(
    segments: Sequence[TranscriptSegment], duration: float
) -> tuple[bool, str | None]:
    """找连续成片的噪声段；单段毛刺不判死，中文视频里的英文术语段也不判死。"""
    if duration <= 0 or not segments:
        return False, None
    overall = "".join(segment.text for segment in segments)
    if not overall:
        return False, None
    if len(_CJK_RE.findall(overall)) / len(overall) < 0.15:
        # 整片不是以中文为主：英文视频里的英文段是正常内容，不能按“没有中文”判噪。
        return False, None

    runs: list[list[TranscriptSegment]] = []
    current: list[TranscriptSegment] = []
    for segment in segments:
        if _segment_looks_noisy(segment.text):
            current.append(segment)
            continue
        if current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    for run in runs:
        if len(run) < 3:
            continue
        span = run[-1].end - run[0].start
        if span < max(20.0, duration * 0.08):
            continue
        start = format_timestamp(run[0].start)
        end = format_timestamp(run[-1].end)
        share = min(1.0, span / duration)
        return True, (
            f"{start}–{end} 连续 {len(run)} 段疑似重复或乱码，"
            f"约占全片 {share:.0%}"
        )
    return False, None


def transcript_quality(
    segments: Sequence[TranscriptSegment], duration: float
) -> dict[str, float | int | bool | str | None]:
    characters = sum(len(segment.text) for segment in segments)
    speech_seconds = sum(max(0, segment.end - segment.start) for segment in segments)
    suspicious, suspicion = _suspicious_transcript(segments, duration)
    if duration <= 0:
        return {
            "characters": characters,
            "speech_seconds": round(speech_seconds, 2),
            "speech_coverage": 0.0,
            "characters_per_second": 0.0,
            "insufficient": False,
            "suspicious": suspicious,
            "suspicion": suspicion,
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
        "suspicious": suspicious,
        "suspicion": suspicion,
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
