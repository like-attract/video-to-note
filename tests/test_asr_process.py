"""转写工人子进程的协议测试。

这条链要钉的是"崩了也不能带走主程序"：工人异常退出时，主程序拿到的是一个可以
显示的失败原因，而不是一起没命。用 `python -c` 起假工人，协议与真工人一致。
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import threading

import pytest

from backend import asr_process, main
from backend.transcript import TranscriptSegment

OK_SCRIPT = """
import json, sys
job = json.loads(sys.stdin.readline())
print(json.dumps({"event": "progress", "done": 1.0, "total": 2.0}), flush=True)
print(json.dumps({"event": "done", "result": {
    "segments": [{"start": 0.0, "end": 1.5, "text": "你好"},
                 {"start": 1.5, "end": 2.0, "text": job["model_name"]}],
    "language": "zh", "device": "cpu", "model": "base", "requested_model": "base",
    "duration": 2.0, "transcribe_seconds": 0.1,
}}), flush=True)
"""

# 用负数退出：Windows 上这会原样变成 0xC0000005，正是 native 访问违例的退出码
CRASH_SCRIPT = "import os; os._exit(-1073741819)"

ERROR_SCRIPT = (
    "import json,sys; sys.stdin.readline();"
    "print(json.dumps({'event':'error','message':'RuntimeError: 模型文件损坏'}), flush=True)"
)

CANCEL_SCRIPT = """
import json, sys
sys.stdin.readline()
for line in sys.stdin:
    event = json.loads(line or "{}")
    if event.get("cmd") == "cancel":
        print(json.dumps({"event": "cancelled"}), flush=True)
        break
