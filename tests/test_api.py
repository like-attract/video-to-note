import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import launcher
from backend import main
from backend.transcript import TranscriptSegment
from backend.video_processor import SubtitleResult, VideoProcessor


def test_health_and_frontend_are_served() -> None:
    client = TestClient(main.app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["version"] == "1.1.7"
    assert health.json()["version"] == launcher.VERSION
    assert health.json()["service"] == "VideoToNo"
    assert health.json()["mode"] == "dev"  # 测试进程非打包；打包版应报 portable
    page = client.get("/")
    assert page.status_code == 200
    assert "VideoToNo" in page.text
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
    app_icon = client.get("/icon.png")
    assert app_icon.status_code == 200
    assert app_icon.headers["content-type"].startswith("image/png")


def test_html_entry_is_no_store_and_assets_no_cache() -> None:
    """旧 HTML 与新 JS 混用会导致按钮全部失效，入口页必须禁止缓存。"""
    client = TestClient(main.app)
    page = client.get("/")
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["content-security-policy"].startswith("default-src 'self'")
    for asset in ("/style.css", "/script.js?v=20260822-1", "/theme-bootstrap.js"):
        response = client.get(asset)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"


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
    assert "const body = renderMarkdown(markdown);" in script
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

    assert main.find_reusable_task("https://www.bilibili.com/video/BV1xx") is None


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


@pytest.mark.asyncio
async def test_cancel_task_requests_cooperative_stop_and_keeps_task_record() -> None:
    task_id = "cancel-task"
    main.tasks[task_id] = main.new_task("processing")
    job = asyncio.create_task(asyncio.sleep(30))
    main.running_jobs[task_id] = job

    result = await main.cancel_task(task_id)
    assert result["cancelled"] is False
    assert result["status"] == "cancelling"
    assert main.tasks[task_id]["_cancel_requested"] is True
    assert not job.done()
    assert any("当前阻塞步骤结束后" in log for log in main.tasks[task_id]["logs"])
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
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

        async def generate_summary(
            self, title, segments, metadata, style="detailed", reasoning_effort="auto",
            progress_callback=None,
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

        async def generate_summary(
            self, title, segments, metadata, style="detailed", reasoning_effort="auto",
            progress_callback=None,
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
