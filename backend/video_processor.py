import os
import re
import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Tuple, List, Optional
from enum import Enum
import yt_dlp
import cv2
from dotenv import load_dotenv

load_dotenv()


class VideoSource(Enum):
    """视频来源类型"""
    BILIBILI = "bilibili"
    YOUTUBE = "youtube"
    DOUYIN = "douyin"
    KUAISHOU = "kuaishou"
    TENCENT = "tencent"
    IQIYI = "iqiyi"
    YOUKU = "youku"
    WEIBO = "weibo"
    LOCAL = "local"
    OTHER = "other"


class VideoProcessor:
    def __init__(self):
        self.work_dir = Path("./workspace")
        self.work_dir.mkdir(exist_ok=True)

    async def detect_source(self, url_or_path: str) -> VideoSource:
        """检测视频来源"""
        # 本地文件 - 检查绝对路径和相对路径
        if os.path.exists(url_or_path):
            return VideoSource.LOCAL
        
        # 也检查是否是Windows绝对路径（例如 D:\path\to\file.mp4）
        try:
            normalized_path = Path(url_or_path).resolve()
            if normalized_path.exists():
                return VideoSource.LOCAL
        except Exception:
            pass

        url_lower = url_or_path.lower()

        # B站
        if "bilibili.com" in url_lower or "b23.tv" in url_lower:
            return VideoSource.BILIBILI

        # YouTube
        if "youtube.com" in url_lower or "youtu.be" in url_lower:
            return VideoSource.YOUTUBE

        # 抖音
        if "douyin.com" in url_lower or "iesdouyin.com" in url_lower:
            return VideoSource.DOUYIN

        # 快手
        if "kuaishou.com" in url_lower:
            return VideoSource.KUAISHOU

        # 腾讯视频
        if "v.qq.com" in url_lower:
            return VideoSource.TENCENT

        # 爱奇艺
        if "iqiyi.com" in url_lower:
            return VideoSource.IQIYI

        # 优酷
        if "youku.com" in url_lower:
            return VideoSource.YOUKU

        # 微博
        if "weibo.com" in url_lower:
            return VideoSource.WEIBO

        # 其他
        return VideoSource.OTHER

    async def get_video_info(self, url_or_path: str, cookie: Dict = None) -> Dict:
        """获取视频信息（支持多种来源）"""
        source = await self.detect_source(url_or_path)

        # 本地文件
        if source == VideoSource.LOCAL:
            return await self._get_local_video_info(url_or_path)

        # 在线视频，使用 yt-dlp 获取信息
        return await self._get_online_video_info(url_or_path, cookie)

    async def _get_local_video_info(self, file_path: str) -> Dict:
        """获取本地视频文件信息"""
        def _read_info():
            cap = cv2.VideoCapture(file_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = frame_count / fps if fps > 0 else 0
            cap.release()
            return fps, frame_count, duration

        fps, frame_count, duration = await asyncio.to_thread(_read_info)

        return {
            "title": Path(file_path).stem,
            "source": "local",
            "duration": duration,
            "file_path": file_path,
            "owner": "",
            "description": ""
        }

    async def _get_online_video_info(self, url: str, cookie: Dict = None) -> Dict:
        """获取在线视频信息（使用 yt-dlp）"""
        def _fetch_info():
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
            }
            if cookie:
                cookie_str = self._build_cookie_string(cookie)
                if cookie_str:
                    ydl_opts['headers'] = {'Cookie': cookie_str}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info

        info = await asyncio.to_thread(_fetch_info)
        return {
            "title": info.get("title", "未知标题"),
            "source": info.get("extractor", "unknown"),
            "duration": info.get("duration", 0),
            "owner": info.get("uploader", ""),
            "description": info.get("description", "")[:500],
            "thumbnail": info.get("thumbnail", ""),
            "view_count": info.get("view_count", 0)
        }

    async def download_video(self, url_or_path: str, task_id: str, cookie: Dict = None) -> Tuple[Path, Path]:
        """下载视频（支持多种来源）"""
        source = await self.detect_source(url_or_path)
        task_dir = self.work_dir / task_id
        task_dir.mkdir(exist_ok=True)

        # 本地文件
        if source == VideoSource.LOCAL:
            return await self._use_local_video(url_or_path, task_dir)

        # 在线视频：使用 yt-dlp 下载
        return await self._download_online_video(url_or_path, task_dir, cookie)

    async def _use_local_video(self, file_path: str, task_dir: Path) -> Tuple[Path, Path]:
        """使用本地视频文件"""
        # 转换为绝对路径并规范化
        video_path = Path(file_path).resolve()
        
        print(f"[本地视频] 原始路径: {file_path}")
        print(f"[本地视频] 规范化路径: {video_path}")
        print(f"[本地视频] 文件是否存在: {video_path.exists()}")

        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        # 如果文件不在当前task目录下，复制过来（不移动，保护用户原始文件）
        if video_path.parent != task_dir.resolve():
            target_path = task_dir / video_path.name
            print(f"[本地视频] 正在复制文件到workspace...")
            print(f"[本地视频] 源文件: {video_path}")
            print(f"[本地视频] 目标文件: {target_path}")
            await asyncio.to_thread(shutil.copy2, str(video_path), str(target_path))
            video_path = target_path
            print(f"[本地视频] 文件复制完成")

        # 提取音频
        audio_path = task_dir / f"{video_path.stem}.mp3"
        if not audio_path.exists():
            await self._extract_audio(video_path, audio_path)

        return video_path, audio_path

    async def _extract_audio(self, video_path: Path, audio_path: Path) -> Path:
        """从视频中提取音频，处理无音轨等异常情况"""
        vpath = str(video_path.resolve())
        apath = str(audio_path.resolve())

        def _run_ffmpeg(cmd):
            return subprocess.run(cmd, capture_output=True)

        # 尝试提取音频
        cmd = [
            'ffmpeg', '-i', vpath,
            '-q:a', '0', '-map', 'a',
            apath, '-y'
        ]
        result = await asyncio.to_thread(_run_ffmpeg, cmd)
        if result.returncode != 0:
            # 音频提取失败，可能是无音轨视频，生成静音音频
            print(f"[音频提取] 提取失败(rc={result.returncode})，生成静音音频: {video_path.name}")
            cmd_silent = [
                'ffmpeg', '-i', vpath,
                '-f', 'lavfi', '-i', 'anullsrc=channel_layout=mono:sample_rate=16000',
                '-t', '1', '-c:a', 'libmp3lame',
                apath, '-y'
            ]
            result2 = await asyncio.to_thread(_run_ffmpeg, cmd_silent)
            if result2.returncode != 0:
                raise RuntimeError(f"音频处理失败: {result2.stderr.decode('utf-8', errors='replace')[:200]}")
        return audio_path

    async def _download_online_video(self, url: str, task_dir: Path, cookie: Dict = None) -> Tuple[Path, Path]:
        """下载在线视频"""
        def _download():
            ydl_opts = {
                'format': 'bestvideo+bestaudio/best',
                'outtmpl': str(task_dir / '%(title)s.%(ext)s'),
                'merge_output_format': 'mp4',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
            }
            if cookie:
                cookie_str = self._build_cookie_string(cookie)
                if cookie_str:
                    ydl_opts['headers'] = {'Cookie': cookie_str}
                    print(f"使用 Cookie: {cookie_str[:50]}...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info

        info = await asyncio.to_thread(_download)
        video_path = task_dir / f"{info['title']}.mp4"

        # 提取音频
        audio_path = task_dir / f"{info['title']}.mp3"
        if not audio_path.exists():
            await self._extract_audio(video_path, audio_path)

        return video_path, audio_path

    def _build_cookie_string(self, cookie: Dict) -> str:
        """构建 Cookie 字符串"""
        cookie_parts = []
        if cookie.get("sessdata"):
            cookie_parts.append(f"SESSDATA={cookie['sessdata']}")
        if cookie.get("bili_jct"):
            cookie_parts.append(f"bili_jct={cookie['bili_jct']}")
        if cookie.get("buvid3"):
            cookie_parts.append(f"buvid3={cookie['buvid3']}")
        return "; ".join(cookie_parts)

    async def extract_frames(self, video_path: Path, task_id: str, interval: int = 10) -> List[Path]:
        """提取视频关键帧"""
        frames_dir = self.work_dir / task_id / "frames"
        frames_dir.mkdir(exist_ok=True)

        def _extract():
            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_interval = int(fps * interval)

            frame_count = 0
            paths = []

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_count % frame_interval == 0:
                    timestamp = frame_count / fps
                    output_path = frames_dir / f"frame_{len(paths):04d}_{int(timestamp)}s.jpg"
                    cv2.imwrite(str(output_path), frame)
                    paths.append(output_path)

                frame_count += 1

            cap.release()
            return paths

        screenshot_paths = await asyncio.to_thread(_extract)
        return screenshot_paths

    async def cleanup(self, task_id: str):
        """清理临时文件"""
        task_dir = self.work_dir / task_id
        if task_dir.exists():
            await asyncio.to_thread(shutil.rmtree, task_dir)

    def _extract_bvid(self, url: str) -> str:
        """提取B站BV号"""
        if "b23.tv" in url:
            import requests
            response = requests.head(url, allow_redirects=True)
            url = response.url

        match = re.search(r'BV\w+', url)
        if match:
            return match.group()
        return None
