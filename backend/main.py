import os
import uuid
import asyncio
from pathlib import Path
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File as FastAPIFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv
import aiofiles
import shutil

# 导入自定义模块
from video_processor import VideoProcessor
from whisper_asr import WhisperTranscriber
from llm_summarizer import LLMSummarizer

# 加载环境变量
load_dotenv()

app = FastAPI(title="多平台视频总结API", version="2.0.0")

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录（使截图可以通过 HTTP 访问）
workspace_path = Path("./workspace")
workspace_path.mkdir(exist_ok=True)
app.mount("/workspace", StaticFiles(directory=str(workspace_path)), name="workspace")

# 初始化处理器
video_processor = VideoProcessor()
transcriber = WhisperTranscriber()

# 任务存储
tasks: Dict[str, Dict] = {}


# ========== 请求模型 ==========
class BilibiliCookie(BaseModel):
    sessdata: str
    bili_jct: str
    buvid3: str


class LLMConfig(BaseModel):
    model_type: str  # deepseek, openai, qwen, glm, moonshot, custom
    api_key: str
    base_url: Optional[str] = None
    model: Optional[str] = None
    custom_base_url: Optional[str] = None
    custom_model_name: Optional[str] = None


class SummarizeRequest(BaseModel):
    video_url: str
    screenshot_interval: int = 10
    whisper_model: str = "base"
    use_gpu: bool = False
    bilibili_cookie: Optional[BilibiliCookie] = None
    llm_config: LLMConfig
    upload_task_id: Optional[str] = None  # 本地文件上传时的task_id


# ========== API 接口 ==========
@app.post("/api/summarize")
async def start_summarize(request: SummarizeRequest, background_tasks: BackgroundTasks):
    """启动视频总结任务（支持B站、YouTube、抖音、本地视频等）"""
    task_id = str(uuid.uuid4())

    # 如果是本地上传的文件，复用upload的task_id和相关数据
    if request.upload_task_id and request.upload_task_id in tasks:
        upload_task = tasks[request.upload_task_id]
        if upload_task.get("status") == "uploaded":
            # 复用已上传文件的task_id
            task_id = request.upload_task_id
            tasks[task_id]["status"] = "pending"
            tasks[task_id]["step"] = 0
            tasks[task_id]["step_name"] = "初始化"
            tasks[task_id]["progress"] = 0
            tasks[task_id]["logs"].append("🚀 开始处理上传的视频...")
        else:
            # 如果不是uploaded状态，创建新任务
            tasks[task_id] = {
                "status": "pending",
                "step": 0,
                "step_name": "初始化",
                "progress": 0,
                "logs": [],
                "result": None,
                "error": None
            }
    else:
        tasks[task_id] = {
            "status": "pending",
            "step": 0,
            "step_name": "初始化",
            "progress": 0,
            "logs": [],
            "result": None,
            "error": None
        }

    background_tasks.add_task(process_video_task, task_id, request)

    return {"task_id": task_id}


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}。可能原因：1)服务器已重启 2)任务ID错误 3)任务已被清理")
    return tasks[task_id]


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok"}


@app.post("/api/upload")
async def upload_video(file: UploadFile = FastAPIFile(...)):
    """上传本地视频文件"""
    task_id = str(uuid.uuid4())
    task_dir = workspace_path / task_id
    task_dir.mkdir(exist_ok=True)

    # 保存上传的文件
    file_path = task_dir / file.filename
    async with aiofiles.open(file_path, 'wb') as f:
        content = await file.read()
        await f.write(content)

    # 创建任务记录，标记为已上传状态
    tasks[task_id] = {
        "status": "uploaded",
        "step": 0,
        "step_name": "等待处理",
        "progress": 0,
        "logs": [f"✅ 文件已上传: {file.filename}"],
        "result": None,
        "error": None,
        "uploaded_file_path": str(file_path.resolve()),
        "uploaded_filename": file.filename
    }

    # 返回绝对路径和task_id，用于后续总结
    return {"task_id": task_id, "file_path": str(file_path.resolve()), "filename": file.filename}


