"""B 站扫码登录：通过独立浏览器实例（CDP）获取会话 cookie。

浏览器日常使用的 cookie 库无法直接读取（运行锁 + Chrome 127+ 的
app-bound 加密），因此用独立配置启动 Edge/Chrome 并开启调试端口，
用户扫码登录后通过 CDP 的 Storage.getCookies 直接取回明文 cookie。

登录态保存在工作目录的 _bili_profile 中，同机器后续导入无需重复登录。
cookie 只保存在内存中，不落盘。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets

LOGIN_URL = "https://passport.bilibili.com/login"
SESSION_TTL_SECONDS = 180
CDP_PORT_START = 9333
CDP_PORT_COUNT = 10

BROWSER_PATHS = [
    # Edge
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    # Chrome
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str | None:
    for path in BROWSER_PATHS:
        if os.path.isfile(path):
            return path
    return None


def free_cdp_port() -> int | None:
    for port in range(CDP_PORT_START, CDP_PORT_START + CDP_PORT_COUNT):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


@dataclass
class BiliLoginSession:
    browser_path: str
    profile_dir: Path
    cdp_port: int
    started_at: float
    process: subprocess.Popen[Any] | None = field(default=None)
    state: str = "waiting"  # waiting | ready | failed | timeout
    message: str = ""
    cookies: dict[str, str] | None = None


class BiliLoginManager:
    def __init__(self, profile_root: Path) -> None:
        self.profile_root = profile_root
        self.session: BiliLoginSession | None = None

    async def start(self) -> dict[str, Any]:
        existing = self.session
        if existing and self._alive() and existing.state != "timeout":
            return self._describe()
        self._terminate_browser()

        browser = await asyncio.to_thread(find_browser)
        if not browser:
            return {"ok": False, "error": "未找到 Edge 或 Chrome，请手动填写凭据"}
        port = free_cdp_port()
        if port is None:
            return {"ok": False, "error": "没有可用的调试端口，请稍后重试"}

        profile_dir = self.profile_root / "_bili_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        # 同一 profile 只能由一个浏览器实例持有，先清理残留进程
        await asyncio.to_thread(self._kill_profile_browsers, profile_dir)
        args = [
            browser,
            f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            # 独立 profile 会被当成新安装，禁用同步/扩展/后台活动避免卡顿
            "--disable-sync",
            "--disable-extensions",
            "--disable-component-update",
            "--disable-background-networking",
            "--window-size=430,700",
            LOGIN_URL,
        ]
        try:
            process = subprocess.Popen(args)
        except OSError as exc:
            return {"ok": False, "error": f"启动浏览器失败：{exc}"}

        self.session = BiliLoginSession(
            browser_path=browser,
            profile_dir=profile_dir,
            cdp_port=port,
            started_at=time.time(),
            process=process,
        )
        cookies = None
        for _ in range(30):
            # 等待浏览器 CDP 就绪并检查是否已有登录（约 8 秒）
            if process.poll() is not None:
                break
            cookies = await self._fetch_cookies()
            if cookies:
                break
            await asyncio.sleep(0.25)
        if cookies:
            # 该浏览器配置已登录过，直接完成
            self.session.cookies = cookies
            self.session.state = "ready"
            self.session.message = "已从上次登录恢复"
            asyncio.create_task(self.cancel())
        return self._describe()

    def _alive(self) -> bool:
        session = self.session
        if not session or not session.process:
            return False
        return session.process.poll() is None

    def _terminate_browser(self) -> None:
        session = self.session
        if session and session.process and session.process.poll() is None:
            try:
                session.process.terminate()
            except OSError:
                pass
        if session:
            self._kill_profile_browsers(session.profile_dir)
        self.session = None

    @staticmethod
    def _kill_profile_browsers(profile_dir: Path) -> None:
        """杀掉占用指定 profile 的浏览器进程（同 profile 单例，残留进程会阻塞启动）。"""
        path_filter = f"$_.CommandLine -like '*{profile_dir}*'"
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { ($_.Name -eq 'msedge.exe' -or $_.Name -eq 'chrome.exe') -and "
            + path_filter
            + " } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                timeout=15,
            )
        except Exception:
            pass

    def _describe(self) -> dict[str, Any]:
        session = self.session
        if not session:
            return {
                "ok": False,
                "state": "idle",
                "cookies": None,
                "message": "无进行中的登录会话",
            }
        return {
            "ok": True,
            "state": session.state,
            "cookies": session.cookies,
            "message": session.message,
            "browser": os.path.basename(session.browser_path),
        }

    async def status(self) -> dict[str, Any]:
        session = self.session
        if not session:
            return self._describe()
        if session.cookies:
            return self._describe()
        if not self._alive():
            session.state = "failed"
            session.message = "登录窗口已被关闭"
            return self._describe()
        if time.time() - session.started_at > SESSION_TTL_SECONDS:
            message = f"登录超时（{SESSION_TTL_SECONDS} 秒）"
            self._terminate_browser()
            return {"ok": False, "state": "timeout", "cookies": None, "message": message}

        try:
            cookies = await asyncio.wait_for(self._fetch_cookies(), timeout=5)
        except asyncio.TimeoutError:
            return self._describe()
        except Exception as exc:
            session.state = "failed"
            session.message = f"读取登录状态失败：{exc}"
            return self._describe()

        if cookies:
            session.cookies = cookies
            session.state = "ready"
            session.message = "登录成功"
            asyncio.create_task(self.cancel())
        return self._describe()

    async def _fetch_cookies(self) -> dict[str, str]:
        session = self.session
        if not session:
            return {}
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{session.cdp_port}/json/version", timeout=2
            ) as response:
                version = json.loads(response.read().decode("utf-8"))
        except Exception:
            return {}
        cookies: list[dict[str, Any]] = []
        try:
            async with websockets.connect(version["webSocketDebuggerUrl"]) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
                    if message.get("id") == 1:
                        cookies = (message.get("result") or {}).get("cookies", [])
                        break
        except Exception:
            return {}
        wanted: dict[str, str] = {}
        for cookie in cookies:
            domain = cookie.get("domain", "")
            name = cookie.get("name", "")
            if "bilibili.com" in domain and name in ("SESSDATA", "bili_jct", "buvid3"):
                wanted[name] = cookie.get("value", "")
        if not wanted.get("SESSDATA"):
            return {}
        return {
            "sessdata": wanted.get("SESSDATA", ""),
            "bili_jct": wanted.get("bili_jct", ""),
            "buvid3": wanted.get("buvid3", ""),
        }

    async def cancel(self) -> dict[str, Any]:
        session = self.session
        if not session:
            return {"ok": True, "message": "无进行中的登录会话"}
        process = session.process
        if process and process.poll() is None:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{session.cdp_port}/json/version", timeout=2
                ) as response:
                    version = json.loads(response.read().decode("utf-8"))
                async with websockets.connect(version["webSocketDebuggerUrl"]) as ws:
                    await ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
                    await asyncio.sleep(0.3)
            except Exception:
                pass
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        if not session.cookies:
            self.session = None
        return {"ok": True, "message": "登录会话已结束"}
