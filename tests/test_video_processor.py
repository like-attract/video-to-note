import json
from pathlib import Path

import pytest

from backend.video_processor import (
    BiliPage,
    SubtitleResult,
    VideoProcessor,
    VideoSource,
    bilibili_page_url,
    merge_bilibili_pages,
    normalize_video_input,
)
from backend.transcript import TranscriptSegment


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
    assert (
        processor.detect_source("https://www.douyin.com/video/123")
        == VideoSource.DOUYIN
    )
    assert (
        processor.detect_source("https://v.douyin.com/abc/")
        == VideoSource.DOUYIN
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
    assert processor.detect_source("https://notdouyin.com/video/123") == VideoSource.OTHER


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


def test_normalize_video_input_accepts_douyin_share_text() -> None:
    assert normalize_video_input("复制打开 https://v.douyin.com/AbCd3Fg/ 看视频") == "https://v.douyin.com/AbCd3Fg"
    assert normalize_video_input("www.douyin.com/video/123456。") == "https://www.douyin.com/video/123456"


def test_douyin_share_page_info_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    processor = VideoProcessor(tmp_path)
    router = {
        "loaderData": {
            "video_(id)/page": {
                "videoInfoRes": {
                    "item_list": [{
                        "desc": "测试抖音视频",
                        "author": {"nickname": "作者"},
                        "video": {
                            "duration": 12_500,
                            "play_addr": {"url_list": ["https://v.douyinvod.com/playwm/demo.mp4"]},
                        },
                        "statistics": {"play_count": 42},
                    }]
                }
            }
        }
    }
    html = f"<script>window._ROUTER_DATA = {json.dumps(router, ensure_ascii=False)}</script>"

    def fake_request(url, headers, return_url=False):
        if "iesdouyin.com" in url:
            return url, html
        return "https://www.douyin.com/video/123456", ""

    monkeypatch.setattr(VideoProcessor, "_request_text", staticmethod(fake_request))
    info = processor._extract_douyin_share_info("https://v.douyin.com/abc", None)
    assert info["source"] == "douyin"
    assert info["title"] == "测试抖音视频"
    assert info["duration"] == 12.5
    assert info["owner"] == "作者"
    assert info["_douyin_video_url"].endswith("/play/demo.mp4")


def test_extract_info_preserves_publish_and_engagement_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)

    class FakeYdl:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            return {
                "title": "测试视频",
                "extractor_key": "BiliBili",
                "duration": 90,
                "uploader": "作者",
                "upload_date": "20260821",
                "timestamp": 1_755_724_800,
                "view_count": 123,
                "like_count": 9,
            }

        def sanitize_info(self, info):
            return info

    monkeypatch.setattr(processor, "_ydl", lambda *args, **kwargs: FakeYdl())
    info = processor._extract_info("https://www.bilibili.com/video/BV1xx", None)
    assert info["upload_date"] == "20260821"
    assert info["timestamp"] == 1_755_724_800
    assert info["view_count"] == 123
    assert info["like_count"] == 9


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


# ---- B 站 AI 字幕抓取与多分 P ----

_FAKE_VIEW = {
    "code": 0,
    "data": {
        "title": "分P测试视频",
        "pages": [
            {"page": 1, "part": "第一部分", "cid": 101, "duration": 60},
            {"page": 2, "part": "第二部分", "cid": 102, "duration": 90},
        ],
    },
}

_SUBTITLE_JSON = '{"body": [{"from": 0, "to": 5, "content": "字幕内容"}, {"from": 5, "to": 9, "content": "继续"}]}'


def _monkeypatch_view(monkeypatch: pytest.MonkeyPatch, view: dict) -> None:
    monkeypatch.setattr(
        VideoProcessor,
        "_bilibili_get_json",
        staticmethod(lambda url, params, headers: view),
    )


@pytest.mark.asyncio
async def test_fetch_bilibili_subtitles_reports_missing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _monkeypatch_view(monkeypatch, _FAKE_VIEW)
    outcome = await processor.fetch_bilibili_subtitles(
        "https://www.bilibili.com/video/BV1xM4y1z7Kt", cookie=None
    )
    assert outcome.result is None
    assert outcome.reason == "credentials_missing"
    assert outcome.total_pages == 2
    assert [p.page for p in outcome.pages_to_transcribe] == [1, 2]


@pytest.mark.asyncio
async def test_fetch_bilibili_subtitles_reports_no_track_when_logged_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _monkeypatch_view(monkeypatch, _FAKE_VIEW)
    monkeypatch.setattr(processor, "_cookie_header", lambda cookie: "SESSDATA=abc;")
    monkeypatch.setattr(
        processor, "_bilibili_subtitle_track", lambda bvid, cid, cookie: None
    )
    outcome = await processor.fetch_bilibili_subtitles(
        "https://www.bilibili.com/video/BV1xM4y1z7Kt", cookie={"sessdata": "abc"}
    )
    assert outcome.result is None
    assert outcome.reason == "no_track"
    assert [p.page for p in outcome.pages_to_transcribe] == [1, 2]


