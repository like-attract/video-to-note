import asyncio
import io
import json
import time
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

import launcher
from backend import bili_login, douyin_login, main
from backend.config_store import BILI_CREDENTIALS_FILE, ConfigStore
from backend.llm_summarizer import LLMSummarizer, utf8_safe
from backend.transcript import TranscriptSegment
from backend.video_processor import (
    BiliPage,
    BiliSubtitleOutcome,
    BiliVideoInfo,
    SubtitleResult,
    VideoProcessor,
)


@pytest.mark.asyncio
async def test_task_notify_hook_fires_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任务成功时触发系统通知回调；回调抛异常不影响任务状态。"""
    processor = VideoProcessor(tmp_path)
    notified: list[tuple[str, str]] = []

    async def fake_info(*args, **kwargs):
        return {"title": "通知测试", "source": "youtube", "duration": 30, "owner": "作者"}

    async def fake_subtitles(*args, **kwargs):
        return SubtitleResult(
            [TranscriptSegment(0, 5, "内容")], "zh", "platform_subtitle"
        )

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass
        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(self, *args, **kwargs):
            return "# 笔记"

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)

    def good_hook(title: str, message: str) -> None:
        notified.append((title, message))

    def bad_hook(title: str, message: str) -> None:
        raise RuntimeError("通知通道坏了")

    main.register_task_notify(good_hook)
    main.register_task_notify(bad_hook)  # 最后注册者生效，但它抛异常也不能弄挂任务
    try:
        task_id = "notify-task"
        (tmp_path / task_id).mkdir()
        main.tasks[task_id] = main.new_task()
        request = main.SummarizeRequest(
            video_url="https://www.youtube.com/watch?v=abc",
            llm_config=main.LLMConfig(model_type="deepseek", api_key="test-key"),
        )
        await main.process_video_task(task_id, request)
        assert main.tasks[task_id]["status"] == "completed"
    finally:
        main._task_notify_hook = None

    # good_hook 被覆盖未触发，但 bad_hook 被调用且异常被吞：验证通知通道被触发过
    # （bad_hook 抛错由 notify_task 内部捕获）——用 good_hook 单独再验证一次内容
    main.register_task_notify(good_hook)
    main.notify_task("任务完成", "视频笔记已生成：通知测试")
    for _ in range(50):
        if notified:
            break
        time.sleep(0.05)
    main._task_notify_hook = None
    assert notified == [("任务完成", "视频笔记已生成：通知测试")]


@pytest.mark.asyncio
async def test_early_credential_failure_still_reaches_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读凭据在流水线最前面就失败：终态必须落盘，日志要点名凭据而不是漂到风控上。"""
    bili_file = tmp_path / BILI_CREDENTIALS_FILE
    store = ConfigStore(tmp_path)
    store.save_bili_credentials({"sessdata": "s1", "bili_jct": "j1", "buvid3": "b1"})
    record = json.loads(bili_file.read_text(encoding="utf-8"))
    record["sessdata"]["alg"] = "aes-256-gcm-v2"  # 假装这份文件来自别的机器
    bili_file.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(main, "config_store", store)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)

    task_id = "bili-cred-failed"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx",
        llm_config=main.LLMConfig(model_type="deepseek", api_key="test-key"),
    )
    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "failed"
    assert "凭据无法解密" in task["error"]
    assert any(line.startswith("处理失败：") for line in task["logs"])
    # 终态通知要用 title：早期失败时它若没有值，except 分支自己就抛错，task.json 停在 processing
    persisted = json.loads((tmp_path / task_id / "task.json").read_text(encoding="utf-8"))
    assert persisted["runtime"]["status"] == "failed"
    assert "凭据无法解密" in persisted["runtime"]["error"]


def test_health_and_frontend_are_served() -> None:
    client = TestClient(main.app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["version"] == "1.4.6"
    assert health.json()["version"] == launcher.VERSION
    assert health.json()["service"] == "VideoToNo"
    assert health.json()["mode"] == "dev"  # 测试进程非打包；打包版应报 portable
    # 前端 MCP 卡片靠这个字段决定"给地址"还是"说本机没装依赖"
    assert health.json()["dependencies"]["mcp_sse"] is True
    page = client.get("/")
    assert page.status_code == 200
    assert "VideoToNo" in page.text
    assert 'id="mcpSseUrl"' in page.text
    assert 'id="mcpStatusChip" class="mcp-chip" type="button" hidden' in page.text
    assert 'id="mcpHint" class="hint-bar info" hidden' in page.text
    assert 'id="copyMcpConfigBtn"' in page.text
    assert 'id="mcpUnavailable" class="mcp-note" hidden' in page.text
    assert "cdn.jsdelivr.net" not in page.text
    assert "vendor/marked-18.0.9.umd.js" in page.text
    assert "vendor/dompurify-3.4.13.min.js" in page.text
    assert "vendor/html2canvas-1.4.1.min.js" in page.text
    assert 'id="recentTaskList"' in page.text
    assert 'id="copyNoteBtn"' in page.text
    assert 'value="png"' in page.text
    assert 'class="brand-mark" src="/icon.png"' in page.text
    assert "记住 LLM 配置" not in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    favicon = client.get("/favicon.ico")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/x-icon")


def test_any_local_request_counts_as_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    """无头实例靠"还有没有人请求"决定收摊，这个计数由安全中间件顺手记下来。

    闲置判定若漏掉任何一条本机请求，agent 后台拉起的服务就会在用户还在用时自己关掉。
    """
    monkeypatch.setattr(main, "_LAST_REQUEST_MONO", time.monotonic() - 9999)
    assert main.idle_seconds() > 9000

    TestClient(main.app).get("/api/health")

    assert main.idle_seconds() < 60


def test_has_active_task_only_counts_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """已完成的记录留在内存里，不能把闲置的服务钉成永远不退出。"""
    monkeypatch.setattr(
        main, "tasks", {"a": {"status": "completed"}, "b": {"status": "failed"}}
    )
    assert not main.has_active_task()

    monkeypatch.setattr(main, "tasks", {"a": {"status": "completed"}, "b": {"status": "processing"}})
    assert main.has_active_task()


def test_missing_frontend_page_explains_itself() -> None:
    """界面文件读不到时不能只甩一句 Not Found——那看着就像端口被别的服务占了。

    打包版是 onefile：解压出来的临时目录被安全软件或磁盘清理删掉之后，`/api/*` 全部
    正常、`/` 却匹配不到任何路由（群测里那位"重启软件就好了"就是这个）。
    """
    client = TestClient(main.app)
    missing = client.get("/no-such-page.html")

    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("text/html")
    assert "Not Found" not in missing.text
    # 用户要的是"下一步做什么"，不是我们的排查过程：动作必须排在解释前面
    assert "退出" in missing.text
    assert missing.text.index("这样做就好") < missing.text.index("界面文件")
    assert "_app.log" in missing.text

    # 接口路径不能被这条改写：调用方按 JSON 的 detail 判错
    api = client.get("/api/no-such-endpoint")
    assert api.status_code == 404
    assert api.json()["detail"] == "Not Found"
    # 我们自己抛的 404 仍要带原来的中文说明
    gone = client.get("/api/task/does-not-exist")
    assert gone.status_code == 404
    assert "任务不存在" in gone.json()["detail"]
    app_icon = client.get("/icon.png")
    assert app_icon.status_code == 200
    assert app_icon.headers["content-type"].startswith("image/png")


def test_html_entry_is_no_store_and_assets_no_cache() -> None:
    """旧 HTML 与新 JS 混用会导致按钮全部失效，入口页必须禁止缓存。"""
    client = TestClient(main.app)
    page = client.get("/")
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["content-security-policy"].startswith("default-src 'self'")
    # 版本号是否需要 bump 由 test_frontend_hygiene 守；这里只查缓存头，故忽略查询串。
    for asset in ("/style.css", "/script.js", "/theme-bootstrap.js"):
        response = client.get(asset)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"


def test_frontend_assets_survive_a_broken_os_mime_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """操作系统把 .js 的类型说错时，界面不能整体失活。

    Windows 上 mimetypes 优先读注册表 HKCR\\<扩展名> 的 "Content Type"，某些软件会
    把 .js 写成 text/plain；本服务带 nosniff，类型一错浏览器就拒绝执行脚本，用户看到
    的是"页面正常但按钮全废、主题锁死暗色"，且只在注册表被动过的那台机器上出现。
    自己资源的类型由代码钉死，不交给操作系统猜。
    """
    import starlette.responses

    monkeypatch.setattr(
        starlette.responses, "guess_type", lambda *args, **kwargs: ("text/plain", None)
    )
    client = TestClient(main.app)
    for asset in ("/script.js", "/theme-bootstrap.js", "/vendor/marked-18.0.9.umd.js"):
        response = client.get(asset)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/javascript"), asset
    assert client.get("/style.css").headers["content-type"].startswith("text/css")
    # 入口页的 no-store 判的是钉完类型之后的头，顺序被挪走这条就会红
    page = client.get("/")
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"

    # 重校验命中 304 时也要带上正确类型：浏览器会用这次响应的头覆盖缓存里那份，
    # 否则已经存下 text/plain 的缓存条目靠 304 能一直坏下去。
    fresh = client.get("/script.js")
    stale = client.get(
        "/script.js",
        headers={"if-none-match": fresh.headers["etag"]},
    )
    assert stale.status_code == 304
    assert stale.headers["content-type"].startswith("text/javascript")


def test_remote_clients_are_rejected() -> None:
    """纯 ASGI 安全中间件：非回环客户端一律 403（桌面应用仅本机可访问）。"""
    client = TestClient(main.app, client=("203.0.113.9", 4321))
    health = client.get("/api/health")
    assert health.status_code == 403
    page = client.get("/")
    assert page.status_code == 403
    assert page.json()["detail"] == "VideoToNo 仅允许本机访问"


def test_frontend_sanitizes_generated_markdown() -> None:
    script = (main.FRONTEND_DIR / "script.js").read_text(encoding="utf-8")
    assert "DOMPurify.sanitize" in script
    assert "content.innerHTML = marked.parse" not in script
    assert "const body = renderMarkdownBase(markdown);" in script
    assert "const body = typeof marked" not in script
    assert "persistApiKeyIfRequested" not in script


def test_frontend_whisper_confirm_dedup_logic() -> None:
    """Whisper 未缓存确认的降噪逻辑：字幕链路跳过、会话内只确认一次、任务后自动刷新状态。"""
    script = (main.FRONTEND_DIR / "script.js").read_text(encoding="utf-8")
    assert "taskWillLikelyUseSubtitles" in script
    assert "whisper_confirm_" in script
    assert "localStorage" in script
    assert "!whisperDownloadConfirmed(modelId)" in script
    assert "rememberWhisperDownloadConfirm(modelId)" in script
    assert "refreshWhisperModelHints();" in script


def test_whisper_manual_folder_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """手动导入模型：创建并返回导入目录，模型 ID 校验，状态接口带 manual_dir。"""
    monkeypatch.setattr(main, "WHISPER_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        main.paraformer_transcriber, "download_root", tmp_path / "cache" / "sherpa"
    )
    opened: list[Path] = []

    def fake_open(path: Path) -> bool:
        opened.append(path)
        return True

    monkeypatch.setattr(main, "_open_in_file_manager", fake_open)
    client = TestClient(main.app)

    response = client.post(
        "/api/whisper-models/manual-folder", json={"model": "base", "open": False}
    )
    assert response.status_code == 200
    data = response.json()
    expected_dir = tmp_path / "cache" / "manual" / "base"
    assert data == {
        "folders": [
            {
                "label": "模型文件",
                "path": str(expected_dir),
                # 词表文件名各仓库不统一（large-v3 用的是 .json），引导里两个都列出来
                "files": [
                    "config.json",
                    "model.bin",
                    "tokenizer.json",
                    "vocabulary.txt 或 vocabulary.json",
                ],
                "download_url": "https://hf-mirror.com/Systran/faster-whisper-base/tree/main",
            }
        ],
        "open_path": str(expected_dir),
        "opened": False,
    }
    assert expected_dir.is_dir()
    # open=false 只取引导信息，不能先把用户的文件管理器窗口弹出来
    assert opened == []

    assert client.post(
        "/api/whisper-models/manual-folder", json={"model": "base"}
    ).json()["opened"] is True
    assert opened == [expected_dir]

    status = client.get("/api/whisper-models")
    assert status.status_code == 200
    assert status.json()["manual_dir"] == str(tmp_path / "cache" / "manual")

    invalid = client.post("/api/whisper-models/manual-folder", json={"model": "nope"})
    assert invalid.status_code == 422


def test_manual_folder_for_paraformer_lists_sherpa_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """paraformer-zh 是三份组件、且压根不读 manual/：照旧提示会让用户把文件放到程序不看的地方。"""
    sherpa = tmp_path / "cache" / "sherpa"
    monkeypatch.setattr(main.paraformer_transcriber, "download_root", sherpa)
    opened: list[Path] = []
    monkeypatch.setattr(main, "_open_in_file_manager", lambda path: opened.append(path) or True)

    data = TestClient(main.app).post(
        "/api/whisper-models/manual-folder", json={"model": "paraformer-zh", "open": False}
    ).json()

    assert [Path(folder["path"]).name for folder in data["folders"]] == ["asr", "punc", "vad"]
    assert data["folders"][0]["files"] == ["model.int8.onnx", "tokens.txt"]
    assert "csukuangfj/sherpa-onnx-paraformer-zh" in data["folders"][0]["download_url"]
    assert "标点模型" in data["folders"][1]["label"]
    # 三个目录都建好，但只开一个窗口（sherpa 根目录），不弹三次文件管理器
    assert all(Path(folder["path"]).is_dir() for folder in data["folders"])
    assert data["open_path"] == str(sherpa)
    assert opened == []


def test_note_metadata_and_footer_are_deterministic() -> None:
    info = {
        "owner": "作者",
        "upload_date": "20260821",
        "duration": 90,
        "view_count": 1234,
        "like_count": 56,
    }
    body = main.add_note_header_metadata("# 视频笔记：《测试》\n\n正文", "测试", info)
    assert "> UP主：作者 · 发布时间：2026-08-21" in body
    assert "时长：01:30" in body
    footer = main.append_note_footer(
        body, "https://www.bilibili.com/video/BV1xx", "deepseek", "deepseek-v4-flash", "faithful"
    )
    assert "来源：https://www.bilibili.com/video/BV1xx" in footer
    assert "生成模型：deepseek · deepseek-v4-flash" in footer
    assert "笔记风格：详细复原（仅视频内容）" in footer


def test_note_metadata_removes_model_duplicate_block() -> None:
    info = {
        "owner": "作者",
        "upload_date": "20260821",
        "duration": 90,
        "like_count": 11780,
    }
    summary = (
        "# 视频笔记：《测试》\n\n"
        "作者：作者｜发布时间：2026-08-21｜时长：01:30｜文字来源：faster_whisper\n\n"
        "正文内容"
    )

    body = main.add_note_header_metadata(summary, "测试", info)

    assert body.count("发布时间：2026-08-21") == 1
    assert body.count("时长：01:30") == 1
    assert "文字来源：faster_whisper" not in body
    assert body.endswith("正文内容")


def test_recent_tasks_are_compact_and_do_not_include_markdown(monkeypatch) -> None:
    task = main.new_task(status="completed", task_id="recent-task")
    task.update(
        created_at="2026-08-09T12:00:00+00:00",
        result={"title": "示例笔记", "markdown": "# private body"},
    )
    monkeypatch.setattr(main, "tasks", {"recent-task": task})

    response = TestClient(main.app).get("/api/tasks")

    assert response.status_code == 200
    item = response.json()["tasks"][0]
    assert item["task_id"] == "recent-task"
    assert item["title"] == "示例笔记"
    assert "result" not in item
    assert "markdown" not in json.dumps(item)


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("localhost", True),
        ("192.168.1.20", False),
        ("example.com", False),
    ],
)
def test_loopback_client_detection(host: str, allowed: bool) -> None:
    assert main.is_loopback_client(host) is allowed


