"""本机抖音登录态管理。

抖音的会话只在本机浏览器 profile 中持久化；API 返回的 cookie 只存在
当前页面/任务内存中，供一次 yt-dlp 请求使用，不写入 task.json 或日志。
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

LOGIN_URL = "https://www.douyin.com/"
SESSION_TTL_SECONDS = 300
CDP_PORT_START = 9343
CDP_PORT_COUNT = 10
BROWSER_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
COOKIE_NAMES = (
    "msToken", "ttwid", "odin_tt", "passport_csrf_token", "sid_guard",
    "sessionid", "sessionid_ss", "s_v_web_id", "__ac_nonce",
    "__ac_signature", "tt_scid", "csrf_session_id",
)


def find_browser() -> str | None:
    return next((path for path in BROWSER_PATHS if os.path.isfile(path)), None)


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
class DouyinLoginSession:
    browser_path: str
    profile_dir: Path
    cdp_port: int
    started_at: float
    process: subprocess.Popen[Any] | None = field(default=None)
    state: str = "waiting"
    message: str = "请在抖音窗口中完成登录或验证"
    cookies: dict[str, str] | None = None


class DouyinLoginManager:
    def __init__(self, profile_root: Path) -> None:
        self.profile_root = profile_root
        self.session: DouyinLoginSession | None = None

    async def start(self) -> dict[str, Any]:
        existing = self.session
        if existing and self._alive() and existing.state == "waiting":
            return self._describe()
        self._terminate_browser()
        browser = await asyncio.to_thread(find_browser)
        if not browser:
            return {"ok": False, "error": "未找到 Edge 或 Chrome，请先安装桌面浏览器"}
        port = free_cdp_port()
        if port is None:
            return {"ok": False, "error": "没有可用的调试端口，请稍后重试"}
        profile_dir = self.profile_root / "_douyin_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._kill_profile_browsers, profile_dir)
        args = [
            browser,
            f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-extensions",
            "--disable-background-networking",
            "--window-size=1200,900",
            LOGIN_URL,
        ]
        try:
            process = subprocess.Popen(args)
        except OSError as exc:
            return {"ok": False, "error": f"启动浏览器失败：{exc}"}
        self.session = DouyinLoginSession(
            browser_path=browser,
            profile_dir=profile_dir,
            cdp_port=port,
            started_at=time.time(),
            process=process,
        )
        # 已登录 profile 可以直接拿到会话；新 profile 则保持窗口打开等待用户。
        for _ in range(20):
            if process.poll() is not None:
                break
            cookies = await self._fetch_cookies()
            if cookies and self._looks_authenticated(cookies):
                self.session.cookies = cookies
                self.session.state = "ready"
                self.session.message = "已从本机浏览器 profile 恢复抖音登录态"
                asyncio.create_task(self.cancel())
                break
            await asyncio.sleep(0.25)
        return self._describe()

    @staticmethod
    def _looks_authenticated(cookies: dict[str, str]) -> bool:
        return bool(cookies.get("sessionid") or cookies.get("sid_guard"))

    def _alive(self) -> bool:
        return bool(self.session and self.session.process and self.session.process.poll() is None)

    def _terminate_browser(self) -> None:
        session = self.session
        if session and self._alive():
            try:
                session.process.terminate()
            except OSError:
                pass
            self._kill_profile_browsers(session.profile_dir)
        self.session = None

    @staticmethod
    def _kill_profile_browsers(profile_dir: Path) -> None:
        if os.name == "nt":
            marker = str(profile_dir).replace("'", "''")
            script = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { ($_.Name -eq 'msedge.exe' -or $_.Name -eq 'chrome.exe') "
                f"-and $_.CommandLine -like '*{marker}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=15)
            except Exception:
                pass
            return
        try:
            result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10)
        except Exception:
            return
        marker = str(profile_dir)
        for line in result.stdout.splitlines():
            parts = line.strip().split(" ", 1)
            if len(parts) == 2 and marker in parts[1] and ("Chrome" in parts[1] or "Edge" in parts[1]):
                try:
                    os.kill(int(parts[0]), 9)
                except (OSError, ValueError):
                    pass

    def _describe(self) -> dict[str, Any]:
        session = self.session
        if not session:
            return {"ok": False, "state": "idle", "cookies": None, "message": "无进行中的抖音登录会话"}
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
            session.message = "抖音浏览器窗口已被关闭"
            return self._describe()
        if time.time() - session.started_at > SESSION_TTL_SECONDS:
            self._terminate_browser()
            return {"ok": False, "state": "timeout", "cookies": None, "message": "浏览器验证超时"}
        try:
            cookies = await asyncio.wait_for(self._fetch_cookies(), timeout=5)
        except asyncio.TimeoutError:
            return self._describe()
        if cookies and self._looks_authenticated(cookies):
            session.cookies = cookies
            session.state = "ready"
            session.message = "抖音登录成功，凭据仅保留在本次任务内存中"
            asyncio.create_task(self.cancel())
        return self._describe()

    async def _fetch_cookies(self) -> dict[str, str]:
        session = self.session
        if not session:
            return {}
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{session.cdp_port}/json/version", timeout=2) as response:
                version = json.loads(response.read().decode("utf-8"))
        except Exception:
            return {}
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
            domain = str(cookie.get("domain") or "")
            name = str(cookie.get("name") or "")
            if ("douyin.com" in domain or "iesdouyin.com" in domain) and name in COOKIE_NAMES:
                wanted[name] = str(cookie.get("value") or "")
        return wanted

    async def cancel(self) -> dict[str, Any]:
        session = self.session
        if not session:
            return {"ok": True, "message": "无进行中的抖音登录会话"}
        process = session.process
        if process and process.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{session.cdp_port}/json/version", timeout=2) as response:
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
        self._kill_profile_browsers(session.profile_dir)
        if not session.cookies:
            self.session = None
        return {"ok": True, "message": "抖音登录会话已结束"}
