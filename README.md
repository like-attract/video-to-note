<p align="center">
  <img src="sources/icon.png" width="96" alt="VideoToNo 图标">
</p>
<h1 align="center">VideoToNo v1.2.3</h1>

<p align="center"><em>把视频变成可回看的 Markdown 笔记</em></p>

<p align="center">🌐 <a href="README.md">简体中文</a> · <a href="README_EN.md">English</a></p>

<p align="center"><span style="white-space: nowrap;"><a href="https://github.com/like-attract/video-to-note/releases/latest"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/v/release/like-attract/video-to-note?display_name=tag&style=flat-square&label=release&color=2563eb" alt="最新版本"></a>&nbsp;<a href="LICENSE"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/license/like-attract/video-to-note?style=flat-square&label=license&color=22c55e" alt="MIT License"></a>&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/frontend-Vanilla%20JS-E34F26?style=flat-square&logo=javascript&logoColor=white" alt="Frontend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/backend-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="Backend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11"></span></p>

<p align="center"><a href="https://github.com/like-attract/video-to-note/releases/latest"><strong>⬇️ 下载 Windows 便携版</strong></a></p>

<p align="center">🎬 宣传视频：<a href="https://www.bilibili.com/video/BV1Qwby6DEu1/">https://www.bilibili.com/video/BV1Qwby6DEu1/</a> &nbsp;·&nbsp; 👥 QQ 交流群：<code>739200648</code></p>

VideoToNo 把「视频 → 结构化笔记」这条链路做成了一个**本地优先**的服务：输入 B 站、抖音、YouTube 链接或本地视频，优先读取平台字幕、B 站 AI 字幕，无字幕时用本地 faster-whisper 离线转写，再由你选择的大模型生成带时间轴的 Markdown 笔记。

> 🤖 **Agent Skill 已上线**：把仓库里的 `skills/video-to-note/` 目录复制到 agent 的技能目录（如 `C:/Users/用户名/.agents/skills/`），即可让 Claude Code、pi 等直接一句话生成视频笔记。详见该目录下的 `SKILL.md`。

## 🆕 What's New（v1.2.2 → v1.2.3）

- **B 站风控也能出笔记**：视频页被 412 拦截时，自动改用 `api.bilibili.com` 开放接口取元数据与音频（支持多分 P、预览视频流），任务日志会标明已回退；接口请求补齐风控 Cookie，b23.tv 短链也能解析出 BV 号（Issue #1）
- **修复「详细复原」卡在生成阶段不动**：长视频笔记省略时间戳会被误判为丢尾，进而自动补写整段剩余转录；现已加上缺口、材料规模、耗时三道护栏，真丢尾（≤10 分钟）仍会自动补写结尾
- **生成进度心跳**：模型输出期间每 20 秒上报「已输出 N 字」或「正在深度思考（已累计 N 字）」，深度思考阶段不再与死锁同形
- **推理强度按风格自适应**：`auto` 档在 DeepSeek 兼容通道对「详细笔记 + 点评」/「详细复原」默认取更高思考量，「精简摘要」取中档；任务日志会打印实际生效档位（Token 消耗相应增加，可在「推理强度」里显式选定）

<details>
<summary>历史版本摘要（v1.2.2 及更早）</summary>

- **v1.2.2**：Agent Skill 接入（skills/video-to-note/，一句话生成笔记）、任务完成/失败的 Windows 托盘通知、仓库与文档定位重写
- **v1.2.1**：取消真正立即生效（LLM 流式/下载钩子/模型下载逐块取消）、上传上限 2GB + 超大视频自动提音频、Whisper base 模型导入包
- **v1.2.0**：B 站多分 P 视频（全部/指定分P合并笔记）、语音转写段粒度取消、B 站无字幕误报修正、Whisper 手动导入模型界面
- **v1.1.8**：运行模式与应用版本隔离、最近任务滚动条、详细复原元信息去重、自定义 Base URL 错误诊断
- **v1.1.7**：抖音单条公开分享链接、浏览器验证回退、转写复用和已取消任务删除。
- **v1.1.6**：Whisper 下载确认记忆和自定义工作目录修复。
- **v1.1.5**：旧缓存页面、MCP SSE 和启动告警修复。
- **v1.1.4**：笔记元信息、托盘更新检查、B 站下载稳定性和 Whisper 回退改进。
- **v1.1.3 及更早**：B 站 412 处理、LLM 测试、MCP、任务恢复和多格式导出等基础能力。

</details>



## 🖼️ 界面预览

### ⚙️ 配置与提交

<p align="center"><img src="sources/preview.png" alt="VideoToNo 配置与视频提交界面" width="100%"></p>