"""

# 一条 done 事件就把整段转录装在一行里：large-v3 跑 20 分钟以上的音频，这行 JSON 轻易超过
# asyncio 默认的 64 KB 行上限（issue #6）。这里用纯 ASCII 把体积撑到约 150 KB，避免编码干扰。
BIG_LINE_SCRIPT = """
import json, sys
sys.stdin.readline()
text = "lorem ipsum dolor sit amet " * 6000
print(json.dumps({"event": "progress", "done": 1.0, "total": 2.0}), flush=True)
print(json.dumps({"event": "done", "result": {
    "segments": [{"start": 0.0, "end": 1.0, "text": text}],
    "language": "zh", "device": "cuda", "model": "large-v3",
    "requested_model": "large-v3", "duration": 1.0, "transcribe_seconds": 0.1,
}}, ensure_ascii=False), flush=True)
"""

# 等待方靠「哨兵」醒，任何一条路漏发哨兵都是永久挂住 —— 所以这里一律加超时兜底，
# 让回归失败成 TimeoutError 而不是把整个测试进程吊死。
WAIT_SECONDS = 30.0


def fake_child(script: str):
    return lambda: [sys.executable, "-c", script]


@pytest.mark.asyncio
async def test_run_in_child_streams_progress_and_decodes_segments(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(asr_process, "child_command", fake_child(OK_SCRIPT))
    seen: list[tuple[float, float]] = []

    result = await asr_process.run_in_child(
        tmp_path / "audio.mp3",
        "belle-turbo-zh",
        False,
        "某个视频",
        None,
        lambda done, total: seen.append((done, total)),
    )

    assert seen == [(1.0, 2.0)]
    assert result["model"] == "base"
    assert result["segments"] == [
        TranscriptSegment(0.0, 1.5, "你好"),
        TranscriptSegment(1.5, 2.0, "belle-turbo-zh"),
    ]


@pytest.mark.asyncio
async def test_native_crash_of_worker_becomes_a_failing_task(tmp_path, monkeypatch) -> None:
    """工人访问违例时只能让任务失败：整条命令是 `python -c` 起的假工人，崩的从来不是主程序。"""
    monkeypatch.setattr(asr_process, "child_command", fake_child(CRASH_SCRIPT))

    with pytest.raises(asr_process.TranscriptionWorkerDied) as raised:
        await asr_process.run_in_child(tmp_path / "a.mp3", "small", False, "标题")

    # 同一个 `os._exit(-1073741819)`：Windows 原样回 0xC0000005，POSIX 只留低 8 位（= 5）。
    # 这条要钉的是"退出码进了文案"，怎么渲染另有 test_exit_code_is_printed_as_ntstatus 管
    expected_code = 0xC0000005 if sys.platform == "win32" else 5
    assert asr_process.format_exit_code(expected_code) in str(raised.value)
    assert "程序本身仍在运行" in str(raised.value)


@pytest.mark.asyncio
async def test_worker_error_message_is_passed_through(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(asr_process, "child_command", fake_child(ERROR_SCRIPT))

    with pytest.raises(asr_process.TranscriptionWorkerDied) as raised:
        await asr_process.run_in_child(tmp_path / "a.mp3", "small", False, "标题")

    assert "模型文件损坏" in str(raised.value)


@pytest.mark.asyncio
async def test_cancel_reaches_the_worker(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(asr_process, "child_command", fake_child(CANCEL_SCRIPT))
    cancel_event = threading.Event()
    cancel_event.set()

    from backend.whisper_asr import TranscriptionCancelledError

    with pytest.raises(TranscriptionCancelledError):
        await asr_process.run_in_child(
            tmp_path / "a.mp3", "small", False, "标题", cancel_event
        )


@pytest.mark.asyncio
async def test_oversized_done_line_is_read_instead_of_hanging(tmp_path, monkeypatch) -> None:
    """issue #6：单行 done 事件超过 asyncio 默认 64 KB 时，任务必须正常拿到转录。

    修复前 `readline()` 抛 ValueError、`pump()` 静默死掉、哨兵永不入队，主循环永远卡在
    `events.get()` 上；界面停在「语音转写 N%」，GPU 还在算，任务既不失败也不完成。
    """
    monkeypatch.setattr(asr_process, "child_command", fake_child(BIG_LINE_SCRIPT))

    result = await asyncio.wait_for(
        asr_process.run_in_child(tmp_path / "a.mp3", "large-v3", True, "长音频"),
        timeout=WAIT_SECONDS,
    )

    assert len(result["segments"]) == 1
    assert len(result["segments"][0].text) > 64 * 1024


@pytest.mark.asyncio
async def test_line_limit_exceeded_fails_the_task_instead_of_hanging(
    tmp_path, monkeypatch
) -> None:
    """上限真被顶破时也要收口：报可读原因并叫停工人，绝不能换个地方继续挂。"""
    monkeypatch.setattr(asr_process, "PIPE_LINE_LIMIT", 512)
    monkeypatch.setattr(asr_process, "child_command", fake_child(BIG_LINE_SCRIPT))

    with pytest.raises(asr_process.TranscriptionWorkerDied) as raised:
        await asyncio.wait_for(
            asr_process.run_in_child(tmp_path / "a.mp3", "large-v3", True, "长音频"),
            timeout=WAIT_SECONDS,
        )

    assert "单行输出超过了管道行上限" in str(raised.value)
    assert "程序本身仍在运行" in str(raised.value)


@pytest.mark.asyncio
async def test_progress_callback_crash_does_not_leak_the_pump_task(
    tmp_path, monkeypatch
) -> None:
    """回调抛异常时 pump/cancel 两个协程也要被收走，不留悬挂任务。"""
    monkeypatch.setattr(asr_process, "child_command", fake_child(OK_SCRIPT))
    created: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def tracking_create_task(coro, **kwargs):
        task = real_create_task(coro, **kwargs)
        created.append(task)
        return task

    monkeypatch.setattr(asr_process.asyncio, "create_task", tracking_create_task)

    def boom(done: float, total: float) -> None:
        raise RuntimeError("进度回调炸了")

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(
            asr_process.run_in_child(
                tmp_path / "a.mp3", "base", False, "标题", None, boom
            ),
            timeout=WAIT_SECONDS,
        )

    await asyncio.sleep(0)
    assert created and all(task.done() for task in created)


def test_exit_code_is_printed_as_ntstatus() -> None:
    assert asr_process.format_exit_code(3221225477) == "0xC0000005"
    assert asr_process.format_exit_code(-11) == "0xFFFFFFF5"
    assert asr_process.format_exit_code(1) == "1"
    assert asr_process.format_exit_code(None) == "未知"


def test_encode_decode_round_trip() -> None:
    payload = asr_process.encode_result(
        {"segments": [TranscriptSegment(1.0, 2.0, "字")], "device": "cpu"}
    )
    assert payload["segments"] == [{"start": 1.0, "end": 2.0, "text": "字"}]
    assert json.dumps(payload, ensure_ascii=False, default=str)
    decoded = asr_process.decode_result(payload)
    assert decoded["segments"] == [TranscriptSegment(1.0, 2.0, "字")]
    assert decoded["device"] == "cpu"


@pytest.mark.asyncio
async def test_packaged_builds_route_transcription_to_the_worker(
    monkeypatch, tmp_path
) -> None:
    """打包版走工人、源码版留在进程里：CI 从源码跑，测的就是这条分岔。"""
    calls: list[str] = []

    async def fake_child(*args, **kwargs):
        calls.append("child")
        return {"segments": []}

    async def fake_local(*args, **kwargs):
        calls.append("local")
        return {"segments": []}

    monkeypatch.setattr(main.asr_process, "run_in_child", fake_child)
    monkeypatch.setattr(main, "run_transcription_local", fake_local)

    monkeypatch.setattr(main.asr_process, "use_child_process", lambda: True)
    await main.run_transcription(tmp_path / "a.mp3", "base", False, "标题", None)
    monkeypatch.setattr(main.asr_process, "use_child_process", lambda: False)
    await main.run_transcription(tmp_path / "a.mp3", "base", False, "标题", None)

    assert calls == ["child", "local"]


def test_worker_is_the_same_executable_when_frozen(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"D:\x\VideoToNo-1.4.3-portable.exe")
    assert asr_process.child_command() == [
        r"D:\x\VideoToNo-1.4.3-portable.exe",
        "--asr-child",
    ]
    assert asr_process.use_child_process() is True


def test_source_checkout_uses_plain_python_module(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert asr_process.child_command() == [sys.executable, "-m", "backend.asr_process"]
    assert asr_process.use_child_process() is False


def test_worker_stdout_does_not_use_the_machine_codepage(monkeypatch) -> None:
    """管道两端都要是 UTF-8：按 GBK 建流时，一个 GBK 装不下的字符能把工人卡死。

    2026-10-04 打包版实测：转写跑完要发 `done` 时 `_emit` 抛
    `'gbk' codec can't encode character '\\ufffd'`，异常从 `except Exception` 的补救里
    再逃出来，noconsole 下变成 PyInstaller 的模态错误框——主程序收不到 `done`，也没有
    超时，任务永远停在转写中。顺带这条路把中文写成了 GBK 字节，主程序按 UTF-8 解会全变问号。
    """
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp936"))

    asr_process.use_utf8_pipes()
    asr_process._emit({"event": "done", "text": "中文转录，末尾带 \ufffd 这种字符"})

    payload = json.loads(raw.getvalue().decode("utf-8"))
    assert payload["text"] == "中文转录，末尾带 \ufffd 这种字符"


def test_worker_builds_utf8_streams_when_stdio_is_missing(monkeypatch) -> None:
    """打包版没有控制台时 `sys.stdout` 可能是 None，得自己按 UTF-8 建在管道句柄上。"""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stdin", None)

    asr_process.use_utf8_pipes()

    assert sys.stdout.encoding.replace("-", "").lower() == "utf8"
    assert sys.stdin.encoding.replace("-", "").lower() == "utf8"

