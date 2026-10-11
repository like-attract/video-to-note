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
    --api-key -                           大模型 API Key（- 表示从标准输入读）
                                          也可设环境变量 VIDEOTONOTES_LLM_API_KEY；
                                          该地址已保存到本机时两者都可省略
    --wait SECONDS                        最长等待秒数（默认 1800）
    --out PATH                            把笔记写入文件（默认打印到 stdout）
    --server-cmd CMD                      本机服务没在跑时用它拉起服务（便携版 exe 路径，
                                          或 "python launcher.py"）；给一次会记住

服务不在运行时会自动后台拉起（无头模式：不开界面、不进托盘，闲置 15 分钟自己退出）。

退出码: 0 成功 | 1 参数/服务错误 | 2 任务失败 | 3 等待超时
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

PORT_RANGE = range(8000, 8020)
POLL_INTERVAL_SECONDS = 3
PROVIDERS = {"deepseek", "openai", "openai_gpt4", "openai_gpt35", "qwen", "glm", "moonshot", "custom"}
UPLOAD_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
# Key 走命令行会留在 shell 历史与进程列表里，所以默认鼓励用环境变量或 stdin
API_KEY_ENV = "VIDEOTONOTES_LLM_API_KEY"
# 服务的启动命令：一次给出后记住，之后 agent 甩链接就不需要用户先点任何东西
SERVER_CMD_ENV = "VIDEOTONOTES_SERVER_CMD"
HEADLESS_IDLE_SECONDS = 900
SERVICE_STARTUP_WAIT_SECONDS = 120
SK_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")
ECHO_PATTERN = re.compile(
    r"\b(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|bearer|sessdata|bili_jct)"
    r"\b([\"'’：:=\s]{1,4})([A-Za-z0-9_\-\.]{8,})",
    re.IGNORECASE,
)
_KNOWN_SECRETS: list[str] = []


def remember_secret(value: str) -> None:
    if value and len(value) >= 8:
        _KNOWN_SECRETS.append(value)


def redact(text: str) -> str:
    """打印或抛出前先洗一遍：服务端的 4xx 详情、告警文本都可能带 Key 原文。"""
    cleaned = SK_KEY_PATTERN.sub("sk-****", str(text))
    cleaned = ECHO_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}****", cleaned)
    for secret in _KNOWN_SECRETS:
        cleaned = cleaned.replace(secret, secret[:4] + "****")
    return cleaned


def die(message: str, code: int = 1) -> "None":
    print(f"[VideoToNo] {redact(message)}", file=sys.stderr)
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
        raise RuntimeError(redact(f"HTTP {exc.code} {url}: {detail or exc.reason}")) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 {url}：{exc.reason}") from exc


