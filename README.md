<p align="center">
  <img src="sources/icon.png" width="96" alt="VideoToNo 图标">
</p>

<h1 align="center">VideoToNo v1.1.1</h1>

<p align="center"><em>把视频变成可回看的 Markdown 笔记</em></p>

<p align="center">🌐 <a href="README.md">简体中文</a> · <a href="README_EN.md">English</a></p>

<p align="center"><span style="white-space: nowrap;"><a href="https://github.com/like-attract/video-to-note/releases/latest"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/v/release/like-attract/video-to-note?display_name=tag&style=flat-square&label=release&color=2563eb" alt="最新版本"></a>&nbsp;<a href="LICENSE"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/license/like-attract/video-to-note?style=flat-square&label=license&color=22c55e" alt="MIT License"></a>&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/frontend-Vanilla%20JS-E34F26?style=flat-square&logo=javascript&logoColor=white" alt="Frontend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/backend-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="Backend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11"></span></p>

<p align="center"><a href="https://github.com/like-attract/video-to-note/releases/latest"><strong>⬇️ 下载 Windows 便携版</strong></a></p>

VideoToNo 是一个面向个人使用的本地视频笔记工具：输入 B 站、YouTube 等视频链接或本地媒体，优先读取平台字幕；没有可用字幕时使用 `faster-whisper` 转写，最后调用你选择的大模型生成带时间轴的 Markdown 笔记。

## 🆕 What's New（v1.1.0 → v1.1.1）

- 🎬 **长视频更稳**：转录内容按完整分段切块并分层归并，生成阶段实时展示逐段整理、成稿和点评进度。
- 📝 **输出更顺手**：支持一键复制、Markdown / HTML / JSON / 纯文本 / PNG 导出，失败任务删除、断点继续、任务取消和笔记自动归档更完整。
- 🧠 **模型状态更透明**：模型返回空正文时会在当前阶段立即提示，并自动关闭深度思考重试一次，避免任务结束后才出现误导性日志。
- 🎨 **界面更统一**：产品页、桌面端和托盘统一使用 `sources/icon.png` 生成的图标，处理进度 6 个环节等距排列，并更新产品截图。
- 🔌 **本地集成更完整**：支持多 Provider、自定义模型、笔记风格、推理强度，以及 Cherry Studio / Codex 等 AI 客户端的 MCP 接入。

## 🖼️ 界面预览

### ⚙️ 配置与提交

<p align="center"><img src="sources/preview.png" alt="VideoToNo 配置与视频提交界面" width="100%"></p>

### ⏳ 处理进度

<p align="center"><img src="sources/process.png" alt="VideoToNo 任务处理进度与运行记录" width="100%"></p>

### 📝 笔记输出

<p align="center"><img src="sources/output-public.png" alt="VideoToNo 生成的详细视频笔记" width="100%"></p>

## 🚀 便携版下载（推荐）

普通用户无需安装 Python 或配置开发环境，直接下载 [最新 Release](https://github.com/like-attract/video-to-note/releases/latest) 中的 `VideoToNo-1.1.1-portable.exe`：

1. 下载并双击 exe；
2. 等待浏览器自动打开本地页面；
3. 填写 Provider、模型和 API Key，粘贴视频链接或上传本地文件；
4. 等待生成笔记。

便携版会自动启动本地服务并驻留系统托盘。第一次使用 Whisper 转写时需要下载模型，请保持网络畅通；生成的任务、转录、截图和笔记默认保存在 exe 同目录的 `workspace/` 中。

## ✨ 主要功能

- 🎥 **多种输入**：B 站、YouTube、其他可被 `yt-dlp` 解析的视频链接，以及本地音视频文件。
- 🇨🇳 **B 站深度适配**：除常规字幕外，可在登录后读取 B 站 AI 字幕；凭据支持扫码导入。
- 🧾 **真实时间轴**：保留字幕或 Whisper 分段的起止时间，不让模型凭空猜时间点。
- 🧠 **长内容整理**：短转录直接生成，长转录自动分块、归并并控制上下文压力。
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

- B 站、YouTube，以及当前版本 `yt-dlp` 能解析的其他 `http` / `https` 视频链接；
- `.mp3`、`.m4a`、`.wav`、`.flac`、`.aac`、`.mp4`、`.mkv`、`.mov`、`.webm`、`.avi` 等本地媒体；
- 抖音、爱奇艺、腾讯视频目前未做专门适配，能否处理取决于平台访问权限、字幕可见性和 `yt-dlp` 的解析能力。

## ⚙️ 使用与配置

启动后按页面提示完成以下操作即可：

1. 选择 Provider、模型并填写 API Key，也可以填写自定义 OpenAI 兼容地址；
2. 选择笔记风格、推理强度和 Whisper 模型；
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

启动 VideoToNo 后，Cherry Studio 等支持 SSE 的客户端可连接：

```text
http://127.0.0.1:8000/mcp/sse
```

Codex 等支持 stdio 的客户端可运行：

```bash
codex mcp add local videotono -- python -m backend.mcp_server
```

MCP 可提交视频任务、等待任务完成、查看 Whisper 模型状态，并按需保存本机的模型/API 配置。

</details>

<details>
<summary>❓ 常见问题</summary>

- **第一次转写为什么比较慢？** 首次使用某个 Whisper 模型时需要下载模型，后续会复用本机缓存。
- **什么时候需要 B 站 Cookie？** 部分登录可见视频或 AI 字幕接口需要当前账号凭据；Cookie 不能绕过账号本身没有的权限。
- **为什么没有生成笔记？** 付费内容、DRM、验证码、地区限制、失效链接、无可用字幕或平台接口变化都可能导致解析失败；应用会尽量保留已生成的转录文件供复用。

</details>

## 📄 开源许可

这是一个面向本地个人使用的工具，采用 [MIT License](LICENSE) 开源。
