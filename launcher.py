"""VideoToNo 启动器。

开发模式（`python launcher.py`）：
  - 控制台窗口显示地址，Ctrl+C 退出

打包模式（PyInstaller onefile，--noconsole）：
  - 无控制台窗口，服务启动后驻留系统托盘
  - 托盘菜单：打开界面 / 查看日志 / 退出
  - 日志写入工作目录的 _app.log

无头模式（`--headless`，两种模式都可用，主要给 agent 后台拉起服务用）：
  - 不开浏览器、不建托盘图标
  - 默认闲置 15 分钟且没有在建任务时自己退出（`--idle-exit 秒` 调整，0 表示常驻）
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable

from backend import update_check
from backend.update_check import GITHUB_LATEST_RELEASE_API, version_tuple

VERSION = "1.4.8"
DEFAULT_PORT = 8000
PORT_SCAN_RANGE = 20
START_TIMEOUT_SECONDS = 60
# 无头实例没人能点"退出"，靠闲置收摊；比一次转写短就会把服务提前关掉，给足余量
DEFAULT_IDLE_EXIT_SECONDS = 900.0
IDLE_POLL_SECONDS = 5.0
APP_NAME = f"VideoToNo v{VERSION}"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def configure_runtime_dirs() -> None:
    """设置工作目录：打包模式默认放在 exe 旁边（便携），允许环境变量覆盖。"""
    if is_frozen():
        exe_dir = base_dir()
        default_workspace = exe_dir / "workspace"
        try:
            default_workspace.mkdir(parents=True, exist_ok=True)
            probe = default_workspace / ".write_test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError:
            # exe 所在目录不可写时（如 Program Files），退回用户目录
            local_app_data = os.environ.get("LOCALAPPDATA")
            fallback_root = Path(local_app_data) if local_app_data else Path.home() / ".videotono"
            default_workspace = fallback_root / "VideoToNo" / "workspace"
            default_workspace.mkdir(parents=True, exist_ok=True)
    else:
        default_workspace = base_dir() / "workspace"
        default_workspace.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("VIDEOTONOTES_WORKSPACE", str(default_workspace))
    # 环境变量覆盖的路径也要确保存在：打包版写日志文件前依赖此目录，缺失会启动崩溃
    Path(os.environ["VIDEOTONOTES_WORKSPACE"]).mkdir(parents=True, exist_ok=True)


RUN_MODE = "portable" if is_frozen() else "dev"


def server_alive(
    url: str,
    mode: str | None = None,
    version: str | None = None,
) -> bool:
    """健康检查；可同时隔离运行模式和应用版本。

    新旧便携版不能只按 ``portable`` 复用，否则旧版服务会继续提供旧前端，
    造成页面脚本、HTML 和后端版本混用。
    """
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            if not (payload.get("status") == "ok" and payload.get("service") == "VideoToNo"):
                return False
            if mode is not None and payload.get("mode") != mode:
                return False
            return version is None or payload.get("version") == version
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_available_port() -> int:
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN_RANGE):
        url = f"http://127.0.0.1:{port}"
        if server_alive(url, mode=RUN_MODE, version=VERSION):
            return port  # 同模式实例已在运行，直接复用
        if port_free(port):
            return port
    raise RuntimeError(f"端口 {DEFAULT_PORT}-{DEFAULT_PORT + PORT_SCAN_RANGE - 1} 均不可用，请稍后重试")


# --------------------------------------------------------------------------
# 服务启动（两种模式共用）
# --------------------------------------------------------------------------

def start_server(port: int) -> tuple[Any, threading.Thread]:
    """在线程中启动 uvicorn，返回 (server, thread)。"""
    import uvicorn

    # 让挂载的 MCP 端点（/mcp/sse）自调命中实际端口
    os.environ.setdefault("VIDEOTONOTES_BACKEND_URL", f"http://127.0.0.1:{port}")
    from backend.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


def wait_healthy(
    url: str,
    timeout: float = START_TIMEOUT_SECONDS,
    mode: str | None = None,
    version: str | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_alive(url, mode=mode, version=version):
            return True
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------
# 无头模式（agent 在后台拉起服务，没有界面也没有托盘）
# --------------------------------------------------------------------------

def read_headless_options(argv: list[str]) -> tuple[bool, float]:
    """取 `--headless` 与 `--idle-exit 秒`；其余参数照旧忽略。

    双击便携版 exe 的人不会带参数，误写参数的人也不该被启动器拒之门外。
    """
    headless = "--headless" in argv
    limit = DEFAULT_IDLE_EXIT_SECONDS
    if "--idle-exit" in argv:
        index = argv.index("--idle-exit") + 1
        try:
            limit = max(0.0, float(argv[index]))
        except (IndexError, ValueError):
            limit = DEFAULT_IDLE_EXIT_SECONDS
    return headless, limit


def backend_activity() -> tuple[float, bool]:
    """读后端自己记的"距上次本机请求多久"与"有没有在建任务"。"""
    from backend import main as backend_main

    return backend_main.idle_seconds(), backend_main.has_active_task()


def idle_watchdog(
    server: Any,
    idle_limit: float,
    activity: Callable[[], tuple[float, bool]] | None = None,
    poll_seconds: float | None = None,
) -> int:
    """无人再用时退出；`idle_limit <= 0` 表示常驻。

    探测必须走进程内函数而不是健康检查——本函数自己发的请求会把空闲时间刷成 0，
    于是闲置的服务永远不退，变成用户找不到的孤儿进程。
    """
    probe = activity or backend_activity
    sleep_seconds = poll_seconds or IDLE_POLL_SECONDS
    while not getattr(server, "should_exit", False):
        try:
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            break
        if idle_limit > 0:
            try:
                idle_seconds, busy = probe()
            except Exception:
                continue  # 探测不出结果就当还有人用，宁可多活一轮
            if idle_seconds >= idle_limit and not busy:
                print(f"闲置 {int(idle_seconds)} 秒且无在建任务，无头实例退出", flush=True)
                break
    server.should_exit = True
    return 0


def run_headless(url: str, workspace: Path, port: int, idle_limit: float) -> int:
    print(
        f"VideoToNo v{VERSION} 无头启动：{url}"
        f"（闲置 {int(idle_limit)} 秒后自动退出，日志：{workspace / '_app.log'}）",
        flush=True,
    )
    server, _ = start_server(port)
    if not wait_healthy(url, mode=RUN_MODE, version=VERSION):
        server.should_exit = True
        print(f"服务在 {START_TIMEOUT_SECONDS} 秒内未就绪，已退出", flush=True)
        return 1
    return idle_watchdog(server, idle_limit)


# --------------------------------------------------------------------------
# 开发模式（控制台）
# --------------------------------------------------------------------------

def print_banner(url: str, workspace: Path) -> None:
    width = 58
    print("=" * width)
    print("  VideoToNo v" + VERSION + "（开发模式）")
    print(f"  界面地址: {url}")
    print(f"  工作目录: {workspace}")
    print("  关闭本窗口或按 Ctrl+C 退出。")
    print("=" * width, flush=True)


def run_console(url: str, workspace: Path, port: int) -> int:
    print_banner(url, workspace)
    print("  正在启动服务，请稍候…", flush=True)
    try:
        server, _ = start_server(port)
        if not wait_healthy(url, mode=RUN_MODE, version=VERSION):
            print(f"警告: {START_TIMEOUT_SECONDS} 秒内未就绪，请手动打开 {url}", flush=True)
        else:
            webbrowser.open(url)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已退出。再见！")
        server.should_exit = True
    except Exception as error:
        print(f"\n启动失败：{error}")
        input("按回车键退出…")
        return 1
    return 0


# --------------------------------------------------------------------------
# 打包模式（系统托盘）
# --------------------------------------------------------------------------

def setup_file_logging(workspace: Path) -> Path:
    """noconsole 模式下把 stdout/stderr 重定向到日志文件。"""
    log_path = workspace / "_app.log"
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file
    return log_path


def show_error(message: str) -> None:
    """启动失败提示：Windows 弹消息框，其他平台写 stderr。"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, f"{APP_NAME} 启动失败", 0x10)
    else:
        print(message, file=sys.stderr, flush=True)


