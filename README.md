# VideoToNo

[简体中文](README.md) | [English](README_EN.md)

VideoToNo 是一个面向个人使用的本地视频笔记工具。它在本机读取视频链接或媒体文件，优先使用平台提供的字幕；没有可用字幕时，下载最佳音频并通过 `faster-whisper` 转写，最后调用用户选择的大模型生成带时间轴的 Markdown 笔记。

前端页面和 API 由同一个 FastAPI 服务提供，默认地址为 <http://127.0.0.1:8000>。

## 界面预览

### 配置与提交

![VideoToNo 配置与视频提交界面](sources/preview.png)

### 处理进度

![VideoToNo 任务处理进度与运行记录](sources/process.png)

### 笔记输出

![VideoToNo 生成的详细视频笔记](sources/output-public.png)

## 功能特点

- 支持视频链接和本地音视频文件。
- 对链接先尝试读取人工字幕或自动字幕，包括 B 站的 `ai-zh` 中文 AI 字幕轨；失败后使用 `yt-dlp` 的 `bestaudio/best` 音轨和 `faster-whisper` 兜底。
- 从字幕或 Whisper 分段中保留真实起止时间，不要求大模型猜测时间点。
- 在调用大模型前检查转录字数和语音覆盖率；源视频被静音、替换或只剩片尾时会停止任务，避免生成误导性笔记。
- 短转录直接生成笔记；只有长转录才按完整分段切块并整合，避免不必要的多次调用。
- 截图默认关闭；启用后按指定间隔提取低清预览帧并作为笔记附件，不参与视觉理解。
- 前端可直接下载 Markdown 笔记；完整转录、任务清单和可选截图保留在本地任务目录。
- API Key 和 B 站 Cookie 不写入浏览器持久化存储或输出文件，只随当前请求在内存中使用。

## 支持范围

当前主要面向：

- B 站视频链接；
- YouTube 视频链接；
- 其他能够被当前版本 `yt-dlp` 正确解析的 `http`/`https` 视频链接；
- 本地媒体文件：`.mp3`、`.m4a`、`.wav`、`.flac`、`.aac`、`.mp4`、`.mkv`、`.mov`、`.webm`、`.avi`。

平台接口、登录限制和反爬策略会变化。本项目不保证上述每个平台、每个视频或每种清晰度始终可用；是否能够处理，最终取决于视频访问权限、字幕可见性以及 `yt-dlp` 对该链接的解析能力。播放列表会按单个视频处理。

**平台适配说明**：B 站为深度适配——除 yt-dlp 可读取的人工字幕外，还会直连官方接口抓取 AI 字幕（ai-zh/ai-en/ai-ja，需登录凭据，界面支持扫码登录导入）。抖音、爱奇艺、腾讯视频**未做适配**，不保证可用（爱奇艺与腾讯视频暂无适配计划）；这些平台建议使用带公开字幕的链接或本地文件。

## 处理流程

1. 读取链接元数据，或接收本地媒体上传。
2. 对在线视频优先查找中文或英文人工字幕，其次查找自动字幕。
3. 如果字幕不存在、不可访问或无法解析，下载 `b## 环境要求

- Windows + PowerShell（其他系统可手动运行 Uvicorn）、Python 3.11
- 网络：可访问视频来源、Whisper 模型下载地址与所选大模型 API
- 大模型服务的 API Key
- 可选：NVIDIA GPU（未启用或不可用时使用 CPU）

> 便携版 exe 不需要 Python 环境。

## 安装

```powershell
py -3.11 -m venv .venv
\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

## 启动

```powershell
\start.ps1                  # 后台启动（自动检查端口与健康状态）
\stop.ps1                   # 关闭
\restart.ps1                # 重启
\start.ps1 -Foreground      # 前台调试，Ctrl+C 退出
```

`HOST`、`PORT`、`RELOAD` 可写入根目录 `.env`。也可以手动运行：

