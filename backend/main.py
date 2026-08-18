from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

import aiofiles
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


class NoCacheStaticFiles(StaticFiles):
    """前端开发期共享：静态资源每次都重新校验，避免浏览器缓存旧版本。"""

    def file_response(self, *args: Any, **kwargs: Any) -> FileResponse:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response
from pydantic import BaseModel, Field, SecretStr

from .config_store import ConfigStore
from .llm_summarizer import LLMSummarizer
from .bili_login import BiliLoginManager
from .transcript import (
    TranscriptSegment,
    format_timestamp,
    segments_to_prompt,
    transcript_quality,
)
from .video_processor import VideoProcessor, VideoSource, normalize_video_input
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
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
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
DOUYIN_HOSTS = ("v.douyin.com", "douyin.com", "iesdouyin.com")
DOUYIN_HINT = (
    "抖音链接暂不支持直接解析（平台签名反爬限制）。"
    "请在抖音 App 或网页保存视频后，改用「本地文件」上传处理。"
)

app = FastAPI(title="VideoToNo API", version="1.1.3")


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


@app.middleware("http")
async def enforce_local_security(request: Request, call_next):
    """VideoToNo is a desktop app; reject remote clients even after a bad HOST override."""
    client_host = request.client.host if request.client else None
    if not is_loopback_client(client_host):
        return JSONResponse(status_code=403, content={"detail": "VideoToNo 仅允许本机访问"})
    response = await call_next(request)
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'sha256-UP0QZg7irvSMvOBz9mH2PIIE28+57UiavRfeVea0l3g='; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response

video_processor = VideoProcessor(WORKSPACE_DIR)
transcriber = WhisperTranscriber(WHISPER_CACHE_DIR)
bili_login_manager = BiliLoginManager(WORKSPACE_DIR)
config_store = ConfigStore(WORKSPACE_DIR)
tasks: dict[str, dict[str, Any]] = {}
running_jobs: dict[str, asyncio.Task[None]] = {}
task_slots = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


class BilibiliCookie(BaseModel):
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""


class LLMConfigPayload(BaseModel):
    model_type: str = "deepseek"
    api_key: SecretStr
    base_url: str | None = None
    model: str | None = None


