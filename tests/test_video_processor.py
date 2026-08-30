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


def test_download_audio_aborts_on_cancel_via_progress_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    processor = VideoProcessor(tmp_path)
    captured: dict = {}

    class FakeYdl:
        def __init__(self, **options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            # 模拟 yt-dlp 下载过程中触发进度钩子：取消生效则立即抛 CancelledError
            for hook in captured.get("progress_hooks") or []:
                hook({"status": "downloading"})
            return {}

    monkeypatch.setattr(processor, "_ydl", lambda *a, **k: FakeYdl(**k))

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await processor.download_audio(
                "https://example.com/a.mp4", "t1", should_abort=lambda: True
            )

    asyncio.run(run())
    assert captured["progress_hooks"]


def test_extract_audio_track_produces_m4a(tmp_path: Path) -> None:
    import math
    import struct
    import wave

    source = tmp_path / "in.wav"
    sr = 16000
    frames = bytearray()
    for i in range(sr):
        frames += struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / sr)))
    with wave.open(str(source), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))

    target = tmp_path / "out.m4a"
    result = VideoProcessor.extract_audio_track(source, target)

    assert result == target
    assert target.is_file() and target.stat().st_size > 0

# ---------- Issue #1：B 站视频页 412 风控 → 开放接口直连回退 ----------

_VIEW_PAYLOAD = {
    "code": 0,
    "data": {
        "title": "风控测试视频",
        "pic": "https://i0.hdslb.com/bfs/test.jpg",
        "desc": "简介",
        "pubdate": 1_755_724_800,
        "owner": {"name": "UP小站"},
        "stat": {"view": 4200, "like": 88},
        "duration": 60,
        "pages": [
            {"page": 1, "part": "上", "cid": 101, "duration": 60},
            {"page": 2, "part": "下", "cid": 102, "duration": 90},
        ],
    },
}

_PLAYURL_PAYLOAD = {
    "code": 0,
    "message": "0",
    "data": {
        "dash": {
            "audio": [
                {
                    "id": 139,
                    "bandwidth": 70_000,
                    "baseUrl": "https://cdn.test/audio_low.m4a",
                },
                {
                    "id": 30280,
                    "bandwidth": 190_000,
                    "baseUrl": "https://cdn.test/audio_high.m4a",
                    "backupUrl": ["https://backup.test/audio_high.m4a"],
                },
            ],
            "video": [
                {
                    "id": 32,
                    "height": 480,
                    "bandwidth": 900_000,
                    "baseUrl": "https://cdn.test/v480.m4s",
                },
                {
                    "id": 16,
                    "height": 240,
                    "bandwidth": 400_000,
                    "baseUrl": "https://cdn.test/v240.m4s",
                },
            ],
        }
    },
}

_BILI_URL = "https://www.bilibili.com/video/BV1xM4y1z7Kt"


def _ydl_blocked(
    monkeypatch: pytest.MonkeyPatch,
    processor: VideoProcessor,
    message: str = "ERROR: [BiliBili] Unable to download webpage: HTTP Error 412: Precondition Failed",
) -> None:
    class FakeYdl:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            raise RuntimeError(message)

    monkeypatch.setattr(processor, "_ydl", lambda *a, **k: FakeYdl())


def _api_json(monkeypatch: pytest.MonkeyPatch, processor: VideoProcessor, playurl=None):
    seen: list[tuple[str, dict]] = []

    def fake_json(url, params, headers):
        seen.append((url, dict(params)))
        if url.endswith("/view"):
            return _VIEW_PAYLOAD
        return _PLAYURL_PAYLOAD if playurl is None else playurl

    monkeypatch.setattr(processor, "_bilibili_get_json", fake_json)
    return seen


def _record_direct_downloads(
    monkeypatch: pytest.MonkeyPatch, processor: VideoProcessor
) -> list[tuple[str, str]]:
    downloaded: list[tuple[str, str]] = []

    def fake_direct(media_url, target, referer, cookie, **kwargs):
        downloaded.append((media_url, referer))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"media")
        return target

    monkeypatch.setattr(processor, "_download_direct_media", fake_direct)
    return downloaded


def test_bilibili_blocked_detector() -> None:
    blocked = VideoProcessor._bilibili_blocked
    assert blocked(RuntimeError("HTTP Error 412: Precondition Failed"))
    assert blocked(RuntimeError('{"code":-352,"message":"风险验证"}'))
    assert blocked(RuntimeError("风控拦截"))
    assert not blocked(RuntimeError("HTTP Error 404: Not Found"))
    assert not blocked(RuntimeError("Requested format is not available"))


def test_bilibili_ids_from_av_and_short_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert VideoProcessor._bilibili_ids(f"{_BILI_URL}?p=2") == ("BV1xM4y1z7Kt", None)
    assert VideoProcessor._bilibili_ids("https://www.bilibili.com/video/av114514") == (
        None,
        114514,
    )

    def fake_resolve(url):
        return _BILI_URL if "b23.tv" in url else url

    monkeypatch.setattr(VideoProcessor, "_resolve_bili_short_url", classmethod(
        lambda cls, url: fake_resolve(url)
    ))
    assert VideoProcessor._bilibili_ids("https://b23.tv/abc123") == ("BV1xM4y1z7Kt", None)


