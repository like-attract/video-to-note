from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

import aiofiles
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from .llm_summarizer import LLMSummarizer
from .transcript import (
    TranscriptSegment,
    format_timestamp,
    segments_to_prompt,
    transcript_quality,
)
from .video_processor import VideoProcessor, VideoSource
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
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
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

app = FastAPI(title="VideoToNotes API", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

video_processor = VideoProcessor(WORKSPACE_DIR)
transcriber = WhisperTranscriber(WHISPER_CACHE_DIR)
tasks: dict[str, dict[str, Any]] = {}
running_jobs: dict[str, asyncio.Task[None]] = {}


class BilibiliCookie(BaseModel):
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""


class LLMConfig(BaseModel):
    model_type: str = "deepseek"
    api_key: SecretStr
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
    reasoning_effort: Literal["auto", "off", "low", "medium", "high", "max"] = "auto"
    prefer_subtitles: bool = True
    include_screenshots: bool = False
    screenshot_interval: int = Field(default=30, ge=5, le=300)
    whisper_model: str = "base"
    use_gpu: bool = False
    bilibili_cookie: BilibiliCookie | None = None
    llm_config: LLMConfig


def new_task(status: str = "pending") -> dict[str, Any]:
    return {
        "status": status,
        "step": 0,
        "step_name": "初始化",
        "progress": 0,
        "logs": [],
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "elapsed_seconds": 0.0,
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
    task.update(step=step, step_name=name, progress=progress)
    task["logs"].append(message)


def task_directory(task_id: str) -> Path:
    candidate = (WORKSPACE_DIR / task_id).resolve()
    if candidate.parent != WORKSPACE_DIR:
        raise ValueError("非法任务 ID")
    return candidate


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


def write_task_manifest(task_id: str, request: SummarizeRequest, reused_task_id: str | None) -> None:
    payload = {
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_url": request.video_url or None,
        "normalized_source_url": normalize_source_url(request.video_url) if request.video_url else None,
        "processing_mode": request.processing_mode,
        "summary_style": request.summary_style,
        "reasoning_effort": request.reasoning_effort,
        "reused_task_id": reused_task_id,
    }
    (task_directory(task_id) / "task.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@app.post("/api/summarize")
async def start_summarize(request: SummarizeRequest) -> dict[str, str | None]:
    if request.whisper_model not in WHISPER_MODELS:
        raise HTTPException(status_code=422, detail="不支持的 Whisper 模型")

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
        tasks[task_id] = new_task()
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
        tasks[task_id] = new_task()
        task_directory(task_id).mkdir(parents=True, exist_ok=False)

    tasks[task_id]["resume_task_id"] = reused_task_id
    if reused_task_id:
        old_task = tasks.get(reused_task_id, {})
        if old_task.get("uploaded_file_path"):
            tasks[task_id]["uploaded_file_path"] = old_task["uploaded_file_path"]
            tasks[task_id]["uploaded_filename"] = old_task.get("uploaded_filename")
        tasks[task_id]["logs"].append(f"将从任务 {reused_task_id} 复用已有产物")
    write_task_manifest(task_id, request, reused_task_id)

    job = asyncio.create_task(process_video_task(task_id, request))
    running_jobs[task_id] = job
    job.add_done_callback(lambda _job, current_id=task_id: running_jobs.pop(current_id, None))
    return {"task_id": task_id, "reused_task_id": reused_task_id}


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str) -> dict[str, Any]:
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在，服务重启后内存任务会清空")
    payload = {key: value for key, value in task.items() if not key.startswith("_")}
    payload["elapsed_seconds"] = task_elapsed_seconds(task)
    return payload


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": app.version,
        "dependencies": {
            "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
            "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
            "openai": importlib.util.find_spec("openai") is not None,
        },
    }


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

    tasks[task_id] = new_task(status="uploaded")
    tasks[task_id].update(
        uploaded_file_path=str(file_path),
        uploaded_filename=filename,
        logs=[f"文件已上传：{filename}"],
    )
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
    safe_name = "".join(char for char in title if char.isalnum() or char in " -_").strip()
    encoded_name = quote(f"{safe_name or 'video-notes'}.md")
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

    elapsed = finish_task_timing(task)
    task.update(status="cancelled", step_name="已取消", error=None)
    task["logs"].append("用户已取消任务；已生成的中间文件将保留")
    job = running_jobs.get(task_id)
    if job and not job.done():
        job.cancel()
    return {"cancelled": True, "status": task["status"], "elapsed_seconds": elapsed}


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str) -> dict[str, bool]:
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    job = running_jobs.pop(task_id, None)
    if job and not job.done():
        job.cancel()
    await video_processor.cleanup(task_id)
    tasks.pop(task_id, None)
    return {"deleted": True}


