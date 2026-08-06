"""VideoToNo 启动器。

开发模式（`python launcher.py`）：
  - 控制台窗口显示地址，Ctrl+C 退出

打包模式（PyInstaller onefile，--noconsole）：
  - 无控制台窗口，服务启动后驻留系统托盘
  - 托盘菜单：打开界面 / 查看日志 / 退出
  - 日志写入工作目录的 _app.log
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

VERSION = "0.2.4"
DEFAULT_PORT = 8000
PORT_SCAN_RANGE = 20
START_TIMEOUT_SECONDS = 60
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
        workspace = exe_dir / "workspace"
        try:
            workspace.mkdir(parents=True, exist_ok=True)
            probe = workspace / ".write_test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError:
            # exe 所在目录不可写时（如 Program Files），退回用户目录
            local_app_data = os.environ.get("LOCALAPPDATA")
            fallback_root = Path(local_app_data) if local_app_data else Path.home() / ".videotono"
            workspace = fallback_root / "VideoToNo" / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
    else:
        workspace = base_dir() / "workspace"
    os.environ.setdefault("VIDEOTONOTES_WORKSPACE", str(workspace))


def server_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as response:
            return response.status == 200
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
        if server_alive(url):
            return port  # 本程序已在运行，直接复用
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


def wait_healthy(url: str, timeout: float = START_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_alive(url):
            return True
        time.sleep(0.2)
    return False


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
        if not wait_healthy(url):
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
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon("videotono", tray_icon_image(), APP_NAME, menu)
    icon.run()
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main() -> int:
    configure_runtime_dirs()
    workspace = Path(os.environ["VIDEOTONOTES_WORKSPACE"])
    no_browser = os.environ.get("VIDEOTONOTES_NO_BROWSER") == "1"

    try:
        port = find_available_port()
    except RuntimeError as error:
        show_error(str(error))
        return 1

    url = f"http://127.0.0.1:{port}"

    if server_alive(url):
        # 服务已在运行，直接打开界面
        if not no_browser:
            webbrowser.open(url)
        return 0

    if not is_frozen():
        return run_console(url, workspace, port)

    # ---- 打包模式 ----
    log_path = setup_file_logging(workspace)
    try:
        server, _ = start_server(port)
        if not wait_healthy(url):
            server.should_exit = True
            show_error(f"服务启动超时，请查看日志：{log_path}")
            return 1
        if not no_browser:
            webbrowser.open(url)
        return run_tray(url, workspace, server)
    except Exception as error:
        show_error(f"启动失败：{error}\n日志：{log_path}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