def test_extract_info_falls_back_to_view_api_when_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _ydl_blocked(monkeypatch, processor)
    seen = _api_json(monkeypatch, processor)
    notes: list[str] = []

    info = processor._extract_info(_BILI_URL, {"sessdata": "abc"}, notes)

    assert info["title"] == "风控测试视频"
    assert info["source"] == "bilibili"
    assert info["duration"] == 150  # 分 P 时长累加
    assert info["owner"] == "UP小站"
    assert info["timestamp"] == 1_755_724_800
    assert info["view_count"] == 4200
    assert info["like_count"] == 88
    assert info["subtitles"] == {}
    assert notes and "风控" in notes[0]
    url, params = seen[0]
    assert url.endswith("/x/web-interface/view")
    assert params == {"bvid": "BV1xM4y1z7Kt"}
    # 分 P 清单缓存下来供 playurl 使用
    assert [p["cid"] for p in processor._bili_view_cache["BV1xM4y1z7Kt"]["pages"]] == [101, 102]


def test_extract_info_keeps_original_error_when_not_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _ydl_blocked(
        monkeypatch, processor, message="ERROR: [BiliBili] Unsupported URL"
    )
    seen = _api_json(monkeypatch, processor)
    notes: list[str] = []

    with pytest.raises(RuntimeError, match="Unsupported URL"):
        processor._extract_info(_BILI_URL, None, notes)

    assert seen == []
    assert notes == []


def test_extract_info_reports_when_fallback_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _ydl_blocked(monkeypatch, processor)
    monkeypatch.setattr(
        processor, "_bilibili_get_json", lambda url, params, headers: {"code": -412, "data": {}}
    )

    with pytest.raises(RuntimeError, match="风控拦截"):
        processor._extract_info(_BILI_URL, None, [])


@pytest.mark.asyncio
async def test_download_audio_falls_back_to_playurl_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _ydl_blocked(monkeypatch, processor)
    seen = _api_json(monkeypatch, processor)
    downloaded = _record_direct_downloads(monkeypatch, processor)
    notes: list[str] = []

    path = await processor.download_audio(
        f"{_BILI_URL}?p=2", "t1", {"sessdata": "abc"}, notes=notes
    )

    assert path.name == "audio.m4a"
    assert path.read_bytes() == b"media"
    # 取带宽最高的音频流，Referer 指回视频页
    assert downloaded[0][0].endswith("audio_high.m4a")
    assert "bilibili.com/video/BV1xM4y1z7Kt" in downloaded[0][1]
    playurl = [params for url, params in seen if url.endswith("/playurl")]
    assert playurl and playurl[0]["cid"] == 102  # ?p=2 → 第二个分 P
    assert playurl[0]["fnval"] == 16
    assert any("playurl 接口直连音频流" in note for note in notes)


@pytest.mark.asyncio
async def test_download_preview_video_falls_back_to_usable_video_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _ydl_blocked(monkeypatch, processor)
    _api_json(monkeypatch, processor)
    downloaded = _record_direct_downloads(monkeypatch, processor)
    notes: list[str] = []

    path = await processor.download_preview_video(
        _BILI_URL, "t1", {"sessdata": "abc"}, notes=notes
    )

    assert path.name == "preview.m4s"
    # 高度 >=360 里取最小的一路（240P 太小、480P 可用）
    assert downloaded[0][0].endswith("v480.m4s")
    assert any("预览视频流" in note for note in notes)


@pytest.mark.asyncio
async def test_download_audio_fallback_reports_clear_error_without_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _ydl_blocked(monkeypatch, processor)
    _api_json(monkeypatch, processor, playurl={"code": 0, "data": {}})

    with pytest.raises(RuntimeError, match="playurl 接口未返回可下载流"):
        await processor.download_audio(_BILI_URL, "t1", {"sessdata": "abc"})


@pytest.mark.asyncio
async def test_download_audio_does_not_swallow_other_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    _ydl_blocked(monkeypatch, processor, message="ERROR: unable to decrypt")
    _api_json(monkeypatch, processor)
    _record_direct_downloads(monkeypatch, processor)

    with pytest.raises(RuntimeError, match="unable to decrypt"):
        await processor.download_audio(_BILI_URL, "t1", {"sessdata": "abc"})


def test_download_direct_media_aborts_when_cancel_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    processor = VideoProcessor(tmp_path)
    target = tmp_path / "task" / "audio.m4a"

    class FakeResponse:
        def __init__(self):
            self.remaining = 3

        def read(self, _size):
            if not self.remaining:
                return b""
            self.remaining -= 1
            return b"x" * 16

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "backend.video_processor.urllib.request.urlopen", lambda *a, **k: FakeResponse()
    )

    state = {"called": 0}

    def should_abort():
        state["called"] += 1
        return state["called"] > 1  # 第一块放行，第二块要求取消

    with pytest.raises(asyncio.CancelledError):
        processor._download_direct_media(
            "https://cdn.test/audio.m4a",
            target,
            "https://www.bilibili.com/",
            None,
            user_agent=VideoProcessor.BILI_USER_AGENT,
            should_abort=should_abort,
        )
    assert not target.exists()
    # 中断不留半文件（否则会被主流程的 audio.* 复用扫描误当成完整音频）
    assert not list(target.parent.glob("*.part"))


def test_cookie_header_includes_risk_control_cookies() -> None:
    header = VideoProcessor._cookie_header(
        {
            "sessdata": "s", "bili_jct": "j", "buvid3": "3",
            "buvid4": "4", "b_nut": "1", "b_lsid": "ls", "junk": "x",
        }
    )
    assert header == "SESSDATA=s; bili_jct=j; buvid3=3; buvid4=4; b_nut=1; b_lsid=ls"


def test_view_api_hint_when_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)
    monkeypatch.setattr(processor, "_bilibili_get_json", lambda url, params, headers: {})
    with pytest.raises(RuntimeError, match="扫码导入"):
        processor._extract_bilibili_api_info(_BILI_URL, None)
    with pytest.raises(RuntimeError, match="view 接口未返回可用数据") as exc:
        processor._extract_bilibili_api_info(_BILI_URL, {"sessdata": "s"})
    assert "扫码导入" not in str(exc.value)
