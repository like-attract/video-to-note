"""VideoToNo 便携版启动器。

PyInstaller 打包为单个 exe 后的入口：
  1. 在 127.0.0.1 上启动本地 FastAPI 后端（默认 8000，被占用时自动顺延）
  2. 服务就绪后自动打开默认浏览器
  3. 控制台窗口保持运行，关闭窗口或按 Ctrl+C 退出

开发模式直接 `python launcher.py` 亦可运行。
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import urllib.request
import webbrowser
from pathlib import Path

VERSION = "0.1.1"
DEFAULT_PORT = 8000
PORT_SCAN_RANGE = 20
START_TIMEOUT_SECONDS = 60


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def configure_runtime_dirs() -> None:
    """设置工作目录：打包模式默认放在 exe 旁边（便携），开发模式为项目 workspace。"""
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


def print_banner(url: str, workspace: Path) -> None:
    width = 58
    line = "=" * width
    print(line)
    print("  VideoToNo v" + VERSION + "（便携版）")
    print(f"  界面地址: {url}")
    print(f"  工作目录: {workspace}")
    print("  说明: 界面将自动在浏览器中打开；关闭本窗口或按 Ctrl+C 退出。")
    print(line, flush=True)


def main() -> int:
    configure_runtime_dirs()
    workspace = Path(os.environ["VIDEOTONOTES_WORKSPACE"])
    no_browser = os.environ.get("VIDEOTONOTES_NO_BROWSER") == "1"

    try:
        port = find_available_port()
    except RuntimeError as error:
        print(f"启动失败：{error}")
        input("按回车键退出…")
        return 1

    url = f"http://127.0.0.1:{port}"

    if server_alive(url):
        print_banner(url, workspace)
        print("  检测到服务已在运行，直接打开界面。")
        if not no_browser:
            webbrowser.open(url)
        input("按回车键退出…")
        return 0

    # 首次运行提示
    if port != DEFAULT_PORT:
        print(f"注意: 端口 {DEFAULT_PORT} 已被占用，已改用 {url}")

    print_banner(url, workspace)
    if not no_browser:
        print("  正在启动服务，请稍候…", flush=True)

    try:
        # 重依赖放在 banner 之后导入，让提示更快显示
        import uvicorn

        from backend.main import app

        async def run() -> None:
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="info",
                access_log=False,
            )
            server = uvicorn.Server(config)

            async def open_browser_when_ready() -> None:
                for _ in range(int(START_TIMEOUT_SECONDS / 0.1)):
                    if server.started:
                        break
                    await asyncio.sleep(0.1)
                if server.started and not no_browser:
                    webbrowser.open(url)
                elif not server.started:
                    print(f"警告: {START_TIMEOUT_SECONDS} 秒内未就绪，请手动打开 {url}", flush=True)

            browser_task = asyncio.create_task(open_browser_when_ready())
            try:
                await server.serve()
            finally:
                browser_task.cancel()

        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n已退出。再见！")
    except Exception as error:
        print(f"\n启动失败：{error}")
        input("按回车键退出…")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
