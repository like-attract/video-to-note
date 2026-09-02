<p align="center">
  <img src="sources/icon.png" width="96" alt="VideoToNo icon">
</p>

<h1 align="center">VideoToNo v1.3.5</h1>

<p align="center"><em>Turn videos into Markdown notes you can revisit</em></p>

<p align="center">🌐 <a href="README.md">简体中文</a> · <a href="README_EN.md">English</a></p>

<p align="center"><span style="white-space: nowrap;"><a href="https://github.com/like-attract/video-to-note/releases/latest"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/v/release/like-attract/video-to-note?display_name=tag&style=flat-square&label=release&color=2563eb" alt="Latest release"></a>&nbsp;<a href="LICENSE"><img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/github/license/like-attract/video-to-note?style=flat-square&label=license&color=22c55e" alt="MIT License"></a>&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/frontend-Vanilla%20JS-E34F26?style=flat-square&logo=javascript&logoColor=white" alt="Frontend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/backend-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="Backend">&nbsp;<img style="display: inline-block; vertical-align: middle;" src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11"></span></p>

<p align="center"><a href="https://github.com/like-attract/video-to-note/releases/latest"><strong>⬇️ Download the Windows portable build</strong></a></p>

<p align="center">🎬 Promo video (Bilibili): <a href="https://www.bilibili.com/video/BV1Qwby6DEu1/">https://www.bilibili.com/video/BV1Qwby6DEu1/</a> &nbsp;·&nbsp; 👥 QQ group: <code>739200648</code></p>

VideoToNo turns the **"video → structured notes" pipeline** into a local-first service: give it a Bilibili, Douyin, YouTube link or a local video; it prefers platform captions including Bilibili AI captions, falls back to local faster-whisper transcription when none exist, then has your chosen LLM write timestamped Markdown notes. 

> 🤖 **Agent Skill available**: copy the `skills/video-to-note/` directory from this repo into your agent's skills directory (e.g. `~/.agents/skills/`) and coding agents like Claude Code or pi can generate video notes from a single sentence. See `SKILL.md` inside that directory.

## 🆕 What's New (v1.3.0 → v1.3.5)

- **Transcripts without an API key**: the interface has a new output type, **Transcript only**. Note style and reasoning settings collapse, the task reads platform captions or runs local Whisper, and **no LLM is called anywhere — so this machine needs no key at all**. The timestamped transcript is kept in *Recent tasks* (tagged *Transcript*) so you can reopen, copy, or download it later, and you can still switch that task to the note route without re-running the transcription
- **Once an agent is connected, the organising is up to you**: the same transcript exit is open to agents (MCP `transcribe_video` + `get_transcript`, Skill `--transcript-only`). After it has the timestamped text, loose follow-up instructions all work — "go deep on section X", "merge the repetition and fill the gaps", "add background the video skipped" — the structure is never dictated by this app
- **Transcripts of past tasks can be taken directly**: availability is decided by the transcript file, not by task status — **tasks whose note generation failed still yield their transcript**, with no re-download and no re-transcription
- **Local videos no longer fail on first run**: the page used to submit the backend's own `input.mp4` path as if it were a link, which was rejected as an unrecognised URL, so the first attempt always failed and only "retry" worked (present since v1.2.1, not a regression from this batch). Submissions now carry the upload task id; no duplicate `input` task is created, and history and note titles use your original file name
- **The 2 GB upload limit is really in place**: the backend allowed 2048 MB while the page still checked 500 MB; the limit now comes from `/api/health`, so the picker and the copy follow the server and `MAX_UPLOAD_MB` in `.env` moves both
- **GPU transcription falls back to CPU instead of failing**: on machines with a working driver but missing cuBLAS/cuDNN (`Library cublas64_12.dll is not found`), transcription no longer dies at stage 4 — it continues on CPU and records the reason plus how to fix it in the run log, and later tasks in the same process skip the doomed GPU init
- **"Pending" tasks can finally be deleted**: tasks uploaded but never started had no delete control and came back after a restart, permanently crowding the 20 most recent tasks

<details>
<summary>Previous releases (v1.3.0 and earlier)</summary>

- **v1.3.0**: API keys and Bilibili credentials can be **saved to this machine per endpoint address** (DPAPI-encrypted on Windows, reused only when the target address matches, with a key-state chip beside the input); model profiles each remember their own model and custom endpoints become named profiles with autosave; cancellation now reaches *Cancelled* instantly in every stage (measured 18.6s → 0.02s); fixed model IDs bleeding across profiles and early failures leaving tasks in `processing`; MCP gained `list_llm_keys`, `save_llm_config` labels, and `endpoints` / `key_storage` in `get_saved_config`.
- **v1.2.3**: Bilibili notes survive risk control (automatic fallback to `api.bilibili.com` open endpoints for metadata, audio and preview streams across multi-part videos, Issue #1), three guardrails against the "Faithful" style hanging in generation, generation progress heartbeat, reasoning effort adapting to the note style, and automatic degradation when a channel rejects a thinking parameter
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

No Python or development setup is required. Download `VideoToNo-1.3.5-portable.exe` from the [latest Release](https://github.com/like-attract/video-to-note/releases/latest):

1. Download and double-click the exe;
2. Wait for the local page to open in your browser;
3. Paste a video URL or upload a local file — for a finished note, enter a provider, model, and API key first; **for a timestamped transcript only, choose "Transcript only" and enter nothing**;
4. Generate and review the note or the transcript.

The portable build starts the local service and stays in the system tray. The first Whisper transcription downloads a model, so keep the network available. Tasks, transcripts, screenshots, and notes are stored under `workspace/` next to the exe by default.

## ✨ Highlights

- 🎥 **Flexible inputs**: Bilibili, Douyin, YouTube, other URLs supported by the installed `yt-dlp`, and local audio/video files.
- 🇨🇳 **Bilibili integration**: fetches Bilibili AI captions after sign-in, with QR-code credential import in the UI; falls back to the open API when the video page is risk-controlled.
- 🎵 **Douyin single-link integration**: handles public share links directly and can retry after manual verification in an isolated local browser.
- 🧾 **Real timestamps**: keeps segment start and end times from captions or Whisper instead of asking the model to invent them.
- 🧠 **Long-transcript handling**: chunks, summarizes, and reduces long material while keeping context pressure under control.
- 🖼️ **Multiple exports**: Markdown, HTML, JSON, plain text, and PNG, with optional video screenshots as note attachments.
- 🤖 **Raw material for agents**: a connected agent can stop at the timestamped transcript — no LLM call and no API key for this machine, and it writes the notes in its own structure and style.
- 🛡️ **Local-first**: media downloads, transcription, and file generation happen locally; the service listens on `127.0.0.1` by default.

## 🔁 Processing pipeline

```text
Video URL / local file
        ↓
Platform captions (Bilibili AI captions first)
        ↓ when unavailable
Local faster-whisper transcription
        ↓
Timestamped transcript ──────→ handed to an agent to write its own notes (no LLM, no API key)
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

1. Choose the output type: **Note** (needs an API key) or **Transcript only** (a timestamped transcript and nothing else — no LLM is called, **no API key needed**, and the note-style and screenshot settings collapse);
2. For notes, pick a model profile and enter an API key: DeepSeek / OpenAI / GLM / Qwen / Kimi each remember their own model, and custom OpenAI-compatible endpoints can be saved as several named profiles — switching profiles never overwrites another one's settings;
3. Choose a note style, reasoning effort, and Whisper model;
4. Paste a video URL or upload a local file;
5. Preview, copy, or download the result. A transcript stays in *Recent tasks* (tagged *Transcript*) for later review and download, and "Write notes from this transcript" switches it to the note route without re-running the transcription.

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

VideoToNo includes an MCP (Model Context Protocol) server, so AI clients such as Cherry Studio and Codex can turn a video link into a timestamped transcript or a note directly. **Start VideoToNo first**: MCP tasks run through the local backend and reuse its Whisper model cache and task directories.

Pick a route: if your client already has a capable model, use `transcribe_video` + `get_transcript` to take the timestamped transcript only — **no API key for this machine**, and the client decides the note structure and style. Reach for `summarize_video` when a finished note is wanted in one shot (or for a long video best processed in the background).

The recommended pattern is **get the transcript first, let the client do the organising**: this service only supplies the text, so reading order, judgement, and the final structure are up to the model on your side and your follow-up instructions can stay loose:

```text
Transcribe this video into timestamped text first: https://www.bilibili.com/video/BV1xx
Then organise it as you see fit: go deep on the XXX part, merge what repeats,
fill in the skipped derivations, add background where the video was thin,
and give me something readable at the end.
```

Re-structuring, digging into a single section, or asking follow-up questions while it writes all work, and none of it needs a key on this machine or applies a fixed note template. Use `summarize_video` when you want a ready-made standard note instead (it needs a key and follows the selected style).

Available tools:

| Tool | Description |
|---|---|
| `summarize_video` | Submit a video summarization task; URL, API key, model, and Bilibili credentials can be omitted when saved local config is available for that endpoint |
| `transcribe_video` | Stop at the timestamped transcript: no LLM call anywhere in this route, so no API key is needed |
| `get_transcript` | Fetch a task's transcript (full markdown or JSON segments); works for historical tasks whose note generation later failed |
| `wait_for_task` | Wait for a terminal task state for up to 45 seconds per call — use it instead of aggressive polling. Note tasks return the finished note; transcript-only tasks return status plus a pointer to `get_transcript` |
| `get_task_status` | Inspect intermediate task progress |
| `list_whisper_models` | Show local Whisper model cache status |
| `save_llm_config` | Save the provider, model, and API key for one endpoint address locally (encrypted on Windows, plaintext with a visible note elsewhere) so calls to that address need no key |
| `save_bilibili_credentials` | Save Bilibili credentials such as SESSDATA for automatic use with Bilibili videos, sealed with the same local encryption as API keys (encrypted on Windows, plaintext with a visible note elsewhere) |
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

```text
Just give me a timestamped transcript of this video, I'll write my own notes: https://www.bilibili.com/video/BV1xx
```

The second phrasing sends the client down `transcribe_video` + `get_transcript`, which needs no API key saved on this machine.

### Codex CLI

For stdio clients such as Codex:

```bash
codex mcp add local videotono -- python -m backend.mcp_server
```

The MCP server automatically scans ports 8000–8019 to locate a running VideoToNo service. Set `VIDEOTONOTES_BACKEND_URL` to explicitly choose the backend URL.

> Privacy: `save_llm_config` (and the web page's **Save to this machine**) writes API keys to `workspace/llm_keys.json`, encrypted per Windows user with the system DPAPI — copying that file to another machine or another Windows account makes it undecryptable and you must re-enter the key. Non-Windows platforms have no DPAPI, so keys are stored in plaintext and the interface says so. A stored key is bound to the endpoint address it was saved for and is only reused when that address matches. `save_bilibili_credentials` seals SESSDATA and bili_jct with the same envelope into `workspace/bili_credentials.json` (also bound to the current Windows account, so re-save it after moving machines; `buvid3` is a device identifier the client already sends in the clear, so it stays plaintext). Nothing is persisted unless you explicitly call a save tool; `workspace/` is excluded by `.gitignore` and is not committed to this repository — do not share those files.

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

```text
Transcribe this video with timestamps, then organise it as you see fit:
go deep on XXX, merge what repeats, add background the video skipped,
and save the result as notes.md: https://www.bilibili.com/video/BV1xx
```

The agent invokes the bundled `scripts/video_note.py`, which auto-detects the service port, submits the task, streams runtime logs, and prints (or saves) the result. The second phrasing takes the `--transcript-only` route: no API key on this machine, and everything after the transcript — judgement, additions, structure — is the agent's call, so the instruction does not need to be precise. Common options: `--transcript-only` (timestamped transcript only, no LLM call), `--style detailed|faithful|concise`, `--wait seconds`, `--out file`; a local video file path works as input too.

> **When the agent has its own model, prefer `--transcript-only`**: that route never calls an LLM, so it needs no API key and the agent keeps full control over note structure and style. Only the finished-note route needs an LLM API key: the agent will ask for it — pass it via `--provider` / `--api-key`. If this machine has a saved key for exactly one endpoint address (web page **Save to this machine** or the MCP `save_llm_config` tool), omit them and that channel is reused.

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
