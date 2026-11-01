"""把语音转写放进子进程跑。

faster-whisper（CT2）与 sherpa-onnx 都是 C++ 库，在打包环境里一旦访问违例，
崩掉的是整个进程——托盘、界面、正在排队的任务一起没，而 Python 层连一行日志都
来不及写（2026-09-30 本机实测：belle-turbo-zh / small 都在加载模型后约 3 秒
静默消失，同一份模型和同一段音频在源码环境跑得好好的）。

这里把转写隔离成一个工人进程：协议是 stdin 收一行任务 JSON，stdout 回 NDJSON
事件；工人 native 崩掉时主程序只是把这次任务报成失败。
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from .transcript import TranscriptSegment

# 工人被取消后，主程序最多再等这么久就硬收
CANCEL_GRACE_SECONDS = 60.0

# 工人是「一行一个 JSON 事件」，done 事件里带着整段转录。asyncio 的默认行上限只有 64 KB，
# 一条二十分钟以上的转录就能顶破它：readline() 抛 ValueError → pump() 静默死掉 → 哨兵永不
# 入队 → 主循环永远卡在 events.get() 上（2026-10-17 issue #6，本机按同样脚本复现过）。
# 16 MB 足够装下十小时量级的转录，真撞上了也还有 pump() 里的兜底把任务判失败。
PIPE_LINE_LIMIT = 16 * 1024 * 1024


def child_command() -> list[str]:
    """工人怎么起：打包版复用自身（带 --asr-child），源码版直接跑本模块。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--asr-child"]
    return [sys.executable, "-m", "backend.asr_process"]


def use_child_process() -> bool:
    if getattr(sys, "frozen", False):
        return True
    return False


def encode_result(result: dict[str, Any]) -> dict[str, Any]:
    data = dict(result)
    data["segments"] = [
        seg.to_dict() if isinstance(seg, TranscriptSegment) else dict(seg)
        for seg in (result.get("segments") or [])
    ]
    return data


