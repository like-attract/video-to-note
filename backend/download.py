"""HTTP 单文件下载：断点续传（Range）+ 重试 + Content-Length 完整性校验。

whisper 与 paraformer 两条模型下载线共用；损坏缓存的历史教训都在这个实现里：
连接中断但未抛异常时会得到截断文件，必须保留 .part 断点续传，
绝不把不完整文件改名成正式文件。

``progress_callback(已下载, 总字节)`` 只在调用方需要展示进度时传入；
总字节来自 Content-Length，服务端不返回时传 0（前端显示不确定进度）。
"""
from __future__ import annotations

import asyncio
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[int, int], None]

# 两次进度回调的最小间隔：256KB 块在多 MB/s 链路上会每秒回几十次，
# 这些回调最终要跨进程/跨线程更新任务状态，没必要这么密。
_PROGRESS_INTERVAL_SECONDS = 0.4


def download_to_file(
    url: str,
    target: Path,
    headers: dict[str, str],
    cancel_event: threading.Event | None = None,
    attempts: int = 4,
    timeout: int = 90,
    progress_callback: ProgressCallback | None = None,
) -> tuple[bool, Exception | None]:
    """下载 url 到 target。返回 (是否成功, 最后一次错误)；成功时错误为 None。"""
    part = target.with_suffix(target.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(attempts):
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError("任务已取消")
        try:
            resume = part.stat().st_size if part.is_file() else 0
            request_headers = dict(headers)
            if resume:
                request_headers["Range"] = f"bytes={resume}-"
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status == 200 and resume:
                    # 服务端忽略 Range，从头重下
                    part.unlink(missing_ok=True)
                    resume = 0
                expected_total: int | None = None
                content_length = response.headers.get("Content-Length")
                if content_length and content_length.isdigit():
                    expected_total = int(content_length) + resume
                mode = "ab" if resume else "wb"
                downloaded = resume
                last_report = 0.0
                with open(part, mode) as out:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise asyncio.CancelledError("任务已取消")
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback is not None:
                            now = time.monotonic()
                            if (
                                now - last_report >= _PROGRESS_INTERVAL_SECONDS
                                or (
                                    expected_total is not None
                                    and downloaded >= expected_total
                                )
                            ):
                                last_report = now
                                progress_callback(downloaded, expected_total or 0)
            actual_size = part.stat().st_size
            if actual_size == 0:
                part.unlink(missing_ok=True)
                return False, RuntimeError("下载内容为空")
            if expected_total is not None and actual_size != expected_total:
                last_error = RuntimeError(
                    f"下载不完整：已接收 {actual_size} 字节，预期 {expected_total} 字节"
                )
                if progress_callback is not None:
                    progress_callback(actual_size, expected_total)
                time.sleep(1.5 * (attempt + 1))
                continue
            if target.exists():
                target.unlink()
            part.rename(target)
            if progress_callback is not None:
                progress_callback(actual_size, actual_size)
            return True, None
        except Exception as exc:
            last_error = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                # 仓库里没有这个文件：重试只会白等，立刻返回让调用方换名或换源
                return False, exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 416:
                # 断点比服务端文件还长（上次拿到了另一个 revision）：丢掉重下
                part.unlink(missing_ok=True)
            time.sleep(1.5 * (attempt + 1))
    return False, last_error