def port_is_listening(port: int, timeout: float = 0.3) -> bool:
    """先做一次裸 TCP 试探。

    没监听的端口会立刻被拒，几微秒就返回；把 `urlopen` 的 1.5 秒超时挨个撞满，
    扫一遍 8000-8019 要 60 秒——长到服务在两轮探测之间就按闲置退了（真机上踩过）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def probe_service() -> str | None:
    """扫一遍 8000-8019，有 VideoToNo 服务就返回它的 base url。"""
    for port in PORT_RANGE:
        if not port_is_listening(port):
            continue
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok" and payload.get("service") == "VideoToNo":
                return url
        except Exception:
            continue
    return None


def server_cmd_file() -> Path:
    """记住"服务怎么起"的位置。

    技能目录是用户自己复制出去的，随时会被再复制一份，不能当配置存放点。
    """
    root = os.environ.get("LOCALAPPDATA")
    base = Path(root) if root else Path.home() / ".videotono"
    return base / "VideoToNo" / "skill-server-cmd.txt"


def read_saved_server_cmd() -> str:
    try:
        return server_cmd_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_server_cmd(value: str) -> None:
    path = server_cmd_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError:
        pass  # 记不住只影响下次还要问一遍，不影响这一次跑完


def split_server_cmd(raw: str) -> list[str]:
    """按非 POSIX 模式切：Windows 路径里的反斜杠不能被当转义符吃掉。"""
    return [token for token in (t.strip('"') for t in shlex.split(raw, posix=False)) if token]


def repo_launcher_cmd() -> tuple[list[str], str] | None:
    """脚本还在仓库里（用户从源码用这套）时，直接用当前 Python 起 launcher。"""
    try:
        root = Path(__file__).resolve().parents[3]
    except IndexError:
        return None
    if (root / "launcher.py").is_file() and (root / "backend").is_dir():
        return [sys.executable, str(root / "launcher.py")], str(root)
    return None


def resolve_server_cmd(cli_value: str) -> tuple[list[str], str, str] | None:
    """取启动服务的命令、它的工作目录、以及命令的来源名。

    来源名要能对上号：报错时说"你上次给的路径找不到了"和"环境变量写错了"
    是两件事，混成一句"启动失败"用户只能瞎猜。
    """
    candidates = (
        ("--server-cmd", cli_value.strip()),
        (SERVER_CMD_ENV, os.environ.get(SERVER_CMD_ENV, "").strip()),
        ("上次记住的启动命令", read_saved_server_cmd()),
    )
    for source, raw in candidates:
        if not raw:
            continue
        argv = split_server_cmd(raw)
        if not argv:
            continue
        head = Path(argv[0]).expanduser()
        if head.suffix.lower() == ".exe":
            if not head.is_file():
                print(f"[VideoToNo] {source} 里的 exe 不存在：{head}", file=sys.stderr)
                continue
            # 只有一个 token 才是"便携版 exe 本身就是全部命令"：工作目录取 exe 同级，
            # 工作区（workspace/）要落在它旁边。带后续参数的（python.exe launcher.py……）
            # 沿用当前目录，否则解释器会在自己的 Scripts 目录里找 launcher.py。
            return argv, (str(head.parent) if len(argv) == 1 else ""), source
        return argv, "", source
    repo = repo_launcher_cmd()
    if repo is None:
        return None
    argv, cwd = repo
    return argv, cwd, "仓库内的 launcher.py"


def headless_command(argv: list[str]) -> list[str]:
    """把用户给的启动命令补成无头模式；用户自己写了参数就不覆盖。"""
    cmd = list(argv)
    if "--headless" not in cmd:
        cmd.append("--headless")
    if "--idle-exit" not in cmd:
        cmd += ["--idle-exit", str(HEADLESS_IDLE_SECONDS)]
    return cmd


def describe_idle(cmd: list[str]) -> str:
    """按真正下发给服务的值说话：说"15 分钟"而用户给的是 25 秒就是骗人。"""
    limit = HEADLESS_IDLE_SECONDS
    if "--idle-exit" in cmd:
        try:
            limit = float(cmd[cmd.index("--idle-exit") + 1])
        except (IndexError, ValueError):
            return "闲置退出时间按你给的命令，脚本不清楚"
    if limit <= 0:
        return "常驻运行，不会自动退出"
    if limit % 60 == 0:
        return f"闲置 {int(limit) // 60} 分钟后自动退出"
    return f"闲置 {int(limit)} 秒后自动退出"


def spawn_headless_service(argv: list[str], cwd: str) -> int:
    """把服务丢到后台跑，脚本不等它——窗口、托盘、浏览器都不该出现。"""
    cmd = headless_command(argv)
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if cwd:
        popen_kwargs["cwd"] = cwd
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **popen_kwargs).pid


def wait_for_service(timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        base = probe_service()
        if base:
            return base
        time.sleep(1.0)
    return None


def find_service(server_cmd: str = "") -> str:
    """找到本机服务；没在跑就按用户给过的命令后台拉起来。

    这一层是"甩链接就能拿到字幕稿"的关键：agent 不该因为用户没先双击 exe 就停下来。
    """
    base = probe_service()
    if base:
        return base

    resolved = resolve_server_cmd(server_cmd)
    if resolved is None:
        die(
            "本机 8000-8019 端口没有发现 VideoToNo 服务，也不知道该怎么启动它。\n"
            "请向用户要一次便携版 exe 的完整路径（只需一次，之后会自动记住并后台拉起），"
            '本次带上：--server-cmd "<……VideoToNo-portable.exe>"；'
            f"也可以设环境变量 {SERVER_CMD_ENV}，或由用户自己启动 VideoToNo。\n"
            f"（记住的路径存在 {server_cmd_file()}）"
        )
    argv, cwd, source = resolved
    cmd = headless_command(argv)

    print(f"[VideoToNo] 本机没有发现服务，正在后台启动（命令来源：{source}）…", flush=True)
    try:
        pid = spawn_headless_service(argv, cwd)
    except OSError as exc:
        die(
            f"启动服务失败：{exc}\n"
            "请用 --server-cmd 重新指一次便携版 exe 的完整路径，或让用户自己启动 VideoToNo。"
        )
    if source == "--server-cmd":
        save_server_cmd(server_cmd.strip())

    base = wait_for_service(SERVICE_STARTUP_WAIT_SECONDS)
    if base is None:
        # 后台进程的 stdout/stderr 都接到了空设备，它要是当场退出就一句话都不会留下，
        # 所以这里必须给一条能自己看到原因的命令。
        die(
            f"已发出启动命令（PID {pid}），但 {SERVICE_STARTUP_WAIT_SECONDS} 秒内没等到服务就绪。\n"
            "便携版第一次启动要先解压自身，可能还要再等一会儿；稍后重跑本命令即可复用它。\n"
            f"想看它到底为什么起不来，就在 VideoToNo 的目录里前台跑一遍：{' '.join(cmd)}\n"
            "打包版的日志在 exe 同级目录的 workspace/_app.log（该目录不可写时在 "
            "%LOCALAPPDATA%/VideoToNo/workspace/_app.log）。"
        )
    print(f"[VideoToNo] 服务已就绪：{base}（PID {pid}，后台无头运行，{describe_idle(cmd)}）")
    return base


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
            print(f"  | {redact(line)}")
        printed_logs = len(logs)
        status = task.get("status")
        if status == "completed":
            return task
        if status == "failed":
            error_text = str(task.get("error") or "未知原因")
            print(f"[VideoToNo] 任务失败：{redact(error_text)}", file=sys.stderr)
            if "API Key" in error_text:
                print(
                    "[VideoToNo] 提示：该接口地址本机没有可复用的 Key。把 Key 交给环境变量"
                    f" {API_KEY_ENV}（或 --api-key - 从标准输入读）后重跑，"
                    "或在网页「总结模型」填入 Key 并点「保存到本机」。",
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


def resolve_api_key(args) -> str:
    """Key 的来源：`--api-key -` 读标准输入 > `--api-key` 明文 > 环境变量。

    命令行参数会留在 shell 历史与进程列表里（任何同用户进程都能读到），
    所以用到那一种时明确提示换法。一个都不给时后端按接口地址复用本机已存 Key。
    """
    raw = (args.api_key or "").strip()
    if raw == "-":
        if sys.stdin.isatty():
            print("[VideoToNo] 请输入 API Key（一行，回车结束）：", file=sys.stderr)
        # 只读一行：管道里 printf 出来的 Key 不必等 EOF，终端里手输回车即可
        return sys.stdin.readline().strip()
    if raw:
        print(
            f"[VideoToNo] 提示：--api-key 会留在 shell 历史与进程列表里；下次请改用环境变量"
            f" {API_KEY_ENV}，或 --api-key - 从标准输入读。",
            file=sys.stderr,
        )
        return raw
    return os.environ.get(API_KEY_ENV, "").strip()


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
    parser.add_argument(
        "--api-key",
        default="",
        help=(
            f"大模型 API Key（该接口地址已保存到本机时可省略）。推荐 `--api-key -` 从标准输入读，"
            f"或设环境变量 {API_KEY_ENV}；直接写在命令行会留在 shell 历史与进程列表里"
        ),
    )
    parser.add_argument("--base-url", default=None, help="custom 供应商的接口地址")
    parser.add_argument("--custom-model", default=None, help="custom 供应商的模型名")
    parser.add_argument("--whisper-model", default="base", help="本地转写模型（默认 base）")
    parser.add_argument("--include-screenshots", action="store_true", help="提取视频截图")
    parser.add_argument("--screenshot-interval", type=int, default=30)
    parser.add_argument("--wait", type=int, default=1800, help="最长等待秒数（默认 1800）")
    parser.add_argument("--out", default=None, help="把笔记写入该 Markdown 文件")
    parser.add_argument(
        "--server-cmd",
        default="",
        help=(
            "本机服务没在跑时用它拉起服务：便携版 exe 的完整路径，或形如 "
            f'"python launcher.py"。给一次会记住（{SERVER_CMD_ENV} 环境变量等效）'
        ),
    )
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

    base = find_service(args.server_cmd)
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
        api_key = resolve_api_key(args)
        if api_key:
            # 记下明文，后面任何回显它的输出都要先被洗掉
            remember_secret(api_key)
        # 没有 Key 时不下发该字段：后端按接口地址复用本机已存的 Key，
        # 地址对不上会直接报错，而不是借用别的接口的 Key。
        if api_key or args.provider:
            llm_config: dict = {"model_type": args.provider or "deepseek"}
            if api_key:
                llm_config["api_key"] = api_key
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
                f"提交失败：{exc}\n提示：该接口地址本机没有可复用的 Key。优先问用户能否改走"
                " --transcript-only（全程不调用大模型，不需要 Key）；确实要成品笔记时，"
                f"让用户把 Key 交给环境变量 {API_KEY_ENV} 或以 --api-key - 从标准输入传入"
                "（配合 --provider，custom 另需 --base-url/--custom-model）。"
                "不要把 Key 写进命令行参数。",
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