def test_llm_stage_error_never_becomes_platform_text() -> None:
    """回归：B 站任务在生成笔记阶段被通道回 403，曾被说成「抖音媒体地址已过期，
    请重新提交链接」，用户照着提示重投了三次本来没问题的链接。"""
    message = main.friendly_task_error(
        "Error code: 403 - {'error': 'no permission'}",
        source=main.VideoSource.BILIBILI,
        step_name=main.NOTE_STEP_NAME,
    )
    assert "抖音" not in message
    assert "模型接口" in message


def test_platform_403_maps_to_the_platform_of_the_task() -> None:
    raw = "ERROR: Unable to download webpage: HTTP Error 403: Forbidden"
    assert "抖音" in main.friendly_task_error(
        raw, source=main.VideoSource.DOUYIN, step_name="下载媒体"
    )
    bilibili = main.friendly_task_error(
        raw, source=main.VideoSource.BILIBILI, step_name="下载媒体"
    )
    assert "B 站" in bilibili and "抖音" not in bilibili
    assert (
        main.friendly_task_error(raw, source=main.VideoSource.LOCAL, step_name="下载媒体")
        == raw
    )


def test_context_limit_error_says_which_window_to_widen() -> None:
    """回归：本地小模型（ollama 默认 num_ctx 只有 2048）跑长一点的视频时整条请求被通道拒掉，
    原样透传的英文报错让用户以为「程序里有个 token 限制可以去调」。"""
    for raw in (
        # ollama：下发的输出额度超过模型窗口
        "Error code: 400 - {'error': {'message': 'maximum context length is 2048 tokens, "
        "however you requested 4600 tokens', 'type': 'invalid_request_error'}}",
        # ollama / vLLM：提示词本身超窗，且带 OpenAI 系的错误码
        "Error code: 400 - {'error': {'message': 'prompt has 5234 tokens which exceeds the "
        "maximum context length (2048 tokens)', 'code': 'context_length_exceeded'}}",
        # Anthropic 系说法
        "Error code: 400 - {'error': {'message': 'prompt is too long: 200000 tokens > "
        "100000 maximum'}}",
        # 排序陷阱：这条含裸子串 "403"，若让权限分支先命中就成了「Key 没有该模型的权限」
        "Error code: 400 - {'error': {'message': \"This model's maximum context length is "
        '8192 tokens. However, you requested 9403 tokens\'}}',
    ):
        message = main.friendly_task_error(
            raw, source=main.VideoSource.LOCAL, step_name=main.NOTE_STEP_NAME
        )
        assert "上下文窗口" in message and "num_ctx" in message, raw


def test_rate_limit_is_not_reported_as_a_context_limit() -> None:
    """OpenAI 限流的原文就是 `Too many tokens per minute`，按 token 字样判会把限流说成
    窗口不够——照着提示去调 num_ctx 不会有任何改变（正是 1.4.0 修掉的那类张冠李戴）。"""
    raw = (
        "Error code: 429 - {'error': {'message': 'Too many tokens per minute, "
        "please wait 10 seconds before trying again', 'type': 'rate_limit_exceeded'}}"
    )
    message = main.friendly_task_error(
        raw, source=main.VideoSource.DOUYIN, step_name=main.NOTE_STEP_NAME
    )
    assert "限流" in message and "上下文" not in message


def test_scrubbed_error_hides_keys_but_keeps_the_status() -> None:
    assert (
        main.scrub_technical_error("Error code: 403 with key sk-abcdef12345\n second line")
        == "Error code: 403 with key sk-**** second line"
    )
    echoed = main.scrub_technical_error(
        'gateway said {"api_key": "tok_live_abcdefgh123", "authorization": Bearer '
        'Zm9vYmFyMTIzNDU2, "tokenizer": "tokenizer.json"}'
    )
    assert "tok_live_abcdefgh123" not in echoed
    assert "Zm9vYmFyMTIzNDU2" not in echoed
    # 字段名脱敏不能把正常词咬掉（tokenizer.json 是模型必需文件）
    assert "tokenizer.json" in echoed
    known = main.scrub_technical_error("upstream echoed the credential we sent", "abcdefg12345")
    assert "abcdefg12345" not in known
    assert known.startswith("upstream echoed")


def test_summarize_rejects_local_path_in_url_field() -> None:
    client = TestClient(main.app)
    response = client.post(
        "/api/summarize",
        json={
            "video_url": "D:/private/video.mp4",
            "llm_config": {"model_type": "deepseek", "api_key": "test-key"},
        },
    )
    assert response.status_code == 422


def test_summarize_accepts_uploaded_local_file_with_path_in_url_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """回归：网页端本地视频首跑必失败——上传后的 video_url 是后端目录里的文件路径，
    曾被链接校验判成「无法识别视频链接」422，点重试才走 resume 分支跑通。"""
    async def fake_run(task_id, request):
        return None

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "run_queued_video_task", fake_run)
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(main, "running_jobs", {})
    client = TestClient(main.app)
    upload = client.post(
        "/api/upload",
        files={"file": ("课程 第一讲.mp4", b"bits", "video/mp4")},
        data={"include_screenshots": "0"},
    ).json()

    response = client.post(
        "/api/summarize",
        json={
            "video_url": upload["file_path"],
            "upload_task_id": upload["task_id"],
            "llm_config": {"model_type": "deepseek", "api_key": "test-key"},
        },
    )

    assert response.status_code == 200
    # 就地复用上传任务：另起一个任务会在历史列表里留下永远删不掉的孤儿
    assert response.json()["task_id"] == upload["task_id"]
    task = main.tasks[upload["task_id"]]
    assert task["status"] == "pending"
    assert task["uploaded_file_path"] == upload["file_path"]
    assert task["uploaded_filename"] == "课程 第一讲.mp4"


def test_summarize_rejects_upload_task_already_consumed_or_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run(task_id, request):
        return None

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "run_queued_video_task", fake_run)
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(main, "running_jobs", {})
    client = TestClient(main.app)
    upload_id = client.post(
        "/api/upload", files={"file": ("a.mp4", b"bits", "video/mp4")}
    ).json()["task_id"]
    payload = {
        "upload_task_id": upload_id,
        "llm_config": {"model_type": "deepseek", "api_key": "test-key"},
    }

    assert client.post("/api/summarize", json=payload).status_code == 200
    assert client.post("/api/summarize", json=payload).status_code == 400
    assert (
        client.post(
            "/api/summarize", json={**payload, "upload_task_id": "not-a-task"}
        ).status_code
        == 400
    )


IMPORT_SRT = """1
00:00:00,500 --> 00:00:03,240
今天我们讲梯度下降

2
00:00:03,240 --> 00:01:05,500
先从一维的例子开始
"""


def post_subtitle(client: TestClient, filename: str = "机器学习第一讲.srt", text: str = IMPORT_SRT):
    return client.post(
        "/api/upload",
        files={"file": (filename, text.encode("utf-8"), "application/octet-stream")},
    )


def test_upload_imports_srt_as_a_finished_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """导入一份字幕 = 转录已经完成的上传任务：不下载、不抽音频、不转写。"""
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})
    upload = post_subtitle(TestClient(main.app))

    assert upload.status_code == 200
    task_id = upload.json()["task_id"]
    task_dir = tmp_path / task_id
    # 落地名不能是 input.*：本地媒体与复用转录都靠 input.* 找媒体，混进去会让
    # 之后的「从头处理」去 ffprobe 一份 .srt
    assert (task_dir / "subtitle.srt").is_file()
    assert not list(task_dir.glob("input.*"))
    payload = json.loads((task_dir / "transcript.json").read_text(encoding="utf-8"))
    assert payload["source"] == "subtitle_import"
    assert payload["segments"] == [
        {"start": 0.5, "end": 3.24, "text": "今天我们讲梯度下降"},
        {"start": 3.24, "end": 65.5, "text": "先从一维的例子开始"},
    ]
    task = main.tasks[task_id]
    assert task["status"] == "uploaded"
    assert task["subtitle_import"] is True
    assert any("字幕已导入" in line for line in task["logs"])