### ⏳ 处理进度

<p align="center"><img src="sources/process.png" alt="VideoToNo 任务处理进度与运行记录" width="100%"></p>

### 📝 笔记输出

<p align="center"><img src="sources/output-public.png" alt="VideoToNo 生成的详细视频笔记" width="100%"></p>

## 🚀 便携版下载（推荐）

普通用户无需安装 Python 或配置开发环境，直接下载 [最新 Release](https://github.com/like-attract/video-to-note/releases/latest) 中的 `VideoToNo-1.2.3-portable.exe`：

1. 下载并双击 exe；
2. 等待浏览器自动打开本地页面；
3. 填写 Provider、模型和 API Key，粘贴视频链接或上传本地文件；
4. 等待生成笔记。

便携版会自动启动本地服务并驻留系统托盘。第一次使用 Whisper 转写时需要下载模型，请保持网络畅通；生成的任务、转录、截图和笔记默认保存在 exe 同目录的 `workspace/` 中。

## ✨ 主要功能

- 🎥 **多种输入**：B 站、抖音、YouTube、其他可被 `yt-dlp` 解析的视频链接，以及本地音视频文件。
- 🇨🇳 **B 站深度适配**：除常规字幕外，可在登录后读取 B 站 AI 字幕；凭据支持扫码导入；视频页被风控时自动回退到开放接口取信息与音频。
- 🎵 **抖音单链接适配**：支持公开分享链接直接解析；需要验证时可在独立本机浏览器完成登录后重试。
- 🧾 **真实时间轴**：保留字幕或 Whisper 分段的起止时间，不让模型凭空猜时间点。
- 🧠 **长内容整理**：短转录直接生成，长转录自动分块、归并并控制上下文压力；生成后会核对是否写到材料结尾，小缺口自动补写。
- 🖼️ **多格式输出**：Markdown 笔记、HTML、JSON、纯文本和 PNG 图片；可选提取视频截图作为附件。
- 🛡️ **本地优先**：媒体下载、转录和文件生成在本机完成；服务默认只监听 `127.0.0.1`。

## 🔁 处理流程

```text
视频链接 / 本地文件
        ↓
平台字幕（B 站 AI 字幕优先）
        ↓ 无可用字幕
faster-whisper 本地转写
        ↓
大模型生成带时间轴的 Markdown 笔记
```

## 🌍 支持范围

- B 站、抖音、YouTube，以及当前版本 `yt-dlp` 能解析的其他 `http` / `https` 视频链接；B 站输入支持分享文本、`b23.tv` 短链（可缺省 scheme）与裸 BV/av 号；
- 抖音支持单条公开分享链接；匿名解析受限时，可从页面打开独立的本机抖音浏览器完成登录或验证后重试；
- `.mp3`、`.m4a`、`.wav`、`.flac`、`.aac`、`.mp4`、`.mkv`、`.mov`、`.webm`、`.avi` 等本地媒体；
- 爱奇艺、腾讯视频目前未做专门适配，能否处理取决于平台访问权限、字幕可见性和 `yt-dlp` 的解析能力。

## ⚙️ 使用与配置

启动后按页面提示完成以下操作即可：

1. 选择 Provider、模型并填写 API Key，也可以填写自定义 OpenAI 兼容地址；
2. 选择笔记风格、推理强度和 Whisper 模型（推理强度保持 `auto` 时，详细类笔记会用更高思考档，任务日志会打印实际生效档位）；
3. 粘贴视频链接，或切换为本地文件上传；
4. 生成完成后预览、复制或下载笔记。

API Key 和扫码取得的 B 站 Cookie 默认只保留在当前页面/进程内存中；只有通过 MCP 保存配置时，才会以明文写入本机 `workspace/`，请勿分享这些文件。

<details>
<summary>🧑‍💻 源码运行与构建（开发者）</summary>

源码用户直接 `git clone` 或在 GitHub 选择 **Code → Download ZIP** 即可获取完整项目。项目主要面向 Windows + Python 3.11，安装 `backend/requirements.txt` 后可使用 `start.ps1` 启动；需要自行构建便携版时运行：

```powershell
git clone https://github.com/like-attract/video-to-note.git
cd video-to-note
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\start.ps1
```

构建 Windows 便携版：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

</details>

<details>
<summary>🤖 MCP / AI 客户端（进阶）</summary>

VideoToNo 内置 MCP（Model Context Protocol）服务，可以在 Cherry Studio、Codex 等 AI 客户端里直接输入视频链接生成笔记。**使用前请先启动 VideoToNo**：MCP 工具通过本地后端执行任务，会复用本机的 Whisper 模型缓存和任务目录，不会重复下载。

可用工具：

| 工具 | 说明 |
|---|---|
| `summarize_video` | 提交视频总结任务；视频链接、API Key、模型和 B 站凭据都可省略，自动使用本机已保存的配置 |
| `wait_for_task` | 等待任务达到终态（最长 45 秒，可重复调用），完成后返回笔记正文；适合代替频繁轮询 |
| `get_task_status` | 查询任务中间进度 |
| `list_whisper_models` | 查看 Whisper 模型的本地缓存状态 |
| `save_llm_config` | 把 Provider、模型和 API Key 保存到本机，之后无需每次传入 |
| `save_bilibili_credentials` | 把 B 站凭据（SESSDATA 等）保存到本机，处理 B 站视频时自动使用 |
| `get_saved_config` | 查看已保存配置的状态，敏感信息会脱敏显示 |

### Cherry Studio（推荐，无需本机 Python）

1. 启动 VideoToNo 后，打开 Cherry Studio「设置 → MCP 服务器 → 添加」；
2. 类型选择 **Server-Sent Events (SSE)**，URL 填：

```text
http://127.0.0.1:8000/mcp/sse
```

若服务端口不是 8000，请填写实际端口。保存并启用后，就可以在对话中让 AI 调用 VideoToNo。

首次使用时，建议让 AI 先保存一次配置；之后不必在每次对话中重复提供敏感信息：

```text
请调用 save_llm_config 保存配置：我的 DeepSeek API Key 是 sk-xxx
再调用 save_bilibili_credentials 保存：sessdata=xxx，bili_jct=xxx，buvid3=xxx
```

之后直接描述需求即可：

```text
帮我总结这个 B 站视频：https://www.bilibili.com/video/BV1xx
```

### Codex CLI

支持 stdio 的客户端可运行：

```bash
codex mcp add local videotono -- python -m backend.mcp_server
```

MCP server 会自动扫描 8000–8019 端口来找到已运行的 VideoToNo 服务；也可以用环境变量 `VIDEOTONOTES_BACKEND_URL` 显式指定服务地址。

> 隐私说明：`save_llm_config` / `save_bilibili_credentials` 会将内容以明文保存在本机工作目录的 `workspace/llm_config.json` 和 `workspace/bili_credentials.json`。请勿分享这些文件；未显式调用保存工具时，凭据不会落盘。`workspace/` 已被 `.gitignore` 排除，不会进入 Git 仓库。

</details>

<details>
<summary>🧩 Agent Skill（进阶）</summary>

除了 MCP，VideoToNo 也提供 **Agent Skill**（`skills/video-to-note/`），供 Claude Code、pi 等支持技能的编码类 agent 使用：agent 读取 `SKILL.md` 后，会自动调用本仓库附带的脚本完成「提交任务 → 轮询进度 → 取回笔记」全流程。**使用前请先启动 VideoToNo**，与 MCP 相同，任务由本地后端执行。

### 安装

把仓库里的 `skills/video-to-note/` 整个目录复制到 agent 的技能目录，例如：

```text
C:\Users\<用户名>\.agents\skills\video-to-note\        # pi / 通用约定
~/.claude/skills/video-to-note/                         # Claude Code
```

### 使用

安装后直接对 agent 说一句话即可：

```text
帮我把这个 B 站视频做成笔记：https://www.bilibili.com/video/BV1xx
```

agent 会调用技能附带的 `scripts/video_note.py`，自动探测服务端口、提交任务、实时打印运行日志，完成后输出（或保存）完整 Markdown 笔记。常用参数：`--style detailed|faithful|concise`、`--wait 秒数`、`--out 文件路径`；本地视频文件路径也可直接作为输入。

> 首次使用同样需要大模型 API Key：agent 会向你要，用 `--provider` / `--api-key` 传入；或先用 MCP 的 `save_llm_config` 保存过配置，则无需再传。

</details>

<details>
<summary>❓ 常见问题</summary>

### 首次转写为什么等待很久？

`faster-whisper` 会在第一次使用某个模型时下载模型文件。下载耗时取决于模型大小和网络状况，后续会复用本机缓存；切换到另一个模型时仍可能再次下载。

模型默认缓存在 `workspace/_model_cache/`。如需改到其他磁盘，可在 `.env` 中设置 `WHISPER_CACHE_DIR`。下载默认走 **hf-mirror.com 镜像**，支持断点续传和重试；如需切换，可设置 `HF_ENDPOINT`，例如 `HF_ENDPOINT=https://huggingface.co` 使用官方源。若仍失败，请检查网络、代理和缓存目录写入权限。

应用默认禁用 Hugging Face Xet 下载后端，改用普通 HTTP 下载，以减少部分 Windows 网络下的 CAS 文件重建错误。如果所选模型尚未完整缓存、但本机已有可用的 `base`，任务会降级到 `base` 并在运行日志中注明，避免长期卡在不稳定的权重下载上。

### 大模型（medium 及以上）下载反复失败怎么办？

大模型体积大（medium 约 1.5GB），网络不佳时容易中断。除了重试（支持断点续传），推荐手动导入：

1. 在「语音转写」设置中选择目标模型，点击「**手动导入模型**」按钮，程序会自动打开导入文件夹（`workspace/_model_cache/manual/<模型名>/`）；
2. 用浏览器（或下载工具）从镜像站下载该模型的 4 个文件：`config.json`、`model.bin`、`tokenizer.json`、`vocabulary.txt`（下载页：`https://hf-mirror.com/Systran/faster-whisper-<模型名>/tree/main`，点开每个文件右上角的下载箭头）；
3. 把 4 个文件原样放入打开的文件夹，程序几秒内自动识别，下拉框会显示「已缓存」。

程序会对下载和缓存做完整性校验；检测到历史中断留下的损坏文件时会自动清理并重新下载，无需手动删除缓存。

### CPU 转写为什么比视频时长还久？

速度取决于 CPU、视频时长和模型大小。CPU 模式使用 `int8` 降低资源压力，但 `medium`、`large-v3` 和 `turbo` 仍可能较慢并占用较多内存。个人电脑建议先使用 `base`；确认 CUDA 环境可用后再启用 GPU。

### 为什么需要 B 站 Cookie？

公开且可直接访问的视频通常不需要 Cookie。部分播放器可见的中文 AI 字幕只会在登录态字幕接口中返回；未填写凭据时，程序会记录提示并回退到 Whisper。登录可见、访问受限或字幕接口受限的视频可能需要当前账号凭据，但 Cookie 不能绕过账号本身没有的权限。凭据过期后需重新获取，平台策略变化也可能导致解析失败。

### 重启服务后任务会怎样？

VideoToNo 会从 `workspace/<task-id>/task.json` 恢复最近任务。已完成任务可继续预览和下载；上传后尚未开始的任务可重新提交。重启时仍在运行的任务会标记为失败，因为外部下载、Whisper 或大模型请求无法跨进程续跑；已经生成的字幕、转录和音频仍可在重新提交时复用。

### 为什么某个链接无法处理？

先确认链接能在当前网络和账号下正常播放，再更新项目依赖中的 `yt-dlp`。付费内容、DRM、验证码、地区限制、临时签名失效或平台接口变化都可能阻止解析；项目不会绕过平台权限控制。

### 没有字幕时会发生什么？

应用会下载该链接可取得的最佳音频，并在本机运行 `faster-whisper`。这比直接读取字幕耗时更多，也会受到音质、口音、背景噪声和专业术语的影响。

### 为什么会提示“模型未返回正文，已关闭深度思考并重试”？

这表示模型这一次真实返回了空正文，并非仅仅是界面显示问题。应用会立即在当前生成阶段提示，并自动关闭深度思考重试一次；若重试后仍无正文，任务会按实际错误状态处理。

### B 站视频页无法访问时为什么任务还在继续？

部分网络环境下 B 站视频网页会被风控拦截（`HTTP 412`），而 `api.bilibili.com` 开放接口仍可用。应用会自动改用接口直连取视频信息与音频（多分 P 与预览截图同样覆盖），运行日志会写明“已改用开放接口直连”，不需要重新提交。项目不会绕过平台权限控制：登录可见、会员专属内容仍需有效凭据。

### 为什么进度会停在“模型正在深度思考”一段时间？

详细笔记与详细复原默认使用更高思考档，长视频可能几分钟只产生思考链。界面每 20 秒会刷新一次“正在深度思考（已累计 N 字）”或“模型已输出 N 字”，说明任务仍在推进；若希望更快或更省 Token，可把推理强度显式改为“关闭深度思考”或“高”。任何时候都可以取消任务，已生成的转录会被保留。

</details>

## 🎬 宣传视频与交流群

- 🎬 宣传视频（B 站）：<a href="https://www.bilibili.com/video/BV1Qwby6DEu1/">BV1Qwby6DEu1</a>
- 👥 QQ 交流群：<code>739200648</code>（欢迎反馈问题、提建议、获取模型附件）

## 📄 开源许可

这是一个面向本地个人使用的工具，采用 [MIT License](LICENSE) 开源。