def open_in_shell(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def latest_release_info() -> dict[str, str]:
    """读取 GitHub 最新 Release；只在用户点击托盘菜单时调用。

    委托到 update_check：版本比较与接口地址单一来源。urlopen 仍发生在本模块
    的测试可见路径上（test_launcher 钉住了 url 与 timeout=5）。
    """
    info = update_check.fetch_latest_release(VERSION)
    return {"tag_name": info["tag_name"], "html_url": info["html_url"]}


def show_update_message(message: str, title: str = APP_NAME, flags: int = 0x40) -> int:
    """显示更新提示；开发模式/非 Windows 下退回日志，便于测试。"""
    if sys.platform == "win32":
        import ctypes

        return int(ctypes.windll.user32.MessageBoxW(None, message, title, flags))
    print(f"{title}: {message}", file=sys.stderr, flush=True)
    return 0


def check_for_updates(_icon: Any = None, _item: Any = None) -> None:
    """手动检查 GitHub Release，不阻塞启动，也不在启动时联网。"""
    try:
        release = latest_release_info()
        if version_tuple(release["tag_name"]) <= version_tuple(VERSION):
            show_update_message(f"当前已是最新版本（v{VERSION}）。", flags=0x40)
            return
        message = (
            f"检测到新版本 {release['tag_name']}（当前 v{VERSION}）。\n\n"
            "是否打开 GitHub Release 页面查看并更新？"
        )
        result = show_update_message(message, title=f"{APP_NAME} 更新", flags=0x24)
        if result == 6:  # IDYES
            webbrowser.open(release["html_url"])
    except Exception as error:
        show_update_message(f"检查更新失败：{error}\n请稍后重试。", title=f"{APP_NAME} 更新", flags=0x10)


def tray_icon_image() -> Any:
    from PIL import Image

    if is_frozen():
        resource = Path(getattr(sys, "_MEIPASS", "")) / "sources" / "icon.png"
    else:
        resource = base_dir() / "sources" / "icon.png"
    if resource.is_file():
        return Image.open(resource).resize((64, 64))
    # 兜底：生成一个简单占位图标
    image = Image.new("RGB", (64, 64), (69, 200, 144))
    return image


def run_tray(url: str, workspace: Path, server: Any) -> int:
    import pystray

    log_path = workspace / "_app.log"
    print(f"VideoToNo v{VERSION} 已启动: {url}（托盘图标可退出）", flush=True)

    def on_open(_icon: Any, _item: Any) -> None:
        webbrowser.open(url)

    def on_logs(_icon: Any, _item: Any) -> None:
        if log_path.is_file():
            open_in_shell(log_path)

    def on_quit(_icon: Any, _item: Any) -> None:
        server.should_exit = True
        _icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("打开界面", on_open, default=True),
        pystray.MenuItem("查看日志", on_logs),
        pystray.MenuItem("检查更新", check_for_updates),
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon("videotono", tray_icon_image(), f"{APP_NAME} · {url}", menu)
    # 任务结果通知：把 backend 的完成/失败事件接到托盘气泡（Windows 通知）
    try:
        from backend import main as backend_main

        backend_main.register_task_notify(
            lambda title, message: icon.notify(message, f"VideoToNo · {title}")
        )
    except Exception:
        pass  # 通知注册失败不影响服务
    icon.run()
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main() -> int:
    if "--asr-child" in sys.argv[1:]:
        # 转写工人：只干活，不起服务、不进托盘、不重定向 stdout（主程序在用管道读它）
        configure_runtime_dirs()
        from backend.asr_process import child_main

        return child_main()
    configure_runtime_dirs()
    headless, idle_limit = read_headless_options(sys.argv[1:])
    workspace = Path(os.environ["VIDEOTONOTES_WORKSPACE"])
    # 无头模式没有界面可开，环境变量也不必用户操心
    no_browser = os.environ.get("VIDEOTONOTES_NO_BROWSER") == "1" or headless

    try:
        port = find_available_port()
    except RuntimeError as error:
        show_error(str(error))
        return 1

    url = f"http://127.0.0.1:{port}"

    if server_alive(url, mode=RUN_MODE, version=VERSION):
        # 同模式服务已在运行，直接打开界面
        if not no_browser:
            webbrowser.open(url)
        return 0

    if not is_frozen():
        if headless:
            return run_headless(url, workspace, port, idle_limit)
        return run_console(url, workspace, port)

    # ---- 打包模式 ----
    log_path = setup_file_logging(workspace)
    try:
        server, _ = start_server(port)
        if not wait_healthy(url, mode=RUN_MODE, version=VERSION):
            server.should_exit = True
            show_error(f"服务启动超时，请查看日志：{log_path}")
            return 1
        if headless:
            print(f"VideoToNo v{VERSION} 无头启动: {url}", flush=True)
            return idle_watchdog(server, idle_limit)
        if not no_browser:
            webbrowser.open(url)
        return run_tray(url, workspace, server)
    except Exception as error:
        show_error(f"启动失败：{error}\n日志：{log_path}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
