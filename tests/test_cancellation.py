"""取消任务的不变式测试：不管卡在流水线的哪一步，取消都必须在秒级内让任务停下。

这里故意不写"某个调用点有没有接 should_abort"这种用例。v1.2.1 的取消是按调用点逐个接线
实现的，任何新阶段、或像 v1.2.3 的 412 回退那样把某个未接线阶段拉长，都会让取消悄悄失效。
现在的收口是 cancel_task 直接取消 asyncio 任务，所以只需证明"任意 await 点上取消都能停"：
把每个外部依赖依次换成永久挂起的桩，任务都必须立即进入 cancelled。
"""

import asyncio
import json
import types
from pathlib import Path

import pytest

from backend import main
from backend.config_store import ConfigStore
from backend.transcript import TranscriptSegment
from backend.video_processor import BiliSubtitleOutcome, SubtitleResult, VideoProcessor

# 从发出取消到任务真正停下允许的墙上时间
CANCEL_BUDGET_SECONDS = 2.0


class Harness:
    """持有"全部立即成功"的外部依赖，测试只把其中一个换成永久挂起的桩。"""

    def __init__(self, processor: VideoProcessor, monkeypatch) -> None:
        self.processor = processor
        self.monkeypatch = monkeypatch
        self.reached: list[str] = []

    def hang(self, stage: str, target: str, attribute: str) -> None:
        if target == "processor":
            owner = self.processor
        elif target == "transcriber":
            owner = types.SimpleNamespace(**{attribute: lambda *a, **k: None})
            self.monkeypatch.setattr(main, "transcriber", owner)
        elif target == "summarizer":
            self.monkeypatch.setattr(main, "LLMSummarizer", _hanging_summarizer(attribute, self.reached, stage))
            return
        else:  # pragma: no cover - 测试内部约束
            raise AssertionError(f"未知依赖：{target}")

        async def _hang(*args, **kwargs):
            self.reached.append(stage)
            await asyncio.Event().wait()  # 永不返回：模拟卡在网络、下载或模型响应上

        self.monkeypatch.setattr(owner, attribute, _hang)

    def stub(self, target: str, attribute: str, value) -> None:
        owner = self.processor if target == "processor" else getattr(main, target, None)
        if owner is None:  # pragma: no cover - 测试内部约束
            raise AssertionError(f"未知依赖：{target}")
        self.monkeypatch.setattr(owner, attribute, value)


def _hanging_summarizer(attribute: str, reached: list[str], stage: str):
    async def _hang(self, *args, **kwargs):
        reached.append(stage)
        await asyncio.Event().wait()

    def __init__(self, **kwargs):
        self.warnings: list[str] = []

    return types.new_class(
        "HangingSummarizer",
        (),
        exec_body=lambda ns: ns.update(
            {"__init__": __init__, "describe_effort": lambda *a, **k: "模型默认", attribute: _hang}
        ),
    )


def _install_fast_pipeline(monkeypatch, tmp_path: Path) -> Harness:
    """每一步外部调用都立即成功返回；测试里只让其中一步永久挂起。"""
    processor = VideoProcessor(tmp_path)
    media = tmp_path / "audio.m4a"
    media.write_bytes(b"fake")

    async def fake_info(*args, **kwargs):
        return {
            "title": "取消测试",
            "source": "bilibili",
            "duration": 30,
            "owner": "UP",
            "upload_date": "",
            "timestamp": 0,
            "view_count": 0,
            "like_count": 0,
            "description": "",
        }

    async def fake_subtitles(*args, **kwargs):
        # 默认"没有平台字幕"，把后面的下载/转写阶段暴露出来
        return None

    async def fake_bili_subtitles(*args, **kwargs):
        return BiliSubtitleOutcome(None, "no_track", "测试用：无字幕轨")

    async def fake_download_audio(*args, **kwargs):
        return media

    async def fake_transcribe(*args, **kwargs):
        return {
            "segments": [TranscriptSegment(0, 5, "测试内容")],
            "language": "zh",
            "duration": 5,
            "device": "cpu",
            "model": "base",
            "requested_model": "base",
        }

    async def fake_generate_summary(self, *args, **kwargs):
        return "# 笔记"

    monkeypatch.setattr(processor, "get_video_info", fake_info)
    monkeypatch.setattr(processor, "fetch_subtitles", fake_subtitles)
    monkeypatch.setattr(processor, "fetch_bilibili_subtitles", fake_bili_subtitles)
    monkeypatch.setattr(processor, "download_audio", fake_download_audio)
    monkeypatch.setattr(processor, "download_preview_video", fake_download_audio)
    monkeypatch.setattr(processor, "cleanup", lambda *a, **k: None)
    monkeypatch.setattr(main, "transcriber", types.SimpleNamespace(transcribe=fake_transcribe))
    monkeypatch.setattr(main, "LLMSummarizer", _summarizer_class(fake_generate_summary))
    monkeypatch.setattr(main, "video_processor", processor)
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    return Harness(processor, monkeypatch)


