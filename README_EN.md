<p align="center">
  <img src="sources/icon.png" width="96" alt="VideoToNo icon">
</p>

<h1 align="center">VideoToNo v1.2.3</h1>

<p align="center"><em>Turn videos into Markdown notes you can revisit</em></p>

<p align="center">🌐 <a href="README.md">简体中文</a> · <a href="README_EN.md">English</a></p>

<p align="center"><span style="white-space: nowrap;"><a href="https://github.com/like-attract/video-to-note/releases/latest"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/v/release/like-attract/video-to-note?display_name=tag&style=flat-square&label=release&color=2563eb" alt="Latest release"></a>&nbsp;<a href="LICENSE"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/license/like-attract/video-to-note?style=flat-square&label=license&color=22c55e" alt="MIT License"></a>&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/frontend-Vanilla%20JS-E34F26?style=flat-square&logo=javascript&logoColor=white" alt="Frontend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/backend-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="Backend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11"></span></p>

<p align="center"><a href="https://github.com/like-attract/video-to-note/releases/latest"><strong>⬇️ Download the Windows portable build</strong></a></p>

<p align="center">🎬 Promo video (Bilibili): <a href="https://www.bilibili.com/video/BV1Qwby6DEu1/">https://www.bilibili.com/video/BV1Qwby6DEu1/</a> &nbsp;·&nbsp; 👥 QQ group: <code>739200648</code></p>

VideoToNo turns the **"video → structured notes" pipeline** into a local-first service: give it a Bilibili, Douyin, YouTube link or a local video; it prefers platform captions including Bilibili AI captions, falls back to local faster-whisper transcription when none exist, then has your chosen LLM write timestamped Markdown notes. 

> 🤖 **Agent Skill available**: copy the `skills/video-to-note/` directory from this repo into your agent's skills directory (e.g. `~/.agents/skills/`) and coding agents like Claude Code or pi can generate video notes from a single sentence. See `SKILL.md` inside that directory.

## 🆕 What's New (v1.2.2 → v1.2.3)

