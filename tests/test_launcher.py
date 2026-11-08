import json
from pathlib import Path

import launcher


def test_version_tuple_accepts_release_tags() -> None:
    assert launcher.version_tuple("v1.1.4") == (1, 1, 4)
    assert launcher.version_tuple("1.2.0-rc1") == (1, 2, 0)
    assert launcher.version_tuple("invalid") == (0,)


def test_server_alive_requires_matching_version(monkeypatch) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"ok","service":"VideoToNo","mode":"portable","version":"1.1.7"}'

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())

    assert launcher.server_alive("http://127.0.0.1:8000", mode="portable", version="1.1.7")
    assert not launcher.server_alive("http://127.0.0.1:8000", mode="portable", version="1.1.8")


def test_configure_runtime_dirs_creates_custom_workspace(monkeypatch, tmp_path) -> None:
    """环境变量指定的 workdir 不存在时也要自动创建（打包版写日志前依赖此目录）。"""
    custom = tmp_path / "custom-ws"
    assert not custom.exists()
    monkeypatch.setenv("VIDEOTONOTES_WORKSPACE", str(custom))
    monkeypatch.setattr(launcher, "is_frozen", lambda: False)
    launcher.configure_runtime_dirs()
    assert custom.is_dir()


def test_latest_release_info_reads_tag_and_url(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"tag_name": "v1.2.0", "html_url": "https://github.com/example/release"}
            ).encode("utf-8")

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(launcher.urllib.request, "urlopen", fake_urlopen)
    assert launcher.latest_release_info() == {
        "tag_name": "v1.2.0",
        "html_url": "https://github.com/example/release",
    }
    assert captured == {"url": launcher.GITHUB_LATEST_RELEASE_API, "timeout": 5}


def test_check_for_updates_opens_new_release_after_confirmation(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        launcher,
        "latest_release_info",
        lambda: {"tag_name": "v9.9.9", "html_url": "https://github.com/example/release"},
    )
    monkeypatch.setattr(launcher, "show_update_message", lambda *args, **kwargs: 6)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)

    launcher._check_for_updates_now()

    assert opened == ["https://github.com/example/release"]


def test_check_for_updates_reports_local_test_build_ahead_of_github(monkeypatch) -> None:
    """本地测试版号高于线上时，不能说“已是最新版本”。"""
    messages: list[str] = []
    monkeypatch.setattr(
        launcher,
        "latest_release_info",
        lambda: {"tag_name": "v1.4.7", "html_url": "https://github.com/example/release"},
    )
    monkeypatch.setattr(launcher, "VERSION", "1.4.8")
    monkeypatch.setattr(
        launcher,
        "show_update_message",
        lambda message, **kwargs: messages.append(message) or 1,
    )

    launcher._check_for_updates_now()

    assert messages
    assert "GitHub 最新发布 v1.4.7" in messages[0]
    assert "本地测试版" in messages[0] or "尚未发布" in messages[0]


def test_check_for_updates_reports_equal_version_as_current(monkeypatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        launcher,
        "latest_release_info",
        lambda: {"tag_name": "v1.4.8", "html_url": "https://github.com/example/release"},
    )
    monkeypatch.setattr(launcher, "VERSION", "1.4.8")
    monkeypatch.setattr(
        launcher,
        "show_update_message",
        lambda message, **kwargs: messages.append(message) or 1,
    )

    launcher._check_for_updates_now()

    assert messages == ["当前已是最新版本（v1.4.8）。"]


