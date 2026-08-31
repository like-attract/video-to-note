#!/usr/bin/env python3
"""VideoToNo Agent Skill 脚本：一条命令完成"视频 → 笔记"。

自动探测本机 VideoToNo 服务端口（8000-8019），提交任务、轮询进度、
输出完整 Markdown 笔记。仅依赖 Python 标准库。

用法:
    python video_note.py "<视频链接或本地文件路径>" [选项]

常用选项:
    --style {detailed,faithful,concise}   笔记风格（默认 detailed）
    --transcript-only                     只做到带时间轴转录，不调用大模型（无需 API Key）
    --no-reuse                            禁止复用同链接已有转录，强制重新转写
    --provider {deepseek,openai,qwen,glm,moonshot,custom}
    --api-key KEY                         大模型 API Key（该地址已保存到本机可省略）
    --wait SECONDS                        最长等待秒数（默认 1800）
    --out PATH                            把笔记写入文件（默认打印到 stdout）

退出码: 0 成功 | 1 参数/服务错误 | 2 任务失败 | 3 等待超时
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PORT_RANGE = range(8000, 8020)
POLL_INTERVAL_SECONDS = 3
PROVIDERS = {"deepseek", "openai", "openai_gpt4", "openai_gpt35", "qwen", "glm", "moonshot", "custom"}
UPLOAD_LIMIT_BYTES = 2 * 1024 * 1024 * 1024


def die(message: str, code: int = 1) -> "None":
    print(f"[VideoToNo] {message}", file=sys.stderr)
    sys.exit(code)


def http_json(method: str, url: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "VideoToNo-Skill/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 {url}：{exc.reason}") from exc


def find_service() -> str:
    """扫描 8000-8019，返回 VideoToNo 服务的 base url。"""
    for port in PORT_RANGE:
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok" and payload.get("service") == "VideoToNo":
                return url
        except Exception:
            continue
    die(
        "本机 8000-8019 端口未发现 VideoToNo 服务。请先启动 VideoToNo"
        "（便携版 exe，或源码目录执行 python launcher.py）后重试。"
    )


def pick_saved_channel(base: str) -> dict | None:
    """本机只保存过一个接口的 Key 时沿用它的通道；保存了多个则返回 None（不猜）。"""
    try:
        status = http_json("GET", f"{base}/api/llm-keys", timeout=10)
    except RuntimeError:
        return None
    entries = [entry for entry in (status.get("entries") or []) if entry.get("has_key")]
    if len(entries) != 1:
        return None
    entry = entries[0]
    channel = {
        "model_type": entry.get("provider") or "custom",
        "base_url": entry.get("base_url"),
        "model": entry.get("model"),
    }
    return {key: value for key, value in channel.items() if value}


def upload_file(base: str, path: Path) -> str:
    size = path.stat().st_size
    if size > UPLOAD_LIMIT_BYTES:
        die(f"文件超过上传上限 2GB：{path}（{size} 字节）")
    boundary = f"----videotono{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        f"{base}/api/upload",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        die(f"上传失败：HTTP {exc.code} {detail or exc.reason}")
    print(f"[VideoToNo] 本地文件已上传（{size} 字节）")
    return payload["task_id"]


def poll_task(base: str, task_id: str, wait_seconds: int) -> dict:
    deadline = time.monotonic() + wait_seconds
    printed_logs = 0
    while True:
        task = http_json("GET", f"{base}/api/task/{task_id}", timeout=15)
        logs = task.get("logs") or []
        for line in logs[printed_logs:]:
            print(f"  | {line}")
        printed_logs = len(logs)
        status = task.get("status")
        if status == "completed":
            return task
        if status == "failed":
            error_text = str(task.get("error") or "未知原因")
            print(f"[VideoToNo] 任务失败：{error_text}", file=sys.stderr)
            if "API Key" in error_text:
                print(
                    "[VideoToNo] 提示：该接口地址本机没有可复用的 Key。用 --api-key 提供，"
                    "或在网页「总结模型」填入 Key 后点「保存到本机」。",
                    file=sys.stderr,
                )
            print(f"[VideoToNo] 可复用中间产物重试，task_id={task_id}", file=sys.stderr)
            sys.exit(2)
        if status == "cancelled":
            die(f"任务已取消（task_id={task_id}）", code=2)
        if time.monotonic() > deadline:
            print(
                f"[VideoToNo] 等待超时（{wait_seconds} 秒），任务仍在后台运行：task_id={task_id}",
                file=sys.stderr,
            )
            print(f"[VideoToNo] 稍后可用 GET {base}/api/task/{task_id} 继续查询", file=sys.stderr)
            sys.exit(3)
        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="VideoToNo：视频 → 带时间轴的 Markdown 笔记")
    parser.add_argument("source", help="视频链接（B站/抖音/YouTube）或本地媒体文件路径")
    parser.add_argument("--style", choices=["detailed", "faithful", "concise"], default="detailed")
    parser.add_argument(
        "--transcript-only",
        action="store_true",
        help="只做到带时间轴转录为止，不调用大模型（无需 API Key，笔记风格由你决定）",
    )
    parser.add_argument(
        "--no-reuse",
        action="store_true",
        help="禁止复用同链接已有转录，强制重新转写（默认只在笔记模式从头处理）",
    )
    parser.add_argument("--provider", default=None, help=f"大模型供应商：{', '.join(sorted(PROVIDERS))}")
    parser.add_argument("--api-key", default="", help="大模型 API Key（该接口地址已保存到本机时可省略）")
    parser.add_argument("--base-url", default=None, help="custom 供应商的接口地址")
    parser.add_argument("--custom-model", default=None, help="custom 供应商的模型名")
    parser.add_argument("--whisper-model", default="base", help="本地转写模型（默认 base）")
    parser.add_argument("--include-screenshots", action="store_true", help="提取视频截图")
    parser.add_argument("--screenshot-interval", type=int, default=30)
    parser.add_argument("--wait", type=int, default=1800, help="最长等待秒数（默认 1800）")
    parser.add_argument("--out", default=None, help="把笔记写入该 Markdown 文件")
    args = parser.parse_args()

    if args.provider and args.provider not in PROVIDERS:
        die(f"不支持的供应商：{args.provider}（可选：{', '.join(sorted(PROVIDERS))}）")
    if args.provider == "custom" and not (args.base_url and args.custom_model):
        die("custom 供应商需要 --base-url 和 --custom-model")
    transcript_only = args.transcript_only
    if transcript_only and (
        args.api_key.strip() or args.provider or args.include_screenshots
    ):
        die(
            "--transcript-only 全程不调用大模型，也不做截图："
            "--api-key/--provider/--include-screenshots 都不需要。"
            "去掉这些参数，或去掉 --transcript-only 改走笔记模式。"
        )

    base = find_service()
    print(f"[VideoToNo] 服务已连接：{base}")

    source_path = Path(args.source)
    if transcript_only:
        endpoint = "/api/transcribe"
        # 转录模式不下发 llm_config：这条路上根本没有大模型调用
        payload: dict = {
            "prefer_subtitles": True,
            "processing_mode": "restart" if args.no_reuse else "reuse",
            "whisper_model": args.whisper_model,
        }
    else:
        endpoint = "/api/summarize"
        # 省略 --api-key 时不下发该字段：后端按接口地址复用本机已存的 Key，
        # 地址对不上会直接报错，而不是借用别的接口的 Key。
        if args.api_key.strip() or args.provider:
            llm_config: dict = {"model_type": args.provider or "deepseek"}
            if args.api_key.strip():
                llm_config["api_key"] = args.api_key.strip()
            if args.base_url:
                llm_config["base_url"] = args.base_url
            if args.custom_model:
                llm_config["model"] = args.custom_model
        else:
            # 什么都没指定：本机只存过一个接口时就沿用它的通道，存了多个则不猜。
            llm_config = pick_saved_channel(base) or {"model_type": "deepseek"}
        payload = {
            "summary_style": args.style,
            "prefer_subtitles": True,
            "processing_mode": "restart",
            "whisper_model": args.whisper_model,
            "include_screenshots": args.include_screenshots,
            "screenshot_interval": max(5, min(300, args.screenshot_interval)),
            "llm_config": llm_config,
        }

    if source_path.is_file():
        payload["upload_task_id"] = upload_file(base, source_path)
        payload["video_url"] = ""
    else:
        if not args.source.strip().lower().startswith(("http://", "https://")) and "b23.tv" not in args.source:
            die(f"输入既不是有效链接也不是存在的本地文件：{args.source}")
        payload["video_url"] = args.source.strip()

    print("[VideoToNo] 正在提交任务…")
    try:
        result = http_json("POST", f"{base}{endpoint}", payload, timeout=60)
    except RuntimeError as exc:
        if transcript_only:
            die(f"提交失败：{exc}")
        if "API Key" in str(exc) or "422" in str(exc):
            die(
                f"提交失败：{exc}\n提示：需要大模型 API Key。请向用户询问供应商与 Key，"
                "用 --provider/--api-key 重新运行（custom 另需 --base-url/--custom-model）。",
                code=1,
            )
        raise

    task_id = result.get("task_id")
    if not task_id:
        die(f"提交失败：服务未返回 task_id（{result}）")
    if result.get("reused_task_id"):
        print(f"[VideoToNo] 已复用任务 {result['reused_task_id']} 的转录结果")
    print(f"[VideoToNo] 任务已创建：{task_id}，开始等待完成…")

    task = poll_task(base, task_id, args.wait)
    task_result = task.get("result") or {}

    if transcript_only:
        transcript = http_json(
            "GET",
            f"{base}/api/task/{task_id}/transcript?output_format=markdown",
            timeout=60,
        )
        body = transcript.get("text") or ""
        label = "转录"
    else:
        body = task_result.get("markdown") or ""
        label = "笔记"
    if not body:
        die(f"任务完成但未返回{label}内容", code=2)
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"[VideoToNo] {label}已保存：{args.out}")
    else:
        print("\n" + "=" * 58 + "\n")
        print(body)
    output_dir = task_result.get("output_directory")
    if output_dir:
        print(f"\n[VideoToNo] 产物目录：{output_dir}")


if __name__ == "__main__":
    main()
