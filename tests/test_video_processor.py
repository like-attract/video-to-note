from pathlib import Path

import pytest

from backend.video_processor import VideoProcessor, VideoSource, normalize_video_input


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


def test_normalize_video_input_accepts_loose_bilibili_text() -> None:
    # 完整链接原样返回
    assert (
        normalize_video_input("https://www.bilibili.com/video/BV1xM4y1z7Kt?p=1")
        == "https://www.bilibili.com/video/BV1xM4y1z7Kt?p=1"
    )
    # 分享文本中的 b23.tv 短链（无 scheme）补全 https
    assert (
        normalize_video_input("【硬核】大模型读懂视频 https://b23.tv/AbCd3Fg 复制打开")
        == "https://b23.tv/AbCd3Fg"
    )
    # 缺省 scheme 的完整域名链接
    assert (
        normalize_video_input("www.bilibili.com/video/BV1xM4y1z7Kt")
        == "https://www.bilibili.com/video/BV1xM4y1z7Kt"
    )
    # 裸 BV 号补全为视频页
    assert (
        normalize_video_input("BV1xM4y1z7Kt")
        == "https://www.bilibili.com/video/BV1xM4y1z7Kt"
    )
    # av 号
    assert normalize_video_input("av170001") == "https://www.bilibili.com/video/av170001"
    # 尾部标点不粘进链接
    assert normalize_video_input("看下这个：b23.tv/AbCd3Fg。") == "https://b23.tv/AbCd3Fg"


def test_normalize_video_input_rejects_unknown_text() -> None:
    assert normalize_video_input("") is None
    assert normalize_video_input("随便一段没有链接的文字") is None
    assert normalize_video_input("ftp://example.com/video.mp4") is None


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