def test_upload_rejects_a_subtitle_without_timed_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """读不出带时间轴的文本要当场说清，并且不许留下半个任务目录。"""
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})

    response = post_subtitle(TestClient(main.app), text="这就是几行普通文字，没有时间码\n第二行\n")

    assert response.status_code == 415
    assert "带时间轴" in response.json()["detail"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_subtitle_import_skips_download_and_transcription(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """字幕导入跑完整任务：第 1~4 步一个都不该碰，标题取字幕文件名。"""
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(main, "running_jobs", {})
    captured: list = []

    async def fake_run(task_id, request):
        captured.append(request)

    monkeypatch.setattr(main, "run_queued_video_task", fake_run)
    client = TestClient(main.app)
    upload_task_id = post_subtitle(client).json()["task_id"]
    submit = client.post(
        "/api/summarize",
        json={
            "upload_task_id": upload_task_id,
            "include_screenshots": True,
            "llm_config": {"model_type": "deepseek", "api_key": "test-key"},
        },
    )
    assert submit.status_code == 200
    request = captured[0]
    # 没有媒体可抽帧：勾选状态在提交处就被否掉，不留到第 5 步再报"没有视频"
    assert request.include_screenshots is False
    assert main.tasks[upload_task_id]["subtitle_import"] is True

    async def never_called(*args, **kwargs):
        raise AssertionError("字幕导入不该再碰下载/转写这条链")

    processor = VideoProcessor(tmp_path)
    processor.get_video_info = never_called
    processor.fetch_subtitles = never_called
    processor.fetch_bilibili_subtitles = never_called
    processor.extract_audio = never_called
    monkeypatch.setattr(main, "video_processor", processor)

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass

        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(self, *args, **kwargs):
            return "# 笔记正文"

    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)
    await main.process_video_task(upload_task_id, request)

    task = main.tasks[upload_task_id]
    assert task["status"] == "completed", task.get("error")
    assert task["result"]["title"] == "机器学习第一讲"
    assert any("已使用导入的字幕：2 条" in line for line in task["logs"])
    assert not any("复用任务 None" in line for line in task["logs"])
    assert (tmp_path / upload_task_id / "notes.md").is_file()


def test_health_reports_upload_limit_for_the_frontend(monkeypatch) -> None:
    """前端不再自己写死 500MB：本机上限只能由健康接口下发。"""
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 1536)
    monkeypatch.setattr(main, "MAX_SUBTITLE_MB", 64)
    response = TestClient(main.app).get("/api/health")
    assert response.json()["max_upload_mb"] == 1536
    # 字幕导入的提示语要跟媒体上限分开说，否则用户以为 20MB 的字幕也能塞进 2GB
    assert response.json()["max_subtitle_mb"] == 64


def test_summarize_accepts_douyin_url_and_does_not_front_reject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run(task_id, request):
        return None

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "run_queued_video_task", fake_run)
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(main, "running_jobs", {})
    response = TestClient(main.app).post(
        "/api/summarize",
        json={
            "video_url": "https://www.douyin.com/video/123456",
            "llm_config": {"model_type": "deepseek", "api_key": "test-key"},
        },
    )
    assert response.status_code == 200
    assert response.json()["task_id"]


def test_image_route_requires_a_known_task() -> None:
    client = TestClient(main.app)
    response = client.get("/api/image/../secret.jpg")
    assert response.status_code in {404, 405}


def test_archive_note_deduplicates_names(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)

    first = main.archive_note("测试 视频", "# 一")
    second = main.archive_note("测试 视频", "# 二")
    third = main.archive_note("测试/视频:非法字符?", "# 三")

    assert first.name == "测试 视频.md"
    assert second.name == "测试 视频-2.md"
    assert third.parent == tmp_path / "notes"
    assert first.read_text(encoding="utf-8") == "# 一"
    assert (tmp_path / "notes").is_dir()


def test_archive_note_ignored_by_task_discovery(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    main.archive_note("某个视频", "# 笔记")
    main.archive_note("某个视频【转录】", "# 转录", folder=main.TRANSCRIPTS_DIR_NAME)

    assert main.find_reusable_task("https://www.bilibili.com/video/BV1xx") is None
    # 两个归档目录都不带 task.json，重启恢复时不能被当成任务
    main.restore_tasks_from_workspace()
    assert main.NOTES_DIR_NAME not in main.tasks
    assert main.TRANSCRIPTS_DIR_NAME not in main.tasks


def test_download_returns_markdown_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = "download-task"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    (task_dir / "notes.md").write_text("# 下载测试", encoding="utf-8")
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    main.tasks[task_id] = main.new_task()
    main.tasks[task_id].update(
        status="completed", result={"title": "下载测试", "markdown": "# 下载测试"}
    )

    response = TestClient(main.app).get(f"/api/download/{task_id}")

    assert response.status_code == 200
    assert response.content.decode("utf-8") == "# 下载测试"
    assert ".md" in response.headers["content-disposition"]
    assert not (task_dir / "video-notes.zip").exists()
    main.tasks.pop(task_id, None)


def test_download_stem_marks_parts_and_transcripts() -> None:
    """同一视频逐 P 跑、或先转写后笔记，产物不能全叫一个名字。"""
    one_of_three = {
        "bili_pages": [{"page": 2, "part": "第二章 梯度"}],
        "bili_total_pages": 3,
    }
    assert main.download_stem("深度学习课", one_of_three) == "深度学习课【P2 第二章 梯度】"
    assert (
        main.download_stem("深度学习课", one_of_three, "transcript")
        == "深度学习课【P2 第二章 梯度】【转录】"
    )
    # 单 P 视频、以及一次跑完的分 P 视频：没有歧义，不加噪声
    assert main.download_stem("深度学习课", {"bili_total_pages": 1}) == "深度学习课"
    assert main.download_stem(
        "深度学习课",
        {
            "bili_pages": [{"page": 1, "part": "第一章"}, {"page": 2, "part": "第二章"}],
            "bili_total_pages": 2,
        },
    ) == "深度学习课"
    # 多个分 P 的子集只报页码：分 P 名逐个拼进文件名会长到没法看
    assert main.download_stem(
        "深度学习课",
        {
            "bili_pages": [{"page": 1, "part": "甲"}, {"page": 3, "part": "丙"}],
            "bili_total_pages": 5,
        },
    ) == "深度学习课【P1、3】"
    # 80 字预算里被保住的是后缀，不是标题尾部
    long_name = main.download_stem("长" * 90, one_of_three)
    assert len(long_name) == 80 and long_name.endswith("【P2 第二章 梯度】")


def test_download_names_files_after_the_part_and_status_shows_the_same_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网页端拼 txt/html/png 用的名字必须来自后端，和 .md 下载同一个来源。"""
    task_id = "part-naming"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    (task_dir / "notes.md").write_text("# 分P笔记", encoding="utf-8")
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "title": "深度学习课",
                "bili_pages": [{"page": 2, "part": "第二章 梯度"}],
                "bili_total_pages": 3,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    main.tasks[task_id] = main.new_task()
    main.tasks[task_id].update(status="completed", result={"title": "深度学习课"})

    status = TestClient(main.app).get(f"/api/task/{task_id}").json()
    response = TestClient(main.app).get(f"/api/download/{task_id}")

    assert status["download_name"] == "深度学习课【P2 第二章 梯度】"
    assert "深度学习课【P2 第二章 梯度】.md" in unquote(
        response.headers["content-disposition"]
    )
    main.tasks.pop(task_id, None)


def test_download_bundles_note_images_with_the_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正文带截图时不能只发一个 .md：相对路径在解压前无处解析，用户看到的就是「导出丢图」。"""
    task_id = "bundle-task"
    task_dir = tmp_path / task_id
    (task_dir / "frames").mkdir(parents=True)
    (task_dir / "frames" / "frame_0000_30s.jpg").write_bytes(b"\xff\xd8jpeg")
    (task_dir / "notes.md").write_text(
        "# 带图笔记\n\n## 视频截图\n\n![截图 1](./frames/frame_0000_30s.jpg)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    main.tasks[task_id] = main.new_task()
    main.tasks[task_id].update(status="completed", result={"title": "带图笔记"})

    response = TestClient(main.app).get(f"/api/download/{task_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert ".zip" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "带图笔记.md",
            "frames/frame_0000_30s.jpg",
        }
        assert archive.read("frames/frame_0000_30s.jpg") == b"\xff\xd8jpeg"
        assert "./frames/frame_0000_30s.jpg" in archive.read("带图笔记.md").decode("utf-8")
    main.tasks.pop(task_id, None)


def test_download_bundle_rewrites_legacy_image_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1.4.1 之前的笔记写的是 ./images/，历史任务重新导出时同样要拿到图。"""
    task_id = "legacy-task"
    task_dir = tmp_path / task_id
    (task_dir / "frames").mkdir(parents=True)
    (task_dir / "frames" / "frame_0001_60s.jpg").write_bytes(b"\xff\xd8old")
    (task_dir / "notes.md").write_text(
        "![截图 1](./images/frame_0001_60s.jpg)", encoding="utf-8"
    )
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    main.tasks[task_id] = main.new_task()
    main.tasks[task_id].update(status="completed", result={"title": "旧任务"})

    response = TestClient(main.app).get(f"/api/download/{task_id}")

    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        note = archive.read("旧任务.md").decode("utf-8")
        assert archive.read("frames/frame_0001_60s.jpg") == b"\xff\xd8old"
    assert "./frames/frame_0001_60s.jpg" in note
    assert "./images/" not in note
    main.tasks.pop(task_id, None)


def test_archive_note_points_refs_at_the_task_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """归档与任务目录同在 workspace/ 下，引用指过去就能就地看图，不必复制几百张 JPEG。"""
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)

    archived = main.archive_note(
        "某个视频", "![截图 1](./images/frame_0000_30s.jpg)", "abc123"
    )

    assert archived.read_text(encoding="utf-8") == (
        "![截图 1](../abc123/frames/frame_0000_30s.jpg)"
    )


@pytest.mark.asyncio
async def test_failure_never_persists_the_plaintext_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网关常把整段请求回显进 4xx；这条路径上 Key 不能跟着 error 与日志落盘。"""
    secret = "Zx9QWertyuio1234ABCD"  # 不带 sk- 前缀：只有"整串替换"这一层能救
    task_id = "leak-task"
    old_task_id = "old-leak"
    (tmp_path / old_task_id).mkdir()
    (tmp_path / old_task_id / "transcript.json").write_text(
        '{"language":"zh","source":"faster_whisper","segments":'
        '[{"start":0,"end":12,"text":"这是一段带有反讽的原始内容"}]}',
        encoding="utf-8",
    )
    (tmp_path / task_id).mkdir()

    async def forbidden(*args, **kwargs):
        raise AssertionError("复用转录的路径不该碰平台与转写")

    class FakeProcessor:
        @staticmethod
        def detect_source(*args, **kwargs):
            return main.VideoSource.BILIBILI

        get_video_info = forbidden
        fetch_subtitles = forbidden
        download_audio = forbidden

    class FakeSummarizer:
        def __init__(self, **kwargs):
            self.warnings = [f"该通道不支持关闭思考，已降级。回显凭据 {secret}"]

        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(self, *args, **kwargs):
            raise RuntimeError(
                'Error code: 401 upstream echoed {"api_key": "'
                + secret
                + '", "authorization": Bearer Q29uZ3JldGVzczEyMzQ1Ng}"'
            )

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "video_processor", FakeProcessor())
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)
    main.tasks[task_id] = main.new_task()
    main.tasks[task_id]["resume_task_id"] = old_task_id
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1leak",
        llm_config=main.LLMConfig(model_type="glm", api_key=secret),
    )

    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "failed"
    live = json.dumps({"error": task["error"], "logs": task["logs"]}, ensure_ascii=False)
    persisted = (tmp_path / task_id / "task.json").read_text(encoding="utf-8")
    assert secret not in live and secret not in persisted
    assert "Q29uZ3JldGVzczEyMzQ1Ng" not in live  # 字段名回显那一层也要洗掉
    # 确实是脱敏而不是整行被丢掉：底层错误那行仍然可读，只是凭据成了掩码
    assert "底层错误：" in live and "****" in live


