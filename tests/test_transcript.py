import json

import pytest

from backend.transcript import (
    TranscriptSegment,
    chunk_segments,
    format_timestamp,
    parse_subtitle_payload,
    segments_to_prompt,
    transcript_quality,
)


def test_parse_bilibili_json_subtitles() -> None:
    payload = json.dumps(
        {
            "body": [
                {"from": 0.2, "to": 2.8, "content": "第一段"},
                {"from": 3, "to": 5, "content": "第二段"},
            ]
        }
    )

    segments = parse_subtitle_payload(payload, "json")

    assert segments == [
        TranscriptSegment(0.2, 2.8, "第一段"),
        TranscriptSegment(3.0, 5.0, "第二段"),
    ]


def test_parse_vtt_and_remove_markup() -> None:
    payload = """WEBVTT

00:00:01.000 --> 00:00:03.500
<c>你好 &amp; 欢迎</c>

00:03.500 --> 00:05.000
第二行
"""

    segments = parse_subtitle_payload(payload, "vtt")

    assert segments[0] == TranscriptSegment(1.0, 3.5, "你好 & 欢迎")
    assert segments[1].text == "第二行"


def test_prompt_uses_real_segment_timestamps() -> None:
    prompt = segments_to_prompt([TranscriptSegment(65, 72, "有时间依据的内容")])
    assert prompt == "[01:05-01:12] 有时间依据的内容"
    assert format_timestamp(3_661) == "01:01:01"


def test_chunking_keeps_segment_boundaries() -> None:
    segments = [
        TranscriptSegment(index, index + 1, "内容" * 150) for index in range(5)
    ]
    chunks = chunk_segments(segments, max_characters=700)
    assert [item for chunk in chunks for item in chunk] == segments
    assert len(chunks) > 1


def test_chunk_size_has_a_sensible_lower_bound() -> None:
    with pytest.raises(ValueError):
        chunk_segments([], max_characters=100)


def test_quality_gate_rejects_sparse_transcript() -> None:
    segments = [TranscriptSegment(560, 688, "少量片尾文字" * 30)]
    quality = transcript_quality(segments, duration=688)
    assert quality["speech_coverage"] < 0.25
    assert quality["insufficient"] is True


def test_quality_gate_accepts_normal_spoken_video() -> None:
    segments = [TranscriptSegment(0, 500, "正常口播内容" * 200)]
    quality = transcript_quality(segments, duration=600)
    assert quality["insufficient"] is False