```powershell
\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000> 即可使用（前端与 API 共用此端口）；健康检查地址为 <http://127.0.0.1:8000/api/health>。

## 便携版（Windows exe）

不需要 Python 环境，从 GitHub Releases 下载 `VideoToNo-0.1.0-portable.exe`（单文件）后双击即可运行：

- 自动在本地启动服务并打开默认浏览器，控制台窗口显示界面地址与工作目录；关闭窗口或按 `Ctrl+C` 退出。
- 端口 8000 被占用时自动顺延；若服务已在运行，再次双击只会打开浏览器，不会重复启动。
- 输出目录（视频、转录、截图、笔记）默认放在 exe 同目录的 `workspace\`；exe 所在目录不可写时回退到 `%LOCALAPPDATA%\VideoToNo\workspace`。可用环境变量 `VIDEOTONOTES_WORKSPACE`、`WHISPER_CACHE_DIR` 覆盖。
- 首次使用 Whisper 转写时会联网下载模型到工作目录，请保持网络通畅。

自行构建（需要 Python 3.11 与已安装的依赖）：

```powershell
.\scripts\build_exe.ps1
```

产物为 `dist\VideoToNo-<版本>-portable.exe`（约 110 MB）。程序图标来自 `sources/icon.ico`（由 `sources/icon_ico.png` 生成）。

## 使用方法

1. 先选择 Provider，再选择预设模型并填写 API Key。DeepSeek 预设会按 `deepseek-v4-flash` 或 `deepseek-v4-pro` 原样提交；运行日志会显示实际 Provider、Model 和 Base URL。也可以在任意 Provider 下手动填写模型 ID，或配置自定义 OpenAI 兼容地址。
2. 选择笔记模式。`精简摘要` 和 `详细复原` 对短转录只调用一次；`详细笔记 + 点评分析` 先生成忠实笔记，再用独立调用补充中性的内容点评与分析。三种模式都使用简短提示词，不强制八股式章节。
3. 选择推理强度。`自动` 会为精简摘要关闭思考、为详细复原使用高档、为点评分析使用最大档；后端会把统一选项映射为各 Provider 实际支持的参数。模型返回空正文时会自动关闭思考重试一次。
4. 选择 Whisper 模型。默认使用下载较小、启动更稳妥的 `base`；`small` 的中文识别通常更准确，但首次下载、内存和处理时间也更多。
5. 粘贴视频链接，或切换到本地文件并上传媒体。
6. `复用转录` 会自动查找同一链接的 `transcript.json`，失败页的“从断点继续”和结果页的“基于转录重新生成”也会显式复用旧任务；选择 `从头处理` 才会重新读取字幕、音频和 Whisper。
7. B 站公开访问受限时，可临时填写 `SESSDATA`、`bili_jct` 和 `buvid3`。
8. 点击开始生成，等待任务完成后预览或下载 Markdown。处理中可以取消任务，已生成的中间文件会保留以供后续复用。

截图选项默认关闭。启用后，在线链接会额外下载低清视频并定时取帧，因此处理时间、网络流量和磁盘占用都会增加。当前截图仅作为附件插入笔记，不会发送给多模态模型进行画面分析。

## 输出内容

每个任务的文件保存在 `workspace/<task-id>/`，结果页会显示该目录的绝对路径。前端下载按钮只下载 `notes.md`；目录中还包含：

复用旧转录时仍会创建新的任务 ID，新 `notes.md` 写入新目录；旧任务的转录和笔记不会被覆盖。

- `notes.md`：最终结构化视频笔记；
- `transcript.md`：便于阅读的带时间戳转录；
- `transcript.json`：包含语言、来源以及每段 `start`、`end`、`text` 的结构化转录；
- `images/*.jpg`：仅在启用截图时存在。

平台字幕的准确性由平台字幕质量决定；Whisper 转写可能误识别人名、数字和专业术语。涉及重要事实时，应回到对应时间段核验原视频。

对于较长媒体，如果可识别语音覆盖率和单位时长文字量同时过低，应用会保留转录文件并在调用大模型前停止。常见原因包括源视频被审核占位、音轨静音、版权替换或内容以音乐为主。

## 隐私与安全

- API Key 和 B 站 Cookie 只随当前页面提交的请求发送到本机后端，不写入 `localStorage`、任务文件或日志。
- 后端使用 API Key 调用所选大模型服务，转录文本会被发送给该服务。请根据对应服务的隐私政策决定是否处理敏感内容。
- 媒体下载、字幕解析、Whisper 转写和文件生成均在本机完成；在线视频本身仍需从来源平台下载。
- 服务默认只监听 `127.0.0.1`，请不要将它改为 `0.0.0.0` 后暴露到公网。当前项目没有身份认证、租户隔离或面向公网部署所需的防护。
- Cookie 相当于登录凭据。仅在确有需要时填写，不要分享给他人；使用结束后关闭或刷新页面。

## 配置

应用会从项目根目录的 `.env` 读取以下可选设置。可以参考 `.env.example`：

```dotenv
HOST=127.0.0.1
PORT=8000
RELOAD=false
MAX_UPLOAD_MB=500
# VIDEOTONOTES_WORKSPACE=D:\path\to\workspace
```

为了保持个人桌面使用的安全边界，建议保留 `HOST=127.0.0.1`。前端只持久化模型选择、Whisper 设置和截图设置等非敏感偏好。

## 测试

安装开发依赖并运行测试：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

还可以在不调用大模型的情况下检查在线视频流程：

```powershell
.\.venv\Scripts\python.exe scripts\smoke_video.py "VIDEO_URL"
.\.venv\Scripts\python.exe scripts\smoke_video.py "VIDEO_URL" --download-audio
.\.venv\Scripts\python.exe scripts\smoke_video.py "VIDEO_URL" --transcribe --model base
```

后两条命令会产生实际网络流量；转写命令在首次使用时还会下载 Whisper 模型。

## 目录结构

```text
VideoToNo/
|-- backend/
|   |-- main.py               # FastAPI 路由、任务和输出打包
|   |-- video_processor.py    # yt-dlp、字幕选择、音频和截图处理
|   |-- whisper_asr.py        # faster-whisper 转写
|   |-- transcript.py         # 字幕解析、时间戳和分块
|   |-- llm_summarizer.py     # 分块摘要与最终笔记
|   |-- requirements.txt
|   `-- requirements-dev.txt
|-- frontend/
|   |-- index.html
|   |-- script.js
|   `-- style.css
|-- scripts/
|   `-- smoke_video.py
|-- tests/
|-- workspace/                # 运行时任务文件，已被 Git 忽略
|-- .env.example
|-- pyproject.toml
|-- start.ps1
|-- README.md
`-- README_EN.md
```

## 常见问题

### 首次转写为什么等待很久？

`faster-whisper` 会在第一次使用某个模型时下载模型文件。下载耗时取决于模型大小和网络状况，后续会复用本机缓存。切换到另一个模型时仍可能再次下载。

模型默认缓存在 `workspace/_model_cache/`。如需改到其他磁盘，可在 `.env` 中设置 `WHISPER_CACHE_DIR`。若首次下载失败，请检查 Hugging Face 的网络连通性、代理设置以及缓存目录写入权限。
应用默认禁用 Hugging Face Xet 下载后端，改用普通 HTTP 下载，以减少 Windows 网络下的 CAS 文件重建错误。
如果所选模型尚未完整缓存且本机已有可用的 `base`，任务会直接降级到 `base` 并在运行日志中注明，避免长时间卡在不稳定的权重下载上。

### CPU 转写为什么比视频时长还久？

速度取决于 CPU、视频时长和模型大小。CPU 模式使用 `int8` 以降低资源压力，但 `medium`、`large-v3` 和 `turbo` 仍可能较慢并占用较多内存。个人电脑上建议先用 `base`；确认 CUDA 环境可用后再启用 GPU。

### 为什么需要 B 站 Cookie？

公开且可直接访问的视频通常不需要 Cookie。部分播放器可见的中文 AI 字幕也只会在登录态字幕接口中返回；未填写凭据时，程序会记录提示并回退到 Whisper。登录可见、访问受限或字幕接口受限的视频可能需要当前账号凭据，但 Cookie 不能绕过账号本身没有的权限。凭据过期后需重新获取，且平台策略变化仍可能导致解析失败。

### 重启服务后为什么找不到任务？

任务状态保存在后端进程内存中，服务重启后任务列表会清空。已经生成的文件仍保留在 `workspace/<task-id>/`；旧状态不能再通过 API 查询，但重新提交同一链接时可以自动复用其中的转录，并在新任务目录生成新笔记。

### 为什么某个链接无法处理？

先确认链接能在当前网络和账号下正常播放，再更新项目依赖中的 `yt-dlp`。付费内容、DRM、验证码、地区限制、临时签名失效或平台接口变化都可能阻止解析。本项目不会绕过平台权限控制。

### 没有字幕时会发生什么？

应用会下载该链接可取得的最佳音频，并在本机运行 `faster-whisper`。这比直接读取字幕耗时更多，也会受到音质、口音、背景噪声和专业术语的影响。

## 项目状态

这是面向本地个人使用的工具，不是多用户服务。项目采用 [MIT License](LICENSE) 开源。
