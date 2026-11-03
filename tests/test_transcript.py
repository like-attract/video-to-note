import json

import pytest

from backend.transcript import (
    TranscriptSegment,
    chunk_segments,
    format_timestamp,
    merge_segment_lines,
    parse_subtitle_payload,
    segments_to_prompt,
    segments_to_srt,
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


def test_srt_writes_standard_timecodes_one_cue_per_segment() -> None:
    """导出的 SRT 整篇比对：序号从 1 起、毫秒补零、跨小时进小时位、不并段。"""
    srt = segments_to_srt(
        [
            TranscriptSegment(0.5, 3.24, "第一段"),
            TranscriptSegment(3.24, 65.5, "第二段"),
            TranscriptSegment(3_661.7, 3_665, "第三段"),
        ]
    )

    assert srt == (
        "1\r\n00:00:00,500 --> 00:00:03,240\r\n第一段\r\n"
        "\r\n2\r\n00:00:03,240 --> 00:01:05,500\r\n第二段\r\n"
        "\r\n3\r\n01:01:01,700 --> 01:01:05,000\r\n第三段\r\n"
    )


def test_exported_srt_round_trips_through_the_subtitle_parser() -> None:
    """导出的 SRT 必须能被本项目自己的字幕解析器原样读回。

    这一条同时钉住两件事：写出的时间码是播放器认的写法（解析器就是按播放器
    口径写的），以及导出与导入互为逆运算、不会在往返里丢内容或错位。
    """
    segments = [
        TranscriptSegment(0.5, 3.24, "今天我们讲梯度下降"),
        TranscriptSegment(3_661.7, 3_665.0, "跨过一整小时"),
    ]

    assert parse_subtitle_payload(segments_to_srt(segments), "srt") == segments


def test_chunking_keeps_segment_boundaries() -> None:
    segments = [
        TranscriptSegment(index, index + 1, "内容" * 150) for index in range(5)
    ]
    chunks = chunk_segments(segments, max_characters=700)
    assert [item for chunk in chunks for item in chunk] == segments
    assert len(chunks) > 1


def test_chunking_can_count_content_only() -> None:
    """长视频逐段直写按净字数切块：时间戳开销不该决定一段有多少话要说。"""
    segments = [
        TranscriptSegment(index, index + 1, "内容" * 75) for index in range(6)
    ]
    with_overhead = chunk_segments(segments, max_characters=500)
    content_only = chunk_segments(
        segments, max_characters=500, include_timestamp_overhead=False
    )
    assert [len(chunk) for chunk in with_overhead] == [2, 2, 2]
    assert [len(chunk) for chunk in content_only] == [3, 3]
    assert [item for chunk in content_only for item in chunk] == segments


def test_merge_segment_lines_groups_short_lines_with_range_timestamps() -> None:
    segments = [
        TranscriptSegment(index, index + 1, "短句" * 4) for index in range(6)
    ]

    merged = merge_segment_lines(segments, target_characters=20)
    group = "短句短句短句短句 短句短句短句短句 短句短句短句短句"

    assert merged == [TranscriptSegment(0, 3, group), TranscriptSegment(3, 6, group)]
    # 一个字都不能丢
    assert " ".join(segment.text for segment in merged).split() == [
        segment.text for segment in segments
    ]


def test_merge_segment_lines_keeps_long_lines_untouched() -> None:
    long = TranscriptSegment(0, 5, "本来就很长的一行口播内容" * 6)
    assert merge_segment_lines([long], target_characters=48) == [long]
    assert merge_segment_lines([], target_characters=48) == []


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
    assert quality["suspicious"] is False


def test_quality_gate_flags_whisper_repetition_loop_tail() -> None:
    normal = [
        TranscriptSegment(i * 30, (i + 1) * 30, "正常的中文口播内容，介绍检索增强生成的具体步骤。")
        for i in range(7)
    ]
    garbage = [
        TranscriptSegment(
            210 + i * 30,
            240 + i * 30,
            "X, C, A, X, X. X.X, XC, XK, XS, XG, XA, Xs, XO, XI, XR, XT, XE",
        )
        for i in range(8)
    ]

    quality = transcript_quality(normal + garbage, duration=450)

    assert quality["suspicious"] is True
    assert "疑似重复或乱码" in str(quality["suspicion"])
    assert quality["insufficient"] is False


def test_quality_gate_does_not_flag_english_terms_in_chinese_video() -> None:
    segments = [
        TranscriptSegment(
            i * 30,
            (i + 1) * 30,
            "这一节讲 trunky、GraphRAG、query transformation 和 hybrid search 的做法。",
        )
        for i in range(15)
    ]
    quality = transcript_quality(segments, duration=450)
    assert quality["suspicious"] is False


def test_quality_gate_does_not_flag_english_only_video() -> None:
    segments = [
        TranscriptSegment(
            i * 30,
            (i + 1) * 30,
            "This section explains retrieval augmented generation, hybrid search and reranking.",
        )
        for i in range(15)
    ]
    quality = transcript_quality(segments, duration=450)
    assert quality["suspicious"] is False