@pytest.mark.asyncio
async def test_summarizer_warnings_are_scrubbed_before_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功任务也会带告警，而告警里嵌的是通道原文——这条路径以前完全没脱敏。"""
    secret = "Vh7NqzL0mBxc8dke"
    task_id = "warn-task"
    old_task_id = "old-warn"
    (tmp_path / old_task_id).mkdir()
    (tmp_path / old_task_id / "transcript.json").write_text(
        '{"language":"zh","source":"faster_whisper","segments":'
        '[{"start":0,"end":12,"text":"这是一段带有反讽的原始内容"}]}',
        encoding="utf-8",
    )
    (tmp_path / task_id).mkdir()

    async def forbidden(*args, **kwargs):
        raise AssertionError("复用转录的路径不该碰平台与转写")

    class FakeProcessor:
        @staticmethod
        def detect_source(*args, **kwargs):
            return main.VideoSource.BILIBILI

        get_video_info = forbidden
        fetch_subtitles = forbidden
        download_audio = forbidden

    class FakeSummarizer:
        def __init__(self, **kwargs):
            # 没有字段名前缀，只有"整串替换"这一层能洗掉
            self.warnings = [f"该通道不支持关闭深度思考，已改用默认值（{secret}）"]

        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(self, *args, **kwargs):
            return "# 正常笔记"

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "video_processor", FakeProcessor())
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)
    main.tasks[task_id] = main.new_task()
    main.tasks[task_id]["resume_task_id"] = old_task_id
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1warn",
        llm_config=main.LLMConfig(model_type="glm", api_key=secret),
    )

    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "completed"
    live = json.dumps(task["logs"], ensure_ascii=False)
    persisted = (tmp_path / task_id / "task.json").read_text(encoding="utf-8")
    assert secret not in live and secret not in persisted
    assert any("该通道不支持关闭深度思考" in line for line in task["logs"])
    assert secret[:4] + "****" in live

@pytest.mark.asyncio
async def test_pipeline_writes_frame_refs_that_exist_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任务目录里的 notes.md 与 frames/ 同级，引用对不上就是坏链——导出丢图的根因在这。"""
    task_id = "shot-task"
    old_task_id = "old-shot-task"
    old_dir = tmp_path / old_task_id
    old_dir.mkdir()
    (tmp_path / task_id).mkdir()
    (old_dir / "transcript.json").write_text(
        '{"language":"zh","source":"faster_whisper","segments":'
        '[{"start":0,"end":12,"text":"这是一段带有反讽的原始内容"}]}',
        encoding="utf-8",
    )

    async def fake_preview(*args, **kwargs):
        return tmp_path / task_id / "preview.mp4"

    async def fake_extract_frames(video_path, target_task_id, interval, should_abort=None):
        frames_dir = tmp_path / target_task_id / "frames"
        frames_dir.mkdir(exist_ok=True)
        paths = []
        for index, timestamp in enumerate((0, interval)):
            path = frames_dir / f"frame_{index:04d}_{timestamp}s.jpg"
            path.write_bytes(b"\xff\xd8tiny")
            paths.append(path)
        return paths

    class FakeProcessor:
        @staticmethod
        def detect_source(*args, **kwargs):
            return main.VideoSource.BILIBILI

        download_preview_video = fake_preview
        extract_frames = staticmethod(fake_extract_frames)

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass

        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(self, *args, **kwargs):
            return "# 带图笔记"

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "video_processor", FakeProcessor())
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)
    main.tasks[task_id] = main.new_task()
    main.tasks[task_id]["resume_task_id"] = old_task_id
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx",
        include_screenshots=True,
        screenshot_interval=30,
        llm_config=main.LLMConfig(model_type="glm", api_key="test-key"),
    )

    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "completed"
    assert task["result"]["screenshot_count"] == 2
    note = (tmp_path / task_id / "notes.md").read_text(encoding="utf-8")
    assert "./frames/frame_0000_0s.jpg" in note
    assert "./images/" not in note
    archived = Path(task["result"]["archived_path"]).read_text(encoding="utf-8")
    assert f"../{task_id}/frames/frame_0001_30s.jpg" in archived


@pytest.mark.asyncio
async def test_cancel_task_flags_task_and_cancels_running_job() -> None:
    """取消 = 置标志位（让后台线程早点收工）+ 直接掐掉 asyncio 任务（任意 await 点立即停）。"""
    task_id = "cancel-task"
    main.tasks[task_id] = main.new_task("processing")
    job = asyncio.create_task(asyncio.sleep(30))
    main.running_jobs[task_id] = job

    result = await main.cancel_task(task_id)
    assert result["cancelled"] is False
    assert result["status"] == "cancelling"
    assert main.tasks[task_id]["_cancel_requested"] is True
    assert main.tasks[task_id]["_cancel_event"].is_set()
    assert any("已收到取消请求" in log for log in main.tasks[task_id]["logs"])

    done, _pending = await asyncio.wait({job}, timeout=2)
    assert done, "取消端点没有停止运行中的任务"
    assert job.cancelled()

    # 第二次取消只更新状态，不再补一刀（否则终态处理过程中会再挨一次 CancelledError）
    assert (await main.cancel_task(task_id))["status"] == "cancelling"
    main.running_jobs.pop(task_id, None)
    main.tasks.pop(task_id, None)


@pytest.mark.asyncio
async def test_delete_failed_task_removes_record_and_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "failed-task"
    cleaned: list[str] = []

    class FakeProcessor:
        async def cleanup(self, value: str) -> None:
            cleaned.append(value)

    monkeypatch.setattr(main, "video_processor", FakeProcessor())
    main.tasks[task_id] = main.new_task("failed", task_id)

    assert await main.delete_task(task_id) == {"deleted": True}
    assert cleaned == [task_id]
    assert task_id not in main.tasks


@pytest.mark.asyncio
async def test_delete_uploaded_task_removes_orphan_record_and_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上传后放弃的「待处理」任务没有运行中的 job，必须能直接删掉。"""
    task_id = "uploaded-task"
    cleaned: list[str] = []

    class FakeProcessor:
        async def cleanup(self, value: str) -> None:
            cleaned.append(value)

    monkeypatch.setattr(main, "video_processor", FakeProcessor())
    main.tasks[task_id] = main.new_task("uploaded", task_id)

    assert await main.delete_task(task_id) == {"deleted": True}
    assert cleaned == [task_id]
    assert task_id not in main.tasks


@pytest.mark.asyncio
async def test_delete_cancelled_task_removes_record_and_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "cancelled-task"
    cleaned: list[str] = []

    class FakeProcessor:
        async def cleanup(self, value: str) -> None:
            cleaned.append(value)

    monkeypatch.setattr(main, "video_processor", FakeProcessor())
    main.tasks[task_id] = main.new_task("cancelled", task_id)

    assert await main.delete_task(task_id) == {"deleted": True}
    assert cleaned == [task_id]
    assert task_id not in main.tasks


@pytest.mark.asyncio
async def test_task_queue_respects_concurrency_limit(monkeypatch) -> None:
    active = 0
    maximum_active = 0
    release = asyncio.Event()

    async def fake_process(task_id, request):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await release.wait()
        active -= 1

    monkeypatch.setattr(main, "task_slots", asyncio.Semaphore(1))
    monkeypatch.setattr(main, "process_video_task", fake_process)
    request = main.SummarizeRequest(
        video_url="https://example.com/video",
        llm_config={"model_type": "deepseek", "api_key": "test-key"},
    )
    for task_id in ("queued-one", "queued-two"):
        main.tasks[task_id] = main.new_task(task_id=task_id)

    jobs = [
        asyncio.create_task(main.run_queued_video_task(task_id, request))
        for task_id in ("queued-one", "queued-two")
    ]
    await asyncio.sleep(0.01)
    assert maximum_active == 1
    release.set()
    await asyncio.gather(*jobs)
    assert maximum_active == 1
    main.tasks.pop("queued-one", None)
    main.tasks.pop("queued-two", None)


@pytest.mark.asyncio
async def test_task_status_reports_and_freezes_elapsed_time() -> None:
    task_id = "timing-task"
    task = main.new_task("processing")
    main.tasks[task_id] = task
    # Windows 上 time.monotonic() 分辨率约 15.6ms，sleep 过短会得到 0.0
    await asyncio.sleep(0.1)

    active = await main.get_task_status(task_id)
    assert active["elapsed_seconds"] > 0
    assert "_started_monotonic" not in active

    frozen = main.finish_task_timing(task)
    await asyncio.sleep(0.01)
    finished = await main.get_task_status(task_id)
    assert finished["elapsed_seconds"] == pytest.approx(frozen)
    main.tasks.pop(task_id, None)


def test_restore_tasks_marks_interrupted_and_loads_completed(monkeypatch, tmp_path) -> None:
    interrupted_id = "interrupted-task"
    interrupted_dir = tmp_path / interrupted_id
    interrupted_dir.mkdir()
    (interrupted_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": interrupted_id,
                "created_at": "2026-01-01T00:00:00+00:00",
                "runtime": {"status": "processing", "logs": ["处理中"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    completed_id = "completed-task"
    completed_dir = tmp_path / completed_id
    completed_dir.mkdir()
    (completed_dir / "notes.md").write_text("# 已恢复笔记", encoding="utf-8")
    (completed_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": completed_id,
                "title": "恢复测试",
                "runtime": {"status": "completed", "elapsed_seconds": 12.5},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    main.restore_tasks_from_workspace()

    assert main.tasks[interrupted_id]["status"] == "failed"
    assert "服务重启" in main.tasks[interrupted_id]["error"]
    assert main.tasks[completed_id]["status"] == "completed"
    assert main.tasks[completed_id]["result"]["markdown"] == "# 已恢复笔记"
    main.tasks.pop(interrupted_id, None)
    main.tasks.pop(completed_id, None)


@pytest.mark.asyncio
async def test_pipeline_uses_platform_subtitles_without_downloading_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)

    async def fake_info(*args, **kwargs):
        return {
            "title": "测试视频",
            "source": "bilibili",
            "duration": 30,
            "owner": "作者",
        }

    async def fake_subtitles(*args, **kwargs):
        return SubtitleResult(
            [TranscriptSegment(0, 10, "字幕内容")], "zh-CN", "platform_subtitle"
        )

    async def fail_download(*args, **kwargs):
        raise AssertionError("audio fallback should not run")

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass
        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(
            self, title, segments, metadata, style="detailed", reasoning_effort="auto",
            progress_callback=None, should_abort=None,
        ):
            if progress_callback:
                await progress_callback(78, "正在生成完整笔记")
            return "# 测试笔记\n\n[00:00] 字幕摘要"

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(processor, "download_audio", fail_download)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)

    task_id = "subtitle-task"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx",
        llm_config=main.LLMConfig(
            model_type="deepseek", api_key="test-key"
        ),
    )

    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "completed"
    assert task["result"]["transcript_source"] == "platform_subtitle"
    assert (tmp_path / task_id / "transcript.json").is_file()
    assert (tmp_path / task_id / "notes.md").is_file()


@pytest.mark.asyncio
async def test_pipeline_reuses_transcript_without_platform_or_whisper_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_task_id = "old-task"
    new_task_id = "new-task"
    old_dir = tmp_path / old_task_id
    old_dir.mkdir()
    (tmp_path / new_task_id).mkdir()
    (old_dir / "transcript.json").write_text(
        '{"language":"zh","source":"faster_whisper","segments":'
        '[{"start":0,"end":12,"text":"这是一段带有反讽的原始内容"}]}',
        encoding="utf-8",
    )
    (old_dir / "notes.md").write_text(
        "# 视频笔记：《旧任务标题》\n\n---\n来源：https://www.bilibili.com/video/BV1xx\n",
        encoding="utf-8",
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("reuse path must not call the platform or Whisper")

    class FakeProcessor:
        @staticmethod
        def detect_source(*args, **kwargs):
            return main.VideoSource.BILIBILI

        get_video_info = forbidden
        fetch_subtitles = forbidden
        download_audio = forbidden

    class FakeTranscriber:
        transcribe = forbidden

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass
        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(
            self, title, segments, metadata, style="detailed", reasoning_effort="auto",
            progress_callback=None, should_abort=None,
        ):
            assert title == "旧任务标题"
            assert style == "detailed"
            assert reasoning_effort == "auto"
            return "# 复用成功"

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "video_processor", FakeProcessor())
    monkeypatch.setattr(main, "transcriber", FakeTranscriber())
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)
    main.tasks[new_task_id] = main.new_task()
    main.tasks[new_task_id]["resume_task_id"] = old_task_id
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx?share_source=copy_web",
        summary_style="detailed",
        llm_config=main.LLMConfig(model_type="glm", api_key="test-key"),
    )

    await main.process_video_task(new_task_id, request)

    task = main.tasks[new_task_id]
    assert task["status"] == "completed"
    assert task["result"]["segment_count"] == 1
    assert any("跳过字幕、音频和 Whisper" in log for log in task["logs"])
    assert (old_dir / "notes.md").read_text(encoding="utf-8").startswith(
        "# 视频笔记：《旧任务标题》"
    )
    assert (tmp_path / new_task_id / "notes.md").read_text(encoding="utf-8").startswith(
        "# 复用成功"
    )


@pytest.mark.asyncio
async def test_local_task_title_uses_uploaded_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归：本地视频落地文件统一叫 input.mp4，历史与笔记标题不能显示成 input。"""
    task_id = "local-task"
    media = tmp_path / task_id / "input.mp4"
    media.parent.mkdir()
    media.write_bytes(b"bits")

    class FakeProcessor:
        @staticmethod
        def detect_source(*args, **kwargs):
            return main.VideoSource.LOCAL

        @staticmethod
        async def get_video_info(url_or_path, cookie=None, allow_local=False, notes=None):
            return {"title": Path(url_or_path).stem, "source": "local", "duration": 0}

        @staticmethod
        async def cleanup(value):
            return None

    class FakeTranscriber:
        @staticmethod
        async def transcribe(
            media_path,
            model,
            use_gpu,
            initial_prompt=None,
            cancel_event=None,
            progress_callback=None,
        ):
            return {
                "segments": [
                    TranscriptSegment(0, 12, "这是一段用来通过文字质量检查的本地视频口播内容")
                ],
                "language": "zh",
                "model": model,
                "requested_model": model,
                "device": "cpu",
                "duration": 12,
            }

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "video_processor", FakeProcessor())
    monkeypatch.setattr(main, "transcriber", FakeTranscriber())
    task = main.new_task(status="uploaded", task_id=task_id)
    task.update(uploaded_file_path=str(media), uploaded_filename="课程 第一讲.mp4")
    main.tasks[task_id] = task

    await main.process_video_task(
        task_id,
        main.SummarizeRequest(
            video_url=str(media), output="transcript", llm_config=main.LLMConfig()
        ),
    )

    finished = main.tasks[task_id]
    assert finished["status"] == "completed"
    assert finished["result"]["title"] == "课程 第一讲"
    assert any("标题：课程 第一讲" in log for log in finished["logs"])


