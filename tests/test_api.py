import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.transcript import TranscriptSegment
from backend.video_processor import SubtitleResult, VideoProcessor


def test_health_and_frontend_are_served() -> None:
    client = TestClient(main.app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["version"] == "3.0.0"
    page = client.get("/")
    assert page.status_code == 200
    assert "VideoToNo" in page.text


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


def test_image_route_requires_a_known_task() -> None:
    client = TestClient(main.app)
    response = client.get("/api/image/../secret.jpg")
    assert response.status_code in {404, 405}


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
async def test_cancel_task_cancels_running_job_and_keeps_task_record() -> None:
    task_id = "cancel-task"
    main.tasks[task_id] = main.new_task("processing")
    job = asyncio.create_task(asyncio.sleep(30))
    main.running_jobs[task_id] = job

    result = await main.cancel_task(task_id)
    await asyncio.sleep(0)

    assert result["cancelled"] is True
    assert main.tasks[task_id]["status"] == "cancelled"
    assert job.cancelled()
    assert any("中间文件将保留" in log for log in main.tasks[task_id]["logs"])
    main.running_jobs.pop(task_id, None)
    main.tasks.pop(task_id, None)


@pytest.mark.asyncio
async def test_task_status_reports_and_freezes_elapsed_time() -> None:
    task_id = "timing-task"
    task = main.new_task("processing")
    main.tasks[task_id] = task
    await asyncio.sleep(0.01)

    active = await main.get_task_status(task_id)
    assert active["elapsed_seconds"] > 0
    assert "_started_monotonic" not in active

    frozen = main.finish_task_timing(task)
    await asyncio.sleep(0.01)
    finished = await main.get_task_status(task_id)
    assert finished["elapsed_seconds"] == pytest.approx(frozen)
    main.tasks.pop(task_id, None)


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
            self, title, segments, metadata, style="detailed", reasoning_effort="auto"
        ):
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
            self, title, segments, metadata, style="detailed", reasoning_effort="auto"
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
