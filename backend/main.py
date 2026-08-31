from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

import aiofiles
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


class NoCacheStaticFiles(StaticFiles):
    """前端静态资源每次都重新校验，避免浏览器缓存旧版本。

    HTML 入口页更是直接 no-store：旧 HTML 与新版 script.js 混用会导致
    页面元素对不上、按钮全部"点了没反应"，因此入口页不允许任何缓存。
    """

    def file_response(self, *args: Any, **kwargs: Any) -> FileResponse:
        response = super().file_response(*args, **kwargs)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response
from pydantic import BaseModel, Field, SecretStr

from . import secret_box
from .config_store import LLM_KEYS_FILE, ConfigStore
from .llm_summarizer import LLMSummarizer, default_base_url, normalize_endpoint_host
from .bili_login import BiliLoginManager
from .douyin_login import DouyinLoginManager
from .transcript import (
    TranscriptSegment,
    format_timestamp,
    segments_to_prompt,
    transcript_quality,
)
from .video_processor import (
    VideoProcessor,
    VideoSource,
    bilibili_page_url,
    merge_bilibili_pages,
    normalize_video_input,
)
from .whisper_asr import WhisperTranscriber


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
WORKSPACE_DIR = Path(
    os.getenv("VIDEOTONOTES_WORKSPACE", str(BASE_DIR / "workspace"))
).resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
WHISPER_CACHE_DIR = Path(
    os.getenv("WHISPER_CACHE_DIR", str(WORKSPACE_DIR / "_model_cache"))
).resolve()
FRONTEND_DIR = BASE_DIR / "frontend"
APP_ICON_PATH = BASE_DIR / "sources" / "icon.png"
FAVICON_PATH = BASE_DIR / "sources" / "icon.ico"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2048"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
# 超过该体积的本地视频上传后自动提取音频（16kHz m4a）并删除原视频，
# 保留与在线视频一致的轻量工作目录；勾选“视频截图”时保留原视频。
LARGE_UPLOAD_EXTRACT_MB = int(os.getenv("LARGE_UPLOAD_EXTRACT_MB", "300"))
LARGE_UPLOAD_EXTRACT_BYTES = LARGE_UPLOAD_EXTRACT_MB * 1024 * 1024
MAX_CONCURRENT_TASKS = max(1, int(os.getenv("MAX_CONCURRENT_TASKS", "1")))
TASK_HISTORY_LIMIT = max(10, int(os.getenv("TASK_HISTORY_LIMIT", "100")))
ALLOWED_MEDIA_SUFFIXES = {
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
    ".aac",
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
}
WHISPER_MODELS = {"tiny", "base", "small", "medium", "large-v3", "turbo"}
DOUYIN_HINT = (
    "抖音链接解析失败。可以先点击“打开抖音浏览器”，在本机窗口完成登录/验证后重试；"
    "也可能是媒体地址已过期，请重新提交链接。"
)

app = FastAPI(title="VideoToNo API", version="1.3.0")