def test_check_for_updates_dispatches_to_worker_thread(monkeypatch) -> None:
    """托盘回调不能在 pystray 的窗口过程线程里同步跑 MessageBox。"""
    started: list[dict] = []

    class FakeLock:
        def __init__(self) -> None:
            self.free = True

        def acquire(self, blocking: bool = False) -> bool:
            if not self.free:
                return False
            self.free = False
            return True

        def release(self) -> None:
            self.free = True

    class FakeThread:
        def __init__(self, target, name, daemon):  # noqa: ANN001
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            started.append(
                {"target": self.target, "name": self.name, "daemon": self.daemon}
            )

    monkeypatch.setattr(launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(launcher, "_UPDATE_CHECK_LOCK", FakeLock())

    launcher.check_for_updates()

    assert len(started) == 1
    assert started[0]["name"] == "VideoToNoUpdateCheck"
    assert started[0]["daemon"] is True
    assert callable(started[0]["target"])


def test_check_for_updates_skips_when_already_running(monkeypatch) -> None:
    started: list[object] = []

    class BusyLock:
        def acquire(self, blocking: bool = False) -> bool:
            return False

        def release(self) -> None:
            raise AssertionError("未取得锁时不应 release")

    class FakeThread:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def start(self) -> None:
            started.append(object())

    monkeypatch.setattr(launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(launcher, "_UPDATE_CHECK_LOCK", BusyLock())

    launcher.check_for_updates()

    assert started == []


class FakeServer:
    def __init__(self) -> None:
        self.should_exit = False


def test_read_headless_options() -> None:
    assert launcher.read_headless_options([]) == (False, launcher.DEFAULT_IDLE_EXIT_SECONDS)
    assert launcher.read_headless_options(["--headless"]) == (
        True,
        launcher.DEFAULT_IDLE_EXIT_SECONDS,
    )
    assert launcher.read_headless_options(["--headless", "--idle-exit", "30"]) == (True, 30.0)
    # 常驻是显式选择，不是笔误：0 表示"没人负责收摊，进程自己活着"
    assert launcher.read_headless_options(["--headless", "--idle-exit", "0"]) == (True, 0.0)
    # 参数写坏不能挡住启动（双击 exe 的人根本不带参数）
    assert launcher.read_headless_options(["--headless", "--idle-exit", "abc"]) == (
        True,
        launcher.DEFAULT_IDLE_EXIT_SECONDS,
    )
    assert launcher.read_headless_options(["--headless", "--idle-exit"]) == (
        True,
        launcher.DEFAULT_IDLE_EXIT_SECONDS,
    )


def test_idle_watchdog_exits_when_nobody_is_using_it() -> None:
    server = FakeServer()
    assert launcher.idle_watchdog(
        server, 60.0, activity=lambda: (120.0, False), poll_seconds=0.01
    ) == 0
    assert server.should_exit is True


def test_idle_watchdog_waits_for_running_task() -> None:
    """界面关了但转写还在跑时收摊，等于把用户正在等的结果扔掉。"""
    server = FakeServer()
    calls: list[int] = []

    def probe() -> tuple[float, bool]:
        calls.append(1)
        if len(calls) >= 3:
            server.should_exit = True  # 测试出口：证明它一直活着，没自己退
        return 9999.0, True

    assert launcher.idle_watchdog(server, 60.0, activity=probe, poll_seconds=0.01) == 0
    assert len(calls) == 3
    assert server.should_exit is True


def test_idle_watchdog_resident_never_probes() -> None:
    """`--idle-exit 0` 是"由我负责关"，那看门狗连空闲探测都不必发起。"""
    server = FakeServer()
    server.should_exit = True
    calls: list[int] = []

    def probe() -> tuple[float, bool]:
        calls.append(1)
        return 9999.0, False

    assert launcher.idle_watchdog(server, 0.0, activity=probe, poll_seconds=0.01) == 0
    assert calls == []


def test_headless_starts_service_without_browser_or_tray(
    monkeypatch, tmp_path: Path
) -> None:
    """agent 后台拉起服务这条路的形状：不开浏览器、不建托盘、闲置到点就退。"""
    import sys

    opened: list[str] = []
    monkeypatch.setattr(sys, "argv", ["launcher.py", "--headless", "--idle-exit", "1"])
    monkeypatch.setattr(launcher, "is_frozen", lambda: False)
    monkeypatch.setenv("VIDEOTONOTES_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(launcher, "find_available_port", lambda: 8000)
    monkeypatch.setattr(launcher, "server_alive", lambda *args, **kwargs: False)
    server = FakeServer()
    monkeypatch.setattr(launcher, "start_server", lambda port: (server, None))
    monkeypatch.setattr(launcher, "wait_healthy", lambda *args, **kwargs: True)
    monkeypatch.setattr(launcher, "backend_activity", lambda: (9999.0, False))
    monkeypatch.setattr(launcher, "IDLE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)

    def no_tray(*args, **kwargs):
        raise AssertionError("无头模式不该建托盘图标")

    monkeypatch.setattr(launcher, "run_tray", no_tray)

    assert launcher.main() == 0
    assert opened == []
    assert server.should_exit is True