def _summarizer_class(generate_summary):
    class FakeSummarizer:
        def __init__(self, **kwargs):
            self.warnings: list[str] = []

        def describe_effort(self, reasoning_effort: str, style: str) -> str:
            return "模型默认"

    FakeSummarizer.generate_summary = generate_summary
    return FakeSummarizer


def _request(**overrides):
    payload = {
        "video_url": "https://www.bilibili.com/video/BV1xx",
        "llm_config": main.LLMConfig(model_type="deepseek", api_key="test-key"),
    }
    payload.update(overrides)
    return main.SummarizeRequest(**payload)


async def _wait_for(predicate, attempts: int = 200) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def _cancel_stops_quickly(
    monkeypatch, tmp_path: Path, stage: str, configure, request=None
) -> dict:
    """卡进指定阶段后取消，断言任务在预算时间内进入 cancelled。"""
    harness = _install_fast_pipeline(monkeypatch, tmp_path)
    configure(harness)

    task_id = f"cancel-{stage}"
    (tmp_path / task_id).mkdir()
    main.tasks[task_id] = main.new_task(task_id=task_id)
    job = asyncio.create_task(
        main.run_queued_video_task(task_id, request or _request())
    )
    main.running_jobs[task_id] = job
    try:
        # 必须先确认真卡进了目标阶段，否则测的是别处
        entered = await _wait_for(lambda: harness.reached == [stage])
        assert entered, f"没有走到 {stage} 阶段，实际：{harness.reached}"
        assert main.tasks[task_id]["status"] in {"queued", "processing"}

        await main.cancel_task(task_id)
        done, pending = await asyncio.wait({job}, timeout=CANCEL_BUDGET_SECONDS)
        for stuck in pending:
            stuck.cancel()
        assert done, f"取消后 {CANCEL_BUDGET_SECONDS}s 内 {stage} 仍未停止"
        task = main.tasks[task_id]
        assert task["status"] == "cancelled"
        assert task["step_name"] == "已取消"
        assert any("取消" in line for line in task["logs"])
        return task
    finally:
        if not job.done():
            job.cancel()
        main.running_jobs.pop(task_id, None)
        main.tasks.pop(task_id, None)


@pytest.mark.asyncio
async def test_cancel_while_reading_video_info(tmp_path, monkeypatch) -> None:
    """读取视频信息从未接 should_abort，412 回退又把它拉长了——取消照样得生效。"""
    await _cancel_stops_quickly(
        monkeypatch,
        tmp_path,
        "读取视频信息",
        lambda h: h.hang("读取视频信息", "processor", "get_video_info"),
    )


@pytest.mark.asyncio
async def test_cancel_while_looking_for_subtitles(tmp_path, monkeypatch) -> None:
    await _cancel_stops_quickly(
        monkeypatch,
        tmp_path,
        "查找平台字幕",
        lambda h: h.hang("查找平台字幕", "processor", "fetch_subtitles"),
    )


@pytest.mark.asyncio
async def test_cancel_while_fetching_bilibili_ai_subtitles(tmp_path, monkeypatch) -> None:
    await _cancel_stops_quickly(
        monkeypatch,
        tmp_path,
        "查找B站AI字幕",
        lambda h: h.hang("查找B站AI字幕", "processor", "fetch_bilibili_subtitles"),
    )


@pytest.mark.asyncio
async def test_cancel_while_downloading_audio(tmp_path, monkeypatch) -> None:
    await _cancel_stops_quickly(
        monkeypatch,
        tmp_path,
        "准备音频",
        lambda h: h.hang("准备音频", "processor", "download_audio"),
    )


@pytest.mark.asyncio
async def test_cancel_while_transcribing(tmp_path, monkeypatch) -> None:
    await _cancel_stops_quickly(
        monkeypatch,
        tmp_path,
        "语音转写",
        lambda h: h.hang("语音转写", "transcriber", "transcribe"),
    )