@pytest.mark.asyncio
async def test_fetch_bilibili_subtitles_reports_download_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _monkeypatch_view(monkeypatch, _FAKE_VIEW)
    monkeypatch.setattr(processor, "_cookie_header", lambda cookie: "SESSDATA=abc;")
    monkeypatch.setattr(
        processor,
        "_bilibili_subtitle_track",
        lambda bvid, cid, cookie: {"language": "ai-zh", "url": "https://example.com/sub.json"},
    )

    def raise_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(processor, "_download_text", raise_error)
    outcome = await processor.fetch_bilibili_subtitles(
        "https://www.bilibili.com/video/BV1xM4y1z7Kt", cookie={"sessdata": "abc"}
    )
    assert outcome.result is None
    assert outcome.reason == "error"
    assert "boom" in outcome.detail
    assert [p.page for p in outcome.pages_to_transcribe] == [1, 2]


@pytest.mark.asyncio
async def test_fetch_bilibili_subtitles_merges_all_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _monkeypatch_view(monkeypatch, _FAKE_VIEW)
    monkeypatch.setattr(processor, "_cookie_header", lambda cookie: "SESSDATA=abc;")
    monkeypatch.setattr(
        processor,
        "_bilibili_subtitle_track",
        lambda bvid, cid, cookie: {"language": "ai-zh", "url": "https://example.com/sub.json"},
    )
    monkeypatch.setattr(
        processor, "_download_text", lambda *args, **kwargs: _SUBTITLE_JSON
    )
    outcome = await processor.fetch_bilibili_subtitles(
        "https://www.bilibili.com/video/BV1xM4y1z7Kt", cookie={"sessdata": "abc"}
    )
    assert outcome.reason == "ok"
    assert outcome.result is not None
    assert outcome.total_pages == 2
    assert outcome.pages_to_transcribe == ()
    merged = outcome.result.segments
    assert len(merged) == 4
    assert merged[0].start == 0.0
    assert merged[2].start == 60.0
    assert merged[2].text.startswith("【P2 第二部分】")
    assert merged[0].text.startswith("【P1 第一部分】")


@pytest.mark.asyncio
async def test_fetch_bilibili_subtitles_respects_p_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _monkeypatch_view(monkeypatch, _FAKE_VIEW)
    monkeypatch.setattr(processor, "_cookie_header", lambda cookie: "SESSDATA=abc;")

    def fake_track(bvid, cid, cookie):
        return {"language": "ai-zh", "url": "https://example.com/sub.json"}

    monkeypatch.setattr(processor, "_bilibili_subtitle_track", fake_track)
    monkeypatch.setattr(
        processor, "_download_text", lambda *args, **kwargs: _SUBTITLE_JSON
    )
    # ?p=2 只处理第二个分P
    outcome = await processor.fetch_bilibili_subtitles(
        "https://www.bilibili.com/video/BV1xM4y1z7Kt?p=2", cookie={"sessdata": "abc"}
    )
    assert outcome.total_pages == 2
    assert [p.page for p in outcome.pages] == [2]
    assert outcome.result is not None
    assert len(outcome.result.segments) == 2


def test_bilibili_page_url_keeps_query_and_sets_p() -> None:
    assert (
        bilibili_page_url("https://www.bilibili.com/video/BV1xM4y1z7Kt", 2)
        == "https://www.bilibili.com/video/BV1xM4y1z7Kt?p=2"
    )
    assert "p=3" in bilibili_page_url(
        "https://www.bilibili.com/video/BV1xM4y1z7Kt?p=1&t=10", 3
    )


def test_merge_bilibili_pages_offsets_and_labels() -> None:
    pages = [
        BiliPage(page=1, part="第一部分", cid=101, duration=60),
        BiliPage(page=2, part="第二部分", cid=102, duration=90),
    ]
    sub1 = SubtitleResult(
        [TranscriptSegment(0, 10, "P1开头"), TranscriptSegment(10, 20, "P1结尾")],
        "zh-CN",
        "bilibili_ai_subtitle",
    )
    whisper2 = {
        "segments": [TranscriptSegment(0, 5, "P2开头"), TranscriptSegment(5, 8, "P2结尾")],
        "language": "zh",
    }
    merged, language = merge_bilibili_pages(pages, {1: sub1}, {2: whisper2})
    assert language == "zh-CN"
    assert merged[0].text.startswith("【P1 第一部分】")
    assert merged[0].start == 0.0
    assert merged[2].start == 60.0
    assert merged[2].text.startswith("【P2 第二部分】 P2开头")
    assert merged[3].start == 65.0
    assert merged[3].text == "P2结尾"


def test_merge_bilibili_pages_single_no_marker() -> None:
    pages = [BiliPage(page=1, part="唯一", cid=101, duration=30)]
    whisper = {"segments": [TranscriptSegment(0, 4, "台词")]}
    merged, _ = merge_bilibili_pages(pages, {}, {1: whisper})
    assert merged[0].text == "台词"
