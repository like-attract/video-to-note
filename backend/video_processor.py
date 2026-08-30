from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urljoin, urlunsplit

import yt_dlp

from .transcript import TranscriptSegment, parse_subtitle_payload


class VideoSource(str, Enum):
    BILIBILI = "bilibili"
    DOUYIN = "douyin"
    YOUTUBE = "youtube"
    LOCAL = "local"
    OTHER = "other"


@dataclass(frozen=True)
class SubtitleResult:
    segments: list[TranscriptSegment]
    language: str
    source: str


@dataclass(frozen=True)
class BiliPage:
    """B 站分 P：页码（1 起）、分 P 名、cid（该 P 独立媒体/字幕 id）、时长。"""

    page: int
    part: str
    cid: int
    duration: int


@dataclass(frozen=True)
class BiliSubtitleOutcome:
    """B 站 AI 字幕抓取结果：成功时 result 非空；失败时 reason 说明原因，
    避免把「没填凭据」与「视频没有字幕」两种失败混为一谈。

    reason: ok | partial | credentials_missing | no_track | error | empty

    多分 P 视频按链接 `?p=N` 或全部 P 处理：
    - total_pages：视频全部分 P 数
    - pages：本次选中的分 P（顺序），subtitle_by_page：有 AI 字幕的分 P
    - pages_to_transcribe：没有 AI 字幕、需要语音转写的分 P
    """

    result: SubtitleResult | None
    reason: str
    detail: str = ""
    title: str = ""
    total_pages: int = 0
    pages: tuple[BiliPage, ...] = ()
    subtitle_by_page: tuple[tuple[int, SubtitleResult], ...] = ()
    pages_to_transcribe: tuple[BiliPage, ...] = ()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """只取 Location，不跟随重定向（b23.tv 短链展开用）。"""

    def redirect_request(  # type: ignore[override]
        self, req, fp, code, msg, headers, newurl
    ):  # noqa: ANN001, ANN201
        return None


def bilibili_page_url(url: str, page: int) -> str:
    """给 B 站链接补/改 ?p=N（保留其他查询参数）。"""
    parsed = urlparse(url)
    query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    query["p"] = str(page)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _page_requested(url: str) -> int:
    """从链接解析 ?p=N；没有或无效返回 0（表示全部）。"""
    try:
        return int(str(parse_qs(urlparse(url).query).get("p", [""])[-1] or "") or 0)
    except (ValueError, IndexError):
        return 0


def merge_bilibili_pages(
    selected_pages: Sequence[BiliPage],
    subtitle_by_page: dict[int, SubtitleResult],
    whisper_by_page: dict[int, dict[str, Any]],
) -> tuple[list[TranscriptSegment], str]:
    """按分 P 顺序把各 P 的 AI 字幕 / 语音转写合并为统一时间轴。

    每个分 P 的片段时间戳向前序分 P 累计时长偏移，时间锚点因此连贯；
    多分 P 时每个分 P 的首段文本前加「【P{n} 分P名】」标记，便于 LLM 区分。
    返回 (合并片段, 首选语言)。
    """
    merged: list[TranscriptSegment] = []
    language = "zh"
    cumulative = 0.0
    first = True
    multi = len(selected_pages) > 1
    for page in selected_pages:
        segment_items: list[TranscriptSegment] = []
        lang_hint = ""
        if page.page in subtitle_by_page:
            sub = subtitle_by_page[page.page]
            segment_items = sub.segments
            lang_hint = sub.language
        elif page.page in whisper_by_page:
            whisper = whisper_by_page[page.page]
            segment_items = whisper.get("segments") or []
            lang_hint = str(whisper.get("language") or "zh")
        if segment_items and first:
            language = lang_hint or language
            first = False
        if segment_items:
            marker = f"【P{page.page} {page.part}】"
            for index, seg in enumerate(segment_items):
                prefix = f"{marker} " if (multi and index == 0) else ""
                merged.append(
                    TranscriptSegment(
                        max(0.0, cumulative + seg.start),
                        max(0.0, cumulative + seg.end),
                        prefix + seg.text,
                    )
                )
        cumulative += float(page.duration or 0)
    return merged, language


_EMBEDDED_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|v\.)?(?:bilibili\.com|b23\.tv|douyin\.com|iesdouyin\.com)/\S+",
    re.I,
)
_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"av(\d+)", re.I)
_TRAILING_PUNCT = ".,;:!?。，；：！？、）】」』”"