async def process_video_task(task_id: str, request: SummarizeRequest) -> None:
    task = tasks[task_id]
    task["status"] = "processing"
    cookie = request.bilibili_cookie.model_dump() if request.bilibili_cookie else None

    try:
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
            set_progress(task, 1, "恢复任务信息", 10, "正在读取已有任务信息")
            info = load_reused_video_info(resume_dir, transcript_result, source_url)
        else:
            media_input = uploaded_path or source_url or ""
            set_progress(task, 1, "读取视频信息", 10, "正在读取视频信息")
            info = await video_processor.get_video_info(
                media_input, cookie, allow_local=is_local
            )
        title = info["title"]
        update_task_manifest(task_id, info)
        task["logs"].append(f"标题：{title}")
        if info.get("duration"):
            task["logs"].append(f"时长：{format_duration(info['duration'])}")

        if transcript_result:
            set_progress(task, 4, "复用转录", 68, "已读取已有转录，跳过字幕、音频和 Whisper")
            task["logs"].append(
                f"复用任务 {resume_task_id} 的 {len(transcript_result['segments'])} 个转录分段"
            )
        elif request.prefer_subtitles and not is_local:
            set_progress(task, 2, "查找平台字幕", 25, "正在查找平台字幕")
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
                if video_processor.detect_source(source_url or "") == VideoSource.BILIBILI:
                    task["logs"].append(
                        "未读取到可用的 B 站字幕轨；部分中文 AI 字幕需要填写当前账号的 "
                        "SESSDATA 等访问凭据"
                    )
                task["logs"].append("未找到可用平台字幕，将进行语音转写")

        if transcript_result is None:
            set_progress(task, 3, "准备音频", 40, "正在准备音频")
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
            set_progress(
                task,
                4,
                "语音转写",
                60,
                f"正在加载 faster-whisper {request.whisper_model}（未缓存时可能下载模型）",
            )
            whisper_result = await transcriber.transcribe(
                media_path,
                request.whisper_model,
                request.use_gpu,
                initial_prompt=title,
            )
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
            set_progress(task, 5, "提取截图", 72, "正在提取低清预览截图")
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
            task["logs"].append(f"已提取 {len(screenshots)} 张截图")

        set_progress(task, 6, "生成笔记", 82, "正在分块生成结构化笔记")
        config = request.llm_config
        base_url = config.base_url or config.custom_base_url
        model = config.model or config.custom_model_name
        task["logs"].append(
            f"调用模型：Provider={config.model_type}，Model={model}，Base URL={base_url}"
        )
        task["logs"].append(f"推理强度：{request.reasoning_effort}（auto 会按笔记风格分配）")
        summarizer = LLMSummarizer(
            model_type=config.model_type,
            api_key=config.api_key.get_secret_value(),
            base_url=base_url,
            model=model,
        )
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
        )
        task["logs"].extend(getattr(summarizer, "warnings", []))
        if screenshots:
            summary += "\n\n## 视频截图\n\n" + "\n\n".join(
                f"![截图 {index}](./images/{path.name})"
                for index, path in enumerate(screenshots, start=1)
            )
        if source_url:
            summary += f"\n\n---\n来源：{source_url}\n"

        task_dir = WORKSPACE_DIR / task_id
        (task_dir / "notes.md").write_text(summary, encoding="utf-8")
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
            "processing_seconds": elapsed,
        }
        task.update(
            status="completed", step=7, step_name="完成", progress=100, error=None
        )
        task["logs"].append("视频笔记生成完成")
    except asyncio.CancelledError:
        finish_task_timing(task)
        task.update(status="cancelled", step_name="已取消", error=None)
        if not task["logs"] or "用户已取消任务" not in task["logs"][-1]:
            task["logs"].append("任务已取消；已生成的中间文件将保留")
    except Exception as exc:
        finish_task_timing(task)
        task.update(status="failed", error=str(exc))
        task["logs"].append(f"处理失败：{exc}")


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


def format_duration(seconds: float) -> str:
    if not seconds:
        return "未知"
    return format_timestamp(float(seconds))


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "false").lower() == "true",
    )