@pytest.mark.asyncio
async def test_cancel_while_downloading_preview_video(tmp_path, monkeypatch) -> None:
    async def subtitles(*args, **kwargs):
        return SubtitleResult([TranscriptSegment(0, 5, "内容")], "zh", "platform_subtitle")

    def configure(h: Harness) -> None:
        h.stub("processor", "fetch_subtitles", subtitles)
        h.hang("提取截图", "processor", "download_preview_video")

    await _cancel_stops_quickly(
        monkeypatch, tmp_path, "提取截图", configure, _request(include_screenshots=True)
    )


@pytest.mark.asyncio
async def test_cancel_while_generating_notes(tmp_path, monkeypatch) -> None:
    """模型侧最典型的卡点：深度思考长时间不吐字，逐块轮询 should_abort 收不到取消。"""
    async def subtitles(*args, **kwargs):
        return SubtitleResult([TranscriptSegment(0, 5, "内容")], "zh", "platform_subtitle")

    def configure(h: Harness) -> None:
        h.stub("processor", "fetch_subtitles", subtitles)
        h.hang("生成笔记", "summarizer", "generate_summary")

    await _cancel_stops_quickly(monkeypatch, tmp_path, "生成笔记", configure)


@pytest.mark.asyncio
async def test_cancel_while_waiting_for_a_slot(tmp_path, monkeypatch) -> None:
    """并发打满时的排队中也要能取消，否则用户只能等前面的任务跑完。"""
    _install_fast_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "task_slots", asyncio.Semaphore(0))

    task_id = "cancel-queued"
    main.tasks[task_id] = main.new_task(task_id=task_id)
    job = asyncio.create_task(main.run_queued_video_task(task_id, _request()))
    main.running_jobs[task_id] = job
    try:
        queued = await _wait_for(lambda: main.tasks[task_id]["status"] == "queued")
        assert queued, "任务没有进入排队状态"

        await main.cancel_task(task_id)
        done, pending = await asyncio.wait({job}, timeout=CANCEL_BUDGET_SECONDS)
        for stuck in pending:
            stuck.cancel()
        assert done, "排队中的任务取消后没有停下"
        assert main.tasks[task_id]["status"] == "cancelled"
    finally:
        if not job.done():
            job.cancel()
        main.running_jobs.pop(task_id, None)
        main.tasks.pop(task_id, None)


@pytest.mark.asyncio
async def test_repeated_cancel_only_cancels_the_job_once(tmp_path, monkeypatch) -> None:
    """连点取消不能重复 job.cancel()：第二刀会落在终态处理过程中。"""
    _install_fast_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "task_slots", asyncio.Semaphore(0))

    task_id = "cancel-twice"
    main.tasks[task_id] = main.new_task(task_id=task_id)
    job = asyncio.create_task(main.run_queued_video_task(task_id, _request()))
    calls: list[int] = []

    class CountingJob:
        def done(self) -> bool:
            return job.done()

        def cancel(self) -> bool:
            calls.append(1)
            return job.cancel()

    main.running_jobs[task_id] = CountingJob()
    try:
        queued = await _wait_for(lambda: main.tasks[task_id]["status"] == "queued")
        assert queued
        await main.cancel_task(task_id)
        second = await main.cancel_task(task_id)
        assert second["status"] == "cancelling"
        assert calls == [1]
        done, _pending = await asyncio.wait({job}, timeout=CANCEL_BUDGET_SECONDS)
        assert done
        assert main.tasks[task_id]["status"] == "cancelled"
    finally:
        if not job.done():
            job.cancel()
        main.running_jobs.pop(task_id, None)
        main.tasks.pop(task_id, None)


@pytest.mark.asyncio
async def test_transcript_files_are_written_atomically(tmp_path, monkeypatch) -> None:
    """取消能落在写文件的 await 上，transcript.json 不能出现半截（复用转录以它为准）。"""
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    task_id = "atomic-transcript"
    (tmp_path / task_id).mkdir()

    await main.write_transcript_files(
        task_id,
        [TranscriptSegment(0, 5, "测试内容")],
        {"language": "zh", "source": "faster_whisper"},
    )

    task_dir = tmp_path / task_id
    assert not (task_dir / "transcript.json.tmp").exists()
    assert not (task_dir / "transcript.md.tmp").exists()
    payload = json.loads((task_dir / "transcript.json").read_text(encoding="utf-8"))
    assert payload["segments"][0]["text"] == "测试内容"