@app.get("/api/image/{task_id}/{filename}")
async def get_image(task_id: str, filename: str):
    """获取截图图片"""
    image_path = workspace_path / task_id / "frames" / filename
    if image_path.exists():
        return FileResponse(image_path)
    raise HTTPException(status_code=404, detail="图片不存在")


@app.get("/api/download/{task_id}")
async def download_summary_zip(task_id: str):
    """下载总结报告和截图的ZIP包"""
    import zipfile
    import io

    print(f"[ZIP下载] 收到下载请求，task_id: {task_id}")

    if task_id not in tasks:
        print(f"[ZIP下载] 错误：任务不存在")
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    print(f"[ZIP下载] 任务状态: {task['status']}")
    
    if task["status"] != "completed":
        print(f"[ZIP下载] 错误：任务尚未完成")
        raise HTTPException(status_code=400, detail="任务尚未完成")

    # 创建内存中的ZIP文件
    zip_buffer = io.BytesIO()

    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 添加Markdown文件
            result = task.get("result", {})
            markdown_content = result.get("markdown", "")
            title = result.get("title", "video_summary")

            print(f"[ZIP下载] 视频标题: {title}")
            print(f"[ZIP下载] Markdown内容长度: {len(markdown_content)}")

            # 清理文件名中的非法字符
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_title = safe_title or "video_summary"

            # 写入Markdown文件
            zip_file.writestr(f"{safe_title}.md", markdown_content.encode('utf-8'))
            print(f"[ZIP下载] 已添加Markdown文件: {safe_title}.md")

            # 添加截图到images文件夹
            frames_dir = workspace_path / task_id / "frames"
            print(f"[ZIP下载] 截图目录: {frames_dir}")
            
            if frames_dir.exists():
                image_count = 0
                for image_file in frames_dir.iterdir():
                    if image_file.is_file():
                        zip_file.write(image_file, f"images/{image_file.name}")
                        image_count += 1
                print(f"[ZIP下载] 已添加 {image_count} 张截图到images文件夹")
            else:
                print(f"[ZIP下载] 警告：截图目录不存在")

        zip_buffer.seek(0)
        print(f"[ZIP下载] ZIP文件创建成功，准备返回")

        # 返回ZIP文件
        from fastapi.responses import StreamingResponse
        from urllib.parse import quote
        
        # 对文件名进行URL编码以支持中文
        zip_filename = f"{safe_title}_summary.zip"
        encoded_filename = quote(zip_filename.encode('utf-8'))
        
        # 下载完成后清理workspace中的任务存档
        task_dir = workspace_path / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir)
            print(f"[ZIP下载] 已清理workspace存档: {task_dir}")

        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            }
        )
    except Exception as e:
        print(f"[ZIP下载] 错误：{str(e)}")
        raise HTTPException(status_code=500, detail=f"创建ZIP文件失败: {str(e)}")