@pytest.mark.asyncio
async def test_transcript_only_task_archives_a_titled_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归（群测原话"现在都叫 transcript.md"）：纯转录只在 `workspace/<UUID>/` 里留一个
    固定名文件，而那个 UUID 目录是工作目录（音频与 task.json 都在里面），不该是用户
    要拿的产物。产物落到 `workspace/transcripts/`，名字与下载名同一个来源。"""
    task_id = "transcript-archive"
    media = tmp_path / task_id / "input.mp4"
    media.parent.mkdir()
    media.write_bytes(b"bits")

    class FakeProcessor:
        @staticmethod
        def detect_source(*args, **kwargs):
            return main.VideoSource.LOCAL

        @staticmethod
        async def get_video_info(url_or_path, cookie=None, allow_local=False, notes=None):
            return {"title": Path(url_or_path).stem, "source": "local", "duration": 12}

        @staticmethod
        async def cleanup(value):
            return None

    class FakeTranscriber:
        @staticmethod
        async def transcribe(
            media_path,
            model,
            use_gpu,
            initial_prompt=None,
            cancel_event=None,
            progress_callback=None,
        ):
            return {
                "segments": [
                    TranscriptSegment(0, 12, "这是一段用来通过文字质量检查的本地视频口播内容")
                ],
                "language": "zh",
                "model": model,
                "requested_model": model,
                "device": "cpu",
                "duration": 12,
            }

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "video_processor", FakeProcessor())
    monkeypatch.setattr(main, "transcriber", FakeTranscriber())
    task = main.new_task(status="uploaded", task_id=task_id)
    task.update(uploaded_file_path=str(media), uploaded_filename="课程 第一讲.mp4")
    main.tasks[task_id] = task

    await main.process_video_task(
        task_id,
        main.SummarizeRequest(
            video_url=str(media), output="transcript", llm_config=main.LLMConfig()
        ),
    )

    finished = main.tasks[task_id]
    assert finished["status"] == "completed"
    archived = Path(finished["result"]["archived_path"])
    assert archived.parent == tmp_path / main.TRANSCRIPTS_DIR_NAME
    assert archived.name == "课程 第一讲【转录】.md"
    assert "口播内容" in archived.read_text(encoding="utf-8")
    # 工作目录里那份照旧留着：音频、transcript.json 与它都在，排查时还要用
    assert (tmp_path / task_id / "transcript.md").is_file()


@pytest.mark.asyncio
async def test_pipeline_merges_all_bilibili_pages_from_ai_subtitles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)

    async def fake_info(*args, **kwargs):
        return {"title": "分P测试视频", "source": "bilibili", "duration": 0, "owner": "作者"}

    async def fake_subtitles(*args, **kwargs):
        return None

    async def fake_bili_subtitles(*args, **kwargs):
        pages = (
            BiliPage(page=1, part="第一部分", cid=101, duration=60),
            BiliPage(page=2, part="第二部分", cid=102, duration=90),
        )
        sub = SubtitleResult(
            [TranscriptSegment(0, 5, "P1字幕"), TranscriptSegment(5, 9, "P1更多")],
            "zh-CN",
            "bilibili_ai_subtitle",
        )
        merged, _ = main.merge_bilibili_pages(pages, {1: sub}, {})
        return BiliSubtitleOutcome(
            SubtitleResult(merged, "zh-CN", "bilibili_ai_subtitle"),
            "ok",
            title="分P测试视频",
            total_pages=2,
            pages=pages,
            subtitle_by_page=((1, sub),),
        )

    async def fail_download(*args, **kwargs):
        raise AssertionError("多分P全有字幕，不应下载音频")

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass
        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(
            self, title, segments, metadata, style="detailed", reasoning_effort="auto",
            progress_callback=None, should_abort=None,
        ):
            if progress_callback:
                await progress_callback(78, "正在生成完整笔记")
            return "# 测试笔记\n\n[00:00] 摘要"

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(processor, "fetch_bilibili_subtitles", fake_bili_subtitles)
    monkeypatch.setattr(processor, "download_audio", fail_download)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)

    task_id = "multi-p-subtitle"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx",
        llm_config=main.LLMConfig(model_type="deepseek", api_key="test-key"),
    )

    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "completed"
    assert task["result"]["transcript_source"] == "bilibili_ai_subtitle"
    assert any("共 2 个分 P" in log for log in task["logs"])
    assert any("本次处理 1、2" in log for log in task["logs"])


@pytest.mark.asyncio
async def test_pipeline_transcribes_missing_bilibili_page_and_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = VideoProcessor(tmp_path)

    async def fake_info(*args, **kwargs):
        return {"title": "分P测试视频", "source": "bilibili", "duration": 0, "owner": "作者"}

    async def fake_subtitles(*args, **kwargs):
        return None

    async def fake_bili_subtitles(*args, **kwargs):
        pages = (
            BiliPage(page=1, part="第一部分", cid=101, duration=60),
            BiliPage(page=2, part="第二部分", cid=102, duration=90),
        )
        sub = SubtitleResult(
            [TranscriptSegment(0, 5, "P1字幕")], "zh-CN", "bilibili_ai_subtitle"
        )
        merged, _ = main.merge_bilibili_pages(pages, {1: sub}, {})
        return BiliSubtitleOutcome(
            SubtitleResult(merged, "zh-CN", "bilibili_ai_subtitle"),
            "partial",
            title="分P测试视频",
            total_pages=2,
            pages=pages,
            subtitle_by_page=((1, sub),),
            pages_to_transcribe=(pages[1],),
        )

    downloaded: list[tuple[str, str]] = []
    captured_segments: list = []

    async def fake_download(url, task_id, cookie=None, media_name="audio", **kwargs):
        downloaded.append((url, media_name))
        return tmp_path / f"{media_name}.mp3"

    class FakeTranscriber:
        async def transcribe(
            self, media_path, model_name="base", use_gpu=False,
            initial_prompt=None, cancel_event=None, progress_callback=None,
        ):
            return {
                "segments": [TranscriptSegment(0, 3, "P2语音内容")],
                "language": "zh",
                "duration": 90,
                "device": "cpu",
                "model": "base",
                "requested_model": "base",
            }

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass
        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(
            self, title, segments, metadata, style="detailed", reasoning_effort="auto",
            progress_callback=None, should_abort=None,
        ):
            captured_segments.extend(segments)
            if progress_callback:
                await progress_callback(78, "正在生成完整笔记")
            return "# 测试笔记\n\n[00:00] 摘要"

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(processor, "fetch_bilibili_subtitles", fake_bili_subtitles)
    monkeypatch.setattr(processor, "download_audio", fake_download)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "transcriber", FakeTranscriber())
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)

    task_id = "multi-p-partial"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx",
        llm_config=main.LLMConfig(model_type="deepseek", api_key="test-key"),
    )

    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "completed"
    assert downloaded[0][0].endswith("?p=2")
    assert downloaded[0][1] == "audio_p2"
    assert any("分 P 2 语音转写完成" in log for log in task["logs"])
    assert any("分 P 转录合并完成" in log for log in task["logs"])
    assert len(captured_segments) == 2
    assert captured_segments[0].text.startswith("【P1 第一部分】 P1字幕")
    assert captured_segments[1].text.startswith("【P2 第二部分】 P2语音内容")
    assert captured_segments[1].start == 60.0


@pytest.mark.asyncio
async def test_pipeline_passes_bilibili_pages_to_subtitle_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """请求里的 bilibili_pages 要原样传给字幕抓取（显式选择覆盖 URL ?p=N）。"""
    processor = VideoProcessor(tmp_path)

    async def fake_info(*args, **kwargs):
        return {"title": "分P测试视频", "source": "bilibili", "duration": 0, "owner": "作者"}

    async def fake_subtitles(*args, **kwargs):
        return None

    captured: dict = {}

    async def fake_bili_subtitles(url, cookie=None, only_pages=None):
        captured["only_pages"] = only_pages
        pages = (
            BiliPage(page=1, part="第一部分", cid=101, duration=60),
            BiliPage(page=2, part="第二部分", cid=102, duration=90),
        )
        sub = SubtitleResult(
            [TranscriptSegment(0, 5, "P1字幕")], "zh-CN", "bilibili_ai_subtitle"
        )
        merged, _ = main.merge_bilibili_pages(pages, {1: sub}, {})
        return BiliSubtitleOutcome(
            SubtitleResult(merged, "zh-CN", "bilibili_ai_subtitle"),
            "ok",
            title="分P测试视频",
            total_pages=2,
            pages=pages,
            subtitle_by_page=((1, sub),),
        )

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass
        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(self, *args, **kwargs):
            return "# 测试笔记"

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(processor, "fetch_bilibili_subtitles", fake_bili_subtitles)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)

    task_id = "multi-p-pages-param"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx?p=2",
        bilibili_pages=[1, 2],
        llm_config=main.LLMConfig(model_type="deepseek", api_key="test-key"),
    )

    await main.process_video_task(task_id, request)

    assert captured["only_pages"] == [1, 2]


@pytest.mark.asyncio
async def test_single_part_task_archives_under_the_part_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只跑一个分 P 的任务，归档与状态里的名字要带上 P 号和分 P 名。

    这条钉的是整条链：view 接口给的分 P 清单 → task.json → 文件名。缺任一环，
    用户从下载目录拿到的就是几个同名的 `深度学习课.md`。
    """
    processor = VideoProcessor(tmp_path)

    async def fake_info(*args, **kwargs):
        return {"title": "深度学习课", "source": "bilibili", "duration": 0, "owner": "作者"}

    async def fake_subtitles(*args, **kwargs):
        return None

    async def fake_bili_subtitles(url, cookie=None, only_pages=None):
        page = BiliPage(page=2, part="第二章 梯度", cid=102, duration=90)
        sub = SubtitleResult(
            [TranscriptSegment(0, 5, "P2字幕")], "zh-CN", "bilibili_ai_subtitle"
        )
        return BiliSubtitleOutcome(
            sub,
            "ok",
            title="深度学习课",
            total_pages=3,
            pages=(page,),
            subtitle_by_page=((2, sub),),
        )

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass
        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(self, *args, **kwargs):
            return "# 测试笔记"

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(processor, "fetch_bilibili_subtitles", fake_bili_subtitles)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)

    task_id = "single-part-name"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx?p=2",
        bilibili_pages=[2],
        llm_config=main.LLMConfig(model_type="deepseek", api_key="test-key"),
    )

    await main.process_video_task(task_id, request)

    expected = "深度学习课【P2 第二章 梯度】"
    assert main.tasks[task_id]["status"] == "completed"
    assert json.loads((tmp_path / task_id / "task.json").read_text("utf-8"))[
        "bili_pages"
    ] == [{"page": 2, "part": "第二章 梯度"}]
    assert (tmp_path / "notes" / f"{expected}.md").is_file()

    client = TestClient(main.app)
    assert client.get(f"/api/task/{task_id}").json()["download_name"] == expected
    assert expected in unquote(client.get(f"/api/download/{task_id}").headers["content-disposition"])
    main.tasks.pop(task_id, None)