class LLMTestPayload(BaseModel):
    """测试连接的可选覆盖参数；为 None 的字段回退到已保存配置。"""

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
    summary_style: Literal["detailed", "faithful", "concise"] = "detailed"
    reasoning_effort: Literal["auto", "off", "high", "max"] = "auto"
    prefer_subtitles: bool = True
    include_screenshots: bool = False
    screenshot_interval: int = Field(default=30, ge=5, le=300)
    whisper_model: str = "base"
    use_gpu: bool = False
    bilibili_cookie: BilibiliCookie | None = None
    llm_config: LLMConfig


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
                detail="无法识别视频链接：支持 http(s) 链接，或包含 B 站链接 / BV 号的文本",
            )
        request.video_url = normalized
    if request.video_url and urlsplit(request.video_url).netloc.endswith(DOUYIN_HOSTS):
        raise HTTPException(status_code=422, detail=DOUYIN_HINT)

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
    ordered = sorted(
        tasks.items(),
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
    return {"models": models}


@app.get("/api/llm-config")
async def get_llm_config() -> dict[str, Any]:
    """Return saved configuration metadata without exposing the API key."""
    config = config_store.load_llm_config()
    if not config:
        return {"saved": False}
    api_key = str(config.get("api_key") or "")
    return {
        "saved": True,
        "model_type": config.get("model_type"),
        "model": config.get("model"),
        "base_url": config.get("base_url"),
        "api_key_masked": f"{api_key[:4]}****" if api_key else None,
    }


@app.post("/api/llm-config")
async def save_llm_config(payload: LLMConfigPayload) -> dict[str, Any]:
    config: dict[str, Any] = {
        "model_type": payload.model_type,
        "api_key": payload.api_key.get_secret_value(),
    }
    if payload.base_url:
        config["base_url"] = payload.base_url
    if payload.model:
        config["model"] = payload.model
    config_store.save_llm_config(config)
    return {"saved": True, "model_type": payload.model_type, "model": config.get("model")}


@app.post("/api/llm-test")
async def test_llm_connection(
    payload: LLMTestPayload | None = None,
) -> dict[str, Any]:
    """用（可选覆盖的）LLM 配置做一次轻量连通测试。"""
    saved = config_store.load_llm_config() or {}
    model_type = (
        payload.model_type if payload and payload.model_type else saved.get("model_type", "deepseek")
    )
    api_key = (
        payload.api_key.get_secret_value()
        if payload and payload.api_key
        else str(saved.get("api_key") or "")
    )
    base_url = (
        payload.base_url if payload and payload.base_url else saved.get("base_url")
    )
    model = payload.model if payload and payload.model else saved.get("model")
    if not api_key:
        return {"ok": False, "error": "未配置 API Key，请先填写"}
    try:
        summarizer = LLMSummarizer(
            model_type=model_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        ok, message, latency = await summarizer.test_connection()
        return {
            "ok": ok,
            "message": message,
            "latency_ms": int(round(latency * 1000)),
            "model": model or summarizer.model,
            "base_url": summarizer.base_url,
        }
    except Exception as exc:
        return {"ok": False, "error": f"初始化失败：{exc}"}


@app.delete("/api/llm-config")
async def clear_llm_config() -> dict[str, bool]:
    config_store.clear_llm_config()
    return {"saved": False}


@app.get("/api/bili-credentials")
async def get_bili_credentials() -> dict[str, Any]:
    """Return saved Bilibili credential status without exposing cookie values."""
    credentials = config_store.load_bili_credentials()
    if not credentials:
        return {"saved": False}
    sessdata = str(credentials.get("sessdata") or "")
    return {
        "saved": True,
        "sessdata_masked": f"{sessdata[:4]}****" if sessdata else None,
        "has_bili_jct": bool(credentials.get("bili_jct")),
        "has_buvid3": bool(credentials.get("buvid3")),
    }


@app.post("/api/bili-credentials")
async def save_bili_credentials(payload: BiliCredentialsPayload) -> dict[str, Any]:
    config_store.save_bili_credentials(
        {"sessdata": payload.sessdata, "bili_jct": payload.bili_jct, "buvid3": payload.buvid3}
    )
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


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)) -> dict[str, str]:
    filename = Path(file.filename or "upload.bin").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_MEDIA_SUFFIXES:
        raise HTTPException(status_code=415, detail="不支持的媒体文件类型")

    task_id = str(uuid.uuid4())
    task_dir = WORKSPACE_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=False)
    file_path = task_dir / f"input{suffix}"
    written = 0
    try:
        async with aiofiles.open(file_path, "wb") as destination:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="上传文件超过大小限制")
                await destination.write(chunk)
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise
    finally:
        await file.close()

    tasks[task_id] = new_task(status="uploaded", task_id=task_id)
    tasks[task_id].update(
        uploaded_file_path=str(file_path),
        uploaded_filename=filename,
        logs=[f"文件已上传：{filename}"],
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

    task["_cancel_requested"] = True
    task.update(status="cancelling", step_name="取消中", error=None)
    task["logs"].append("已收到取消请求；当前阻塞步骤结束后将停止任务")
    persist_task_runtime(task_id)
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
    cookie = (
        request.bilibili_cookie.model_dump()
        if request.bilibili_cookie
        else config_store.load_bili_credentials()
    )

    try:
        raise_if_cancel_requested(task)
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
        transcript_result = load_transcript_result(resume_dir) if resume_dir else None

        if transcript_result:
            set_progress(task, 1, "恢复任务信息", 8, "正在读取已有任务信息")
            info = load_reused_video_info(resume_dir, transcript_result, source_url)
        else:
            media_input = uploaded_path or source_url or ""
            set_progress(task, 1, "读取视频信息", 8, "正在读取视频信息")
            info = await video_processor.get_video_info(
                media_input, cookie, allow_local=is_local
            )
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
                subtitle = (
                    await video_processor.fetch_bilibili_subtitles(source_url or "", cookie)
                    if is_bilibili
                    else None
                )
                if subtitle:
                    transcript_result = {
                        "segments": subtitle.segments,
                        "language": subtitle.language,
                        "source": subtitle.source,
                    }
                    task["logs"].append(
                        f"已使用 B 站 AI 字幕：{subtitle.language}，共 {len(subtitle.segments)} 段"
                    )
                else:
                    if is_bilibili:
                        task["logs"].append(
                            "未读取到可用的 B 站字幕轨；AI 字幕需要填写当前账号的 "
                            "SESSDATA 等访问凭据"
                        )
                    task["logs"].append("未找到可用平台字幕，将进行语音转写")

        raise_if_cancel_requested(task)
        if transcript_result is None:
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
                    source_url or "", task_id, cookie
                )
            raise_if_cancel_requested(task)
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
            raise RuntimeError(
                "可识别语音覆盖过低，已在调用大模型前停止。源视频可能被静音、替换、"
                "受版权或审核限制，或主要内容并非语音；请先检查原视频。"
            )

        screenshots: list[Path] = []
        if request.include_screenshots:
            set_progress(task, 5, "提取截图", 50, "正在提取低清预览截图")
            video_path = (
                Path(uploaded_path)
                if is_local
                else await video_processor.download_preview_video(
                    source_url or "", task_id, cookie
                )
            )
            screenshots = await video_processor.extract_frames(
                video_path, task_id, request.screenshot_interval
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
        api_key = config.api_key.get_secret_value().strip()
        if not api_key:
            saved_config = config_store.load_llm_config() or {}
            api_key = str(saved_config.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("未提供 API Key，且本机没有已保存的 LLM 配置")
        task["logs"].append(
            f"调用模型：Provider={config.model_type}，Model={model}，Base URL={base_url}"
        )
        task["logs"].append(f"推理设置：{request.reasoning_effort}（auto 使用模型默认）")
        summarizer = LLMSummarizer(
            model_type=config.model_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
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
                "duration_text": format_duration(info.get("duration", 0)),
                "transcript_source": transcript_result["source"],
            },
            style=request.summary_style,
            reasoning_effort=request.reasoning_effort,
            progress_callback=report_llm_progress,
        )
        raise_if_cancel_requested(task)
        for warning in getattr(summarizer, "warnings", []):
            if warning not in task["logs"]:
                task["logs"].append(warning)
        if screenshots:
            summary += "\n\n## 视频截图\n\n" + "\n\n".join(
                f"![截图 {index}](./images/{path.name})"
                for index, path in enumerate(screenshots, start=1)
            )
        if source_url:
            summary += f"\n\n---\n来源：{source_url}\n"

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
        persist_task_runtime(task_id)


async def write_transcript_files(
    task_id: str, segments: list[TranscriptSegment], metadata: dict[str, Any]
) -> None:
    task_dir = WORKSPACE_DIR / task_id
    json_payload = {
        "language": metadata["language"],
        "source": metadata["source"],
        "quality": metadata.get("quality"),
        "segments": [segment.to_dict() for segment in segments],
    }
    async with aiofiles.open(task_dir / "transcript.json", "w", encoding="utf-8") as file:
        await file.write(json.dumps(json_payload, ensure_ascii=False, indent=2))
    async with aiofiles.open(task_dir / "transcript.md", "w", encoding="utf-8") as file:
        await file.write("# 带时间戳转录\n\n")
        await file.write(segments_to_prompt(segments))
        await file.write("\n")


def friendly_task_error(message: str) -> str:
    """把已知的平台限制错误转成对用户友好的提示。"""
    if "Douyin" in message or "douyin" in message or "Fresh cookies" in message:
        return DOUYIN_HINT
    return message


def format_duration(seconds: float) -> str:
    if not seconds:
        return "未知"
    return format_timestamp(float(seconds))


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
        notes_path = task_dir / "notes.md"
        uploaded_media = next(
            (path for path in task_dir.glob("input.*") if path.is_file()), None
        )
        status = str(runtime.get("status") or "")
        interrupted = status in {"pending", "queued", "processing", "cancelling"}
        if notes_path.is_file():
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
        if status == "completed":
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