# ========== 核心处理函数 ==========
async def process_video_task(task_id: str, request: SummarizeRequest):
    """后台处理视频任务（支持多平台）"""
    task = tasks[task_id]
    task["status"] = "processing"

    try:
        # ===== 步骤1: 获取视频信息 =====
        task["step"] = 1
        task["step_name"] = "获取视频信息"
        task["progress"] = 10
        task["logs"].append("📹 正在获取视频信息...")
        
        # 检查是否是已上传的本地文件
        video_url = request.video_url
        if request.upload_task_id and request.upload_task_id in tasks:
            upload_task = tasks[request.upload_task_id]
            if upload_task.get("uploaded_file_path"):
                video_url = upload_task["uploaded_file_path"]
                task["logs"].append(f"📁 使用已上传的本地文件: {upload_task.get('uploaded_filename', '')}")
        
        print(f"[任务{task_id}] video_url: {video_url}")

        # 检测视频来源
        source = await video_processor.detect_source(video_url)
        source_names = {
            "bilibili": "B站",
            "youtube": "YouTube",
            "douyin": "抖音",
            "local": "本地文件",
            "other": "其他网站"
        }
        task["logs"].append(f"📌 检测到视频来源: {source_names.get(source.value, source.value)}")

        # 获取 Cookie（仅 B站需要）
        cookie_dict = None
        if request.bilibili_cookie and source.value == "bilibili":
            cookie_dict = request.bilibili_cookie.dict()

        video_info = await video_processor.get_video_info(video_url, cookie_dict)
        title = video_info.get("title", "未知标题")
        task["logs"].append(f"✅ 视频标题: {title}")
        task["logs"].append(f"⏱️ 视频时长: {format_duration(video_info.get('duration', 0))}")

        # ===== 步骤2: 下载视频 =====
        task["step"] = 2
        task["step_name"] = "下载视频"
        task["progress"] = 30
        task["logs"].append("⬇️ 正在下载视频...")

        video_path, audio_path = await video_processor.download_video(
            video_url, task_id, cookie_dict
        )
        task["logs"].append(f"✅ 视频下载完成: {video_path.name}")

        # ===== 步骤3: 提取截图 =====
        task["step"] = 3
        task["step_name"] = "提取关键帧"
        task["progress"] = 50
        task["logs"].append("📸 正在提取截图...")

        screenshots = await video_processor.extract_frames(
            video_path, task_id, interval=request.screenshot_interval
        )
        task["logs"].append(f"✅ 提取了 {len(screenshots)} 张截图")

        # ===== 步骤4: 语音转文字 =====
        task["step"] = 4
        task["step_name"] = "语音识别"
        task["progress"] = 70
        task["logs"].append("🎙️ 正在进行语音识别...")

        transcription = await transcriber.transcribe(
            audio_path,
            model_name=request.whisper_model,
            use_gpu=request.use_gpu
        )
        task["logs"].append(f"✅ 语音识别完成，共 {len(transcription['segments'])} 个段落")

        # ===== 步骤5: AI总结 =====
        task["step"] = 5
        task["step_name"] = "AI生成总结"
        task["progress"] = 85
        task["logs"].append(f"🤖 AI正在生成总结... 使用模型: {request.llm_config.model_type}")

        # 构建 LLM 参数
        llm_kwargs = {"api_key": request.llm_config.api_key}

        if request.llm_config.model_type == "custom":
            llm_kwargs["base_url"] = request.llm_config.custom_base_url
            llm_kwargs["model"] = request.llm_config.custom_model_name
        else:
            llm_kwargs["base_url"] = request.llm_config.base_url
            llm_kwargs["model"] = request.llm_config.model

        # 创建总结器并生成总结
        summarizer = LLMSummarizer(
            model_type=request.llm_config.model_type,
            **llm_kwargs
        )

        summary = await summarizer.generate_summary(title, transcription, screenshots, task_id)

        # ===== 完成 =====
        task["result"] = {
            "title": title,
            "markdown": summary,
            "source": source.value,
            "duration": video_info.get("duration", 0),
            "screenshot_count": len(screenshots)
        }

        task["status"] = "completed"
        task["step"] = 6
        task["step_name"] = "完成"
        task["progress"] = 100
        task["logs"].append("🎉 视频总结完成！")

        # 清理临时文件（可选，取消注释则自动删除）
        # await video_processor.cleanup(task_id)

    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        task["logs"].append(f"❌ 处理失败: {str(e)}")


def format_duration(seconds: int) -> str:
    """格式化时长"""
    if seconds <= 0:
        return "未知"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}小时{minutes}分钟"
    elif minutes > 0:
        return f"{minutes}分钟{secs}秒"
    else:
        return f"{secs}秒"


# ========== 启动入口 ==========
# 在 main.py 末尾应该有
if __name__ == "__main__":
    uvicorn.run(
        "main:app",  # 注意这里是 "main:app" 不是 "main:main"
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        reload=True
    )