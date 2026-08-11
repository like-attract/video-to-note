# VideoToNo

[简体中文](README.md) | [English](README_EN.md)

VideoToNo 是一个面向个人使用的本地视频笔记工具。它在本机读取视频链接或媒体文件，优先使用平台提供的字幕；**对 B 站做了深度适配**——除常规字幕外，可直接抓取 B 站 AI 字幕（登录后扫码即可导入凭据）；没有可用字幕时，下载最佳音频并通过 `faster-whisper` 转写，最后调用用户选择的大模型生成带时间轴的 Markdown 笔记。

前端页面和 API 由同一个 FastAPI 服务提供，默认地址为 <http://127.0.0.1:8000>。

## VideoToNo 1.1.1

- 产品页左上角品牌标记改用统一的 `sources/icon.png`，与桌面和托盘图标保持一致。
- 处理进度的 6 个环节改为等距排列，窄屏继续采用两列布局。
- 长视频分段、成稿和点评阶段的空正文自动重试提示会即时显示具体阶段，避免生成完成后才出现误导性日志。
- 更新配置、处理进度和笔记输出的产品截图。

## VideoToNo 1.1.0

- 页面显示当前版本；最近任务可直接删除失败记录及其本地产物。
- 笔记支持一键复制，并可导出白底 PNG 长图或 3:4 分页图片。
- 长转录采用更细粒度的分块与分层归并，降低最终整合时的上下文压力。
- 生成笔记阶段提供逐段整理、归并、成稿与点评进度；长内容会给出模型能力与 Token 消耗提示。
- 建立完整的“平台字幕 / B 站 AI 字幕 → Whisper 兜底 → LLM 笔记”处理链，并保留真实时间轴。
- 支持结构化转录复用、断点继续、任务取消、处理计时以及 Markdown 笔记自动归档。
- 提供多 Provider / 自定义模型、三种笔记风格、推理强度和可选截图设置。
- 提供本地 Web 界面与 MCP 接入；`wait_for_task` 可等待任务终态，减少 AI 客户端频繁轮询。
- 明确个人桌面工具的安全边界：服务仅应监听回环地址，保存到本机的 API Key 与 B 站凭据需要按敏感明文文件保护。

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
- 短转录直接生成笔记；长转录按完整分段切块，并在材料仍然过长时分层归并，避免最终请求超出上下文预算。
- 截图默认关闭；启用后按指定间隔提取低清预览帧并作为笔记附件，不参与视觉理解。
- 前端可复制完整笔记，并导出 Markdown、HTML、JSON、纯文本或 PNG 图片；完整转录、任务清单和可选截图保留在本地任务目录。
- Web 界面不会把 API Key 写入浏览器存储，扫码登录取得的 Cookie 也只保留在内存中；只有显式调用 MCP 的保存工具时，凭据才会明文写入本机 `workspace/`。

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
3. 如果字幕不存在、不可访问或无法解析，下载 `bestaudio/best` 音轨并使用 `faster-whisper` 转写；本地文件直接进入转写流程。
4. 保存字幕或转写分段及其真实起止时间。
5. 长转录按分段边界切块，分别处理后整合为最终笔记。
6. 根据设置提取预览帧，并输出 Markdown 笔记、转录文件和可选截图。

## 环境要求

- Windows + PowerShell（其他系统可手动运行 Uvicorn）、Python 3.11
- 网络：可访问视频来源、Whisper 模型下载地址与所选大模型 API
- 大模型服务的 API Key
- 可选：NVIDIA GPU（未启用或不可用时使用 CPU）

> 便携版 exe 不需要 Python 环境。

## 安装

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

## 启动

```powershell
.\start.ps1                  # 后台启动（自动检查端口与健康状态）
.\stop.ps1                   # 关闭
.\restart.ps1                # 重启
.\start.ps1 -Foreground      # 前台调试，Ctrl+C 退出
```

`HOST`、`PORT`、`RELOAD` 可写入根目录 `.env`。也可以手动运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000> 即可使用（前端与 API 共用此端口）；健康检查地址为 <http://127.0.0.1:8000/api/health>。