def normalize_video_input(value: str) -> str | None:
    """宽松识别视频输入：完整 http(s) 链接原样返回；否则从分享文本中提取
    B 站或抖音链接（可缺省 scheme），或裸 BV/av 号补全为视频页 URL。
    无法识别时返回 None。"""
    text = (value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    match = _EMBEDDED_LINK_RE.search(text)
    if match:
        link = match.group(0).rstrip(_TRAILING_PUNCT + "/")
        return link if link.startswith(("http://", "https://")) else f"https://{link}"
    bv = _BV_RE.search(text)
    if bv:
        return f"https://www.bilibili.com/video/{bv.group(0)}"
    av = _AV_RE.search(text)
    if av:
        return f"https://www.bilibili.com/video/av{av.group(1)}"
    return None


class VideoProcessor:
    # B 站风控要求真实浏览器 UA；yt-dlp 的 bilibili 提取器依赖它通过 /web-interface 接口检查
    BILI_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    DOUYIN_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    # Issue #1：部分环境下 www.bilibili.com 视频页永久 412（Cookie 补齐也过不去），
    # 但 api.bilibili.com 的开放接口可用。yt-dlp 拉不到页面时回退到这些接口。
    BILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
    BILI_PLAYURL_API = "https://api.bilibili.com/x/player/playurl"
    BILI_BLOCKED_MARKERS = (
        "412",
        "precondition failed",
        "-352",
        "risk control",
        "security check",
        "风控",
        "访问过于频繁",
        "验证码",
    )

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._douyin_media_cache: dict[str, dict[str, Any]] = {}
        # bvid / aid -> {"bvid", "aid", "pages": [{page, cid, part, duration}]}
        self._bili_view_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def detect_source(value: str, allow_local: bool = False) -> VideoSource:
        if allow_local and Path(value).is_file():
            return VideoSource.LOCAL
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Video URL must use http or https")
        host = (parsed.hostname or "").lower()
        if host == "bilibili.com" or host.endswith(".bilibili.com") or host == "b23.tv":
            return VideoSource.BILIBILI
        if (
            host == "douyin.com"
            or host.endswith(".douyin.com")
            or host == "iesdouyin.com"
            or host.endswith(".iesdouyin.com")
        ):
            return VideoSource.DOUYIN
        if host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be":
            return VideoSource.YOUTUBE
        return VideoSource.OTHER

    async def get_video_info(
        self,
        url_or_path: str,
        cookie: dict[str, str] | None = None,
        allow_local: bool = False,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        source = self.detect_source(url_or_path, allow_local=allow_local)
        if source == VideoSource.LOCAL:
            path = Path(url_or_path)
            return {
                "title": path.stem,
                "source": source.value,
                "duration": 0,
                "owner": "",
                "upload_date": "",
                "timestamp": 0,
                "view_count": 0,
                "like_count": 0,
                "description": "",
            }
        return await asyncio.to_thread(self._extract_info, url_or_path, cookie, notes)

    async def fetch_subtitles(
        self,
        url: str,
        info: dict[str, Any],
        cookie: dict[str, str] | None = None,
    ) -> SubtitleResult | None:
        for language, track, source in self._subtitle_candidates(info):
            try:
                if track.get("data") is not None:
                    payload = str(track["data"])
                else:
                    payload = await asyncio.to_thread(
                        self._download_text, track["url"], url, cookie
                    )
                segments = parse_subtitle_payload(payload, track.get("ext", "vtt"))
            except Exception:
                continue
            if segments:
                return SubtitleResult(
                    segments=segments,
                    language=language,
                    source=source,
                )
        return None

    async def fetch_bilibili_subtitles(
        self,
        url: str,
        cookie: dict[str, str] | None = None,
    ) -> BiliSubtitleOutcome:
        """B 站 AI 字幕专属通道（支持多分 P）。

        yt-dlp 的 bilibili 提取器不暴露 AI 字幕（ai-*）轨道，这里直调
        player API 获取字幕清单并下载。需要登录凭据（SESSDATA），下载
        字幕文件时需携带 Origin 头。

        多分 P 视频：链接带 ?p=N 则只处理该分 P，否则处理全部分 P。返回
        BiliSubtitleOutcome 而非裸 None：调用方需要区分「凭据缺失」、「视频
        没有字幕」等不同原因，也能知道哪些分 P 需要语音转写补齐。
        """
        match = _BV_RE.search(url) or _BV_RE.search(self._resolve_bili_short_url(url))
        if not match:
            return BiliSubtitleOutcome(None, "no_track", "未解析到视频 BV 号")
        bvid = match.group(0)
        headers = self._bili_headers(cookie)
        view = await asyncio.to_thread(
            self._bilibili_get_json,
            self.BILI_VIEW_API,
            {"bvid": bvid},
            headers,
        )
        view_data = view.get("data") or {}
        pages = [
            BiliPage(
                page=int(item.get("page") or 0),
                part=str(item.get("part") or f"P{item.get('page')}"),
                cid=int(item.get("cid") or 0),
                duration=int(item.get("duration") or 0),
            )
            for item in (view_data.get("pages") or [])
            if item.get("cid")
        ]
        if not pages:
            return BiliSubtitleOutcome(
                None, "no_track", "接口未返回分 P 信息（网络或风控异常）"
            )
        title = str(view_data.get("title") or "")
        p_param = _page_requested(url)
        if p_param > 0:
            selected = [page for page in pages if page.page == p_param] or list(pages)
        else:
            selected = list(pages)
        if not self._cookie_header(cookie):
            return BiliSubtitleOutcome(
                None,
                "credentials_missing",
                title=title,
                total_pages=len(pages),
                pages=tuple(selected),
                pages_to_transcribe=tuple(selected),
            )
        subtitle_by_page: dict[int, SubtitleResult] = {}
        pages_to_transcribe: list[BiliPage] = []
        first_error = ""
        saw_track_without_segments = False
        for page in selected:
            track = await asyncio.to_thread(
                self._bilibili_subtitle_track, bvid, page.cid, cookie
            )
            if not track:
                pages_to_transcribe.append(page)
                continue
            try:
                payload = await asyncio.to_thread(
                    self._download_text,
                    track["url"],
                    url,
                    cookie,
                    {"Origin": "https://www.bilibili.com"},
                )
                segments = parse_subtitle_payload(payload, "json")
            except Exception as exc:
                pages_to_transcribe.append(page)
                first_error = first_error or f"{type(exc).__name__}: {exc}"
                continue
            if not segments:
                pages_to_transcribe.append(page)
                saw_track_without_segments = True
                continue
            subtitle_by_page[page.page] = SubtitleResult(
                segments=segments,
                language=track["language"],
                source="bilibili_ai_subtitle",
            )
        if subtitle_by_page:
            merged, language = merge_bilibili_pages(selected, subtitle_by_page, {})
            return BiliSubtitleOutcome(
                SubtitleResult(
                    segments=merged, language=language, source="bilibili_ai_subtitle"
                ),
                "partial" if pages_to_transcribe else "ok",
                detail=first_error,
                title=title,
                total_pages=len(pages),
                pages=tuple(selected),
                subtitle_by_page=tuple(subtitle_by_page.items()),
                pages_to_transcribe=tuple(pages_to_transcribe),
            )
        if first_error:
            return BiliSubtitleOutcome(
                None,
                "error",
                first_error,
                title=title,
                total_pages=len(pages),
                pages=tuple(selected),
                pages_to_transcribe=tuple(selected),
            )
        if saw_track_without_segments:
            return BiliSubtitleOutcome(
                None,
                "empty",
                title=title,
                total_pages=len(pages),
                pages=tuple(selected),
                pages_to_transcribe=tuple(selected),
            )
        return BiliSubtitleOutcome(
            None,
            "no_track",
            title=title,
            total_pages=len(pages),
            pages=tuple(selected),
            pages_to_transcribe=tuple(selected),
        )

    BILI_SUBTITLE_LANGUAGES = (
        "ai-zh",
        "zh-hans",
        "zh-cn",
        "zh",
        "zh-hant",
        "zh-tw",
        "ai-en",
        "en",
        "ai-ja",
        "ja",
    )

    @classmethod
    def _bilibili_subtitle_track(
        cls, bvid: str, cid: int, cookie: dict[str, str] | None
    ) -> dict[str, Any] | None:
        """拉取某个分 P（cid）的字幕清单，返回首个可用 AI 字幕轨。"""
        headers = cls._bili_headers(cookie)
        player = cls._bilibili_get_json(
            "https://api.bilibili.com/x/player/wbi/v2",
            {"bvid": bvid, "cid": cid},
            headers,
        )
        tracks = ((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        for language in cls.BILI_SUBTITLE_LANGUAGES:
            for track in tracks:
                if track.get("lan") == language and track.get("subtitle_url"):
                    subtitle_url = track["subtitle_url"]
                    if subtitle_url.startswith("//"):
                        subtitle_url = "https:" + subtitle_url
                    return {"language": language, "url": subtitle_url}
        return None

    @staticmethod
    def _bilibili_get_json(
        url: str, params: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{url}?{urlencode(params)}", headers=headers),
                timeout=20,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            return {}
        if data.get("code") == 0:
            return data
        # 无签名被拒时补 wbi 签名重试
        try:
            signed = VideoProcessor._wbi_sign(params)
            with urllib.request.urlopen(
                urllib.request.Request(f"{url}?{urlencode(signed)}", headers=headers),
                timeout=20,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return data

    _WBI_IMG_KEY = "7cd084941338484aae1ad9425b84077c"
    _WBI_SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"

    @classmethod
    def _wbi_sign(cls, params: dict[str, Any]) -> dict[str, Any]:
        mixin_key = "".join(sorted(cls._WBI_IMG_KEY + cls._WBI_SUB_KEY))[:32]
        signed = dict(params)
        signed["wts"] = int(time.time())
        query = urlencode(sorted(signed.items()))
        signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
        return signed

    @classmethod
    def _bili_headers(cls, cookie: dict[str, str] | None) -> dict[str, str]:
        """B 站接口统一请求头：真实浏览器 UA + Referer/Origin（缺 Origin 会被 CDN 403）。"""
        headers = {
            "User-Agent": cls.BILI_USER_AGENT,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }
        cookie_header = cls._cookie_header(cookie)
        if cookie_header:
            headers["Cookie"] = cookie_header
        return headers

    @classmethod
    def _resolve_bili_short_url(cls, url: str) -> str:
        """展开 b23.tv 短链（只取重定向目标，不拉页面，避开视频页风控）。"""
        if "b23.tv" not in (urlparse(url).hostname or ""):
            return url
        opener = urllib.request.build_opener(_NoRedirect())
        request = urllib.request.Request(url, headers={"User-Agent": cls.BILI_USER_AGENT})
        try:
            with opener.open(request, timeout=15) as response:
                return response.geturl()
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.headers else None
            if exc.code in {301, 302, 303, 307, 308} and location:
                return urljoin(url, str(location))
        except Exception:
            return url
        return url

    @classmethod
    def _bilibili_ids(cls, url: str) -> tuple[str | None, int | None]:
        """从链接解析 (bvid, aid)；只有短链才会产生一次网络请求。"""
        target = url
        for _ in range(2):
            bvid, aid = _BV_RE.search(target), _AV_RE.search(target)
            if bvid or aid:
                return (
                    bvid.group(0) if bvid else None,
                    int(aid.group(1)) if aid else None,
                )
            resolved = cls._resolve_bili_short_url(target)
            if resolved == target:
                break
            target = resolved
        return None, None

    @classmethod
    def _bilibili_blocked(cls, exc: Exception) -> bool:
        """判断 yt-dlp 失败是否属于风控拦截（可回退接口直连）。"""
        text = f"{type(exc).__name__}: {exc}".lower()
        return any(marker in text for marker in cls.BILI_BLOCKED_MARKERS)

    def _extract_bilibili_api_info(
        self, url: str, cookie: dict[str, str] | None, notes: list[str] | None = None
    ) -> dict[str, Any]:
        """风控回退通道：视频页 412 时用 view 接口取元数据（Issue #1）。

        返回与 `_extract_info` 同构的字典，并把分 P 清单缓存在 `_bili_view_cache`，
        供后续 playurl 取音频/预览流使用。
        """
        bvid, aid = self._bilibili_ids(url)
        if not bvid and not aid:
            raise RuntimeError("未能从链接解析出 BV/av 号，接口回退无法定位视频")
        payload = self._bilibili_get_json(
            self.BILI_VIEW_API,
            {"bvid": bvid} if bvid else {"aid": aid},
            self._bili_headers(cookie),
        )
        data = payload.get("data") or {}
        if int(payload.get("code") or 0) != 0 or not data.get("title"):
            hint = (
                "；未携带 B 站凭据，开放接口也会返回 412——请先扫码导入或填写 SESSDATA"
                if not self._cookie_header(cookie)
                else ""
            )
            raise RuntimeError(
                f"view 接口未返回可用数据（code={payload.get('code')}）{hint}"
            )
        pages = [
            {
                "page": int(item.get("page") or index),
                "cid": int(item.get("cid") or 0),
                "part": str(item.get("part") or f"P{item.get('page')}"),
                "duration": float(item.get("duration") or 0),
            }
            for index, item in enumerate(data.get("pages") or [], start=1)
            if item.get("cid")
        ]
        duration = sum(page["duration"] for page in pages) or float(
            data.get("duration") or 0
        )
        stat = data.get("stat") or {}
        owner = data.get("owner") or {}
        key = bvid or f"aid{aid}"
        self._bili_view_cache[key] = {"bvid": bvid, "aid": aid, "pages": pages}
        if notes is not None:
            notes.append("B 站视频页被风控拦截，已改用开放接口直连获取视频信息与媒体流")
        return {
            "title": str(data.get("title")),
            "source": VideoSource.BILIBILI.value,
            "duration": duration,
            "owner": str(owner.get("name") or data.get("mid") or ""),
            "upload_date": "",
            "timestamp": float(data.get("pubdate") or 0),
            "description": str(data.get("desc") or "")[:1_000],
            "thumbnail": str(data.get("pic") or ""),
            "view_count": int(stat.get("view") or 0),
            "like_count": int(stat.get("like") or 0),
            # 接口直连不返回字幕清单；AI 字幕本来就由 fetch_bilibili_subtitles 独立取
            "subtitles": {},
            "automatic_captions": {},
            "_bili_api_fallback": True,
        }

    def _bilibili_play_pages(
        self, url: str, cookie: dict[str, str] | None
    ) -> tuple[str, list[dict[str, Any]]]:
        bvid, aid = self._bilibili_ids(url)
        if not bvid and not aid:
            raise RuntimeError("未能从链接解析出 BV/av 号，接口回退无法定位视频")
        key = bvid or f"aid{aid}"
        cached = self._bili_view_cache.get(key)
        if not cached or not cached["pages"]:
            self._extract_bilibili_api_info(url, cookie)
            cached = self._bili_view_cache.get(key) or {"pages": []}
        return key, list(cached["pages"])

    def _download_bilibili_media_via_api(
        self,
        url: str,
        target: Path,
        cookie: dict[str, str] | None,
        kind: str,
        should_abort: Callable[[], bool] | None = None,
        notes: list[str] | None = None,
    ) -> Path:
        """风控回退下载：playurl 取 dash 流直链（kind=audio / video）。

        音频取带宽最高的 dash.audio；预览取高度 >=360 中最小的一路 video。
        拿不到 dash 时退到 durl（完整 mp4，体积大但转写/截图都能用）。
        """
        key, pages = self._bilibili_play_pages(url, cookie)
        if not pages:
            raise RuntimeError("view 接口未返回分 P 信息，无法取播放地址")
        wanted = _page_requested(url)
        page = next((item for item in pages if item["page"] == wanted), pages[0])
        identifiers = self._bili_view_cache.get(key) or {}
        params: dict[str, Any] = {"cid": page["cid"], "fnval": 16}
        if identifiers.get("bvid"):
            params["bvid"] = identifiers["bvid"]
        else:
            params["aid"] = identifiers.get("aid")
        payload = self._bilibili_get_json(
            self.BILI_PLAYURL_API, params, self._bili_headers(cookie)
        )
        data = payload.get("data") or {}
        dash = data.get("dash") or {}
        streams = (dash.get("audio") if kind == "audio" else dash.get("video")) or []
        if kind == "audio":
            ordered = sorted(
                streams, key=lambda item: int(item.get("bandwidth") or 0), reverse=True
            )
        else:
            by_quality = sorted(
                streams,
                key=lambda item: (
                    int(item.get("height") or 0),
                    int(item.get("bandwidth") or 0),
                ),
            )
            acceptable = [
                item for item in by_quality if int(item.get("height") or 0) >= 360
            ]
            ordered = acceptable or by_quality[::-1]
        candidates: list[str] = []
        for stream in ordered:
            candidates.append(str(stream.get("baseUrl") or ""))
            candidates.extend(str(item) for item in stream.get("backupUrl") or [])
        candidates.extend(str(item.get("url") or "") for item in data.get("durl") or [])
        urls = [value for value in candidates if value.startswith("http")]
        if not urls:
            raise RuntimeError(
                f"playurl 接口未返回可下载流（code={payload.get('code')}，"
                f"msg={payload.get('message') or '无'}）"
            )
        if notes is not None:
            notes.append(
                "yt-dlp 下载被 B 站风控拦截，已改用 playurl 接口直连"
                + ("音频流" if kind == "audio" else "预览视频流")
                + f"（分 P {page['page']}）"
            )
        last_error: Exception | None = None
        for media_url in urls[:4]:
            try:
                return self._download_direct_media(
                    media_url,
                    target,
                    f"https://www.bilibili.com/video/{key}/",
                    cookie,
                    user_agent=self.BILI_USER_AGENT,
                    should_abort=should_abort,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                target.unlink(missing_ok=True)
        raise RuntimeError(f"B 站接口直连下载失败：{last_error}")

    async def download_audio(
        self,
        url: str,
        task_id: str,
        cookie: dict[str, str] | None = None,
        media_name: str = "audio",
        should_abort: Callable[[], bool] | None = None,
        notes: list[str] | None = None,
    ) -> Path:
        task_dir = self._task_dir(task_id)

        def _abort_hook(_status: dict[str, Any]) -> None:
            if should_abort is not None and should_abort():
                raise asyncio.CancelledError("任务已取消")

        def _download() -> Path:
            ydl_error: Exception = RuntimeError("yt-dlp completed without producing an audio file")
            try:
                extra: dict[str, Any] = {}
                if should_abort is not None:
                    extra["progress_hooks"] = [_abort_hook]
                with self._ydl(
                    cookie,
                    cookie_domain=self._cookie_domain_for_url(url),
                    format="bestaudio/best",
                    outtmpl=str(task_dir / f"{media_name}.%(ext)s"),
                    noplaylist=True,
                    **extra,
                ) as ydl:
                    ydl.extract_info(url, download=True)
                matches = [
                    item
                    for item in task_dir.glob(f"{media_name}.*")
                    if item.suffix not in {".part", ".ytdl"}
                ]
                if matches:
                    return max(matches, key=lambda item: item.stat().st_size)
            except Exception as exc:
                kind = self.detect_source(url)
                if kind == VideoSource.BILIBILI and self._bilibili_blocked(exc):
                    return self._download_bilibili_media_via_api(
                        url,
                        task_dir / f"{media_name}.m4a",
                        cookie,
                        "audio",
                        should_abort,
                        notes,
                    )
                if kind != VideoSource.DOUYIN:
                    raise
                ydl_error = exc
            if self.detect_source(url) != VideoSource.DOUYIN:
                raise RuntimeError("yt-dlp completed without producing an audio file")
            info = self._douyin_media_cache.get(url) or self._extract_douyin_share_info(url, cookie)
            media_url = info.get("_douyin_video_url") or info.get("_douyin_audio_url")
            if not media_url:
                raise RuntimeError(f"抖音未返回可下载媒体地址（{ydl_error!s}）")
            return self._download_direct_media(media_url, task_dir / "audio.mp4", url, cookie)

        return await asyncio.to_thread(_download)

    async def download_preview_video(
        self,
        url: str,
        task_id: str,
        cookie: dict[str, str] | None = None,
        should_abort: Callable[[], bool] | None = None,
        notes: list[str] | None = None,
    ) -> Path:
        task_dir = self._task_dir(task_id)

        def _abort_hook(_status: dict[str, Any]) -> None:
            if should_abort is not None and should_abort():
                raise asyncio.CancelledError("任务已取消")

        def _download() -> Path:
            try:
                extra: dict[str, Any] = {}
                if should_abort is not None:
                    extra["progress_hooks"] = [_abort_hook]
                with self._ydl(
                    cookie,
                    cookie_domain=self._cookie_domain_for_url(url),
                    format="worstvideo[height>=360]/bestvideo[height<=720]/worst",
                    outtmpl=str(task_dir / "preview.%(ext)s"),
                    noplaylist=True,
                    **extra,
                ) as ydl:
                    ydl.extract_info(url, download=True)
                matches = [
                    item
                    for item in task_dir.glob("preview.*")
                    if item.suffix not in {".part", ".ytdl"}
                ]
                if matches:
                    return max(matches, key=lambda item: item.stat().st_size)
            except Exception as exc:
                kind = self.detect_source(url)
                if kind == VideoSource.BILIBILI and self._bilibili_blocked(exc):
                    return self._download_bilibili_media_via_api(
                        url,
                        task_dir / "preview.m4s",
                        cookie,
                        "video",
                        should_abort,
                        notes,
                    )
                if kind != VideoSource.DOUYIN:
                    raise
            if self.detect_source(url) != VideoSource.DOUYIN:
                raise RuntimeError("yt-dlp completed without producing a preview video")
            info = self._douyin_media_cache.get(url) or self._extract_douyin_share_info(url, cookie)
            media_url = info.get("_douyin_video_url")
            if not media_url:
                raise RuntimeError("抖音未返回可下载视频地址")
            return self._download_direct_media(media_url, task_dir / "preview.mp4", url, cookie)

        return await asyncio.to_thread(_download)

    @staticmethod
    def extract_audio_track(source: Path, target: Path) -> Path | None:
        """用 PyAV 把视频/音频文件的音轨提取为 16kHz 单声道 m4a（aac）。

        供大文件上传时只保留音频（与在线视频一致，减小工作目录体积）。
        失败或没有音轨时返回 None，调用方保留原文件直接用 faster-whisper 转写。
        """
        try:
            import av  # 截图功能已依赖 PyAV；未安装时返回 None 保持原逻辑
        except ImportError:
            return None
        try:
            container = av.open(str(source))
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                container.close()
                return None
            container.seek(0)
            output = av.open(str(target), "w")
            output_stream = output.add_stream("aac", rate=16000)
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
            try:
                for frame in container.decode(stream):
                    for resampled in resampler.resample(frame):
                        for packet in output_stream.encode(resampled):
                            output.mux(packet)
                for packet in output_stream.encode(None):
                    output.mux(packet)
            finally:
                output.close()
                container.close()
            if target.is_file() and target.stat().st_size > 0:
                return target
            return None
        except Exception:
            target.unlink(missing_ok=True)
            return None

    async def copy_local_media(self, source: Path, task_id: str) -> Path:
        task_dir = self._task_dir(task_id)
        source = source.resolve()
        target = (task_dir / f"input{source.suffix.lower()}").resolve()
        if source != target:
            await asyncio.to_thread(shutil.copy2, source, target)
        return target

    async def extract_frames(
        self,
        video_path: Path,
        task_id: str,
        interval: int,
        should_abort: Callable[[], bool] | None = None,
    ) -> list[Path]:
        if interval <= 0:
            return []
        return await asyncio.to_thread(
            self._extract_frames_sync, video_path, task_id, interval, should_abort
        )

    def _extract_frames_sync(
        self,
        video_path: Path,
        task_id: str,
        interval: int,
        should_abort: Callable[[], bool] | None = None,
    ) -> list[Path]:
        try:
            import av
        except ImportError as exc:
            raise RuntimeError("PyAV is required to extract screenshots") from exc

        frames_dir = self._task_dir(task_id) / "frames"
        frames_dir.mkdir(exist_ok=True)
        paths: list[Path] = []
        with av.open(str(video_path)) as container:
            if not container.streams.video:
                return []
            duration = (container.duration or 0) / av.time_base
            if duration <= 0:
                return []
            for timestamp in range(0, int(duration) + 1, interval):
                if should_abort is not None and should_abort():
                    raise asyncio.CancelledError("任务已取消")
                container.seek(int(timestamp * av.time_base), backward=True)
                frame = next(container.decode(video=0), None)
                if frame is None:
                    continue
                path = frames_dir / f"frame_{len(paths):04d}_{timestamp}s.jpg"
                frame.to_image().save(path, quality=82)
                paths.append(path)
        return paths

    async def cleanup(self, task_id: str) -> None:
        task_dir = self.work_dir / task_id
        if task_dir.exists():
            await asyncio.to_thread(shutil.rmtree, task_dir)

    def _extract_info(
        self,
        url: str,
        cookie: dict[str, str] | None,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            with self._ydl(
                cookie,
                cookie_domain=self._cookie_domain_for_url(url),
                skip_download=True,
                noplaylist=True,
            ) as ydl:
                raw_info = ydl.extract_info(url, download=False)
                info = ydl.sanitize_info(raw_info)
        except Exception as exc:
            kind = self.detect_source(url)
            if kind == VideoSource.DOUYIN:
                return self._extract_douyin_share_info(url, cookie)
            if kind == VideoSource.BILIBILI and self._bilibili_blocked(exc):
                try:
                    return self._extract_bilibili_api_info(url, cookie, notes)
                except Exception as api_exc:
                    raise RuntimeError(
                        f"B 站视频页被风控拦截（{exc}），改用开放接口直连也未成功：{api_exc}"
                    ) from exc
            raise
        return {
            "title": info.get("title") or "Untitled video",
            "source": info.get("extractor_key", "other").lower(),
            "duration": info.get("duration") or 0,
            "owner": info.get("uploader") or "",
            "upload_date": info.get("upload_date") or info.get("release_date") or "",
            "timestamp": info.get("release_timestamp") or info.get("timestamp") or 0,
            "description": (info.get("description") or "")[:1_000],
            "thumbnail": info.get("thumbnail") or "",
            "view_count": info.get("view_count") or 0,
            "like_count": info.get("like_count") or 0,
            "subtitles": info.get("subtitles") or {},
            "automatic_captions": info.get("automatic_captions") or {},
        }

    def _extract_douyin_share_info(
        self, url: str, cookie: dict[str, str] | None
    ) -> dict[str, Any]:
        """尝试公开分享页解析；这是低成本探测器，失败由浏览器会话回退。"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            "Referer": "https://www.douyin.com/",
        }
        cookie_header = self._cookie_header(cookie, douyin=True)
        if cookie_header:
            headers["Cookie"] = cookie_header
        try:
            resolved = self._request_text(url, headers, return_url=True)
            final_url, _ = resolved
            match = re.search(r"/video/(\d+)", final_url) or re.search(r"modal_id=(\d+)", final_url)
            if not match:
                raise RuntimeError("无法从抖音链接提取作品 ID")
            aweme_id = match.group(1)
            share_url = f"https://www.iesdouyin.com/share/video/{aweme_id}/"
            _, html = self._request_text(share_url, headers, return_url=True)
            router_match = re.search(
                r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*;?\s*</script>", html, re.S
            )
            if not router_match:
                raise RuntimeError("抖音分享页未返回 _ROUTER_DATA（可能触发了验证）")
            router_data = json.loads(router_match.group(1))
            item = self._find_douyin_item(router_data)
            if not item:
                raise RuntimeError("抖音分享页未找到视频详情")
            video = item.get("video") or {}
            video_urls = self._url_list(video.get("play_addr"))
            audio_urls = self._url_list((item.get("music") or {}).get("play_url"))
            video_url = next((value.replace("playwm", "play") for value in video_urls if value), None)
            audio_url = next((value for value in audio_urls if value), None)
            if not video_url and not audio_url:
                raise RuntimeError("抖音详情没有可下载媒体地址")
            author = item.get("author") or {}
            info = {
                "title": item.get("desc") or "Untitled video",
                "source": VideoSource.DOUYIN.value,
                "duration": round(float(video.get("duration") or 0) / 1000, 3),
                "owner": author.get("nickname") or author.get("unique_id") or "",
                "upload_date": "",
                "timestamp": item.get("create_time") or 0,
                "description": (item.get("desc") or "")[:1_000],
                "thumbnail": (self._url_list(video.get("origin_cover")) or self._url_list(video.get("cover")) or [""])[0],
                "view_count": int((item.get("statistics") or {}).get("play_count") or 0),
                "like_count": int((item.get("statistics") or {}).get("digg_count") or 0),
                "subtitles": {},
                "automatic_captions": {},
                "_douyin_video_url": video_url,
                "_douyin_audio_url": audio_url,
            }
            self._douyin_media_cache[url] = info
            return info
        except Exception as exc:
            raise RuntimeError(f"Douyin share-page parse failed: {exc}") from exc

    @staticmethod
    def _request_text(
        url: str,
        headers: dict[str, str],
        return_url: bool = False,
    ) -> tuple[str, str] | str:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            if return_url:
                return response.geturl(), body
            return body

    @staticmethod
    def _url_list(value: Any) -> list[str]:
        if not isinstance(value, dict):
            return []
        raw = value.get("url_list") or []
        return [str(item) for item in raw if item]

    @classmethod
    def _find_douyin_item(cls, value: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 12:
            return None
        if isinstance(value, dict):
            item_list = value.get("item_list")
            if isinstance(item_list, list):
                for item in item_list:
                    if isinstance(item, dict) and isinstance(item.get("video"), dict):
                        return item
            for child in value.values():
                found = cls._find_douyin_item(child, depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = cls._find_douyin_item(child, depth + 1)
                if found:
                    return found
        return None

    def _download_direct_media(
        self,
        media_url: str,
        target: Path,
        referer: str,
        cookie: dict[str, str] | None,
        user_agent: str | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> Path:
        headers = {"User-Agent": user_agent or self.DOUYIN_USER_AGENT, "Referer": referer}
        # B 站 CDN 要带 B 站凭据；抖音走自己的会话字段
        cookie_header = (
            self._cookie_header(cookie)
            if user_agent is not None
            else self._cookie_header(cookie, douyin=True)
        )
        if cookie_header:
            headers["Cookie"] = cookie_header
        request = urllib.request.Request(media_url, headers=headers)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 先写 .part 再改名：Windows 不能删除还打开的文件，且中断的半文件不能被
        # 主流程的 audio.* 复用扫描当成可用产物（.part 已在调用方的排除列表里）。
        partial = target.with_name(target.name + ".part")
        try:
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    if should_abort is not None and should_abort():
                        raise asyncio.CancelledError("任务已取消")
                    output.write(chunk)
            if partial.stat().st_size == 0:
                raise RuntimeError("媒体直连下载结果为空")
            partial.replace(target)
            return target
        finally:
            partial.unlink(missing_ok=True)


    @staticmethod
    def _select_subtitle(
        info: dict[str, Any]
    ) -> tuple[str, dict[str, Any], str] | None:
        return next(VideoProcessor._subtitle_candidates(info), None)

    @staticmethod
    def _subtitle_candidates(
        info: dict[str, Any]
    ) -> Iterator[tuple[str, dict[str, Any], str]]:
        language_preferences = (
            "zh-hans",
            "zh-cn",
            "zh",
            "zh-hant",
            "zh-tw",
            "ai-zh",
            "en",
            "ai-en",
        )
        format_preferences = ("json3", "json", "vtt", "srt")
        for source_key, source_name in (
            ("subtitles", "platform_subtitle"),
            ("automatic_captions", "platform_auto_caption"),
        ):
            tracks = info.get(source_key) or {}
            for preferred_language in language_preferences:
                matching_languages = [
                    language
                    for language in tracks
                    if language.lower() == preferred_language
                    or language.lower().startswith(preferred_language + "-")
                ]
                for language in matching_languages:
                    entries = tracks.get(language) or []
                    selected_source = (
                        "platform_auto_caption"
                        if source_key == "automatic_captions"
                        or language.lower().startswith("ai-")
                        else source_name
                    )
                    for extension in format_preferences:
                        for entry in entries:
                            has_content = entry.get("url") or entry.get("data") is not None
                            if has_content and entry.get("ext", "").lower() == extension:
                                yield language, entry, selected_source

    @staticmethod
    def _download_text(
        subtitle_url: str,
        referer: str,
        cookie: dict[str, str] | None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url
        headers = {
            "Referer": referer,
            "User-Agent": VideoProcessor.BILI_USER_AGENT,
        }
        if extra_headers:
            headers.update(extra_headers)
        cookie_header = VideoProcessor._cookie_header(cookie)
        if cookie_header:
            headers["Cookie"] = cookie_header
        request = urllib.request.Request(subtitle_url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8-sig", errors="replace")

    @staticmethod
    def _base_ydl_options(user_agent: str | None = None) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "http_headers": {"User-Agent": user_agent or VideoProcessor.BILI_USER_AGENT},
        }

    @staticmethod
    def _write_cookie_file(
        cookie: dict[str, str] | None,
        domain: str = ".bilibili.com",
    ) -> Path | None:
        """把单个平台的登录态写成短生命周期 Netscape cookiefile。

        新版 yt-dlp 已弃用通过 Cookie header 传凭据（存在安全风险且会被
        scope 到下载地址域名），cookiefile 才是受支持的方式。文件退出
        context 后立即删除，不把 cookie 写入任务产物或日志。
        """
        if not cookie:
            return None
        if domain == ".bilibili.com":
            mapping = (
                ("SESSDATA", cookie.get("sessdata")),
                ("bili_jct", cookie.get("bili_jct")),
                ("buvid3", cookie.get("buvid3")),
                ("buvid4", cookie.get("buvid4")),
                ("b_nut", cookie.get("b_nut")),
                ("b_lsid", cookie.get("b_lsid")),
            )
        else:
            # 仅允许浏览器登录管理器导出的 Douyin 会话字段，避免把任意
            # 请求体键写入 cookiefile；值中的换行/制表符也不能穿透格式。
            names = (
                "msToken", "ttwid", "odin_tt", "passport_csrf_token",
                "sid_guard", "sessionid", "sessionid_ss", "s_v_web_id",
                "__ac_nonce", "__ac_signature", "tt_scid", "csrf_session_id",
            )
            mapping = tuple((name, cookie.get(name)) for name in names)
        mapping = tuple(
            (name, str(value).replace("\t", "").replace("\r", "").replace("\n", ""))
            for name, value in mapping
            if value
        )
        if not mapping:
            return None
        lines = ["# Netscape HTTP Cookie File"]
        for name, value in mapping:
            lines.append(f"{domain}\tTRUE\t/\tFALSE\t0\t{name}\t{value}")
        fd, raw_path = tempfile.mkstemp(prefix="videotono-cookies-", suffix=".txt")
        os.close(fd)
        path = Path(raw_path)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @contextlib.contextmanager
    def _ydl(
        self,
        cookie: dict[str, str] | None,
        cookie_domain: str = ".bilibili.com",
        **extra: Any,
    ) -> Iterator[yt_dlp.YoutubeDL]:
        """构造 YoutubeDL：cookie 走临时 cookiefile，退出时自动清理。"""
        user_agent = self.DOUYIN_USER_AGENT if cookie_domain in {".douyin.com", ".iesdouyin.com"} else self.BILI_USER_AGENT
        options = self._base_ydl_options(user_agent)
        cookie_file: Path | None = None
        try:
            cookie_file = self._write_cookie_file(cookie, cookie_domain)
            if cookie_file is not None:
                options["cookiefile"] = str(cookie_file)
            options.update(extra)
            with yt_dlp.YoutubeDL(options) as ydl:
                yield ydl
        finally:
            if cookie_file is not None:
                try:
                    cookie_file.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _cookie_domain_for_url(url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if host == "iesdouyin.com" or host.endswith(".iesdouyin.com"):
            return ".iesdouyin.com"
        if host == "douyin.com" or host.endswith(".douyin.com"):
            return ".douyin.com"
        return ".bilibili.com"

    @staticmethod
    def _cookie_header(
        cookie: dict[str, str] | None,
        douyin: bool = False,
    ) -> str:
        if not cookie:
            return ""
        if douyin:
            names = (
                "msToken", "ttwid", "odin_tt", "passport_csrf_token", "sid_guard",
                "sessionid", "sessionid_ss", "s_v_web_id", "__ac_nonce",
                "__ac_signature", "tt_scid", "csrf_session_id",
            )
            return "; ".join(f"{name}={cookie[name]}" for name in names if cookie.get(name))
        names = {
            "sessdata": "SESSDATA",
            "bili_jct": "bili_jct",
            "buvid3": "buvid3",
            # 风控 Cookie：扫码登录会拿到这些字段，一起带上才能让 api.bilibili.com
            # 在视频页 412 的环境里照常返回（Issue #1 报告的可用组合）。
            "buvid4": "buvid4",
            "b_nut": "b_nut",
            "b_lsid": "b_lsid",
        }
        return "; ".join(
            f"{header_name}={cookie[key]}"
            for key, header_name in names.items()
            if cookie.get(key)
        )

    def _task_dir(self, task_id: str) -> Path:
        task_dir = (self.work_dir / task_id).resolve()
        if task_dir.parent != self.work_dir.resolve():
            raise ValueError("Invalid task id")
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir
