---
name: video-to-note
description: Generate structured, timestamped Markdown notes from videos (Bilibili, Douyin, YouTube, or local media files) using the local VideoToNo service. Use when the user asks to summarize a video, turn a video/lecture/talk into notes, extract video content or a transcript, or mentions VideoToNo.
---

# VideoToNo · 视频笔记生成

通过本机运行的 VideoToNo 服务，把视频变成带时间轴的 Markdown 笔记：优先读取平台字幕（B 站 AI 字幕深度适配，支持多分 P 合并），没有字幕时用本地 faster-whisper 离线转写，最后由已配置的大模型生成笔记。

## 先选路线

| 你要什么 | 走哪条 | 需要 API Key |
|---|---|---|
| 带时间轴的转录原料，笔记结构与风格你自己定 | **转录路线** `--transcript-only` | **不需要** |
| 一份现成的成品笔记（用户只要"给我笔记"，或视频很长希望后台跑完） | 笔记路线 `--style` | 需要（本机按接口地址保存过即可省略） |

你自己就有模型可以写笔记时，**优先走转录路线**：本机只负责取字幕或离线转写，写作由你完成，不用向用户索要 Key，也不会被套进固定的笔记模板。

## 第 0 步：确认服务在运行

服务监听 `127.0.0.1` 的 8000-8019 中的一个端口。逐个探测健康检查：

```bash
curl -s --max-time 2 http://127.0.0.1:8000/api/health
# 期望返回 {"status":"ok","service":"VideoToNo",...}
```

全部端口不通时：请用户启动 VideoToNo（便携版 exe，或源码目录执行 `python launcher.py`），启动后重试。不要替用户猜端口以外的地址。

## 推荐方式：用附带脚本一条命令完成

```bash
# 转录路线（默认推荐，零配置）
python "<本技能目录>/scripts/video_note.py" "<视频链接或本地文件路径>" --transcript-only --wait 1800

# 笔记路线（一键成品）
python "<本技能目录>/scripts/video_note.py" "<视频链接或本地文件路径>" --style detailed --wait 1800
```

脚本会自动：探测服务端口 → （本地文件先上传）→ 提交任务 → 轮询进度（实时打印运行日志）→ 输出完整 Markdown。

- `--transcript-only`：只做到转录为止，不调用大模型；此模式**默认复用同链接已有的转录**（秒回），加 `--no-reuse` 才强制重新转写
- `--style`：`detailed`（翔实+点评，默认）/ `faithful`（忠实复原）/ `concise`（精简摘要）
- `--wait`：最长等待秒数，默认 1800；长视频（>30 分钟）建议加大
- `--out <path.md>`：把结果写入文件（不加则打印到 stdout）
- 服务未运行、API Key 未配置、任务失败时都会给出明确的中文提示，按提示向用户询问即可

## 需要用户提供的信息

- **API Key**：**只有笔记路线需要**。本机为某个接口地址保存过 Key（网页端「保存到本机」或 MCP 的 `save_llm_config`）时可不传；只存过一个地址时脚本会自动沿用该通道，存了多个则不会猜。任务失败提示"未提供 API Key"时会点名具体接口地址，此时向用户询问供应商（deepseek/openai/qwen/glm/moonshot/custom）和 Key，用 `--provider` / `--api-key`（custom 另加 `--base-url` / `--custom-model`）重新提交。已保存的 Key 只在目标地址一致时复用，不会被发给别的网关。用户不想另配 Key 时，改走转录路线。
- 本地文件上传上限 2GB；大视频（默认 ≥300MB）未要求截图时服务端会自动只保留音频。

## 手动走 API（需要自定义流程时）

1. 健康检查：`GET /api/health`
2. 提交任务：

   ```bash
   # 转录路线：请求体里没有任何大模型字段
   curl -s -X POST http://127.0.0.1:8000/api/transcribe \
     -H "Content-Type: application/json" \
     -d '{"video_url": "https://www.bilibili.com/video/BVxxxx", "whisper_model": "base"}'

   # 笔记路线
   curl -s -X POST http://127.0.0.1:8000/api/summarize \
     -H "Content-Type: application/json" \
     -d '{
       "video_url": "https://www.bilibili.com/video/BVxxxx",
       "summary_style": "detailed",
       "llm_config": {"model_type": "deepseek", "api_key": "sk-..."}
     }'
   # 都返回 {"task_id": "..."}；本地文件改为先 POST /api/upload 拿 upload_task_id
   ```

3. 轮询：`GET /api/task/{task_id}`，直到 `status` 变为 `completed` / `failed` / `cancelled`（`logs` 数组是实时运行日志）。转录任务的 `result` 里没有 `markdown`，`result.output` 是 `"transcript"`
4. 取结果：
   - 笔记路线：`result.markdown` 是完整笔记
   - 转录路线：`GET /api/task/{task_id}/transcript?output_format=markdown` 拿整篇 `[MM:SS-MM:SS] 正文`，`output_format=json` 拿分段数组（时间为秒）。该端点默认 `json`，MCP 的 `get_transcript` 默认 `markdown`，两边都建议显式传
   - 两条路的 `result.output_directory` 都是产物目录（`notes.md` / `transcript.json` / `transcript.md`）；**超长内容建议直接读该目录下的 `transcript.json` 自行切片**，不必整篇塞进上下文
5. 取消运行中的任务：`POST /api/task/{task_id}/cancel`（秒级生效）

## 注意事项

- 服务只监听本机回环地址，外部机器无法访问；这是设计使然（隐私边界）
- 默认同时只处理 1 个任务，不要并行提交多个
- 任务产物保留在 workspace 下，重复提交同一在线链接会自动复用已有转录（更快、更省 token）
- 已经走完转写的历史任务都能用 `/api/task/{id}/transcript` 取转录，**包括后来生成笔记失败的**——不必重跑