def decode_result(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    segments = []
    for seg in payload.get("segments") or []:
        if isinstance(seg, TranscriptSegment):
            segments.append(seg)
        else:
            segments.append(
                TranscriptSegment(
                    float(seg.get("start") or 0.0),
                    float(seg.get("end") or 0.0),
                    str(seg.get("text") or ""),
                )
            )
    data["segments"] = segments
    return data


class TranscriptionWorkerDied(RuntimeError):
    """工人没能正常交回结果：崩溃、被系统杀掉或自己退出。"""


def format_exit_code(code: int | None) -> str:
    """Windows 上 native 崩溃回的是 NTSTATUS（如 3221225477 = 0xC0000005），按十六进制才读得懂。"""
    if code is None:
        return "未知"
    if code < 0 or code >= 0xC0000000:
        return f"0x{code & 0xFFFFFFFF:08X}"
    return str(code)


async def run_in_child(
    media_path: Path,
    model_name: str,
    use_gpu: bool,
    title: str | None,
    cancel_event: Any = None,
    progress_callback: Callable[[float, float], None] | None = None,
) -> dict[str, Any]:
    """在工人进程里转写一个文件，事件照原样回传给主程序的回调。"""
    job = {
        "media_path": str(media_path),
        "model_name": model_name,
        "use_gpu": bool(use_gpu),
        "title": title,
    }
    process = await asyncio.create_subprocess_exec(
        *child_command(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        # stderr 不接管道：让它照原样进 _app.log，native 库的唠叨不会撑满管道把工人卡死
        stderr=None,
        # 单行事件（done 的 result 带着整段转录）可能远超 asyncio 默认的 64 KB，见 PIPE_LINE_LIMIT
        limit=PIPE_LINE_LIMIT,
    )
    assert process.stdin and process.stdout
    writer = process.stdin
    writer.write((json.dumps(job, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()

    events: asyncio.Queue[str] = asyncio.Queue()
    # 输出通道已经判死：不再等工人退出（它被 kill 了，等下去只会换来第二个挂死点）
    abandoned = False

    async def fail(message: str) -> None:
        """读取输出这条路已经不可用：报一条 error 事件，把工人和它的管道一起收掉。"""
        nonlocal abandoned
        abandoned = True
        await events.put(
            json.dumps({"event": "error", "message": message}, ensure_ascii=False) + "\n"
        )
        abandon_worker(process)

    async def pump() -> None:
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                await events.put(line.decode("utf-8", "replace"))
        except (asyncio.LimitOverrunError, ValueError):
            await fail(
                f"转写工人的单行输出超过了管道行上限（{PIPE_LINE_LIMIT // (1024 * 1024)} MB）；"
                "本次任务已停止，可换一个转写档位重试"
            )
        except Exception as exc:  # noqa: BLE001
            await fail(f"读取转写输出失败：{type(exc).__name__}: {exc}")
        finally:
            # 管道关了就说明工人没了（崩溃、被杀或自己退出）：用哨兵把等待的一方叫醒。
            # 无论正常结束还是异常退出都要发，等待方不能靠「猜」来醒。
            await events.put("")

    pump_task = asyncio.create_task(pump())
    cancel_task = asyncio.create_task(
        _send_cancel_when_asked(process, writer, cancel_event)
    )

    payload: dict[str, Any] | None = None
    failure: str | None = None
    cancelled = False
    exit_code: int | None = None
    try:
        while True:
            line = await events.get()
            if not line:
                break
            event = _parse_event(line)
            if event is None:
                continue
            kind = event.get("event")
            if kind == "progress":
                if progress_callback:
                    progress_callback(
                        float(event.get("done") or 0.0), float(event.get("total") or 0.0)
                    )
            elif kind == "cancelled":
                cancelled = True
                break
            elif kind == "done":
                payload = event.get("result") if isinstance(event.get("result"), dict) else {}
                break
            elif kind == "error":
                failure = str(event.get("message") or "转写工人未给出原因")
                break
        exit_code = process.returncode if abandoned else await _reap(process, cancelled)
    finally:
        # 放在 finally 里：循环里任何一处抛异常（比如 progress 回调炸了）都不会留下悬挂协程
        cancel_task.cancel()
        pump_task.cancel()
        await asyncio.gather(cancel_task, pump_task, return_exceptions=True)
        if exit_code is None and process.returncode is None:
            # 循环是抛异常退出的：工人还活着就会继续占着 GPU，而主程序已经认定这次任务结束
            abandon_worker(process)

    if cancelled:
        from .whisper_asr import TranscriptionCancelledError

        raise TranscriptionCancelledError("转写已取消")
    if payload is None:
        reason = failure or f"退出码 {format_exit_code(exit_code)}"
        raise TranscriptionWorkerDied(
            f"语音转写进程异常退出（{reason}）；本次任务已停止，程序本身仍在运行，"
            "可换一个转写档位重试"
        )
    return decode_result(payload)


async def _send_cancel_when_asked(
    process: asyncio.subprocess.Process, writer: asyncio.StreamWriter, cancel_event: Any
) -> None:
    if cancel_event is None:
        return
    while process.returncode is None:
        if cancel_event.is_set():
            try:
                writer.write(b'{"cmd": "cancel"}\n')
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                return
            return
        await asyncio.sleep(0.5)


def abandon_worker(process: asyncio.subprocess.Process) -> None:
    """杀掉工人，并把它的管道一起关掉。

    只 kill 是不够的：行超限时 StreamReader 已经把管道暂停（不再有挂起的读），工人死掉
    之后这条管道也等不到 EOF，transport 就永远不算 disconnected，`_try_finish()` 不触发，
    `process.wait()` 的等待者永远不会被叫醒 —— 等于换个地方挂死。2026-11-01 本机实测：
    kill 后 returncode 0.1 秒就变成 1，`wait()` 却一直不返回；close() 之后立刻返回。
    """
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass
    transport = getattr(process, "_transport", None)
    close = getattr(transport, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001
        pass


async def _reap(process: asyncio.subprocess.Process, cancelled: bool) -> int | None:
    if process.returncode is not None:
        return process.returncode
    try:
        if cancelled:
            await asyncio.wait_for(process.wait(), timeout=CANCEL_GRACE_SECONDS)
        else:
            await process.wait()
    except asyncio.TimeoutError:
        # 宽限期过了就硬收；收完再给 5 秒看它退没退，退不掉也不再等（同一条管道可能也收不到 EOF）
        abandon_worker(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
    return process.returncode


def _parse_event(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text or not text.startswith("{"):
        return None
    try:
        event = json.loads(text)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


# --------------------------------------------------------------------------
# 工人这一侧
# --------------------------------------------------------------------------

_write_lock = threading.Lock()


def _emit(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, default=str)
    with _write_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def use_utf8_pipes() -> None:
    """把工人的 stdin/stdout 钉成 UTF-8——管道另一端的 `pump()` 就是按 UTF-8 解的。

    打包版被重定向到管道时 `sys.stdout` 不是 `None`，Python 会按本机码页建流（本机是
    cp936）。转写结果里只要有一个 GBK 装不下的字符（2026-10-04 实测是 U+FFFD），`_emit`
    就抛 `UnicodeEncodeError`；它发生在 `except Exception` 的处理里，于是异常逃出
    `child_main`，noconsole 下变成 PyInstaller 的模态错误框——工人既不回 `done` 也不退出，
    主程序对"工人一直不回话"没有超时，任务就永远停在转写中。
    """
    for name, fd, mode in (("stdout", 1, "w"), ("stdin", 0, "r")):
        stream = getattr(sys, name)
        if stream is None:
            setattr(
                sys,
                name,
                open(fd, mode, encoding="utf-8", buffering=1 if mode == "w" else -1,
                     closefd=False),
            )
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def child_main() -> int:
    """读一行任务，转写完把结果发回 stdout。由 `--asr-child` 或 `-m backend.asr_process` 进入。"""
    from .main import run_transcription_local
    from .whisper_asr import TranscriptionCancelledError

    # 打包版是 noconsole 应用：管道句柄是主程序给的，编码不能交给本机码页
    use_utf8_pipes()

    job_line = sys.stdin.readline()
    if not job_line.strip():
        _emit({"event": "error", "message": "转写工人没有收到任务"})
        return 2
    try:
        job = json.loads(job_line)
    except ValueError as exc:
        _emit({"event": "error", "message": f"任务描述无法解析：{exc}"})
        return 2

    cancel_event = threading.Event()

    def watch_cancel() -> None:
        for line in sys.stdin:
            event = _parse_event(line)
            if event and event.get("cmd") == "cancel":
                cancel_event.set()
                return

    threading.Thread(target=watch_cancel, daemon=True).start()

    def report(done: float, total: float) -> None:
        _emit({"event": "progress", "done": done, "total": total})

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            run_transcription_local(
                Path(str(job.get("media_path") or "")),
                str(job.get("model_name") or "base"),
                bool(job.get("use_gpu")),
                job.get("title"),
                cancel_event,
                report,
            )
        )
    except TranscriptionCancelledError:
        _emit({"event": "cancelled"})
        return 3
    except Exception as exc:
        _emit({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        loop.close()
    _emit({"event": "done", "result": encode_result(result)})
    return 0


if __name__ == "__main__":
    raise SystemExit(child_main())
