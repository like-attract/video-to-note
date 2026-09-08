"""HTTP 单文件下载：断点续传（Range）+ 重试 + Content-Length 完整性校验。

whisper 与 paraformer 两条模型下载线共用；损坏缓存的历史教训都在这个实现里：
连接中断但未抛异常时会得到截断文件，必须保留 .part 断点续传，
绝不把不完整文件改名成正式文件。
"""
from __future__ import annotations

import asyncio
import threading
import time
import urllib.request
from pathlib import Path


def download_to_file(
    url: str,
    target: Path,
    headers: dict[str, str],
    cancel_event: threading.Event | None = None,
    attempts: int = 3,
    timeout: int = 60,
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
                with open(part, mode) as out:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise asyncio.CancelledError("任务已取消")
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
            actual_size = part.stat().st_size
            if actual_size == 0:
                part.unlink(missing_ok=True)
                return False, RuntimeError("下载内容为空")
            if expected_total is not None and actual_size != expected_total:
                last_error = RuntimeError(
                    f"下载不完整：已接收 {actual_size} 字节，预期 {expected_total} 字节"
                )
                time.sleep(1.5 * (attempt + 1))
                continue
            if target.exists():
                target.unlink()
            part.rename(target)
            return True, None
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    return False, last_error
