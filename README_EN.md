<p align="center">
  <img src="sources/icon.png" width="96" alt="VideoToNo icon">
</p>

<h1 align="center">VideoToNo v1.1.6</h1>

<p align="center"><em>Turn videos into Markdown notes you can revisit</em></p>

<p align="center">🌐 <a href="README.md">简体中文</a> · <a href="README_EN.md">English</a></p>

<p align="center"><span style="white-space: nowrap;"><a href="https://github.com/like-attract/video-to-note/releases/latest"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/v/release/like-attract/video-to-note?display_name=tag&style=flat-square&label=release&color=2563eb" alt="Latest release"></a>&nbsp;<a href="LICENSE"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/license/like-attract/video-to-note?style=flat-square&label=license&color=22c55e" alt="MIT License"></a>&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/frontend-Vanilla%20JS-E34F26?style=flat-square&logo=javascript&logoColor=white" alt="Frontend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/backend-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="Backend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11"></span></p>

<p align="center"><a href="https://github.com/like-attract/video-to-note/releases/latest"><strong>⬇️ Download the Windows portable build</strong></a></p>

VideoToNo is a local, personal-use video-to-notes tool. Give it a Bilibili, YouTube, or other supported video URL—or a local media file. It prefers platform captions, falls back to local `faster-whisper` transcription when needed, and asks your selected language model to produce timestamped Markdown notes.

## 🆕 What's New (v1.1.5 → v1.1.6)

Bugfix-only release:

- Stop the repeated “Whisper model missing” confirmation: submissions that will use platform subtitles (Bilibili with credentials, Douyin verified) skip it, per-session it only asks once, and model cache status refreshes automatically after tasks

## 🆕 What's New (v1.1.4 → v1.1.5)

Bugfix-only release:

- Fix unresponsive page buttons caused by stale cached HTML (cache-disabled entry page, resilient script init, visible failure banner)
- Fix MCP SSE request crash
- Silence benign `lifespan` warning noise from the mcp SDK

## 🆕 What's New (v1.1.3 → v1.1.4)

- 📝 **Richer note metadata**: notes show available author, publish date, and duration details below the title, then append the source URL, model, and note style at the end.
- 🎛️ **Consistent style handling**: all three styles can naturally use verified video metadata without forcing a rigid template or inventing missing fields.
- 🔄 **Tray update check**: manually check the GitHub Release page from the system tray and open it after confirmation when a newer version is available.
- 🧠 **More faithful Whisper selection**: the selected model is actually attempted first; fallback to a cached `base` model happens only after a real load/download failure.