- **Bilibili notes survive risk control**: when the video page returns 412, VideoToNo falls back to the `api.bilibili.com` open endpoints for metadata and audio (multi-part videos and preview streams included), and the task log says so. API requests now carry the full risk-control cookie set, `b23.tv` short links resolve to BV ids, and anonymous use gets a device fingerprint automatically (Issue #1)
- **Fixed "Faithful" style hanging in the generation stage**: on long videos a note that simply omits timestamps was mistaken for a truncated one, triggering an extra regeneration of the whole remaining transcript. Three guardrails now bound the gap, the input size and the wall-clock time; a genuinely missing tail (within 10 minutes) is still patched automatically
- **Generation progress heartbeat**: while the model streams, progress reports "model produced N characters" or "deep thinking (N characters so far)" every 20 seconds, so a thinking phase no longer looks like a deadlock
- **Reasoning effort adapts to the note style**: on DeepSeek-compatible channels `auto` now picks a higher thinking level for Detailed/Faithful notes and a medium one for Concise summaries; the task log prints the effective level (more tokens consumed — pin a level explicitly under "Reasoning effort" if you prefer)
- **No more hard-coded vendor parameters**: thinking is controlled with the standard `reasoning_effort` (the private `thinking` field is only used to disable thinking on the official DeepSeek API), and when a channel rejects a parameter VideoToNo degrades automatically (`reasoning_effort` → `reasoning.effort` → send nothing; an over-large `max_tokens` also falls back), remembering the outcome instead of failing the task

<details>
<summary>Previous releases (v1.2.2 and earlier)</summary>

- **v1.2.2**: Agent Skill integration (`skills/video-to-note/`, one-sentence note generation), Windows tray notifications for task results, rewritten repo and docs positioning
- **v1.2.1**: immediate cancellation across all stages (LLM streaming/download hooks/model download per-chunk), upload limit 2 GB + auto audio extraction for large videos, Whisper base model bundle
- **v1.2.0**: Bilibili multi-part support, per-segment Whisper cancellation, false-positive SESSDATA fix, manual Whisper model import UI
- **v1.1.8**: run-mode & app-version service isolation, recent-task scrollbar, faithful-note metadata de-duplication, custom Base URL diagnostics
- **v1.1.7**: single public Douyin links, browser-verification fallback, transcript reuse, and cancelled-task deletion.
- **v1.1.6**: persistent Whisper download confirmation and packaged custom-workspace fixes.
- **v1.1.5**: stale-page, MCP SSE, and startup-warning fixes.
- **v1.1.4**: note metadata, tray update checks, more reliable Bilibili downloads, and Whisper fallback improvements.
- **v1.1.3 and earlier**: Bilibili 412 handling, LLM connection testing, MCP integration, long-video chunking, task recovery, and multi-format exports.

</details>

## 🖼️ Screenshots

### ⚙️ Configuration

<p align="center"><img src="sources/preview.png" alt="VideoToNo configuration and video submission interface" width="100%"></p>

### ⏳ Processing

<p align="center"><img src="sources/process.png" alt="VideoToNo task progress and runtime log" width="100%"></p>

### 📝 Generated note

<p align="center"><img src="sources/output-public.png" alt="Detailed video notes generated by VideoToNo" width="100%"></p>

## 🚀 Portable build (recommended)

No Python or development setup is required. Download `VideoToNo-1.2.3-portable.exe` from the [latest Release](https://github.com/like-attract/video-to-note/releases/latest):

1. Download and double-click the exe;
2. Wait for the local page to open in your browser;
3. Enter a provider, model, and API key, then paste a video URL or upload a local file;
4. Generate and review your note.

The portable build starts the local service and stays in the system tray. The first Whisper transcription downloads a model, so keep the network available. Tasks, transcripts, screenshots, and notes are stored under `workspace/` next to the exe by default.

## ✨ Highlights

- 🎥 **Flexible inputs**: Bilibili, Douyin, YouTube, other URLs supported by the installed `yt-dlp`, and local audio/video files.
- 🇨🇳 **Bilibili integration**: fetches Bilibili AI captions after sign-in, with QR-code credential import in the UI; falls back to the open API when the video page is risk-controlled.
- 🎵 **Douyin single-link integration**: handles public share links directly and can retry after manual verification in an isolated local browser.
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

- Bilibili, Douyin, YouTube, and other `http` / `https` video URLs that the installed `yt-dlp` can parse;
- single public Douyin share links, with an isolated local browser available for sign-in or verification when anonymous parsing is restricted;
- local `.mp3`, `.m4a`, `.wav`, `.flac`, `.aac`, `.mp4`, `.mkv`, `.mov`, `.webm`, and `.avi` files;
- iQiyi and Tencent Video are not specially adapted, so support depends on access permissions, caption availability, and `yt-dlp` behavior.

## ⚙️ Use and configure

After starting the app:

1. Pick a model profile and enter an API key: DeepSeek / OpenAI / GLM / Qwen / Kimi each remember their own model, and custom OpenAI-compatible endpoints can be saved as several named profiles — switching profiles never overwrites another one's settings;
2. Choose a note style, reasoning effort, and Whisper model;
3. Paste a video URL or upload a local file;
4. Preview, copy, or download the generated note.

Profiles, recognition settings and the theme are non-sensitive and are remembered automatically in your local browser. An API key stays in the current page only, and is lost on refresh until you press **Save to this machine**; that writes it to `workspace/llm_keys.json`, encrypted with Windows' built-in DPAPI (a copy of the file on another machine or Windows account cannot be decrypted) — other platforms store it in plaintext and the interface says so. **A saved key is bound to its endpoint address**: it is only reused when the target address matches, never sent to another gateway. QR-login Bilibili cookies still live only in the local process unless you explicitly save them; do not share those workspace files.

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
| `summarize_video` | Submit a video summarization task; URL, API key, model, and Bilibili credentials can be omitted when saved local config is available for that endpoint |
| `wait_for_task` | Wait for a terminal task state for up to 45 seconds per call and return the note when complete; use it instead of aggressive polling |
| `get_task_status` | Inspect intermediate task progress |
| `list_whisper_models` | Show local Whisper model cache status |
| `save_llm_config` | Save the provider, model, and API key for one endpoint address locally (encrypted on Windows, plaintext with a visible note elsewhere) so calls to that address need no key |
| `save_bilibili_credentials` | Save Bilibili credentials such as SESSDATA for automatic use with Bilibili videos |
| `list_llm_keys` | List the endpoint addresses whose keys are saved locally (masked values and storage algorithm only) |
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

> Privacy: `save_llm_config` (and the web page's **Save to this machine**) writes API keys to `workspace/llm_keys.json`, encrypted per Windows user with the system DPAPI — copying that file to another machine or another Windows account makes it undecryptable and you must re-enter the key. Non-Windows platforms have no DPAPI, so keys are stored in plaintext and the interface says so. A stored key is bound to the endpoint address it was saved for and is only reused when that address matches. `save_bilibili_credentials` still writes plaintext to `workspace/bili_credentials.json`. Nothing is persisted unless you explicitly call a save tool; `workspace/` is excluded by `.gitignore` and is not committed to this repository — do not share those files.

</details>

<details>
<summary>🧩 Agent Skill (advanced)</summary>

Besides MCP, VideoToNo also ships an **Agent Skill** (`skills/video-to-note/`) for skill-capable coding agents such as Claude Code and pi: the agent reads `SKILL.md`, then drives the bundled script through the full flow — submit a task, stream progress, and fetch the finished note. **Start VideoToNo first**; like MCP, tasks run through the local backend.

### Install

Copy the whole `skills/video-to-note/` directory from this repo into your agent's skills directory, for example:

```text
C:\Users\<you>\.agents\skills\video-to-note\        # pi / common convention
~/.claude/skills/video-to-note/                       # Claude Code
```

### Usage

After installing, just ask in one sentence:

```text
Turn this Bilibili video into notes: https://www.bilibili.com/video/BV1xx
```

The agent invokes the bundled `scripts/video_note.py`, which auto-detects the service port, submits the task, streams runtime logs, and prints (or saves) the full Markdown note. Common options: `--style detailed|faithful|concise`, `--wait seconds`, `--out file`; a local video file path works as input too.

> First use requires an LLM API key: the agent will ask for it — pass it via `--provider` / `--api-key`. If this machine has a saved key for exactly one endpoint address (web page **Save to this machine** or the MCP `save_llm_config` tool), omit them and that channel is reused.

</details>

<details>
<summary>❓ FAQ</summary>

### Why does the first transcription take so long?

`faster-whisper` downloads a model the first time that model is used. Download time depends on model size and network conditions. The local cache is reused later, but choosing another model can trigger another download.

Models are cached under `workspace/_model_cache/` by default. Set `WHISPER_CACHE_DIR` in `.env` to use another disk. Downloads use the **hf-mirror.com mirror** by default with resume and retry support; set `HF_ENDPOINT` to switch sources, for example `HF_ENDPOINT=https://huggingface.co` for the official host. If downloads still fail, check network connectivity, proxy settings, and write access to the cache directory.

The app disables Hugging Face's Xet download backend by default and uses regular HTTP downloads to avoid CAS reconstruction failures on some Windows networks. If the selected model is incomplete but a usable `base` model is already cached, the job falls back to `base` and records that decision in the task log rather than waiting indefinitely for a weight download.

### What if large models (medium and above) keep failing to download?

Large models are big (medium ≈ 1.5 GB) and prone to interruptions on unstable networks. Besides retrying (resume supported), you can import a model manually:

1. Pick the model under the transcription settings and click "**手动导入模型**" (Import model manually). The app opens the import folder (`workspace/_model_cache/manual/<model>/`);
2. Download the model's 4 files in a browser: `config.json`, `model.bin`, `tokenizer.json`, `vocabulary.txt` from `https://hf-mirror.com/Systran/faster-whisper-<model>/tree/main`;
3. Drop the 4 files into that folder unchanged. The app detects them within seconds and marks the model as cached.

Downloads and cached files are verified for integrity; corrupt leftovers from interrupted downloads are cleaned up and re-downloaded automatically.

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

### What about a 400 `unsupported_parameter` for `thinking` / `reasoning_effort`?

OpenAI-compatible channels differ a lot in how thinking is controlled: many only accept the standard `reasoning_effort` and reject the vendor-specific `thinking` field with a 400. VideoToNo now **sends standard parameters by default** (the private `thinking` field is only used to disable thinking on the official DeepSeek API) and degrades automatically when a channel rejects a parameter: `reasoning_effort` → nested `reasoning.effort` → no parameter at all (model default), logging "this channel does not support the X parameter". The same applies to the output budget: if the `max_tokens` we raise for the thinking chain exceeds your gateway's cap, it falls back to the note length and finally omits the parameter. To skip the first doomed request, declare the capability up front via an environment variable (comma-separated; `thinking`, `reasoning_effort`, `reasoning`, `enable_thinking`):

```powershell
$env:VIDEOTONOTES_REJECTED_LLM_PARAMS = "thinking,reasoning_effort"
```

### Why does the task keep going when the Bilibili video page is unreachable?

In some networks the Bilibili video page is risk-controlled (`HTTP 412`) while the `api.bilibili.com` open endpoints still work. VideoToNo automatically falls back to those endpoints for metadata, audio and preview streams (multi-part videos included) and writes "fell back to the open API" into the run log, so you do not need to resubmit. Nothing here bypasses platform permissions: sign-in-only and VIP content still needs valid credentials.

### Why does progress sit on "deep thinking" for a while?

Detailed and Faithful notes default to a higher thinking level, so long videos can spend several minutes producing reasoning only. The UI refreshes "deep thinking (N characters so far)" or "model produced N characters" every 20 seconds, which means the task is still moving. Pick a lower level explicitly under Reasoning effort if you prefer speed or cost. Tasks can be cancelled at any time and transcripts are kept.

</details>

## 📄 License

This is a local personal-use tool released under the [MIT License](LICENSE).