def test_bili_pages_endpoint_returns_page_list(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_pages(url, cookie=None):
        return BiliVideoInfo(
            bvid="BV1xx",
            aid=None,
            title="多P视频",
            pages=(
                BiliPage(page=1, part="第一课", cid=101, duration=600),
                BiliPage(page=2, part="第二课", cid=102, duration=900),
            ),
        )

    monkeypatch.setattr(main.video_processor, "bilibili_video_pages", fake_pages)
    monkeypatch.setattr(main.config_store, "load_bili_credentials", lambda: None)
    client = TestClient(main.app)
    response = client.post(
        "/api/bili-pages", json={"video_url": "https://www.bilibili.com/video/BV1xx"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "多P视频"
    assert data["total_pages"] == 2
    assert [page["page"] for page in data["pages"]] == [1, 2]
    assert data["pages"][0]["part"] == "第一课"
    assert data["pages"][1]["duration"] == 900


def test_bili_pages_endpoint_rejects_non_bilibili(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.config_store, "load_bili_credentials", lambda: None)
    client = TestClient(main.app)
    response = client.post(
        "/api/bili-pages", json={"video_url": "https://www.youtube.com/watch?v=abc"}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_cancel_during_llm_generation_marks_task_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证取消能从主流程贯通到 LLM 生成阶段（should_abort 接线正确）。"""
    processor = VideoProcessor(tmp_path)

    async def fake_info(*args, **kwargs):
        return {"title": "取消测试", "source": "youtube", "duration": 30, "owner": "作者"}

    async def fake_subtitles(*args, **kwargs):
        return SubtitleResult(
            [TranscriptSegment(0, 5, "测试内容")], "zh", "platform_subtitle"
        )

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass
        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(self, *args, **kwargs):
            raise asyncio.CancelledError()

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)

    task_id = "cancel-during-llm"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    request = main.SummarizeRequest(
        video_url="https://www.youtube.com/watch?v=abc",
        llm_config=main.LLMConfig(model_type="deepseek", api_key="test-key"),
    )

    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "cancelled"
    assert task["step_name"] == "已取消"


def test_upload_rejects_file_over_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 100)
    client = TestClient(main.app)
    response = client.post(
        "/api/upload",
        files={"file": ("big.mp4", b"x" * 200, "video/mp4")},
    )
    assert response.status_code == 413
    assert "上限" in response.json()["detail"]


def test_upload_large_video_auto_extracts_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LARGE_UPLOAD_EXTRACT_BYTES", 1)

    def fake_extract(source, target):
        target.write_bytes(b"_audio_stream_")
        return target

    monkeypatch.setattr(
        main.video_processor, "extract_audio_track", staticmethod(fake_extract)
    )
    client = TestClient(main.app)
    response = client.post(
        "/api/upload",
        files={"file": ("video.mp4", b"bits" * 1000, "video/mp4")},
        data={"include_screenshots": "0"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "video.mp4"
    assert data["file_path"].endswith("input.m4a")
    task = main.tasks[data["task_id"]]
    assert task["uploaded_file_path"].endswith("input.m4a")
    assert any("提取音频" in log for log in task["logs"])


def test_upload_keeps_video_when_screenshots_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LARGE_UPLOAD_EXTRACT_BYTES", 1)
    called = {"extract": False}

    def fake_extract(source, target):
        called["extract"] = True
        target.write_bytes(b"_audio_stream_")
        return target

    monkeypatch.setattr(
        main.video_processor, "extract_audio_track", staticmethod(fake_extract)
    )
    client = TestClient(main.app)
    response = client.post(
        "/api/upload",
        files={"file": ("video.mp4", b"bits" * 1000, "video/mp4")},
        data={"include_screenshots": "1"},
    )
    assert response.status_code == 200
    assert called["extract"] is False
    assert response.json()["file_path"].endswith("input.mp4")

@pytest.mark.asyncio
async def test_rerun_faithful_after_failure_has_no_oversized_tail_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归：失败任务重跑（复用转录）后选“详细复原”卡在生成阶段不动。

    根因是尾部核对把“笔记省略了时间戳”当成丢尾，自动补发一次覆盖剩余全部转录的
    超大请求；“详细+点评”同场景只记一条告警，所以只有“详细复原”会不动。
    """
    from types import SimpleNamespace

    from backend.llm_summarizer import LLMSummarizer

    old_task_id, new_task_id = "old-failed", "rerun-faithful"
    old_dir = tmp_path / old_task_id
    old_dir.mkdir()
    (tmp_path / new_task_id).mkdir()
    segments = [
        {
            "start": index * 18.0,
            "end": index * 18.0 + 18.0,
            "text": f"第{index}句口播内容，这里展开了例子和论证过程。",
        }
        for index in range(300)  # 约 90 分钟
    ]
    (old_dir / "transcript.json").write_text(
        json.dumps(
            {"language": "zh", "source": "faster_whisper", "segments": segments},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (old_dir / "task.json").write_text(
        json.dumps({"title": "长视频", "duration": 5_400, "owner": "UP 主"}),
        encoding="utf-8",
    )

    # 内容写到了结尾，但时间戳只标到 12:00 —— 提示词允许“不必每段都加”。
    draft_lines = ["# 视频笔记：《长视频》", ""]
    for item in segments:
        stamp = (
            f"[{int(item['start'] // 60):02d}:{int(item['start'] % 60):02d}"
            f"-{int(item['end'] // 60):02d}:{int(item['end'] % 60):02d}] "
            if item["start"] < 12 * 60
            else ""
        )
        draft_lines.append(f"{stamp}第 {item['start']} 段的完整复原内容")
    draft = "\n".join(draft_lines)

    calls: list[tuple[str, int]] = []

    def _stream(text: str):
        async def gen():
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=text), finish_reason="stop"
                    )
                ]
            )

        return gen()

    async def create(**kwargs):
        prompt = kwargs["messages"][-1]["content"]
        head = prompt[:40]
        stage = "成稿" if "写一份" in head else "补写结尾" if "视频《" in head else "整理"
        calls.append((stage, len(prompt)))
        if stage == "成稿":
            return _stream(draft)
        return _stream(f"片段笔记（{len(prompt)} 字）")

    def fake_summarizer(**kwargs):
        instance = object.__new__(LLMSummarizer)
        instance.model_type = "custom"
        instance.model = "deepseek-ai/DeepSeek-V4-Flash"
        instance.base_url = "https://api-inference.modelscope.cn/v1/"
        instance.warnings = []
        instance.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        return instance

    class Forbidden:
        @staticmethod
        def detect_source(*args, **kwargs):
            return main.VideoSource.BILIBILI

        async def get_video_info(self, *args, **kwargs):
            raise AssertionError("复用转录时不应再读视频信息")

    async def forbidden(*args, **kwargs):
        raise AssertionError("复用转录时不应再走字幕/下载/转写")

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "video_processor", Forbidden())
    monkeypatch.setattr(main, "transcriber", SimpleNamespace(transcribe=forbidden))
    monkeypatch.setattr(main, "LLMSummarizer", fake_summarizer)
    main.tasks[new_task_id] = main.new_task()
    main.tasks[new_task_id]["resume_task_id"] = old_task_id
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx",
        summary_style="faithful",
        llm_config=main.LLMConfig(
            model_type="custom",
            api_key="test-key",
            base_url="https://api-inference.modelscope.cn/v1/",
            model="deepseek-ai/DeepSeek-V4-Flash",
        ),
    )

    await asyncio.wait_for(main.process_video_task(new_task_id, request), timeout=60)

    task = main.tasks[new_task_id]
    assert task["status"] == "completed"
    assert not [stage for stage, _size in calls if stage == "补写结尾"]
    assert all(size < 14_000 for _stage, size in calls)
    assert any("已跳过自动补写" in log for log in task["logs"])
    note_text = (tmp_path / new_task_id / "notes.md").read_text(encoding="utf-8")
    assert "详细复原（仅视频内容）" in note_text
    assert draft_lines[-1] in note_text

@pytest.mark.asyncio
async def test_pipeline_logs_bilibili_api_fallback_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #1 回退可见性：视频页被风控改用接口直连时，任务日志要说明。"""
    processor = VideoProcessor(tmp_path)
    notes: list[str] = []

    async def fake_info(url, cookie, allow_local=False, notes_out=None):
        assert notes_out is not None, "get_video_info 需要拿到回退说明的收集列表"
        notes_out.append("B 站视频页被风控拦截，已改用开放接口直连获取视频信息与媒体流")
        return {
            "title": "风控视频",
            "source": "bilibili",
            "duration": 30,
            "owner": "作者",
        }

    async def fake_subtitles(*args, **kwargs):
        return SubtitleResult(
            [TranscriptSegment(0, 10, "接口直连拿到的字幕内容")], "zh-CN", "platform_subtitle"
        )

    async def fail_download(*args, **kwargs):
        raise AssertionError("已有字幕时不应下载音频")

    class FakeSummarizer:
        def __init__(self, **kwargs):
            pass

        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return reasoning_effort

        async def generate_summary(self, *args, **kwargs):
            return "# 测试笔记\n\n[00:00-00:10] 内容"

    monkeypatch.setattr(
        processor, "get_video_info", lambda url, cookie, allow_local=False, notes=None: fake_info(
            url, cookie, allow_local, notes
        )
    )
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(processor, "download_audio", fail_download)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LLMSummarizer", FakeSummarizer)

    task_id = "fallback-note-task"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx",
        summary_style="faithful",
        llm_config=main.LLMConfig(model_type="glm", api_key="test-key"),
    )

    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "completed"
    assert any("开放接口直连" in log for log in task["logs"])


# ---- 本机 API Key 只按接口地址复用 ----

DEEPSEEK_HOST = "https://api.deepseek.com"
CORP_GATEWAY = "https://gateway.corp.example/v1"


def _run_to_generation_stage(monkeypatch, tmp_path: Path, captured: list) -> None:
    """把任务推到"生成笔记"阶段：字幕直接给，LLM 只记录构造参数，不发请求。"""
    processor = VideoProcessor(tmp_path)

    async def fake_info(*args, **kwargs):
        return {"title": "档案测试", "source": "bilibili", "duration": 30, "owner": "UP"}

    async def fake_subtitles(*args, **kwargs):
        return SubtitleResult([TranscriptSegment(0, 5, "内容")], "zh", "platform_subtitle")

    class RecordingSummarizer:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.model = kwargs.get("model")
            self.base_url = kwargs.get("base_url")

        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return "模型默认"

        async def generate_summary(self, *args, **kwargs):
            return "# 笔记"

        async def test_connection(self):
            return True, "连接正常", 0.01

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "LLMSummarizer", RecordingSummarizer)


@pytest.mark.asyncio
async def test_task_reuses_key_saved_for_the_same_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    main.config_store.put_key(
        provider="custom", base_url=CORP_GATEWAY, api_key="sk-saved-here", label="公司代理"
    )
    captured: list = []
    _run_to_generation_stage(monkeypatch, tmp_path, captured)

    task_id = "same-endpoint-key"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    try:
        await main.process_video_task(
            task_id,
            main.SummarizeRequest(
                video_url="https://www.bilibili.com/video/BV1xx",
                llm_config=main.LLMConfig(
                    model_type="custom", base_url=CORP_GATEWAY, model="kimi-k2.6"
                ),
            ),
        )
        task = main.tasks[task_id]

        assert task["status"] == "completed"
        assert captured[0]["api_key"] == "sk-saved-here"
        assert any("Key=本机已存 sk-s****（公司代理）" in log for log in task["logs"])
    finally:
        main.tasks.pop(task_id, None)