- 🛡️ **Reliable Bilibili downloads**: fixed HTTP 412 anti-bot rejections (real browser User-Agent, Cookies moved to yt-dlp's cookiefile channel, and missing risk cookies like buvid/b_nut/b_lsid now collected), so downloads work after signing in.
- 🩺 **One-click LLM connection test**: a new “Test connection” button in the settings panel verifies Provider / API Key / Base URL in seconds and tells you whether a failed test is a bad key or a wrong endpoint.
- ⚡ **Frontend updates apply instantly**: static assets now use a no-cache policy, so a simple refresh loads new pages/buttons instead of stale cached files.

<details>
<summary>v1.1.2 and earlier</summary>

- 🔗 **Looser link input**: the URL field no longer requires an `https://` prefix — Bilibili share text, scheme-less `b23.tv` short links, and bare BV/av IDs are recognized and normalized automatically.
- 🎨 **New 3D product icon**: the exe, system tray, web sidebar, and browser favicon all use the refreshed icon.
- 🧠 **More reliable long answers**: long DeepSeek responses are streamed; reasoning defaults and request timeouts are aligned.
- 🖥️ **Portable and dev instances can coexist**: the health endpoint reports the run mode and the launcher only reuses same-mode instances; a dev server on port 8000 no longer blocks the portable build's tray.
- 📚 **Docs restored**: detailed MCP integration and FAQ guides are back in the README.

</details>

<details>
<summary>v1.1.1 and earlier</summary>

- 🎬 **More reliable long-video generation**: transcripts are split at segment boundaries and reduced hierarchically, with live chunk, draft, and analysis progress.
- 📝 **Smoother output workflow**: copy complete notes, export Markdown / HTML / JSON / plain text / PNG, delete failed tasks, resume from saved transcripts, cancel jobs, and archive notes automatically.
- 🧠 **Clearer model status**: when a model returns an empty body, the affected stage reports it immediately and retries once with reasoning disabled.
- 🎨 **More consistent UI**: the product page, desktop app, and tray use the shared `sources/icon.png` asset; the six progress stages are evenly spaced; product screenshots were refreshed.
- 🔌 **Better local integration**: multiple providers, custom model IDs, note styles, reasoning controls, and MCP access for clients such as Cherry Studio and Codex.

</details>

## 🖼️ Screenshots

### ⚙️ Configuration

<p align="center"><img src="sources/preview.png" alt="VideoToNo configuration and video submission interface" width="100%"></p>

### ⏳ Processing

<p align="center"><img src="sources/process.png" alt="VideoToNo task progress and runtime log" width="100%"></p>

### 📝 Generated note

<p align="center"><img src="sources/output-public.png" alt="Detailed video notes generated by VideoToNo" width="100%"></p>

## 🚀 Portable build (recommended)

No Python or development setup is required. Download `VideoToNo-1.1.3-portable.exe` from the [latest Release](https://github.com/like-attract/video-to-note/releases/latest):

1. Download and double-click the exe;
2. Wait for the local page to open in your browser;
3. Enter a provider, model, and API key, then paste a video URL or upload a local file;
4. Generate and review your note.

The portable build starts the local service and stays in the system tray. The first Whisper transcription downloads a model, so keep the network available. Tasks, transcripts, screenshots, and notes are stored under `workspace/` next to the exe by default.

## ✨ Highlights

- 🎥 **Flexible inputs**: Bilibili, YouTube, other URLs supported by the installed `yt-dlp`, and local audio/video files.
- 🇨🇳 **Bilibili integration**: fetches Bilibili AI captions after sign-in, with QR-code credential import in the UI.
- 🧾 **Real timestamps**: keeps segment start and end times from captions or Whisper instead of asking the model to invent them.
- 🧠 **Long-transcript handling**: chunks, summarizes, and reduces long material while keeping context pressure under control.
- 🖼️ **Multiple exports**: Markdown, HTML, JSON, plain text, and PNG, with optional video screenshots as note attachments.
- 🛡️ **Local-first**: media downloads, transcription, and file generation happen locally; the service listens on `127.0.0.1` by default.

## 🔁 Processing pipeline

```text
Video URL / local file
        ↓
Platform captions (Bilibili AI captions first)
        ↓ when unavailable
Local faster-whisper transcription
        ↓
LLM-generated timestamped Markdown note
```

## 🌍 Supported inputs

- Bilibili, YouTube, and other `http` / `https` video URLs that the installed `yt-dlp` can parse;
- local `.mp3`, `.m4a`, `.wav`, `.flac`, `.aac`, `.mp4`, `.mkv`, `.mov`, `.webm`, and `.avi` files;
- Douyin, iQiyi, and Tencent Video are not specially adapted, so support depends on access permissions, caption availability, and `yt-dlp` behavior.

## ⚙️ Use and configure

After starting the app:

1. Choose a provider and model, enter an API key, or provide a custom OpenAI-compatible endpoint;
2. Choose a note style, reasoning effort, and Whisper model;
3. Paste a video URL or upload a local file;
4. Preview, copy, or download the generated note.

API keys and QR-login Bilibili cookies stay in the current page/process by default. They are written as plaintext under the local `workspace/` only when an MCP save tool is explicitly used; do not share those files.

<details>
<summary>🧑‍💻 Source use and building (developers)</summary>

If you want the source, use `git clone` or GitHub **Code → Download ZIP**. The project targets Windows + Python 3.11; install `backend/requirements.txt` and use `start.ps1` to run it:

```powershell
git clone https://github.com/like-attract/video-to-note.git
cd video-to-note
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\start.ps1
```

Build a Windows portable executable with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

</details>

<details>
<summary>🤖 MCP / AI clients (advanced)</summary>

VideoToNo includes an MCP (Model Context Protocol) server, so AI clients such as Cherry Studio and Codex can turn a video link into a note directly. **Start VideoToNo first**: MCP tasks run through the local backend and reuse its Whisper model cache and task directories.

Available tools:

| Tool | Description |
|---|---|
| `summarize_video` | Submit a video summarization task; URL, API key, model, and Bilibili credentials can be omitted when saved local config is available |
| `wait_for_task` | Wait for a terminal task state for up to 45 seconds per call and return the note when complete; use it instead of aggressive polling |
| `get_task_status` | Inspect intermediate task progress |
| `list_whisper_models` | Show local Whisper model cache status |
| `save_llm_config` | Save provider, model, and API key locally so they do not need to be passed again |
| `save_bilibili_credentials` | Save Bilibili credentials such as SESSDATA for automatic use with Bilibili videos |
| `get_saved_config` | View saved configuration status with secrets masked |

### Cherry Studio (recommended; no local Python needed)

1. Start VideoToNo, then open Cherry Studio **Settings → MCP Servers → Add**;
2. Choose **Server-Sent Events (SSE)** and enter:

```text
http://127.0.0.1:8000/mcp/sse
```

Use the actual port if it is not 8000. Save and enable the server, then ask the AI client to use VideoToNo.

For first-time use, ask the AI to save your configuration once. You will not need to repeat sensitive information in later conversations:

```text
Please call save_llm_config to save my configuration: my DeepSeek API key is sk-xxx.
Then call save_bilibili_credentials to save: sessdata=xxx, bili_jct=xxx, buvid3=xxx.
```

Afterward, simply ask for what you need:

```text
Summarize this Bilibili video: https://www.bilibili.com/video/BV1xx
```

### Codex CLI

For stdio clients such as Codex:

```bash
codex mcp add local videotono -- python -m backend.mcp_server
```

The MCP server automatically scans ports 8000–8019 to locate a running VideoToNo service. Set `VIDEOTONOTES_BACKEND_URL` to explicitly choose the backend URL.

> Privacy: `save_llm_config` and `save_bilibili_credentials` store plaintext files under the local workspace: `workspace/llm_config.json` and `workspace/bili_credentials.json`. Do not share them. Nothing is persisted unless you explicitly call a save tool; `workspace/` is excluded by `.gitignore` and is not committed to this repository.

</details>

<details>
<summary>❓ FAQ</summary>

### Why does the first transcription take so long?

`faster-whisper` downloads a model the first time that model is used. Download time depends on model size and network conditions. The local cache is reused later, but choosing another model can trigger another download.

Models are cached under `workspace/_model_cache/` by default. Set `WHISPER_CACHE_DIR` in `.env` to use another disk. Downloads use the **hf-mirror.com mirror** by default with resume and retry support; set `HF_ENDPOINT` to switch sources, for example `HF_ENDPOINT=https://huggingface.co` for the official host. If downloads still fail, check network connectivity, proxy settings, and write access to the cache directory.

The app disables Hugging Face's Xet download backend by default and uses regular HTTP downloads to avoid CAS reconstruction failures on some Windows networks. If the selected model is incomplete but a usable `base` model is already cached, the job falls back to `base` and records that decision in the task log rather than waiting indefinitely for a weight download.

### Why is CPU transcription slower than the video duration?

Speed depends on CPU performance, media duration, and model size. CPU mode uses `int8` to reduce resource pressure, but `medium`, `large-v3`, and `turbo` can still be slow and memory-intensive. Start with `base` on a personal computer, and enable GPU mode only after confirming CUDA works correctly.

### Why would I need a Bilibili cookie?

Publicly accessible videos normally do not need one. Some visible Chinese AI captions are returned only by signed-in caption endpoints; without credentials, the app records the reason and falls back to Whisper. Signed-in, restricted, or caption-limited videos may need credentials for an account that already has access. A cookie cannot grant permissions the account does not have, can expire, and may stop working when the platform changes its rules.

### What happens to tasks after restarting the service?

VideoToNo restores recent tasks from `workspace/<task-id>/task.json`. Completed tasks remain available for preview and download, and uploaded tasks that have not started can be submitted again. A task that was running during restart is marked failed because external downloads, Whisper, and LLM requests cannot continue across processes; already generated captions, transcripts, and audio can still be reused on a later submission.

### Why can a particular URL not be processed?

First confirm that it plays in the current network and account, then update the project's `yt-dlp` dependency. Paid content, DRM, CAPTCHAs, region restrictions, expired signatures, and platform API changes can all prevent extraction. This project does not bypass platform access controls.

### What happens when no captions are available?

The app downloads the best available audio stream and runs `faster-whisper` locally. This takes longer than using captions and is affected by audio quality, accents, background noise, and domain-specific vocabulary.

### Why does the log say that the model returned no body and reasoning was disabled for a retry?

That message means the model actually returned an empty body; it is not merely a display issue. The app reports it immediately at the current generation stage and retries once with reasoning disabled. If the retry is also empty, the task follows its actual error path.

</details>

## 📄 License

This is a local personal-use tool released under the [MIT License](LICENSE).
