"""B 站扫码登录：通过独立浏览器实例（CDP）获取会话 cookie。

浏览器日常使用的 cookie 库无法直接读取（运行锁 + Chrome 127+ 的
app-bound 加密），因此用独立配置启动 Edge/Chrome 并开启调试端口，
用户扫码登录后通过 CDP 的 Storage.getCookies 直接取回明文 cookie。

登录态保存在工作目录的 _bili_profile 中，同机器后续导入无需重复登录。
管理器自己不留 cookie 副本；拿到 cookie 时交给构造时传入的 ``save_cookies`` 落本机
（由 main 接成加密存储），没传就只放在会话里——扫码这条路不该每次都让用户重扫。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import websockets

LOGIN_URL = "https://passport.bilibili.com/login"
SESSION_TTL_SECONDS = 300
CDP_PORT_START = 9333
CDP_PORT_COUNT = 10
BILI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# urllib 在 Windows 上会读注册表里的系统代理（changelog/1.4.2.md 排查 500 悬案时
# 就记了这条）。系统代理指向一个已经僵死的本地代理进程时，连 127.0.0.1 上的 CDP
# 都会被劫走，扫码登录于是"永远等不到 cookie"。CDP 的 HTTP 调用一律走这条
# 明确不挂任何代理的 opener。
def _no_proxy_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


_CDP_OPENER = _no_proxy_opener()

BROWSER_PATHS = [
    # Windows: Edge
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    # Windows: Chrome
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    # macOS: Chrome / Edge
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def find_browser() -> str | None:
    for path in BROWSER_PATHS:
        if os.path.isfile(path):
            return path
    return None


def free_cdp_port() -> int | None:
    for port in range(CDP_PORT_START, CDP_PORT_START + CDP_PORT_COUNT):
        # socket() / setsockopt() 本身也可能抛（Winsock 被系统级问题拖垮时），
        # 这里必须一起兜住：漏出去就是一次裸 500，而"没有可用端口"才是实情
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
    saved: bool = False
    save_error: str = ""


class BiliLoginManager:
    def __init__(
        self,
        profile_root: Path,
        save_cookies: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        self.profile_root = profile_root
        self.save_cookies = save_cookies
        self.session: BiliLoginSession | None = None

    def _mark_ready(
        self, session: BiliLoginSession, cookies: dict[str, str], message: str
    ) -> None:
        session.cookies = cookies
        session.state = "ready"
        session.message = message
        if self.save_cookies is None:
            return
        # 扫一次码就得重扫一遍是群测里真实的抱怨：拿到就替用户存进本机。存失败不能把
        # 这次登录判成失败——cookie 已经在手里，这一趟至少得在页面上可用。
        try:
            self.save_cookies(cookies)
            session.saved = True
        except Exception as exc:
            session.save_error = str(exc)

    async def start(self, force: bool = False, _retry_invalid: bool = False) -> dict[str, Any]:
        profile_dir = self.profile_root / "_bili_profile"
        if force:
            # 用户明确要换账号/重扫：连浏览器 profile 一起清掉，否则下一次启动
            # 又会从同一个旧会话里把失效 cookie 读回来，扫码窗口根本不会出现。
            self._terminate_browser()
            await asyncio.to_thread(self._kill_profile_browsers, profile_dir)
            await asyncio.to_thread(shutil.rmtree, profile_dir, ignore_errors=True)

        existing = self.session
        if not force and existing and self._alive() and existing.state != "timeout":
            return self._describe()
        self._terminate_browser()

        browser = await asyncio.to_thread(find_browser)
        if not browser:
            return {"ok": False, "error": "未找到 Edge 或 Chrome，请手动填写凭据"}
        port = free_cdp_port()
        if port is None:
            return {"ok": False, "error": "没有可用的调试端口，请稍后重试"}

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
        for retry in range(2):
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
                break
            if process.poll() is None:
                # 窗口还活着：用户未扫码时没有 cookie 属正常状态，绝不能重开窗口
                break
            if retry == 0:
                # 仅当浏览器进程真正退出（多为残留进程锁住 profile）：清理后重试一次
                await asyncio.to_thread(self._kill_profile_browsers, profile_dir)
                await asyncio.sleep(1)
        if cookies:
            # 该浏览器配置已登录过，但 cookie 可能早已失效：先在线校验一次。
            # 失效时清掉 profile 重新起一个干净窗口，让用户能看到二维码。
            valid = await asyncio.to_thread(self._cookies_look_logged_in, cookies)
            if valid is False and not _retry_invalid:
                self._terminate_browser()
                await asyncio.to_thread(self._kill_profile_browsers, profile_dir)
                await asyncio.to_thread(shutil.rmtree, profile_dir, ignore_errors=True)
                return await self.start(force=True, _retry_invalid=True)
            if valid is False:
                # 干净 profile 里仍读到失效 cookie：不冒充登录成功，留在当前窗口让用户重扫
                self.session.state = "waiting"
                self.session.message = "检测到旧的登录态已失效，请在窗口中重新扫码登录"
                return self._describe()
            message = "已从上次登录恢复"
            if valid is None:
                message += "（未能在线校验登录态；若提示 412 请点「重新扫码（换账号）」）"
            self._mark_ready(self.session, cookies, message)
            asyncio.create_task(self.cancel())
        return self._describe()

    @staticmethod
    def _cookies_look_logged_in(cookies: dict[str, str]) -> bool | None:
        """在线校验 cookie 是否仍是有效登录。

        返回 True/False 是确定结论，None 表示无法判断（网络、代理或 B 站风控）。
        只有 False 才值得清掉 profile：把“网断了”误判成“登录失效”会白白逼用户重扫。
        """
        if not cookies.get("sessdata"):
            return False
        names = (
            ("SESSDATA", "sessdata"),
            ("bili_jct", "bili_jct"),
            ("buvid3", "buvid3"),
            ("buvid4", "buvid4"),
            ("b_nut", "b_nut"),
            ("b_lsid", "b_lsid"),
        )
        cookie_header = "; ".join(
            f"{header}={cookies[value]}"
            for header, value in names
            if cookies.get(value)
        )
        request = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={
                "User-Agent": BILI_USER_AGENT,
                "Referer": "https://www.bilibili.com/",
                "Cookie": cookie_header,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except Exception:
            return None
        code = payload.get("code")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if code == 0:
            return bool(data.get("isLogin"))
        if code == -101:
            return False
        return None

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
            # 仅当进程还存活时才枚举清理（PowerShell 枚举较慢，避免阻塞热路径）
            self._kill_profile_browsers(session.profile_dir)
        self.session = None

    @staticmethod
    def _kill_profile_browsers(profile_dir: Path) -> None:
        """杀掉占用指定 profile 的浏览器进程（同 profile 单例，残留进程会阻塞启动）。"""
        if os.name == "nt":
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
            return
        # macOS/Linux: 按命令行匹配浏览器进程并终止
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return
        marker = str(profile_dir)
        for line in result.stdout.splitlines():
            parts = line.strip().split(" ", 1)
            if len(parts) != 2:
                continue
            pid, command = parts
            if marker in command and ("Chrome" in command or "Edge" in command):
                try:
                    os.kill(int(pid), 9)
                except (OSError, ValueError):
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
            # 前端据此决定是报"已存本机，下次不用再扫"还是提醒存不下来
            "saved": session.saved,
            "save_error": session.save_error,
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
            self._mark_ready(session, cookies, "登录成功")
            asyncio.create_task(self.cancel())
        return self._describe()

    async def _fetch_cookies(self) -> dict[str, str]:
        session = self.session
        if not session:
            return {}
        try:
            with _CDP_OPENER.open(
                f"http://127.0.0.1:{session.cdp_port}/json/version", timeout=2
            ) as response:
                version = json.loads(response.read().decode("utf-8"))
        except Exception:
            return {}
        cookies: list[dict[str, Any]] = []
        try:
            # websockets 14+ 默认跟随系统/环境代理；本机 CDP 的 ws 连接绝不能被代理劫走
            async with websockets.connect(version["webSocketDebuggerUrl"], proxy=None) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
                    if message.get("id") == 1:
                        cookies = (message.get("result") or {}).get("cookies", [])
                        break
        except Exception:
            return {}
        wanted: dict[str, str] = {}
        wanted_keys = (
            "SESSDATA",
            "bili_jct",
            "buvid3",
            "buvid4",
            "b_nut",
            "b_lsid",
        )
        for cookie in cookies:
            domain = cookie.get("domain", "")
            name = cookie.get("name", "")
            if "bilibili.com" in domain and name in wanted_keys:
                wanted[name] = cookie.get("value", "")
        if not wanted.get("SESSDATA"):
            return {}
        return {
            "sessdata": wanted.get("SESSDATA", ""),
            "bili_jct": wanted.get("bili_jct", ""),
            "buvid3": wanted.get("buvid3", ""),
            "buvid4": wanted.get("buvid4", ""),
            "b_nut": wanted.get("b_nut", ""),
            "b_lsid": wanted.get("b_lsid", ""),
        }

    async def cancel(self) -> dict[str, Any]:
        session = self.session
        if not session:
            return {"ok": True, "message": "无进行中的登录会话"}
        process = session.process
        if process and process.poll() is None:
            try:
                with _CDP_OPENER.open(
                    f"http://127.0.0.1:{session.cdp_port}/json/version", timeout=2
                ) as response:
                    version = json.loads(response.read().decode("utf-8"))
                async with websockets.connect(version["webSocketDebuggerUrl"], proxy=None) as ws:
                    await ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
                    await asyncio.sleep(0.3)
            except Exception:
                pass
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        # 兜底：清除仍占用该 profile 的浏览器进程（避免窗口残留）
        self._kill_profile_browsers(session.profile_dir)
        if not session.cookies:
            self.session = None
        return {"ok": True, "message": "登录会话已结束"}
