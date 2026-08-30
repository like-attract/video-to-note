import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import launcher
from backend import main
from backend.config_store import ConfigStore
from backend.transcript import TranscriptSegment
from backend.video_processor import (
    BiliPage,
    BiliSubtitleOutcome,
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


def test_health_and_frontend_are_served() -> None:
    client = TestClient(main.app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["version"] == "1.2.3"
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
    for asset in ("/style.css", "/script.js?v=20260825-1", "/theme-bootstrap.js"):
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


def test_whisper_manual_folder_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """手动导入模型：创建并返回导入目录，模型 ID 校验，状态接口带 manual_dir。"""
    monkeypatch.setattr(main, "WHISPER_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(main, "_open_in_file_manager", lambda _path: False)
    client = TestClient(main.app)

    response = client.post("/api/whisper-models/manual-folder", json={"model": "base"})
    assert response.status_code == 200
    data = response.json()
    expected_dir = tmp_path / "cache" / "manual" / "base"
    assert data == {
        "path": str(expected_dir),
        "opened": False,
        "files": ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
        "download_url": "https://hf-mirror.com/Systran/faster-whisper-base/tree/main",
    }
    assert expected_dir.is_dir()

    status = client.get("/api/whisper-models")
    assert status.status_code == 200
    assert status.json()["manual_dir"] == str(tmp_path / "cache" / "manual")

    invalid = client.post("/api/whisper-models/manual-folder", json={"model": "nope"})
    assert invalid.status_code == 422


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
    assert main.tasks[task_id]["_cancel_event"].is_set()
    assert not job.done()
    assert any("已收到取消请求" in log for log in main.tasks[task_id]["logs"])
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
            initial_prompt=None, cancel_event=None,
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