def is_loopback_client(host: str | None) -> bool:
    if not host or host == "testclient":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() == "localhost"
    if address.is_loopback:
        return True
    return bool(address.version == 6 and address.ipv4_mapped and address.ipv4_mapped.is_loopback)


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'sha256-UP0QZg7irvSMvOBz9mH2PIIE28+57UiavRfeVea0l3g='; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class LocalSecurityMiddleware:
    """纯 ASGI 中间件：只允许本机回环客户端访问，并为响应附加安全头。

    不用 Starlette 的 BaseHTTPMiddleware（@app.middleware("http")）：
    它对流式响应（如 /mcp/sse 的 SSE 长连接）会在转发响应体时遇到第二次
    http.response.start 而触发 AssertionError，导致 MCP 会话请求直接崩溃；
    这里直接包装 ASGI 应用，流式响应可以原样透传。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        client_host = client[0] if isinstance(client, (tuple, list)) and client else None
        if not is_loopback_client(client_host):
            body = json.dumps(
                {"detail": "VideoToNo 仅允许本机访问"}, ensure_ascii=False
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode("latin-1")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        async def wrapped_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                existing = {key.lower() for key, _value in headers}
                for key, value in SECURITY_HEADERS.items():
                    if key.lower() not in existing:
                        headers.append((key.encode("latin-1"), value.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, wrapped_send)


app.add_middleware(LocalSecurityMiddleware)

video_processor = VideoProcessor(WORKSPACE_DIR)
transcriber = WhisperTranscriber(WHISPER_CACHE_DIR)
bili_login_manager = BiliLoginManager(WORKSPACE_DIR)
douyin_login_manager = DouyinLoginManager(WORKSPACE_DIR)
config_store = ConfigStore(WORKSPACE_DIR)
tasks: dict[str, dict[str, Any]] = {}
running_jobs: dict[str, asyncio.Task[None]] = {}
task_slots = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


class BilibiliCookie(BaseModel):
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""


class DouyinCookie(BaseModel):
    """浏览器验证后短暂回传的抖音会话字段；不写入任务清单。"""

    model_config = {"extra": "allow"}


class LLMConfigPayload(BaseModel):
    model_type: str = "deepseek"
    api_key: SecretStr
    base_url: str | None = None
    model: str | None = None
    label: str | None = None


class LLMKeyPayload(BaseModel):
    """按接口地址保存一把 API Key：provider + base_url 决定它属于哪个端点。"""

    api_key: SecretStr
    provider: str = "deepseek"
    base_url: str | None = None
    label: str | None = None
    model: str | None = None


class LLMTestPayload(BaseModel):
    """测试连接的可选覆盖参数。

    字段不再与已保存配置逐项混合（那会把一个端点的 Key 配到另一个端点上），
    只有 API Key 会在地址完全一致时复用本机已存的那一把。
    """

    model_type: str | None = None
    api_key: SecretStr | None = None
    base_url: str | None = None
    model: str | None = None


class BiliCredentialsPayload(BaseModel):
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""


class LLMConfig(BaseModel):
    model_type: str = "deepseek"
    api_key: SecretStr = SecretStr("")
    base_url: str | None = None
    model: str | None = None
    custom_base_url: str | None = None
    custom_model_name: str | None = None


class SummarizeRequest(BaseModel):
    video_url: str = ""
    upload_task_id: str | None = None
    resume_task_id: str | None = None
    processing_mode: Literal["reuse", "restart"] = "reuse"
    # transcript：只做「视频 → 带时间轴转录」，不调用大模型，产物到 transcript.json 为止
    output: Literal["note", "transcript"] = "note"
    summary_style: Literal["detailed", "faithful", "concise"] = "detailed"
    reasoning_effort: Literal["auto", "off", "high", "max"] = "auto"
    prefer_subtitles: bool = True
    include_screenshots: bool = False
    screenshot_interval: int = Field(default=30, ge=5, le=300)
    whisper_model: str = "base"
    use_gpu: bool = False
    bilibili_cookie: BilibiliCookie | None = None
    douyin_cookie: DouyinCookie | None = None
    llm_config: LLMConfig


class TranscribeRequest(BaseModel):
    """`POST /api/transcribe` 的请求体：刻意没有任何大模型字段。"""

    video_url: str = ""
    upload_task_id: str | None = None
    resume_task_id: str | None = None
    processing_mode: Literal["reuse", "restart"] = "reuse"
    prefer_subtitles: bool = True
    whisper_model: str = "base"
    use_gpu: bool = False
    bilibili_cookie: BilibiliCookie | None = None
    douyin_cookie: DouyinCookie | None = None


def new_task(status: str = "pending", task_id: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "step": 0,
        "step_name": "初始化",
        "progress_message": "等待任务开始",
        "progress": 0,
        "logs": [],
        "result": None,
        "error": None,
        "advisory": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "elapsed_seconds": 0.0,
        "_task_id": task_id,
        "_cancel_requested": False,
        "_cancel_event": threading.Event(),
        "_started_monotonic": time.monotonic(),
    }


def task_elapsed_seconds(task: dict[str, Any]) -> float:
    if task.get("finished_at"):
        return float(task.get("elapsed_seconds") or 0)
    started = task.get("_started_monotonic")
    return max(0.0, time.monotonic() - float(started)) if started is not None else 0.0


def finish_task_timing(task: dict[str, Any]) -> float:
    elapsed = task_elapsed_seconds(task)
    task["elapsed_seconds"] = elapsed
    task["finished_at"] = datetime.now(timezone.utc).isoformat()
    return elapsed


def set_progress(
    task: dict[str, Any], step: int, name: str, progress: int, message: str
) -> None:
    task.update(step=step, step_name=name, progress=progress, progress_message=message)
    task["logs"].append(message)
    task_id = task.get("_task_id")
    if task_id:
        persist_task_runtime(str(task_id))


def task_directory(task_id: str) -> Path:
    candidate = (WORKSPACE_DIR / task_id).resolve()
    if candidate.parent != WORKSPACE_DIR:
        raise ValueError("非法任务 ID")
    return candidate


def persist_task_runtime(task_id: str) -> None:
    """Persist non-secret runtime state atomically beside task artifacts."""
    task = tasks.get(task_id)
    if not task:
        return
    directory = task_directory(task_id)
    if not directory.is_dir():
        return
    path = directory / "task.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError, TypeError):
        payload = {"task_id": task_id}
    result = task.get("result")
    result_metadata = None
    if isinstance(result, dict):
        result_metadata = {key: value for key, value in result.items() if key != "markdown"}
    payload["runtime"] = {
        "status": task.get("status"),
        "step": task.get("step", 0),
        "step_name": task.get("step_name", ""),
        "progress": task.get("progress", 0),
        "logs": list(task.get("logs", []))[-200:],
        "error": task.get("error"),
        "created_at": task.get("created_at"),
        "finished_at": task.get("finished_at"),
        "elapsed_seconds": task_elapsed_seconds(task),
        "result": result_metadata,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def raise_if_cancel_requested(task: dict[str, Any]) -> None:
    if task.get("_cancel_requested"):
        raise asyncio.CancelledError


# 任务结果系统通知：由启动器把托盘气泡接进来（打包模式才有效），
# backend 不直接依赖 pystray，避免引入循环依赖。
_task_notify_hook: Callable[[str, str], None] | None = None


def register_task_notify(callback: Callable[[str, str], None]) -> None:
    """注册通知回调：callback(title, message)。"""
    global _task_notify_hook
    _task_notify_hook = callback


def notify_task(title: str, message: str) -> None:
    """发送系统通知；异步执行且绝不影响任务主流程。"""
    callback = _task_notify_hook
    if callback is None:
        return

    def _run() -> None:
        try:
            callback(title, message)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def normalize_source_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").lower()
    netloc = hostname
    if parsed.port:
        netloc = f"{hostname}:{parsed.port}"
    path = parsed.path.rstrip("/")
    query = "" if hostname == "bilibili.com" or hostname.endswith(".bilibili.com") else parsed.query
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def source_url_from_task(task_dir: Path) -> str | None:
    manifest_path = task_dir / "task.json"
    if manifest_path.is_file():
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8")).get("source_url")
            if value:
                return str(value)
        except (OSError, ValueError, TypeError):
            pass

    notes_path = task_dir / "notes.md"
    if notes_path.is_file():
        try:
            for line in reversed(notes_path.read_text(encoding="utf-8").splitlines()):
                if line.startswith("来源："):
                    return line.removeprefix("来源：").strip()
        except OSError:
            pass
    return None


def find_reusable_task(source_url: str) -> str | None:
    normalized_source = normalize_source_url(source_url)
    candidates = sorted(
        (path for path in WORKSPACE_DIR.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if not (candidate / "transcript.json").is_file():
            continue
        existing_source = source_url_from_task(candidate)
        if existing_source and normalize_source_url(existing_source) == normalized_source:
            return candidate.name
    return None


def load_transcript_result(task_dir: Path) -> dict[str, Any] | None:
    path = task_dir / "transcript.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        segments = [
            TranscriptSegment(float(item["start"]), float(item["end"]), str(item["text"]))
            for item in payload.get("segments", [])
            if str(item.get("text", "")).strip()
        ]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not segments:
        return None
    return {
        "segments": segments,
        "language": payload.get("language") or "unknown",
        "source": payload.get("source") or "reused_transcript",
        "quality": payload.get("quality"),
    }


def load_reused_video_info(
    task_dir: Path,
    transcript_result: dict[str, Any],
    source_url: str | None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    manifest_path = task_dir / "task.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            pass

    title = str(manifest.get("title") or "").strip()
    if not title:
        notes_path = task_dir / "notes.md"
        try:
            first_line = notes_path.read_text(encoding="utf-8").splitlines()[0]
            if first_line.startswith("# 视频笔记：《") and first_line.endswith("》"):
                title = first_line.removeprefix("# 视频笔记：《")[:-1]
        except (OSError, IndexError):
            pass
    if not title:
        title = f"已恢复任务 {task_dir.name}"

    segments: list[TranscriptSegment] = transcript_result["segments"]
    duration = float(manifest.get("duration") or max(segment.end for segment in segments))
    if source_url:
        source = video_processor.detect_source(source_url).value
    else:
        source = VideoSource.LOCAL.value
    return {
        "title": title,
        "source": source,
        "duration": duration,
        "owner": str(manifest.get("owner") or ""),
        "upload_date": str(manifest.get("upload_date") or ""),
        "timestamp": manifest.get("timestamp") or 0,
        "view_count": manifest.get("view_count") or 0,
        "like_count": manifest.get("like_count") or 0,
        "description": "",
    }


def update_task_manifest(task_id: str, info: dict[str, Any]) -> None:
    path = task_directory(task_id) / "task.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {"task_id": task_id}
    payload.update(
        title=info.get("title"),
        owner=info.get("owner"),
        duration=info.get("duration"),
        source=info.get("source"),
        upload_date=info.get("upload_date"),
        timestamp=info.get("timestamp"),
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_task_manifest(task_id: str) -> dict[str, Any]:
    path = task_directory(task_id) / "task.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_task_manifest(task_id: str, request: SummarizeRequest, reused_task_id: str | None) -> None:
    uploaded_filename = tasks.get(task_id, {}).get("uploaded_filename")
    payload = {
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": request.video_url if request.video_url and not uploaded_filename else None,
        "normalized_source_url": (
            normalize_source_url(request.video_url)
            if request.video_url and not uploaded_filename
            else None
        ),
        "uploaded_filename": uploaded_filename,
        "processing_mode": request.processing_mode,
        "output": request.output,
        "summary_style": request.summary_style,
        "reasoning_effort": request.reasoning_effort,
        "reused_task_id": reused_task_id,
    }
    (task_directory(task_id) / "task.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_upload_manifest(task_id: str, filename: str) -> None:
    payload = {
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": None,
        "uploaded_filename": filename,
    }
    (task_directory(task_id) / "task.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@app.post("/api/summarize")
async def start_summarize(request: SummarizeRequest) -> dict[str, str | None]:
    if request.whisper_model not in WHISPER_MODELS:
        raise HTTPException(status_code=422, detail="不支持的 Whisper 模型")
    if request.video_url:
        normalized = normalize_video_input(request.video_url)
        if not normalized:
            raise HTTPException(
                status_code=422,
                detail="无法识别视频链接：支持 http(s) 链接，或包含 B 站/抖音链接的分享文本",
            )
        request.video_url = normalized
    reused_task_id = request.resume_task_id
    if request.processing_mode == "restart":
        reused_task_id = None
    elif not reused_task_id and request.video_url:
        reused_task_id = find_reusable_task(request.video_url)

    if reused_task_id:
        resume_dir = task_directory(reused_task_id)
        if not resume_dir.is_dir():
            raise HTTPException(status_code=404, detail="要继续的任务目录不存在")

    task_id = request.upload_task_id or str(uuid.uuid4())
    if request.upload_task_id:
        existing = tasks.get(request.upload_task_id)
        if not existing or existing.get("status") != "uploaded":
            raise HTTPException(status_code=400, detail="上传任务不存在或已经处理")
        uploaded_path = existing.get("uploaded_file_path")
        tasks[task_id] = new_task(task_id=task_id)
        tasks[task_id]["uploaded_file_path"] = uploaded_path
        tasks[task_id]["uploaded_filename"] = existing.get("uploaded_filename")
    else:
        if request.video_url:
            try:
                video_processor.detect_source(request.video_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        elif not reused_task_id:
            raise HTTPException(status_code=422, detail="请提供视频链接、上传文件或可继续的任务")
        tasks[task_id] = new_task(task_id=task_id)
        task_directory(task_id).mkdir(parents=True, exist_ok=False)

    tasks[task_id]["resume_task_id"] = reused_task_id
    tasks[task_id]["output"] = request.output
    if reused_task_id:
        old_task = tasks.get(reused_task_id, {})
        if old_task.get("uploaded_file_path"):
            tasks[task_id]["uploaded_file_path"] = old_task["uploaded_file_path"]
            tasks[task_id]["uploaded_filename"] = old_task.get("uploaded_filename")
        tasks[task_id]["logs"].append(f"将从任务 {reused_task_id} 复用已有产物")
    write_task_manifest(task_id, request, reused_task_id)
    persist_task_runtime(task_id)

    job = asyncio.create_task(run_queued_video_task(task_id, request))
    running_jobs[task_id] = job
    job.add_done_callback(lambda _job, current_id=task_id: running_jobs.pop(current_id, None))
    return {"task_id": task_id, "reused_task_id": reused_task_id}


@app.post("/api/transcribe")
async def start_transcribe(request: TranscribeRequest) -> dict[str, str | None]:
    """只做「视频 → 带时间轴转录」，全程不调用大模型，本机没有 API Key 也能用。

    给自带模型的 agent 用：拿原料自己组织笔记，不必先给本机配一把 Key。
    复用同一套任务机制（进度、取消、并发闸门、产物复用），产物停在
    `transcript.json` / `transcript.md`；正文用 `GET /api/task/{id}/transcript` 取。
    """
    return await start_summarize(
        SummarizeRequest(
            **request.model_dump(), output="transcript", llm_config=LLMConfig()
        )
    )


def task_status_payload(task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in task.items() if not key.startswith("_")}
    payload["elapsed_seconds"] = task_elapsed_seconds(task)
    manifest = read_task_manifest(task_id)
    source_url = str(manifest.get("source_url") or "")
    payload["source_url"] = source_url if source_url.startswith(("http://", "https://")) else None
    payload["uploaded_filename"] = payload.get("uploaded_filename") or manifest.get(
        "uploaded_filename"
    )
    return payload


@app.get("/api/tasks")
async def list_recent_tasks() -> dict[str, list[dict[str, Any]]]:
    # 纯转录任务不进网页端历史：它没有 result.markdown，而且 agent 批量取原料
    # 不应该把用户的笔记历史挤出这 20 条（过滤在截断之前）
    ordered = sorted(
        (
            item
            for item in tasks.items()
            if item[1].get("output") != "transcript"
        ),
        key=lambda item: str(item[1].get("created_at") or ""),
        reverse=True,
    )[:20]
    summaries: list[dict[str, Any]] = []
    for task_id, task in ordered:
        manifest = read_task_manifest(task_id)
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        summaries.append(
            {
                "task_id": task_id,
                "status": task.get("status"),
                "step_name": task.get("step_name"),
                "progress": task.get("progress", 0),
                "title": result.get("title") or manifest.get("title") or manifest.get("uploaded_filename") or "未命名任务",
                "created_at": task.get("created_at"),
                "finished_at": task.get("finished_at"),
                "elapsed_seconds": task_elapsed_seconds(task),
            }
        )
    return {"tasks": summaries}


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str) -> dict[str, Any]:
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已被清理")
    return task_status_payload(task_id, task)


@app.get("/api/task/{task_id}/transcript")
async def get_task_transcript(
    task_id: str, output_format: Literal["json", "markdown"] = "json"
) -> dict[str, Any]:
    """取该任务的带时间轴转录（`segments` 的时间单位是秒）。

    只看转录产物在不在，不看任务状态：即便笔记在第 6 步失败，已完成的转录
    照样能取走，不必重跑。`markdown` 返回人读版整篇文本，`json` 返回分段。
    """
    try:
        directory = task_directory(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法任务 ID") from exc
    json_path = directory / "transcript.json"
    if not json_path.is_file():
        raise HTTPException(
            status_code=409, detail="该任务还没有转录产物，可先调用 POST /api/transcribe"
        )
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(status_code=500, detail="转录文件无法读取") from None

    task = tasks.get(task_id) or {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    manifest = read_task_manifest(task_id)
    title = str(
        result.get("title") or manifest.get("title") or manifest.get("uploaded_filename") or ""
    )

    if output_format == "markdown":
        markdown_path = directory / "transcript.md"
        if not markdown_path.is_file():
            raise HTTPException(status_code=409, detail="转录 Markdown 不存在")
        return {
            "task_id": task_id,
            "title": title,
            "format": "markdown",
            "text": markdown_path.read_text(encoding="utf-8"),
        }

    segments = payload.get("segments") or []
    return {
        "task_id": task_id,
        "title": title,
        "format": "json",
        "language": payload.get("language"),
        "transcript_source": payload.get("source"),
        "transcript_quality": payload.get("quality"),
        "segment_count": len(segments),
        "segments": segments,
    }


@app.get("/api/whisper-models")
async def whisper_models_status() -> dict[str, Any]:
    """返回各 Whisper 模型在本机的缓存状态（供前端标注下拉框）。"""
    models = [
        {
            "id": model_id,
            "status": transcriber._model_cache_status(model_id),
        }
        for model_id in sorted(WHISPER_MODELS)
    ]
    return {"models": models, "manual_dir": str(WHISPER_CACHE_DIR / "manual")}


class WhisperManualFolderPayload(BaseModel):
    model: str


def _open_in_file_manager(path: Path) -> bool:
    """用系统文件管理器打开目录；失败时返回 False（前端展示路径兜底）。"""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        return False


@app.post("/api/whisper-models/manual-folder")
async def open_whisper_manual_folder(payload: WhisperManualFolderPayload) -> dict[str, Any]:
    """创建并打开手动导入模型的目标文件夹（大模型网络下载失败时的替代方案）。"""
    if payload.model not in WHISPER_MODELS:
        raise HTTPException(status_code=422, detail="不支持的 Whisper 模型")
    manual_dir = WHISPER_CACHE_DIR / "manual" / payload.model
    manual_dir.mkdir(parents=True, exist_ok=True)
    return {
        "path": str(manual_dir),
        "opened": _open_in_file_manager(manual_dir),
        "files": list(WhisperTranscriber.WHISPER_MODEL_FILES),
        "download_url": f"https://hf-mirror.com/Systran/faster-whisper-{payload.model}/tree/main",
    }


def _key_storage_info() -> dict[str, Any]:
    return {"algorithm": secret_box.storage_backend(), "secure": secret_box.is_secure_storage()}


def resolve_llm_credentials(*, model_type: str, base_url: str | None, api_key: str) -> tuple[str, str]:
    """显式 Key 永远优先；否则只按接口地址复用本机已存 Key。

    地址不一致就直接失败，不会"先拿本机某把 Key 试试"——那正是把 DeepSeek 的
    Key 发给第三方网关的路径。返回 ``(api_key, 来源说明)``，来源说明进任务日志。
    """
    explicit = (api_key or "").strip()
    if explicit:
        return explicit, "本次请求提供"
    resolved = config_store.resolve_stored_key(model_type=model_type, base_url=base_url or "")
    secret = str(resolved.get("api_key") or "")
    if secret:
        entry = resolved.get("entry") or {}
        where = str(entry.get("label") or resolved.get("host") or "")
        masked = str(entry.get("api_key_masked") or "")
        return secret, f"本机已存 {masked}（{where}）"
    raise RuntimeError(_missing_key_message(model_type, base_url, resolved))


def _missing_key_message(model_type: str, base_url: str | None, resolved: dict[str, Any]) -> str:
    target = (base_url or "").strip() or default_base_url(model_type) or "未指定接口地址"
    reason = str(resolved.get("reason") or "")
    detail = {
        "undecryptable": (
            f"为 {target} 保存的 Key 无法解密（可能来自其他机器或其他 Windows 账户），"
            "请重新填写并保存"
        ),
        "corrupt_keys_file": (
            f"本机密钥文件 {LLM_KEYS_FILE} 已损坏，请在模型配置页清除本机配置后重新保存"
        ),
        "unknown_endpoint": f"接口地址无法识别（{target}），不会复用任何本机 Key",
    }.get(reason) or f"本机没有为 {target} 保存 Key"
    return (
        f"未提供 API Key，{detail}。"
        "请在模型配置页填写 API Key 并「保存到本机」，或在请求中直接带上 api_key。"
    )


@app.get("/api/llm-keys")
async def get_llm_keys(base_url: str = "", provider: str = "") -> dict[str, Any]:
    """列出本机保存的接口 Key（只有掩码）；带 base_url 时顺带回答"这个地址存了没有"。"""
    if config_store.keys_are_corrupt():
        return {"storage": _key_storage_info(), "error": "corrupt_keys_file", "entries": []}
    payload: dict[str, Any] = {"storage": _key_storage_info(), "entries": config_store.list_keys()}
    if base_url or provider:
        resolved = config_store.resolve_stored_key(model_type=provider, base_url=base_url)
        entry = resolved.get("entry")
        key_state = "none"
        if entry:
            key_state = "saved" if resolved.get("api_key") else "undecryptable"
        payload["match"] = {
            "saved": bool(entry),
            "key_state": key_state,
            "endpoint": normalize_endpoint_host(base_url, provider),
            **(entry or {}),
        }
    return payload


@app.put("/api/llm-keys")
async def put_llm_key(payload: LLMKeyPayload) -> dict[str, Any]:
    """显式保存：把某个接口地址的 API Key 加密写入本机。清除只能走 DELETE。"""
    try:
        entry = config_store.put_key(
            provider=payload.provider,
            base_url=payload.base_url or "",
            api_key=payload.api_key.get_secret_value(),
            label=payload.label,
            model=payload.model,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except secret_box.SecretBoxError as error:
        raise HTTPException(status_code=500, detail=f"本机加密保存失败：{error}") from error
    return {"saved": True, **entry}


@app.delete("/api/llm-keys")
async def delete_llm_key(base_url: str = "", provider: str = "") -> dict[str, Any]:
    return {"removed": config_store.delete_key(base_url=base_url, model_type=provider)}


@app.get("/api/llm-config")
async def get_llm_config() -> dict[str, Any]:
    """旧接口：报告本机最近保存的一条配置（含掩码，不含 Key 原文），供 MCP 免传参复用。"""
    entry = config_store.most_recent_entry()
    if not entry or not entry["has_key"]:
        return {"saved": False}
    return {
        "saved": True,
        "model_type": entry["provider"],
        "model": entry["model"],
        "base_url": entry["base_url"] or None,
        "api_key_masked": entry["api_key_masked"] or None,
    }


@app.post("/api/llm-config")
async def save_llm_config(payload: LLMConfigPayload) -> dict[str, Any]:
    """旧接口：等价于按接口地址保存 Key（MCP 的 save_llm_config 仍走这里）。"""
    try:
        entry = config_store.put_key(
            provider=payload.model_type,
            base_url=payload.base_url or "",
            api_key=payload.api_key.get_secret_value(),
            label=payload.label,
            model=payload.model,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except secret_box.SecretBoxError as error:
        raise HTTPException(status_code=500, detail=f"本机加密保存失败：{error}") from error
    return {
        "saved": True,
        "model_type": entry["provider"],
        "model": entry["model"],
        "label": entry["label"],
    }


@app.post("/api/llm-test")
async def test_llm_connection(
    payload: LLMTestPayload | None = None,
) -> dict[str, Any]:
    """用（可选覆盖的）LLM 配置做一次轻量连通测试。"""
    body = payload or LLMTestPayload()
    model_type = body.model_type or "deepseek"
    base_url = body.base_url or default_base_url(model_type) or None
    try:
        api_key, key_source = resolve_llm_credentials(
            model_type=model_type,
            base_url=base_url,
            api_key=body.api_key.get_secret_value() if body.api_key else "",
        )
    except RuntimeError as error:
        return {"ok": False, "error": str(error)}
    try:
        summarizer = LLMSummarizer(
            model_type=model_type,
            api_key=api_key,
            base_url=base_url,
            model=body.model,
        )
        ok, message, latency = await summarizer.test_connection()
        return {
            "ok": ok,
            "message": message,
            "latency_ms": int(round(latency * 1000)),
            "model": body.model or summarizer.model,
            "base_url": summarizer.base_url,
            "key_source": key_source,
        }
    except Exception as exc:
        return {"ok": False, "error": f"初始化失败：{exc}"}


@app.delete("/api/llm-config")
async def clear_llm_config() -> dict[str, Any]:
    """旧接口：只清除它刚报告的那一条，不会一次清空全部本机 Key。"""
    entry = config_store.most_recent_entry()
    removed = bool(entry) and config_store.delete_key(
        base_url=entry["base_url"], model_type=entry["provider"]
    )
    return {"saved": False, "removed": removed}


@app.get("/api/bili-credentials")
async def get_bili_credentials() -> dict[str, Any]:
    """Return saved Bilibili credential status without exposing cookie values.

    走 ``bili_credentials_status()``：只用保存时写死的掩码，全程不解密，
    所以换机器解不开时这个端点仍然有得报。
    """
    return config_store.bili_credentials_status()


@app.post("/api/bili-credentials")
async def save_bili_credentials(payload: BiliCredentialsPayload) -> dict[str, Any]:
    try:
        config_store.save_bili_credentials(
            {"sessdata": payload.sessdata, "bili_jct": payload.bili_jct, "buvid3": payload.buvid3}
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except secret_box.SecretBoxError as error:
        raise HTTPException(status_code=500, detail=f"本机加密保存失败：{error}") from error
    return {"saved": True}


@app.delete("/api/bili-credentials")
async def clear_bili_credentials() -> dict[str, bool]:
    config_store.clear_bili_credentials()
    return {"saved": False}


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "VideoToNo",
        "version": app.version,
        # portable/dev 互不复用对方实例，避免 dev 服务占用端口导致打包版不驻留托盘
        "mode": "portable" if getattr(sys, "frozen", False) else "dev",
        # 本机保存的 API Key 是加密还是明文回退，答疑时看一眼健康接口即可确认
        "llm_key_storage": secret_box.storage_backend(),
        "dependencies": {
            "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
            "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
            "openai": importlib.util.find_spec("openai") is not None,
        },
    }


@app.post("/api/bili-login/start")
async def bili_login_start() -> dict[str, Any]:
    return await bili_login_manager.start()


@app.get("/api/bili-login/status")
async def bili_login_status() -> dict[str, Any]:
    return await bili_login_manager.status()


@app.post("/api/bili-login/cancel")
async def bili_login_cancel() -> dict[str, Any]:
    return await bili_login_manager.cancel()


@app.post("/api/douyin-login/start")
async def douyin_login_start() -> dict[str, Any]:
    """打开独立的本机浏览器 profile，用户可手动完成抖音登录/验证。"""
    return await douyin_login_manager.start()


@app.get("/api/douyin-login/status")
async def douyin_login_status() -> dict[str, Any]:
    return await douyin_login_manager.status()


@app.post("/api/douyin-login/cancel")
async def douyin_login_cancel() -> dict[str, Any]:
    return await douyin_login_manager.cancel()


# 视频容器后缀（可能带音轨、需要时提取）；纯音频后缀直接使用
VIDEO_CONTAINER_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


@app.post("/api/upload")
async def upload_video(
    file: UploadFile = File(...),
    include_screenshots: str | None = Form(default=None),
) -> dict[str, str]:
    filename = Path(file.filename or "upload.bin").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_MEDIA_SUFFIXES:
        raise HTTPException(status_code=415, detail="不支持的媒体文件类型")

    task_id = str(uuid.uuid4())
    task_dir = WORKSPACE_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=False)
    file_path = task_dir / f"input{suffix}"
    wants_screenshots = (include_screenshots or "").strip().lower() in {
        "1", "true", "yes"
    }
    written = 0
    try:
        async with aiofiles.open(file_path, "wb") as destination:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"上传文件超过大小限制（上限 {MAX_UPLOAD_MB} MB）",
                    )
                await destination.write(chunk)
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise
    finally:
        await file.close()

    log_lines: list[str] = [f"文件已上传：{filename}"]
    # 大视频且不需要截图时：提取音频、删除原视频，减少工作目录体积
    if (
        written >= LARGE_UPLOAD_EXTRACT_BYTES
        and suffix in VIDEO_CONTAINER_SUFFIXES
        and not wants_screenshots
    ):
        audio_path = task_dir / "input.m4a"
        try:
            extracted = await asyncio.to_thread(
                video_processor.extract_audio_track, file_path, audio_path
            )
        except Exception:
            extracted = None
        if extracted is not None and extracted.is_file():
            original_size = written
            file_path.unlink(missing_ok=True)
            file_path = audio_path
            log_lines.append(
                f"大视频已提取音频：{format_bytes_size(original_size)} → "
                f"{format_bytes_size(audio_path.stat().st_size)}（原视频已清理；"
                "如需截图请取消勾选该任务后重新上传）"
            )
        else:
            log_lines.append(
                "视频较大，未能提取音频，将直接用原文件转写（不影响使用）"
            )

    tasks[task_id] = new_task(status="uploaded", task_id=task_id)
    tasks[task_id].update(
        uploaded_file_path=str(file_path),
        uploaded_filename=filename,
        logs=log_lines,
    )
    write_upload_manifest(task_id, filename)
    persist_task_runtime(task_id)
    return {"task_id": task_id, "file_path": str(file_path), "filename": filename}


@app.get("/api/image/{task_id}/{filename}")
async def get_image(task_id: str, filename: str) -> FileResponse:
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    task_dir = (WORKSPACE_DIR / task_id).resolve()
    if task_dir.parent != WORKSPACE_DIR:
        raise HTTPException(status_code=400, detail="非法任务 ID")
    expected_parent = (task_dir / "frames").resolve()
    image_path = (expected_parent / filename).resolve()
    if image_path.parent != expected_parent or not image_path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(image_path)


@app.get("/api/download/{task_id}")
async def download_summary_markdown(task_id: str) -> FileResponse:
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "completed":
        raise HTTPException(status_code=409, detail="任务尚未完成")

    task_dir = WORKSPACE_DIR / task_id
    output_path = task_dir / "notes.md"
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Markdown 笔记不存在")

    title = task["result"].get("title", "video-notes")
    safe_name = _safe_filename(title)
    encoded_name = quote(f"{safe_name}.md")
    return FileResponse(
        output_path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{safe_name or 'video-notes'}.md",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


@app.post("/api/task/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict[str, Any]:
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="任务已经结束")

    first_request = not task.get("_cancel_requested")
    task["_cancel_requested"] = True
    cancel_event = task.get("_cancel_event")
    if cancel_event is not None:
        cancel_event.set()
    task.update(status="cancelling", step_name="取消中", error=None)
    task["logs"].append("已收到取消请求；正在尽快停止当前步骤")
    persist_task_runtime(task_id)
    if first_request:
        # 取消的唯一收口：直接掐掉 asyncio 任务，让 CancelledError 落在当前 await 点。
        # should_abort / cancel_event 只是让后台线程早点收工的优化，新增阶段忘了接线
        # 也不会再让取消"失效"（v1.2.3 的 412 回退把没接线的读取信息/查找字幕阶段
        # 拉长，表现为取消不动）。这一句之后不能再有 await，否则会覆盖处理器写的终态。
        job = running_jobs.get(task_id)
        if job is not None and not job.done():
            job.cancel()
    return {
        "cancelled": False,
        "status": task["status"],
        "elapsed_seconds": task_elapsed_seconds(task),
    }


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str) -> dict[str, bool]:
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    job = running_jobs.get(task_id)
    if job and not job.done():
        raise HTTPException(status_code=409, detail="任务仍在运行，请先取消并等待其停止")
    running_jobs.pop(task_id, None)
    await video_processor.cleanup(task_id)
    tasks.pop(task_id, None)
    return {"deleted": True}


def _format_page_nums(pages) -> str:
    """把分 P 对象 / 页码列表格式化为 “1、2、3”。"""
    nums = sorted(int(page.page if hasattr(page, "page") else page) for page in pages)
    return "、".join(str(num) for num in nums)


async def run_queued_video_task(task_id: str, request: SummarizeRequest) -> None:
    task = tasks[task_id]
    task.update(status="queued", step_name="等待执行", progress=0)
    task["logs"].append(
        f"任务已进入队列；当前最多同时处理 {MAX_CONCURRENT_TASKS} 个任务"
    )
    persist_task_runtime(task_id)
    try:
        async with task_slots:
            raise_if_cancel_requested(task)
            await process_video_task(task_id, request)
    except asyncio.CancelledError:
        finish_task_timing(task)
        task.update(status="cancelled", step_name="已取消", error=None)
        task["logs"].append("任务已取消；已生成的中间文件将保留")
        persist_task_runtime(task_id)


async def process_video_task(task_id: str, request: SummarizeRequest) -> None:
    task = tasks[task_id]
    task["status"] = "processing"
    persist_task_runtime(task_id)

    def should_abort() -> bool:
        return bool(task.get("_cancel_requested"))

    douyin_cookie = request.douyin_cookie.model_dump() if request.douyin_cookie else None
    # 终态通知会用 title 组文案：早期失败（读凭据、取元数据）时 title 还没赋值，
    # 少了这个初值 except 分支自己会 NameError，任务就只改了内存、task.json 留在 processing
    title = request.video_url or "上传的视频"
    # B 站视频页被风控时（Issue #1）处理链会回退到开放接口直连，回退说明经这里进任务日志
    fallback_notes: list[str] = []

    def note_api_fallback() -> None:
        for note in fallback_notes:
            if note not in task["logs"]:
                task["logs"].append(note)

    try:
        raise_if_cancel_requested(task)
        bili_cookie = (
            request.bilibili_cookie.model_dump()
            if request.bilibili_cookie
            else config_store.load_bili_credentials()
        )
        resume_task_id = task.get("resume_task_id")
        resume_dir = task_directory(resume_task_id) if resume_task_id else None
        source_url = request.video_url or (source_url_from_task(resume_dir) if resume_dir else None)
        uploaded_path = task.get("uploaded_file_path")
        if not uploaded_path and resume_dir:
            uploaded_media = next(
                (path for path in resume_dir.glob("input.*") if path.is_file()), None
            )
            uploaded_path = str(uploaded_media) if uploaded_media else None
        is_local = bool(uploaded_path) or not source_url
        source_kind = (
            video_processor.detect_source(source_url) if source_url else VideoSource.LOCAL
        )
        cookie = douyin_cookie if source_kind == VideoSource.DOUYIN else bili_cookie
        transcript_result = load_transcript_result(resume_dir) if resume_dir else None
        bili_pages_to_transcribe: list = []

        if transcript_result:
            set_progress(task, 1, "恢复任务信息", 8, "正在读取已有任务信息")
            info = load_reused_video_info(resume_dir, transcript_result, source_url)
        else:
            media_input = uploaded_path or source_url or ""
            set_progress(task, 1, "读取视频信息", 8, "正在读取视频信息")
            info = await video_processor.get_video_info(
                media_input, cookie, allow_local=is_local, notes=fallback_notes
            )
            note_api_fallback()
        raise_if_cancel_requested(task)
        title = info["title"]
        update_task_manifest(task_id, info)
        task["logs"].append(f"标题：{title}")
        if info.get("duration"):
            task["logs"].append(f"时长：{format_duration(info['duration'])}")

        if transcript_result:
            set_progress(task, 4, "复用转录", 48, "已读取已有转录，跳过字幕、音频和 Whisper")
            task["logs"].append(
                f"复用任务 {resume_task_id} 的 {len(transcript_result['segments'])} 个转录分段"
            )
        elif request.prefer_subtitles and not is_local:
            set_progress(task, 2, "查找平台字幕", 16, "正在查找平台字幕")
            subtitle = await video_processor.fetch_subtitles(source_url or "", info, cookie)
            if subtitle:
                transcript_result = {
                    "segments": subtitle.segments,
                    "language": subtitle.language,
                    "source": subtitle.source,
                }
                task["logs"].append(
                    f"已使用平台字幕：{subtitle.language}，共 {len(subtitle.segments)} 段"
                )
            else:
                is_bilibili = (
                    video_processor.detect_source(source_url or "")
                    == VideoSource.BILIBILI
                )
                subtitle_outcome = (
                    await video_processor.fetch_bilibili_subtitles(source_url or "", cookie)
                    if is_bilibili
                    else None
                )
                if subtitle_outcome is not None:
                    bili_pages_to_transcribe = list(subtitle_outcome.pages_to_transcribe)
                    if subtitle_outcome.title:
                        info["title"] = subtitle_outcome.title
                    if subtitle_outcome.pages:
                        info["duration"] = sum(
                            page.duration for page in subtitle_outcome.pages
                        ) or info.get("duration")
                    if subtitle_outcome.total_pages > 1:
                        task["logs"].append(
                            f"检测到 B 站分 P 视频：共 {subtitle_outcome.total_pages} 个分 P，"
                            f"本次处理 {_format_page_nums(subtitle_outcome.pages)}"
                        )
                        update_task_manifest(task_id, info)
                if subtitle_outcome is not None and subtitle_outcome.result:
                    transcript_result = {
                        "segments": subtitle_outcome.result.segments,
                        "language": subtitle_outcome.result.language,
                        "source": subtitle_outcome.result.source,
                    }
                    task["logs"].append(
                        f"已使用 B 站 AI 字幕：{subtitle_outcome.result.language}，"
                        f"共 {len(subtitle_outcome.result.segments)} 段"
                    )
                    if bili_pages_to_transcribe:
                        task["logs"].append(
                            "分 P "
                            f"{_format_page_nums(bili_pages_to_transcribe)}"
                            " 无 AI 字幕，将通过语音转写补齐"
                        )
                elif subtitle_outcome is not None:
                    reason = subtitle_outcome.reason
                    if reason == "credentials_missing":
                        task["logs"].append(
                            "未读取到可用的 B 站字幕轨；AI 字幕需要填写当前账号的 "
                            "SESSDATA 等访问凭据"
                        )
                    elif reason == "no_track":
                        task["logs"].append(
                            "当前账号已登录，但该视频没有可用的 AI 字幕轨"
                        )
                    elif reason == "error":
                        task["logs"].append(
                            "读取 B 站 AI 字幕失败（网络或接口异常，"
                            f"{subtitle_outcome.detail}）"
                        )
                    elif reason == "empty":
                        task["logs"].append("B 站字幕轨存在但内容为空")
                    if bili_pages_to_transcribe:
                        task["logs"].append(
                            f"分 P {_format_page_nums(bili_pages_to_transcribe)} 将通过语音转写"
                        )
                    task["logs"].append("未找到可用平台字幕，将进行语音转写")
            title = info["title"]

        raise_if_cancel_requested(task)
        if transcript_result is None or bili_pages_to_transcribe:
            set_progress(task, 3, "准备音频", 25, "正在准备音频")
            if bili_pages_to_transcribe:
                # 多分 P：对没有 AI 字幕的分 P 逐个下载音频并转写，再按顺序合并
                whisper_by_page: dict[int, dict] = {}
                for page in bili_pages_to_transcribe:
                    raise_if_cancel_requested(task)
                    set_progress(
                        task, 3, "准备音频", 25, f"正在下载分 P {page.page} 音频"
                    )
                    media_path = await video_processor.download_audio(
                        bilibili_page_url(source_url or "", page.page),
                        task_id,
                        cookie,
                        media_name=f"audio_p{page.page}",
                        should_abort=should_abort,
                        notes=fallback_notes,
                    )
                    note_api_fallback()
                    raise_if_cancel_requested(task)
                    set_progress(
                        task,
                        4,
                        "语音转写",
                        38,
                        f"正在转写分 P {page.page}（{request.whisper_model} 模型）",
                    )
                    whisper_result = await transcriber.transcribe(
                        media_path,
                        request.whisper_model,
                        request.use_gpu,
                        initial_prompt=title,
                        cancel_event=task.get("_cancel_event"),
                    )
                    raise_if_cancel_requested(task)
                    whisper_by_page[page.page] = whisper_result
                    task["logs"].append(
                        f"分 P {page.page} 语音转写完成：{len(whisper_result['segments'])} 段，"
                        f"设备 {whisper_result['device']}"
                    )
                    if whisper_result["model"] != whisper_result["requested_model"]:
                        task["logs"].append(
                            f"所选 Whisper {whisper_result['requested_model']} 未缓存或无法加载，"
                            f"已自动降级为本机缓存的 {whisper_result['model']}"
                        )
                if subtitle_outcome is not None and subtitle_outcome.pages:
                    merged_segments, language = merge_bilibili_pages(
                        subtitle_outcome.pages,
                        dict(subtitle_outcome.subtitle_by_page),
                        whisper_by_page,
                    )
                else:
                    merged_segments = [
                        seg
                        for whisper in whisper_by_page.values()
                        for seg in (whisper.get("segments") or [])
                    ]
                    language = next(
                        (
                            str(whisper.get("language") or "zh")
                            for whisper in whisper_by_page.values()
                        ),
                        "zh",
                    )
                transcript_result = {
                    "segments": merged_segments,
                    "language": language,
                    "source": "bilibili_multi_page",
                }
                if not info.get("duration"):
                    info["duration"] = sum(
                        float(whisper.get("duration") or 0)
                        for whisper in whisper_by_page.values()
                    )
                task["logs"].append(f"分 P 转录合并完成：共 {len(merged_segments)} 段")
            elif transcript_result is None:
                set_progress(task, 3, "准备音频", 25, "正在准备音频")
                reused_audio = None
                if resume_dir:
                    reused_audio = next(
                        (path for path in resume_dir.glob("audio.*") if path.is_file()), None
                    )
                if reused_audio:
                    media_path = reused_audio
                    task["logs"].append(f"复用已有音频：{reused_audio.name}")
                elif is_local:
                    media_path = Path(uploaded_path)
                else:
                    media_path = await video_processor.download_audio(
                        source_url or "", task_id, cookie, should_abort=should_abort,
                        notes=fallback_notes,
                    )
                raise_if_cancel_requested(task)
                note_api_fallback()
                set_progress(
                    task,
                    4,
                    "语音转写",
                    38,
                    f"正在加载 faster-whisper {request.whisper_model}（未缓存时可能下载模型）",
                )
                whisper_result = await transcriber.transcribe(
                    media_path,
                    request.whisper_model,
                    request.use_gpu,
                    initial_prompt=title,
                    cancel_event=task.get("_cancel_event"),
                )
                raise_if_cancel_requested(task)
                transcript_result = {
                    "segments": whisper_result["segments"],
                    "language": whisper_result["language"],
                    "source": "faster_whisper",
                }
                if not info.get("duration"):
                    info["duration"] = whisper_result["duration"]
                task["logs"].append(
                    f"语音转写完成：{len(whisper_result['segments'])} 段，设备 {whisper_result['device']}"
                )
                if whisper_result["model"] != whisper_result["requested_model"]:
                    task["logs"].append(
                        f"所选 Whisper {whisper_result['requested_model']} 未缓存或无法加载，"
                        f"已自动降级为本机缓存的 {whisper_result['model']}"
                    )

        segments: list[TranscriptSegment] = transcript_result["segments"]
        if not segments:
            raise RuntimeError("没有从视频中获得可总结的文字")
        quality = transcript_quality(segments, float(info.get("duration") or 0))
        transcript_result["quality"] = quality
        task["logs"].append(
            f"文字质量检查：{quality['characters']} 字，语音覆盖 {quality['speech_coverage']:.0%}"
        )
        await write_transcript_files(task_id, segments, transcript_result)
        if transcript_result["source"] == "faster_whisper" and quality["insufficient"]:
            stage = "调用大模型" if request.output == "note" else "返回转录"
            raise RuntimeError(
                f"可识别语音覆盖过低，已在{stage}前停止。源视频可能被静音、替换、"
                "受版权或审核限制，或主要内容并非语音；请先检查原视频。"
            )

        if request.output == "transcript":
            # 正文只留在 transcript.json / transcript.md：result 会被 wait_for_task
            # 每 2 秒轮询一次并由 persist_task_runtime 反复落盘，不能放大文本
            elapsed = finish_task_timing(task)
            task["result"] = {
                "title": title,
                "output": "transcript",
                "source": VideoSource.LOCAL.value if is_local else info.get("source", "other"),
                "duration": info.get("duration", 0),
                "segment_count": len(segments),
                "characters": quality["characters"],
                "transcript_source": transcript_result["source"],
                "transcript_language": transcript_result["language"],
                "transcript_quality": quality,
                "output_directory": str(WORKSPACE_DIR / task_id),
                "processing_seconds": elapsed,
            }
            task.update(
                status="completed", step=7, step_name="转录完成", progress=100, error=None
            )
            task["logs"].append(
                f"转录完成：{len(segments)} 段，可用 GET /api/task/{task_id}/transcript 取正文"
            )
            notify_task("转录完成", f"已生成带时间轴转录：{title}")
            persist_task_runtime(task_id)
            return

        screenshots: list[Path] = []
        if request.include_screenshots:
            set_progress(task, 5, "提取截图", 50, "正在提取低清预览截图")
            video_path = (
                Path(uploaded_path)
                if is_local
                else await video_processor.download_preview_video(
                    source_url or "", task_id, cookie, should_abort=should_abort,
                    notes=fallback_notes,
                )
            )
            note_api_fallback()
            screenshots = await video_processor.extract_frames(
                video_path, task_id, request.screenshot_interval, should_abort
            )
            raise_if_cancel_requested(task)
            task["logs"].append(f"已提取 {len(screenshots)} 张截图")

        transcript_characters = int(quality.get("characters") or 0)
        duration_seconds = float(info.get("duration") or 0)
        if transcript_characters >= 9_000 or duration_seconds >= 1_800:
            task["advisory"] = (
                "这段内容较长，将通过多轮整理生成完整笔记，耗时和 Token 消耗会相应增加。"
                "上下文容量较大、指令理解能力较强的模型通常更稳定；免费或轻量模型可能出现遗漏或截断。"
            )
            task["logs"].append(task["advisory"])
        set_progress(task, 6, "生成笔记", 55, "正在规划笔记生成流程")
        config = request.llm_config
        base_url = config.base_url or config.custom_base_url
        model = config.model or config.custom_model_name
        api_key, key_source = resolve_llm_credentials(
            model_type=config.model_type,
            base_url=base_url,
            api_key=config.api_key.get_secret_value(),
        )
        summarizer = LLMSummarizer(
            model_type=config.model_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        task["logs"].append(
            f"调用模型：Provider={config.model_type}，Model={model}，"
            f"Base URL={base_url}，Key={key_source}"
        )
        task["logs"].append(
            f"推理设置：{summarizer.describe_effort(request.reasoning_effort, request.summary_style)}"
        )

        async def report_llm_progress(progress: int, message: str) -> None:
            raise_if_cancel_requested(task)
            mapped = 55 + round(max(0, min(100, progress)) * 0.44)
            set_progress(task, 6, "生成笔记", mapped, message)

        summary = await summarizer.generate_summary(
            title,
            segments,
            {
                "owner": info.get("owner"),
                "published_at": format_publish_time(info),
                "duration_text": format_duration(info.get("duration", 0)),
                "view_count": info.get("view_count") or 0,
                "like_count": info.get("like_count") or 0,
                "transcript_source": transcript_result["source"],
            },
            style=request.summary_style,
            reasoning_effort=request.reasoning_effort,
            progress_callback=report_llm_progress,
            should_abort=should_abort,
        )
        raise_if_cancel_requested(task)
        for warning in getattr(summarizer, "warnings", []):
            if warning not in task["logs"]:
                task["logs"].append(warning)
        summary = add_note_header_metadata(summary, title, info)
        if screenshots:
            summary += "\n\n## 视频截图\n\n" + "\n\n".join(
                f"![截图 {index}](./images/{path.name})"
                for index, path in enumerate(screenshots, start=1)
            )
        summary = append_note_footer(
            summary,
            source_url,
            config.model_type,
            model,
            request.summary_style,
        )

        task_dir = WORKSPACE_DIR / task_id
        (task_dir / "notes.md").write_text(summary, encoding="utf-8")
        archived_path = archive_note(title, summary)
        if archived_path:
            task["logs"].append(f"笔记已归档：{archived_path}")
        elapsed = finish_task_timing(task)
        task["result"] = {
            "title": title,
            "markdown": summary,
            "source": VideoSource.LOCAL.value if is_local else info.get("source", "other"),
            "duration": info.get("duration", 0),
            "screenshot_count": len(screenshots),
            "transcript_source": transcript_result["source"],
            "transcript_language": transcript_result["language"],
            "segment_count": len(segments),
            "transcript_quality": quality,
            "output_directory": str(task_dir),
            "archived_path": str(archived_path) if archived_path else None,
            "processing_seconds": elapsed,
        }
        task.update(
            status="completed", step=7, step_name="完成", progress=100, error=None
        )
        task["logs"].append("视频笔记生成完成")
        notify_task("任务完成", f"视频笔记已生成：{title}")
        persist_task_runtime(task_id)
    except asyncio.CancelledError:
        finish_task_timing(task)
        task.update(status="cancelled", step_name="已取消", error=None)
        if not task["logs"] or "用户已取消任务" not in task["logs"][-1]:
            task["logs"].append("任务已取消；已生成的中间文件将保留")
        persist_task_runtime(task_id)
    except Exception as exc:
        finish_task_timing(task)
        message = friendly_task_error(str(exc))
        task.update(status="failed", error=message)
        task["logs"].append(f"处理失败：{message}")
        notify_task("任务失败", f"{title}\n{message}")
        persist_task_runtime(task_id)


async def write_transcript_files(
    task_id: str, segments: list[TranscriptSegment], metadata: dict[str, Any]
) -> None:
    """转录文件先写 .tmp 再改名。

    取消能落在任意 await 点（见 cancel_task），而「复用转录」以 transcript.json
    是否存在为准——半截文件会比没有文件更糟。
    """
    task_dir = WORKSPACE_DIR / task_id
    json_payload = {
        "language": metadata["language"],
        "source": metadata["source"],
        "quality": metadata.get("quality"),
        "segments": [segment.to_dict() for segment in segments],
    }
    await _write_atomically(
        task_dir / "transcript.json",
        (json.dumps(json_payload, ensure_ascii=False, indent=2),),
    )
    await _write_atomically(
        task_dir / "transcript.md",
        ("# 带时间戳转录\n\n", segments_to_prompt(segments), "\n"),
    )


async def _write_atomically(path: Path, chunks: tuple[str, ...]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    async with aiofiles.open(temporary, "w", encoding="utf-8") as file:
        for chunk in chunks:
            await file.write(chunk)
    os.replace(temporary, path)


def friendly_task_error(message: str) -> str:
    """把已知的平台限制错误转成对用户友好的提示。"""
    lowered = message.lower()
    if "certificate_verify_failed" in lowered or "certificate verify failed" in lowered:
        return (
            "Whisper 模型下载时 TLS 证书校验失败。程序已尝试备用下载源；"
            "如果仍失败，请检查系统时间、网络代理或企业根证书，"
            "并通过 HF_ENDPOINT 指定能正常验证证书的镜像后重试。"
        )
    if "fresh cookies" in lowered or "challenge" in lowered or "验证码" in message:
        return "抖音要求浏览器验证。点击输入区下方的“打开抖音浏览器”，完成登录/验证后重新提交。"
    if "403" in lowered or "forbidden" in lowered:
        return "抖音媒体地址已过期或被拒绝，请重新提交链接；如果仍失败，先用本机抖音浏览器完成验证。"
    if "douyin" in lowered or "iesdouyin" in lowered or "抖音" in message:
        return DOUYIN_HINT
    return message


def format_duration(seconds: float) -> str:
    if not seconds:
        return "未知"
    return format_timestamp(float(seconds))


def format_bytes_size(num_bytes: float) -> str:
    """把字节数格式化为人类可读大小（MB / GB）。"""
    size = max(0.0, float(num_bytes))
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 ** 3):.2f} GB"
    return f"{size / (1024 ** 2):.2f} MB"


def format_publish_time(info: dict[str, Any]) -> str:
    """优先使用 yt-dlp 的发布日期；没有日期时再使用 Unix 时间戳。"""
    raw_date = str(info.get("upload_date") or info.get("release_date") or "").strip()
    if len(raw_date) == 8 and raw_date.isdigit():
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    raw_timestamp = info.get("timestamp") or info.get("release_timestamp")
    try:
        timestamp = float(raw_timestamp)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def add_note_header_metadata(summary: str, title: str, info: dict[str, Any]) -> str:
    """在模型标题下放置可验证的视频元信息，不让模型负责记住固定排版。"""
    value = summary.strip()
    lines = value.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).lstrip()
    else:
        heading = f"# 视频笔记：《{title}》"
        body = value

    body = strip_model_metadata_block(body)

    metadata: list[str] = []
    owner = str(info.get("owner") or "").strip()
    published_at = format_publish_time(info)
    duration = format_duration(info.get("duration", 0))
    view_count = int(info.get("view_count") or 0)
    like_count = int(info.get("like_count") or 0)
    if owner:
        metadata.append(f"UP主：{owner}")
    if published_at:
        metadata.append(f"发布时间：{published_at}")
    if duration != "未知":
        metadata.append(f"时长：{duration}")
    if view_count > 0:
        metadata.append(f"播放：{view_count:,}")
    if like_count > 0:
        metadata.append(f"点赞：{like_count:,}")
    if not metadata:
        return value or heading
    metadata_block = "> " + " · ".join(metadata)
    return f"{heading}\n\n{metadata_block}" + (f"\n\n{body}" if body else "")


def strip_model_metadata_block(body: str) -> str:
    """移除模型紧跟标题生成的重复元信息，保留正文内容。

    提示词已经把作者、时间、时长和文字来源作为参考资料传给模型，
    但模型仍可能输出一行 ``作者：…｜发布时间：…``。这些字段由代码
    确定性写入标题下方，因此只清理标题后的重复元信息块。
    """
    lines = body.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    start = index
    metadata_labels = ("作者", "UP主", "发布时间", "时长", "播放", "点赞", "文字来源")
    removed = 0
    while index < len(lines):
        line = lines[index].strip().lstrip("> ")
        label_count = sum(f"{label}：" in line or f"{label}:" in line for label in metadata_labels)
        if label_count < 2:
            break
        removed += 1
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    if not removed:
        return body
    return "\n".join(lines[index:]).lstrip()


def summary_style_label(style: str) -> str:
    return {
        "detailed": "详细笔记 + 点评分析",
        "faithful": "详细复原（仅视频内容）",
        "concise": "精简摘要",
    }.get(style, style)


def append_note_footer(
    summary: str,
    source_url: str | None,
    model_type: str,
    model: str | None,
    style: str,
) -> str:
    """确定性追加来源与生成配置，避免不同风格遗漏关键追溯信息。"""
    source = source_url.strip() if source_url else ""
    source_line = source or "本地文件（未提供视频链接）"
    model_line = model.strip() if model else "未指定模型"
    footer = "\n".join(
        [
            "---",
            f"来源：{source_line}",
            f"生成模型：{model_type} · {model_line}",
            f"笔记风格：{summary_style_label(style)}",
        ]
    )
    return f"{summary.rstrip()}\n\n{footer}\n"


def _safe_filename(value: str) -> str:
    """把标题清洗为安全的文件名：只去掉 Windows 非法字符，保留中文标点。"""
    invalid = set('\\/:*?"<>|')
    cleaned = "".join(char for char in value if char not in invalid and ord(char) >= 32)
    return (cleaned.strip() or "video-notes")[:80]


def archive_note(title: str, content: str) -> Path | None:
    """把笔记归档到 workspace/notes/ 下（按标题命名，重名自动加序号），便于集中回顾。

    返回归档路径；写入失败时返回 None（不影响任务本身）。
    """
    notes_root = WORKSPACE_DIR / "notes"
    try:
        notes_root.mkdir(parents=True, exist_ok=True)
        safe = _safe_filename(title)
        candidate = notes_root / f"{safe}.md"
        index = 2
        while candidate.exists():
            candidate = notes_root / f"{safe}-{index}.md"
            index += 1
        candidate.write_text(content, encoding="utf-8")
        return candidate
    except OSError:
        return None


def restore_tasks_from_workspace() -> None:
    """Rebuild recent task status from manifests after a service restart."""
    try:
        candidates = sorted(
            (
                path
                for path in WORKSPACE_DIR.iterdir()
                if path.is_dir() and (path / "task.json").is_file()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:TASK_HISTORY_LIMIT]
    except OSError:
        return

    for task_dir in candidates:
        try:
            manifest = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        task_id = task_dir.name
        runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
        # 纯转录任务按设计不产出 notes.md，完成与否只能看 transcript.json
        transcript_only = manifest.get("output") == "transcript"
        notes_path = task_dir / "notes.md"
        uploaded_media = next(
            (path for path in task_dir.glob("input.*") if path.is_file()), None
        )
        status = str(runtime.get("status") or "")
        interrupted = status in {"pending", "queued", "processing", "cancelling"}
        if notes_path.is_file() or (
            transcript_only and (task_dir / "transcript.json").is_file()
        ):
            status = "completed"
        elif interrupted:
            status = "failed"
        elif status not in {"failed", "cancelled", "uploaded"}:
            status = "uploaded" if uploaded_media else "failed"

        task = new_task(status=status, task_id=task_id)
        task["_started_monotonic"] = None
        task.update(
            step=int(runtime.get("step") or 0),
            step_name=str(runtime.get("step_name") or "已恢复"),
            progress=int(runtime.get("progress") or (100 if status == "completed" else 0)),
            logs=list(runtime.get("logs") or []),
            error=runtime.get("error"),
            created_at=runtime.get("created_at") or manifest.get("created_at"),
            finished_at=runtime.get("finished_at"),
            elapsed_seconds=float(runtime.get("elapsed_seconds") or 0),
            output=str(manifest.get("output") or "note"),
        )
        if interrupted:
            task.update(
                step_name="服务已重启",
                error="任务因服务重启而中断；已有转录和音频仍可复用",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            task["logs"].append("服务重启时任务尚未结束，已标记为中断")
        if status == "uploaded" and uploaded_media:
            task["uploaded_file_path"] = str(uploaded_media)
            task["uploaded_filename"] = manifest.get("uploaded_filename") or uploaded_media.name
        if status == "completed" and transcript_only:
            # 纯转录任务没有 notes.md，正文留在 transcript.json 里
            result = dict(runtime.get("result") or {})
            result.update(
                title=result.get("title") or manifest.get("title") or "",
                output="transcript",
                output_directory=str(task_dir),
                processing_seconds=task["elapsed_seconds"],
            )
            task["result"] = result
            task["finished_at"] = task["finished_at"] or datetime.now(timezone.utc).isoformat()
        elif status == "completed":
            try:
                markdown = notes_path.read_text(encoding="utf-8")
            except OSError:
                continue
            result = dict(runtime.get("result") or {})
            result.update(
                title=result.get("title") or manifest.get("title") or "video-notes",
                markdown=markdown,
                output_directory=str(task_dir),
                processing_seconds=task["elapsed_seconds"],
            )
            task["result"] = result
            task["finished_at"] = task["finished_at"] or datetime.now(timezone.utc).isoformat()
        tasks[task_id] = task
        if interrupted:
            persist_task_runtime(task_id)


restore_tasks_from_workspace()


# MCP 端点：SSE 传输（/mcp/sse，供 Cherry Studio 等 MCP 客户端接入）
try:
    from .mcp_server import use_in_process_backend, mcp as mcp_app
    # 同进程调用端点函数：HTTP 自调会被 SSE 长连接阻塞（自调死锁）
    use_in_process_backend()
    app.mount("/mcp", mcp_app.sse_app())
except ImportError:
    # mcp 依赖未安装时跳过，不影响主服务
    pass


@app.get("/icon.png", include_in_schema=False)
async def app_icon() -> FileResponse:
    if not APP_ICON_PATH.is_file():
        raise HTTPException(status_code=404, detail="应用图标不存在")
    return FileResponse(APP_ICON_PATH, media_type="image/png")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    if not FAVICON_PATH.is_file():
        raise HTTPException(status_code=404, detail="应用图标不存在")
    return FileResponse(FAVICON_PATH, media_type="image/x-icon")


if FRONTEND_DIR.is_dir():
    app.mount(
        "/",
        NoCacheStaticFiles(directory=FRONTEND_DIR, html=True),
        name="frontend",
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    os.environ.setdefault("VIDEOTONOTES_BACKEND_URL", f"http://127.0.0.1:{port}")
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
    )
