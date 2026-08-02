from pathlib import Path

import pytest

from backend.video_processor import VideoProcessor, VideoSource


def test_detect_known_sources(tmp_path: Path) -> None:
    processor = VideoProcessor(tmp_path)
    assert (
        processor.detect_source("https://www.bilibili.com/video/BV1xx")
        == VideoSource.BILIBILI
    )
    assert processor.detect_source("https://youtu.be/abc") == VideoSource.YOUTUBE
    assert (
        processor.detect_source("https://www.bilibili.com:443/video/BV1xx")
        == VideoSource.BILIBILI
    )


def test_reject_non_http_and_do_not_misclassify_lookalike_domains(
    tmp_path: Path,
) -> None:
    processor = VideoProcessor(tmp_path)
    with pytest.raises(ValueError):
        processor.detect_source("D:/private/video.mp4")
    assert (
        processor.detect_source("https://notbilibili.com/video/BV1xx")
        == VideoSource.OTHER
    )


def test_choose_manual_chinese_subtitles_before_automatic(tmp_path: Path) -> None:
    processor = VideoProcessor(tmp_path)
    info = {
        "subtitles": {
            "zh-CN": [{"ext": "vtt", "url": "https://example.test/manual"}]
        },
        "automatic_captions": {
            "zh-CN": [{"ext": "vtt", "url": "https://example.test/auto"}]
        },
    }

    language, track, source = processor._select_subtitle(info)

    assert language == "zh-CN"
    assert track["url"].endswith("manual")
    assert source == "platform_subtitle"


@pytest.mark.asyncio
async def test_fetch_bilibili_ai_chinese_subtitle_from_inline_data(
    tmp_path: Path,
) -> None:
    processor = VideoProcessor(tmp_path)
    info = {
        "subtitles": {
            "ai-zh": [
                {
                    "ext": "srt",
                    "data": "1\n00:00:01,000 --> 00:00:03,000\n这是 B 站 AI 字幕\n\n",
                }
            ]
        }
    }

    result = await processor.fetch_subtitles(
        "https://www.bilibili.com/video/BV1xx", info
    )

    assert result is not None
    assert result.language == "ai-zh"
    assert result.source == "platform_auto_caption"
    assert result.segments[0].text == "这是 B 站 AI 字幕"


def test_manual_chinese_track_precedes_bilibili_ai_track(tmp_path: Path) -> None:
    processor = VideoProcessor(tmp_path)
    info = {
        "subtitles": {
            "ai-zh": [{"ext": "srt", "data": "AI"}],
            "zh-CN": [{"ext": "srt", "data": "manual"}],
        }
    }

    language, track, source = processor._select_subtitle(info)

    assert language == "zh-CN"
    assert track["data"] == "manual"
    assert source == "platform_subtitle"


def test_cookie_header_omits_empty_values() -> None:
    header = VideoProcessor._cookie_header(
        {"sessdata": "secret", "bili_jct": "", "buvid3": "device"}
    )
    assert header == "SESSDATA=secret; buvid3=device"