@pytest.mark.asyncio
async def test_task_never_borrows_a_key_for_another_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换 Provider / 换网关时不许"顺手"用本机另一把 Key：那正是密钥外流的形状。"""
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    main.config_store.put_key(provider="deepseek", base_url=DEEPSEEK_HOST, api_key="sk-deepseek")
    captured: list = []
    _run_to_generation_stage(monkeypatch, tmp_path, captured)

    task_id = "cross-endpoint-key"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    try:
        await main.process_video_task(
            task_id,
            main.SummarizeRequest(
                video_url="https://www.bilibili.com/video/BV1xx",
                llm_config=main.LLMConfig(
                    model_type="custom", base_url=CORP_GATEWAY, model="kimi-k2.6"
                ),
            ),
        )
        task = main.tasks[task_id]

        assert task["status"] == "failed"
        assert captured == []  # 连客户端都没构造，不可能有出站请求
        assert CORP_GATEWAY in task["error"]
        assert "sk-deepseek" not in task["error"]
    finally:
        main.tasks.pop(task_id, None)


def test_llm_test_resolves_key_by_endpoint_without_mixing_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    captured: list = []
    _run_to_generation_stage(monkeypatch, tmp_path, captured)
    client = TestClient(main.app)

    rejected = client.post(
        "/api/llm-test",
        json={"model_type": "custom", "base_url": CORP_GATEWAY, "model": "kimi-k2.6"},
    ).json()
    assert rejected["ok"] is False
    assert CORP_GATEWAY in rejected["error"]
    assert captured == []

    main.config_store.put_key(
        provider="custom", base_url=CORP_GATEWAY, api_key="sk-corp", model="saved-model-id"
    )
    passed = client.post(
        "/api/llm-test", json={"model_type": "custom", "base_url": CORP_GATEWAY}
    ).json()
    assert passed["ok"] is True
    assert captured[-1]["api_key"] == "sk-corp"
    # 只有 Key 会按地址复用；已存档案里的 model 不会被"顺"过来。
    assert captured[-1]["model"] is None
    assert passed["base_url"] == CORP_GATEWAY
    assert passed["key_source"].startswith("本机已存")

    typed = client.post(
        "/api/llm-test",
        json={"model_type": "custom", "base_url": CORP_GATEWAY, "api_key": "sk-typed"},
    ).json()
    assert typed["key_source"] == "本次请求提供"
    assert captured[-1]["api_key"] == "sk-typed"


LONE_SURROGATE = chr(0xD83D)  # 半个代理对：被 max_tokens 截断的 emoji，编不成 UTF-8


def test_utf8_safe_only_touches_unencodable_characters() -> None:
    assert utf8_safe("正常 🏓 中文 换行\n") == "正常 🏓 中文 换行\n"
    cleaned = utf8_safe("半个" + LONE_SURROGATE + "代理对")
    assert LONE_SURROGATE not in cleaned
    cleaned.encode("utf-8")


@pytest.mark.asyncio
async def test_generated_note_drops_unencodable_characters() -> None:
    """正文里的半个代理对不能带进 notes.md 与 task.json 的写入。"""
    summarizer = LLMSummarizer(model_type="deepseek", api_key="sk-x")

    class FakeStream:
        async def __aiter__(self):
            for piece in ("# 笔记\n", LONE_SURROGATE, "收尾"):
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=piece), finish_reason=None
                        )
                    ]
                )
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None), finish_reason="stop"
                    )
                ]
            )

    class FakeCompletions:
        async def create(self, **kwargs):
            return FakeStream()

    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    text = await summarizer._complete("转录内容", max_tokens=100)

    assert LONE_SURROGATE not in text
    text.encode("utf-8")  # 修复前这一步抛 UnicodeEncodeError


def test_llm_test_survives_lone_surrogate_in_model_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型回半个代理对时端点要正常返回，而不是崩成裸 500。

    崩点在端点 return 之后的响应序列化阶段，罩在端点里的 except 抓不到；纯文本 500
    到了前端只剩「测试请求失败（HTTP 500）」这句兜底话，看着完全像上游挂了。
    """

    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Pong " + LONE_SURROGATE + " OK!")
                    )
                ]
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/api/llm-test",
        json={
            "model_type": "custom",
            "base_url": CORP_GATEWAY,
            "model": "deepseek-v4-flash",
            "api_key": "sk-typed",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert LONE_SURROGATE not in body["message"]


def test_llm_test_reports_unreadable_key_store_without_http_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """密钥档案读不了（磁盘被拖垮 / DPAPI 失灵 / 文件损坏）要回可读错误，不能裸 500。

    1.4.2 悬案里前端只剩「（HTTP 500）」，就是这类异常从 except RuntimeError 的
    缝隙漏出去的结果。
    """

    def boom(*args, **kwargs):
        raise OSError("模拟磁盘被拖垮")

    monkeypatch.setattr(main, "resolve_llm_credentials", boom)
    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/api/llm-test", json={"model_type": "deepseek"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "读取本机保存的 Key 失败" in body["error"]
    assert "OSError" in body["error"]


# --------------------------------------------------------------------------- 本机自检（1.4.2 悬案的取证出口）


def test_diagnostics_reports_probe_results_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/diagnostics 的探测全部可注入；报告里永远没有密钥与代理账号段。"""

    def fake_probe(url: str, timeout: float = 4.0, *, use_proxy: bool) -> dict:
        if "127.0.0.1" in url:
            return {"ok": True, "status": 200, "body": json.dumps({"service": "VideoToNo"})}
        return {"ok": True, "status": 200, "body": ""}

    monkeypatch.setattr(main, "_http_probe", fake_probe)
    monkeypatch.setattr(main, "_resolve_host", lambda host: {"ok": True, "addresses": ["203.0.113.7"]})
    monkeypatch.setattr(main, "_wininet_proxy_snapshot", lambda: {"available": False})
    monkeypatch.setattr(main, "_count_free_cdp_ports", lambda start=9333, count=20: 20)
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:supersecret@127.0.0.1:7890")

    response = TestClient(main.app).get("/api/diagnostics")

    assert response.status_code == 200
    body = response.json()
    checks = body["checks"]
    assert body["ok"] is True
    assert checks["loopback_http"]["is_ours"] is True
    assert checks["service"]["version"] == main.app.version
    assert checks["cdp_ports_free"] == 20
    assert checks["dns"]["api.deepseek.com"]["ok"] is True
    assert checks["outbound"]["direct"]["ok"] is True
    assert "***@127.0.0.1:7890" in checks["system_proxy"]["env"]
    assert "supersecret" not in response.text
    assert body["hints"]


def test_unhandled_error_handler_survives_redaction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兜底处理器里的脱敏再炸，也要回得出 JSON——兜错误的地方不能自己再崩。"""

    class Boom:
        def keys_are_corrupt(self):
            raise RuntimeError("boom")

    def exploding_redact(text):
        raise RuntimeError("脱敏炸了")

    monkeypatch.setattr(main, "redact_secrets", exploding_redact)
    monkeypatch.setattr(main, "config_store", Boom())
    response = TestClient(main.app, raise_server_exceptions=False).get("/api/llm-keys")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert "后端内部错误" in response.json()["detail"]


def test_scanned_bili_cookies_are_saved_on_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫码成功必须顺手落本机：以前网页端从来没有这一步，用户每次开页面都得重扫一遍。

    存不上也不该把这次登录判失败——cookie 已经在手里，至少这一趟在页面上要能用。
    """
    manager = bili_login.BiliLoginManager(
        tmp_path, save_cookies=main.save_scanned_bili_credentials
    )
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    session = bili_login.BiliLoginSession(
        browser_path="msedge.exe",
        profile_dir=tmp_path / "_bili_profile",
        cdp_port=9333,
        started_at=time.time(),
    )
    manager.session = session

    manager._mark_ready(
        session,
        {"sessdata": "abcdefghijklmnop1234", "bili_jct": "j1", "buvid3": "b1"},
        "登录成功",
    )

    assert session.state == "ready" and session.saved is True
    status = TestClient(main.app).get("/api/bili-credentials").json()
    assert ConfigStore(tmp_path).load_bili_credentials() == {
        "sessdata": "abcdefghijklmnop1234",
        "bili_jct": "j1",
        "buvid3": "b1",
    }
    # 状态视图只给掩码、全程不解密（换机器解不开时这个端点仍然有得报）
    assert "abcdefghijklmnop1234" not in json.dumps(status)
    assert status["saved"] is True and status["sessdata_masked"]

    def broken(_cookies: dict[str, str]) -> None:
        raise RuntimeError("本机加密不可用")

    failing = bili_login.BiliLoginManager(tmp_path, save_cookies=broken)
    late = bili_login.BiliLoginSession(
        browser_path="msedge.exe",
        profile_dir=tmp_path / "_bili_profile",
        cdp_port=9334,
        started_at=time.time(),
    )
    failing.session = late
    failing._mark_ready(late, {"sessdata": "s2"}, "登录成功")

    assert late.state == "ready" and late.cookies == {"sessdata": "s2"}
    assert late.saved is False and "加密" in late.save_error


def test_free_cdp_port_survives_winsock_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Winsock 被系统级问题拖垮时返回"没有可用端口"，而不是把裸 OSError 漏成 500。"""

    def boom(*args, **kwargs):
        raise OSError("Winsock 不可用")

    for module in (bili_login, douyin_login):
        monkeypatch.setattr(module.socket, "socket", boom)
        assert module.free_cdp_port() is None


def test_cdp_openers_bypass_system_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """urllib 在 Windows 会读注册表系统代理；CDP 的 opener 必须显式不挂任何代理。

    空 ProxyHandler 不贡献任何方法，不会留在 handlers 里——所以这里把
    getproxies 投毒后重新构建一次：默认 opener 会把毒代理吃进去，显式空代理的
    构建方式则无论 handlers 里有没有 ProxyHandler，代理表都是空的。
    """
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:9"})
    for module in (bili_login, douyin_login):
        opener = module._no_proxy_opener()
        proxy_handlers = [
            handler
            for handler in opener.handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        assert all(handler.proxies == {} for handler in proxy_handlers)


def test_update_check_endpoint_reports_newer_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """启动自检的更新端点：新版本在场时给全 tag/地址/节选，交前端弹窗。"""
    monkeypatch.setattr(
        main.update_check,
        "fetch_latest_release",
        lambda current, timeout=5.0: {
            "tag_name": "v9.9.9",
            "html_url": "https://github.com/example/release",
            "update_available": True,
            "notes": "修复若干",
        },
    )
    response = TestClient(main.app).get("/api/update/check")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["update_available"] is True
    assert body["latest_version"] == "v9.9.9"
    assert body["current_version"] == main.app.version
    assert body["release_url"] == "https://github.com/example/release"
    assert body["notes"] == "修复若干"


def test_update_check_endpoint_survives_github_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub 连不上时回 200 + ok=False，前端静默，不当成错误打扰用户。"""

    def boom(current, timeout=5.0):
        raise OSError("网络不通")

    monkeypatch.setattr(main.update_check, "fetch_latest_release", boom)
    response = TestClient(main.app).get("/api/update/check")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "OSError" in body["error"]


def test_unhandled_backend_error_comes_back_as_json(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """后端自己崩了也得回 JSON，否则前端拿不到任何原因。"""

    class Boom:
        def keys_are_corrupt(self):
            raise RuntimeError("模拟崩了 sk-abcdefghijklmnopqrstuvwxyz")

    monkeypatch.setattr(main, "config_store", Boom())
    response = TestClient(main.app, raise_server_exceptions=False).get("/api/llm-keys")
    detail = response.json()["detail"]

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert "模拟崩了" in detail
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in detail  # 沿用 1.4.1 的脱敏口径


# --------------------------------------------------------------------------- 转录出口


def test_transcribe_endpoint_needs_no_llm_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    queued: list = []

    async def fake_run(task_id, request):
        queued.append(request)

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "run_queued_video_task", fake_run)
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(main, "running_jobs", {})

    response = TestClient(main.app).post(
        "/api/transcribe", json={"video_url": "https://www.bilibili.com/video/BV1xx"}
    )

    assert response.status_code == 200
    assert response.json()["task_id"]
    assert queued[0].output == "transcript"
    assert queued[0].llm_config.api_key.get_secret_value() == ""
    manifest = json.loads((tmp_path / response.json()["task_id"] / "task.json").read_text("utf-8"))
    assert manifest["output"] == "transcript"
    assert manifest["origin"] == "agent"


@pytest.mark.asyncio
async def test_transcript_only_task_finishes_before_any_llm_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """宿主 agent 自带模型时的路线：本机没有 Key 也必须跑得通。"""
    processor = VideoProcessor(tmp_path)

    async def fake_info(*args, **kwargs):
        return {"title": "转录出口", "source": "bilibili", "duration": 30, "owner": "作者"}

    async def fake_subtitles(*args, **kwargs):
        return SubtitleResult(
            [TranscriptSegment(0, 10, "第一段字幕"), TranscriptSegment(10, 20, "第二段字幕")],
            "zh-CN",
            "platform_subtitle",
        )

    def forbid_credentials(**kwargs):
        raise AssertionError("转录路线不得解析 API Key")

    def forbid_summarizer(**kwargs):
        raise AssertionError("转录路线不得调用大模型")

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "resolve_llm_credentials", forbid_credentials)
    monkeypatch.setattr(main, "LLMSummarizer", forbid_summarizer)

    task_id = "transcript-task"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task()
    main.tasks[task_id]["output"] = "transcript"
    request = main.SummarizeRequest(
        video_url="https://www.bilibili.com/video/BV1xx",
        output="transcript",
        llm_config=main.LLMConfig(model_type="deepseek"),
    )

    await main.process_video_task(task_id, request)

    task = main.tasks[task_id]
    assert task["status"] == "completed"
    assert task["step_name"] == "转录完成"
    assert task["result"]["output"] == "transcript"
    assert "markdown" not in task["result"]
    assert task["result"]["segment_count"] == 2
    assert not (tmp_path / task_id / "notes.md").exists()
    assert "第一段字幕" in (tmp_path / task_id / "transcript.md").read_text("utf-8")
    main.tasks.pop(task_id, None)


def test_agent_transcript_tasks_stay_out_of_the_web_history(monkeypatch) -> None:
    """agent 批量取原料不得挤掉用户的笔记历史；网页端自己点的「仅转录」要能回看。"""
    note_task = main.new_task(status="completed", task_id="note-task")
    note_task.update(
        created_at="2026-08-09T12:00:00+00:00",
        result={"title": "笔记任务", "markdown": "# private body"},
    )
    agent_task = main.new_task(status="completed", task_id="agent-transcript")
    agent_task.update(
        created_at="2026-08-09T13:00:00+00:00",
        output="transcript",
        _origin="agent",
        result={"title": "agent 取的转录", "output": "transcript"},
    )
    web_task = main.new_task(status="completed", task_id="web-transcript")
    web_task.update(
        created_at="2026-08-09T14:00:00+00:00",
        output="transcript",
        _origin="web",
        result={"title": "网页端字幕稿", "output": "transcript"},
    )
    monkeypatch.setattr(
        main,
        "tasks",
        {"note-task": note_task, "agent-transcript": agent_task, "web-transcript": web_task},
    )

    items = TestClient(main.app).get("/api/tasks").json()["tasks"]

    assert [item["task_id"] for item in items] == ["web-transcript", "note-task"]
    # 前端靠 output 打「转录」标记，并决定结果是走 result.markdown 还是现取转录
    assert [item["output"] for item in items] == ["transcript", "note"]
    assert all("markdown" not in item for item in items)


def test_web_transcript_submission_needs_no_key(monkeypatch, tmp_path: Path) -> None:
    """网页端「仅转录」仍走 /api/summarize：本机没有 Key 也必须收下并记成 web 来源。"""
    queued: list = []

    async def fake_run(task_id, request):
        queued.append(request)

    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "run_queued_video_task", fake_run)
    monkeypatch.setattr(main, "tasks", {})
    monkeypatch.setattr(main, "running_jobs", {})

    response = TestClient(main.app).post(
        "/api/summarize",
        json={
            "video_url": "https://www.bilibili.com/video/BV1xx",
            "output": "transcript",
            "llm_config": {},
        },
    )

    assert response.status_code == 200
    task_id = response.json()["task_id"]
    assert queued[0].output == "transcript"
    assert main.tasks[task_id]["_origin"] == "web"
    manifest = json.loads((tmp_path / task_id / "task.json").read_text("utf-8"))
    assert manifest["origin"] == "web"


def test_download_falls_back_to_transcript_without_notes(tmp_path, monkeypatch) -> None:
    """纯转录任务按设计不产出 notes.md，下载要能拿到 transcript.md。"""
    write_transcript_artifacts(tmp_path, "transcript-download", "字幕稿正文")
    task_id = "transcript-download"
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    main.tasks[task_id] = main.new_task(
        status="completed",
        task_id=task_id,
    )
    main.tasks[task_id].update(
        output="transcript",
        _origin="web",
        result={"title": "字幕稿", "output": "transcript"},
    )

    response = TestClient(main.app).get(f"/api/download/{task_id}")

    assert response.status_code == 200
    assert "字幕稿正文" in response.text
    assert "字幕稿【转录】.md" in unquote(response.headers["content-disposition"])
    main.tasks.pop(task_id, None)


def test_restart_keeps_web_transcript_task_in_history(monkeypatch, tmp_path) -> None:
    """origin 必须落盘：重启后网页端的字幕稿仍要留在「最近任务」里。"""
    task_id = "restored-web-transcript"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    write_transcript_artifacts(tmp_path, task_id)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "title": "网页端字幕稿",
                "output": "transcript",
                "origin": "web",
                "runtime": {
                    "status": "completed",
                    "step": 7,
                    "step_name": "转录完成",
                    "result": {"title": "网页端字幕稿", "output": "transcript"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})

    main.restore_tasks_from_workspace()

    assert main.tasks[task_id]["_origin"] == "web"
    items = TestClient(main.app).get("/api/tasks").json()["tasks"]
    assert [item["task_id"] for item in items] == [task_id]


def write_transcript_artifacts(workspace: Path, task_id: str, text: str = "转录内容") -> None:
    task_dir = workspace / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "transcript.json").write_text(
        json.dumps(
            {
                "language": "zh",
                "source": "faster_whisper",
                "quality": {"characters": len(text), "insufficient": False},
                "segments": [{"start": 0.0, "end": 12.5, "text": text}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (task_dir / "transcript.md").write_text(f"# 带时间戳转录\n\n[00:00-00:12] {text}\n", "utf-8")


def test_transcript_endpoint_returns_segments_and_text(tmp_path, monkeypatch) -> None:
    write_transcript_artifacts(tmp_path, "t1")
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    client = TestClient(main.app)

    as_json = client.get("/api/task/t1/transcript")
    as_markdown = client.get("/api/task/t1/transcript?output_format=markdown")

    assert as_json.status_code == 200
    body = as_json.json()
    assert body["segment_count"] == 1
    assert body["segments"][0] == {"start": 0.0, "end": 12.5, "text": "转录内容"}
    assert body["transcript_source"] == "faster_whisper"
    assert as_markdown.json()["text"].startswith("# 带时间戳转录")
    assert as_markdown.json()["title"] == ""
    assert client.get("/api/task/t1/transcript?output_format=bogus").status_code == 422


def test_transcript_endpoint_serves_srt_one_cue_per_segment(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SRT 与 json 版要逐条对得上：序号从 1 起、不并段、时间码按 SubRip 标准写。

    Issue #4 点名了这类格式最容易写错的四件事（小时从 0 还是 1 起、毫秒补零、
    逗号还是点、停顿处要不要并段），所以这里整篇比对而不是抽查。
    """
    task_dir = tmp_path / "srt-case"
    task_dir.mkdir(parents=True)
    (task_dir / "transcript.json").write_text(
        json.dumps(
            {
                "language": "zh",
                "source": "platform_subtitle",
                "segments": [
                    {"start": 0.5, "end": 3.24, "text": "第一段"},
                    {"start": 3.24, "end": 65.5, "text": "第二段"},
                    {"start": 3661.7, "end": 3665.0, "text": "第三段"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})

    body = TestClient(main.app).get("/api/task/srt-case/transcript?output_format=srt").json()

    assert body["format"] == "srt"
    assert body["text"] == (
        "1\r\n00:00:00,500 --> 00:00:03,240\r\n第一段\r\n"
        "\r\n2\r\n00:00:03,240 --> 00:01:05,500\r\n第二段\r\n"
        "\r\n3\r\n01:01:01,700 --> 01:01:05,000\r\n第三段\r\n"
    )


def test_transcript_endpoint_serves_tasks_that_failed_after_transcribing(
    tmp_path, monkeypatch
) -> None:
    """转录在第 4 步就落盘了：第 6 步失败的任务不必重跑也能把原料取走。"""
    write_transcript_artifacts(tmp_path, "failed-note", "已经转写好的内容")
    (tmp_path / "failed-note" / "task.json").write_text(
        json.dumps({"task_id": "failed-note", "title": "老视频"}), encoding="utf-8"
    )
    failed = main.new_task(status="failed", task_id="failed-note")
    failed["error"] = "未提供 API Key"
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {"failed-note": failed})

    body = TestClient(main.app).get("/api/task/failed-note/transcript").json()

    assert body["segments"][0]["text"] == "已经转写好的内容"
    assert body["title"] == "老视频"


def test_transcript_endpoint_errors_are_explicit(tmp_path, monkeypatch) -> None:
    (tmp_path / "no-transcript").mkdir()
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})
    client = TestClient(main.app)

    missing = client.get("/api/task/no-transcript/transcript")
    traversal = client.get("/api/task/..%2Fsecret/transcript")

    assert missing.status_code == 409
    assert "transcribe" in missing.json()["detail"]
    assert traversal.status_code in {400, 404}


def test_restore_keeps_transcript_task_completed_and_hidden(monkeypatch, tmp_path) -> None:
    task_id = "restored-transcript"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    write_transcript_artifacts(tmp_path, task_id)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "title": "转录恢复",
                "output": "transcript",
                "runtime": {
                    "status": "completed",
                    "step": 7,
                    "step_name": "转录完成",
                    "result": {"title": "转录恢复", "segment_count": 1},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "tasks", {})

    main.restore_tasks_from_workspace()

    restored = main.tasks[task_id]
    assert restored["status"] == "completed"
    assert restored["output"] == "transcript"
    assert restored["result"]["segment_count"] == 1
    assert TestClient(main.app).get("/api/tasks").json()["tasks"] == []


def test_previous_bili_pages_returns_latest_explicit_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重新处理多分 P 视频时默认沿用上次勾选：按链接匹配、取最近一次显式勾选。"""
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    old = tmp_path / "task_old"
    new = tmp_path / "task_new"
    other = tmp_path / "task_other"
    for path in (old, new, other):
        path.mkdir()
        (path / "task.json").write_text("{}", encoding="utf-8")
    (old / "task.json").write_text(
        json.dumps(
            {
                "normalized_source_url": "https://www.bilibili.com/video/BV1ab",
                "bilibili_pages": [1, 2],
            }
        ),
        encoding="utf-8",
    )
    (new / "task.json").write_text(
        json.dumps(
            {
                "normalized_source_url": "https://www.bilibili.com/video/BV1ab",
                "bilibili_pages": [3],
            }
        ),
        encoding="utf-8",
    )
    (other / "task.json").write_text(
        json.dumps(
            {
                "normalized_source_url": "https://www.bilibili.com/video/BVxxxx",
                "bilibili_pages": [9],
            }
        ),
        encoding="utf-8",
    )
    # 目录扫描按 mtime 排序，显式固定先后避免同秒创建的抖动
    import os

    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))

    assert main.previous_bili_pages("https://www.bilibili.com/video/BV1ab") == [3]
    assert main.previous_bili_pages("https://www.bilibili.com/video/BVzzzz") is None

    # 上次是「全部」（没有显式勾选记录）时不预勾，维持默认全不勾的保护
    (old / "task.json").unlink()
    (new / "task.json").write_text(
        json.dumps(
            {
                "normalized_source_url": "https://www.bilibili.com/video/BV1ab",
                "bilibili_pages": None,
            }
        ),
        encoding="utf-8",
    )
    assert main.previous_bili_pages("https://www.bilibili.com/video/BV1ab") is None