## 便携版（Windows exe）

不需要 Python 环境。请先查看 [GitHub Releases 的最新版本](https://github.com/like-attract/video-to-note/releases/latest)；如果发布页附带 `VideoToNo-1.1.1-portable.exe`，下载后双击即可运行。仓库源码本身不代表发布页一定已有便携资产。

- 便携版没有控制台窗口，会自动在本机启动服务、打开默认浏览器并驻留系统托盘；通过托盘菜单打开界面、查看日志或退出。
- 端口 8000 被占用时自动顺延；若服务已在运行，再次双击只会打开浏览器，不会重复启动。
- 输出目录（视频、转录、截图、笔记）默认放在 exe 同目录的 `workspace\`；exe 所在目录不可写时回退到 `%LOCALAPPDATA%\VideoToNo\workspace`。可用环境变量 `VIDEOTONOTES_WORKSPACE`、`WHISPER_CACHE_DIR` 覆盖。
- 首次使用 Whisper 转写时会联网下载模型到工作目录，请保持网络通畅。

自行构建（需要 Python 3.11 与已安装的依赖）：

```powershell
.\scripts\build_exe.ps1
```

产物为 `dist\VideoToNo-1.1.1-portable.exe`，无控制台窗口，驻留系统托盘（右键菜单：打开界面 / 查看日志 / 退出），日志写入工作目录的 `_app.log`。程序图标使用 `sources/icon.ico`，可通过 `scripts/make_icon.py` 从 `sources/icon.png` 重新生成。

当前只发布和维护 Windows 便携版；macOS 暂不提供构建产物和兼容性承诺。

## 使用方法

1. 先选择 Provider，再选择预设模型并填写 API Key。DeepSeek 预设会按 `deepseek-v4-flash` 或 `deepseek-v4-pro` 原样提交；运行日志会显示实际 Provider、Model 和 Base URL。也可以在任意 Provider 下手动填写模型 ID，或配置自定义 OpenAI 兼容地址。
2. 选择笔记模式。`精简摘要` 和 `详细复原` 对短转录只调用一次；`详细笔记 + 点评分析` 先生成忠实笔记，再用独立调用补充中性的内容点评与分析。三种模式都使用简短提示词，不强制八股式章节。
3. 选择推理强度。`自动` 会为精简摘要关闭思考、为详细复原使用高档、为点评分析使用最大档；后端会把统一选项映射为各 Provider 实际支持的参数。模型返回空正文时会自动关闭思考重试一次。
4. 选择 Whisper 模型。默认使用下载较小、启动更稳妥的 `base`；`small` 的中文识别通常更准确，但首次下载、内存和处理时间也更多。
5. 粘贴视频链接，或切换到本地文件并上传媒体。
6. `复用转录` 会自动查找同一链接的 `transcript.json`，失败页的“从断点继续”和结果页的“基于转录重新生成”也会显式复用旧任务；选择 `从头处理` 才会重新读取字幕、音频和 Whisper。
7. B 站公开访问受限时，可临时填写 `SESSDATA`、`bili_jct` 和 `buvid3`。
8. 点击开始生成，等待任务完成后预览或下载 Markdown。处理中可以请求取消；当前下载、Whisper 或模型调用返回后任务会停止，已生成的中间文件会保留以供后续复用。

截图选项默认关闭。启用后，在线链接会额外下载低清视频并定时取帧，因此处理时间、网络流量和磁盘占用都会增加。当前截图仅作为附件插入笔记，不会发送给多模态模型进行画面分析。

## 输出内容

每个任务的文件保存在 `workspace/<task-id>/`，结果页会显示该目录的绝对路径。**每次完成时笔记还会自动归档到 `workspace/notes/<视频标题>.md`**（重名自动加序号），便于集中回顾整理。前端下载按钮只下载 `notes.md`；目录中还包含：

复用旧转录时仍会创建新的任务 ID，新 `notes.md` 写入新目录；旧任务的转录和笔记不会被覆盖。

- `notes.md`：最终结构化视频笔记；
- `transcript.md`：便于阅读的带时间戳转录；
- `transcript.json`：包含语言、来源以及每段 `start`、`end`、`text` 的结构化转录；
- `images/*.jpg`：仅在启用截图时存在。

平台字幕的准确性由平台字幕质量决定；Whisper 转写可能误识别人名、数字和专业术语。涉及重要事实时，应回到对应时间段核验原视频。

对于较长媒体，如果可识别语音覆盖率和单位时长文字量同时过低，应用会保留转录文件并在调用大模型前停止。常见原因包括源视频被审核占位、音轨静音、版权替换或内容以音乐为主。

## 隐私与安全

- 本项目仅按个人桌面工具设计。服务必须保持监听 `127.0.0.1`；不要设置为 `0.0.0.0`、不要做公网端口映射，也不要部署到共享主机。当前应用没有面向公网所需的身份认证或租户隔离。
- Web 界面中的 API Key 仅保留在当前页面内存，不写入浏览器存储；扫码登录取得的 Cookie 也只保留在当前进程内存中。显式调用 MCP 的保存工具后，相关凭据会以明文保存在本机 `workspace/` 配置文件中；它们并未由系统凭据库加密，请保护本机账户及工作目录，不要同步、分享或提交这些文件。读取配置的 API 只返回脱敏状态，不会把完整凭据传回前端。
- 后端使用 API Key 调用所选大模型服务，转录文本会被发送给该服务。请根据对应服务的隐私政策决定是否处理敏感内容。
- 媒体下载、字幕解析、Whisper 转写和文件生成均在本机完成；在线视频本身仍需从来源平台下载。
- Cookie 相当于登录凭据，只能用于账号本身有权访问的内容。通过 MCP 保存的凭据不再需要时，请删除相应本地配置文件或调用本地配置清除 API。

## MCP 接入（在 AI 客户端中调用）

VideoToNo 内置 MCP（Model Context Protocol）服务，可以在 Cherry Studio、Codex 等 AI 客户端里直接输入视频链接生成笔记。**使用前请先启动 VideoToNo**（MCP 工具通过本地后端执行任务，Whisper 模型缓存、任务目录等全部复用本机资源，不会重复下载）。

暴露 7 个工具：

| 工具 | 说明 |
|---|---|
| `summarize_video` | 提交视频总结任务（视频链接；API Key / 模型 / B 站凭据均可省略，自动使用本机已保存的配置），返回任务 ID |
| `wait_for_task` | 等待任务达到终态（最长 45 秒，可重复调用），完成后返回笔记正文；用它代替频繁轮询 |
| `get_task_status` | 查询任务中间进度（一般无需频繁调用） |
| `list_whisper_models` | 查看 Whisper 模型的本地缓存状态 |
| `save_llm_config` | 把大模型配置（Provider/模型/API Key）保存到本机，之后无需每次传入 |
| `save_bilibili_credentials` | 把 B 站凭据（SESSDATA 等）保存到本机，处理 B 站视频时自动使用 |
| `get_saved_config` | 查看已保存配置的状态（敏感信息脱敏显示） |

### Cherry Studio（推荐，无需本机 Python）

1. 启动 VideoToNo 后，打开 Cherry Studio「设置 → MCP 服务器 → 添加」；
2. 类型选择 **Server-Sent Events (SSE)**，URL 填 `http://127.0.0.1:8000/mcp/sse`（若服务端口不是 8000，改成实际端口）；
3. 保存并启用，在对话中即可让 AI 调用工具。

**首次使用建议让 AI 先执行一次保存**（之后无需再提供敏感信息）：

```text
请调用 save_llm_config 保存配置：我的 DeepSeek API Key 是 sk-xxx
再调用 save_bilibili_credentials 保存：sessdata=xxx，bili_jct=xxx，buvid3=xxx
```

之后直接说需求即可：

```text
帮我总结这个 B 站视频：https://www.bilibili.com/video/BV1xx
```

### Codex CLI

```bash
codex mcp add local videotono -- python -m backend.mcp_server
```

从任意目录运行时，MCP server 会自动扫描 8000-8019 端口找到正在运行的 VideoToNo 服务；也可用 `VIDEOTONOTES_BACKEND_URL` 环境变量显式指定。

> 隐私说明：`save_llm_config` / `save_bilibili_credentials` 保存的内容为明文，存放在本机工作目录（`workspace/llm_config.json`、`workspace/bili_credentials.json`）。程序会在系统支持时尝试限制文件权限，但实际访问控制仍取决于操作系统与本机账户配置；请勿分享这些文件。调用方未显式保存时，凭据不会落盘。工作目录整体已被 `.gitignore` 排除，这些文件不会进入 Git 仓库。

## 配置

应用会从项目根目录的 `.env` 读取以下可选设置。可以参考 `.env.example`：

```dotenv
HOST=127.0.0.1
PORT=8000
RELOAD=false
MAX_UPLOAD_MB=500
MAX_CONCURRENT_TASKS=1
TASK_HISTORY_LIMIT=100
# VIDEOTONOTES_WORKSPACE=D:\path\to\workspace
```

为了保持个人桌面使用的安全边界，必须保留 `HOST=127.0.0.1`。`MAX_CONCURRENT_TASKS` 控制同时执行的任务数，默认 `1` 可避免多个 Whisper 任务争抢内存；其余任务会排队。`TASK_HISTORY_LIMIT` 控制启动时最多恢复多少条历史任务。通过 MCP 显式保存的 API Key 与 B 站凭据位于本地工作目录，请按敏感文件保护。

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

模型默认缓存在 `workspace/_model_cache/`。如需改到其他磁盘，可在 `.env` 中设置 `WHISPER_CACHE_DIR`。

下载默认走 **hf-mirror.com 镜像**（国内可直接访问），并内置断点续传与重试；如需切换，在 `.env` 中设置 `HF_ENDPOINT`（如 `HF_ENDPOINT=https://huggingface.co` 使用官方源，或自建镜像地址）。若仍失败，请检查网络连通性、代理设置以及缓存目录写入权限。
应用默认禁用 Hugging Face Xet 下载后端，改用普通 HTTP 下载，以减少 Windows 网络下的 CAS 文件重建错误。
如果所选模型尚未完整缓存且本机已有可用的 `base`，任务会直接降级到 `base` 并在运行日志中注明，避免长时间卡在不稳定的权重下载上。

### CPU 转写为什么比视频时长还久？

速度取决于 CPU、视频时长和模型大小。CPU 模式使用 `int8` 以降低资源压力，但 `medium`、`large-v3` 和 `turbo` 仍可能较慢并占用较多内存。个人电脑上建议先用 `base`；确认 CUDA 环境可用后再启用 GPU。

### 为什么需要 B 站 Cookie？

公开且可直接访问的视频通常不需要 Cookie。部分播放器可见的中文 AI 字幕也只会在登录态字幕接口中返回；未填写凭据时，程序会记录提示并回退到 Whisper。登录可见、访问受限或字幕接口受限的视频可能需要当前账号凭据，但 Cookie 不能绕过账号本身没有的权限。凭据过期后需重新获取，且平台策略变化仍可能导致解析失败。

### 重启服务后任务会怎样？

1.0 会从 `workspace/<task-id>/task.json` 恢复最近的任务。已完成任务可继续预览和下载；上传后尚未开始的任务可重新提交；重启时仍在运行的任务会标记为失败，因为外部下载、Whisper 或大模型请求无法跨进程续跑，但已生成的字幕、转录和音频仍可供重新提交时复用。

### 为什么某个链接无法处理？

先确认链接能在当前网络和账号下正常播放，再更新项目依赖中的 `yt-dlp`。付费内容、DRM、验证码、地区限制、临时签名失效或平台接口变化都可能阻止解析。本项目不会绕过平台权限控制。

### 没有字幕时会发生什么？

应用会下载该链接可取得的最佳音频，并在本机运行 `faster-whisper`。这比直接读取字幕耗时更多，也会受到音质、口音、背景噪声和专业术语的影响。

## 项目状态

这是面向本地个人使用的工具，不是多用户服务。项目采用 [MIT License](LICENSE) 开源。
