from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode, urlparse

import yt_dlp

from .transcript import TranscriptSegment, parse_subtitle_payload


class VideoSource(str, Enum):
    BILIBILI = "bilibili"
    YOUTUBE = "youtube"
    LOCAL = "local"
    OTHER = "other"


@dataclass(frozen=True)
class SubtitleResult:
    segments: list[TranscriptSegment]
    language: str
    source: str


_EMBEDDED_LINK_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?(?:bilibili\.com|b23\.tv)/\S+", re.I)
_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"av(\d+)", re.I)
_TRAILING_PUNCT = ".,;:!?。，；：！？、）】」』”"


def normalize_video_input(value: str) -> str | None:
    """宽松识别视频输入：完整 http(s) 链接原样返回；否则从分享文本中提取
    B 站链接（bilibili.com / b23.tv，可缺省 scheme），或裸 BV/av 号补全为视频页 URL。
    无法识别时返回 None。"""
    text = (value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    match = _EMBEDDED_LINK_RE.search(text)
    if match:
        link = match.group(0).rstrip(_TRAILING_PUNCT)
        return link if link.startswith(("http://", "https://")) else f"https://{link}"
    bv = _BV_RE.search(text)
    if bv:
        return f"https://www.bilibili.com/video/{bv.group(0)}"
    av = _AV_RE.search(text)
    if av:
        return f"https://www.bilibili.com/video/av{av.group(1)}"
    return None


class VideoProcessor:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

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
        if host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be":
            return VideoSource.YOUTUBE
        return VideoSource.OTHER

    async def get_video_info(
        self, url_or_path: str, cookie: dict[str, str] | None = None, allow_local: bool = False
    ) -> dict[str, Any]:
        source = self.detect_source(url_or_path, allow_local=allow_local)
        if source == VideoSource.LOCAL:
            path = Path(url_or_path)
            return {
                "title": path.stem,
                "source": source.value,
                "duration": 0,
                "owner": "",
                "description": "",
            }
        return await asyncio.to_thread(self._extract_info, url_or_path, cookie)

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
    ) -> SubtitleResult | None:
        """B 站 AI 字幕专属通道。

        yt-dlp 的 bilibili 提取器不暴露 AI 字幕（ai-*）轨道，这里直调
        player API 获取字幕清单并下载。需要登录凭据（SESSDATA），下载
        字幕文件时需携带 Origin 头。
        """
        match = re.search(r"BV[0-9A-Za-z]{10}", url)
        if not match or not self._cookie_header(cookie):
            return None
        try:
            track = await asyncio.to_thread(
                self._bilibili_subtitle_track, match.group(0), cookie
            )
            if not track:
                return None
            payload = await asyncio.to_thread(
                self._download_text,
                track["url"],
                url,
                cookie,
                {"Origin": "https://www.bilibili.com"},
            )
            segments = parse_subtitle_payload(payload, "json")
        except Exception:
            return None
        if not segments:
            return None
        return SubtitleResult(
            segments=segments,
            language=track["language"],
            source="bilibili_ai_subtitle",
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
        cls, bvid: str, cookie: dict[str, str] | None
    ) -> dict[str, Any] | None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }
        cookie_header = cls._cookie_header(cookie)
        if cookie_header:
            headers["Cookie"] = cookie_header
        view = cls._bilibili_get_json(
            "https://api.bilibili.com/x/web-interface/view", {"bvid": bvid}, headers
        )
        cid = (view.get("data") or {}).get("cid")
        if not cid:
            return None
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

    async def download_audio(
        self, url: str, task_id: str, cookie: dict[str, str] | None = None
    ) -> Path:
        task_dir = self._task_dir(task_id)

        def _download() -> Path:
            options = self._base_ydl_options(cookie)
            options.update(
                {
                    "format": "bestaudio/best",
                    "outtmpl": str(task_dir / "audio.%(ext)s"),
                    "noplaylist": True,
                }
            )
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=True)
            matches = [
                item
                for item in task_dir.glob("audio.*")
                if item.suffix not in {".part", ".ytdl"}
            ]
            if not matches:
                raise RuntimeError("yt-dlp completed without producing an audio file")
            return max(matches, key=lambda item: item.stat().st_size)

        return await asyncio.to_thread(_download)

    async def download_preview_video(
        self, url: str, task_id: str, cookie: dict[str, str] | None = None
    ) -> Path:
        task_dir = self._task_dir(task_id)

        def _download() -> Path:
            options = self._base_ydl_options(cookie)
            options.update(
                {
                    "format": "worstvideo[height>=360]/bestvideo[height<=720]/worst",
                    "outtmpl": str(task_dir / "preview.%(ext)s"),
                    "noplaylist": True,
                }
            )
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=True)
            matches = [
                item
                for item in task_dir.glob("preview.*")
                if item.suffix not in {".part", ".ytdl"}
            ]
            if not matches:
                raise RuntimeError("yt-dlp completed without producing a preview video")
            return max(matches, key=lambda item: item.stat().st_size)

        return await asyncio.to_thread(_download)

    async def copy_local_media(self, source: Path, task_id: str) -> Path:
        task_dir = self._task_dir(task_id)
        source = source.resolve()
        target = (task_dir / f"input{source.suffix.lower()}").resolve()
        if source != target:
            await asyncio.to_thread(shutil.copy2, source, target)
        return target

    async def extract_frames(
        self, video_path: Path, task_id: str, interval: int
    ) -> list[Path]:
        if interval <= 0:
            return []
        return await asyncio.to_thread(
            self._extract_frames_sync, video_path, task_id, interval
        )

    def _extract_frames_sync(
        self, video_path: Path, task_id: str, interval: int
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
        self, url: str, cookie: dict[str, str] | None
    ) -> dict[str, Any]:
        options = self._base_ydl_options(cookie)
        options.update({"skip_download": True, "noplaylist": True})
        with yt_dlp.YoutubeDL(options) as ydl:
            raw_info = ydl.extract_info(url, download=False)
            info = ydl.sanitize_info(raw_info)
        return {
            "title": info.get("title") or "Untitled video",
            "source": info.get("extractor_key", "other").lower(),
            "duration": info.get("duration") or 0,
            "owner": info.get("uploader") or "",
            "description": (info.get("description") or "")[:1_000],
            "thumbnail": info.get("thumbnail") or "",
            "view_count": info.get("view_count") or 0,
            "subtitles": info.get("subtitles") or {},
            "automatic_captions": info.get("automatic_captions") or {},
        }

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
            "User-Agent": "Mozilla/5.0 VideoToNo/1.0",
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
    def _base_ydl_options(cookie: dict[str, str] | None) -> dict[str, Any]:
        headers = {"User-Agent": "Mozilla/5.0 VideoToNo/1.0"}
        cookie_header = VideoProcessor._cookie_header(cookie)
        if cookie_header:
            headers["Cookie"] = cookie_header
        return {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "http_headers": headers,
        }

    @staticmethod
    def _cookie_header(cookie: dict[str, str] | None) -> str:
        if not cookie:
            return ""
        names = {
            "sessdata": "SESSDATA",
            "bili_jct": "bili_jct",
            "buvid3": "buvid3",
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
