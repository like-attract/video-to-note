"""Agent Skill 脚本的凭据处理与服务自启测试。

`skills/video-to-note/scripts/video_note.py` 不在包里（没有可导入的包路径），
所以用 importlib 按文件加载——这个脚本是发给用户机器跑的，Key 的来路和出口
必须钉住：命令行明文要能换成环境变量/stdin，任何回显凭据的文本都要先脱敏；
"服务没在跑就自己拉起来"这条新路的命令来源与出口同样要钉住。
"""
from __future__ import annotations

import importlib.util
import io
import os
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "video-to-note"
    / "scripts"
    / "video_note.py"
)
spec = importlib.util.spec_from_file_location("video_note_skill", SCRIPT)
assert spec and spec.loader
video_note = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video_note)


def test_skill_redacts_every_credential_shape() -> None:
    plain = "Vh7NqzL0mBxc8dke"
    video_note.remember_secret(plain)
    out = video_note.redact(
        'HTTP 401 upstream echoed {"api_key": "sk-abcdef12345", '
        '"authorization": [BEARER_REDACTED], "note": "' + plain + '"}'
    )
    assert "sk-abcdef12345" not in out
    assert "Q29uZ3JldGVzczEyMzQ1Ng" not in out
    assert plain not in out
    assert plain[:4] + "****" in out
    # 不能顺手把模型文件名单咬坏
    assert "tokenizer.json" in video_note.redact("missing tokenizer.json in snapshot")


def test_skill_reads_key_from_stdin_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env_name = video_note.API_KEY_ENV
    monkeypatch.setenv(env_name, "envkey-9f8e7d6c")
    from_argv = SimpleNamespace(api_key="argvkey-1a2b3c4d")
    from_env = SimpleNamespace(api_key="")
    from_stdin = SimpleNamespace(api_key="-")

    monkeypatch.setattr(video_note.sys, "stdin", io.StringIO("  piped-key-1a2b3c4d \n"))
    assert video_note.resolve_api_key(from_stdin) == "piped-key-1a2b3c4d"
    assert video_note.resolve_api_key(from_env) == "envkey-9f8e7d6c"
    assert video_note.resolve_api_key(from_argv) == "argvkey-1a2b3c4d"
    monkeypatch.delenv(env_name)
    assert video_note.resolve_api_key(from_env) == ""


def point_cmd_file_at_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """别把测试写的启动命令落进用户真实的 LOCALAPPDATA。"""
    target = tmp_path / "skill-server-cmd.txt"
    monkeypatch.setattr(video_note, "server_cmd_file", lambda: target)
    return target


def make_fake_exe(directory: Path, name: str = "VideoToNo-portable.exe") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / name
    exe.write_text("", encoding="utf-8")
    return exe


def test_split_server_cmd_keeps_windows_path_separators() -> None:
    sep = chr(92)
    raw = f'"{sep}Users{sep}a b{sep}VideoToNo-portable.exe"'

    assert video_note.split_server_cmd(raw) == [f"{sep}Users{sep}a b{sep}VideoToNo-portable.exe"]
    assert video_note.split_server_cmd("python launcher.py") == ["python", "launcher.py"]


def test_resolve_server_cmd_prefers_cli_then_env_then_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd_file = point_cmd_file_at_tmp(monkeypatch, tmp_path)
    exe = make_fake_exe(tmp_path)
    other = make_fake_exe(tmp_path / "elsewhere", name="second.exe")
    monkeypatch.delenv(video_note.SERVER_CMD_ENV, raising=False)

    argv, cwd, source = video_note.resolve_server_cmd(f'"{exe}"')
    assert argv == [str(exe)] and cwd == str(tmp_path) and source == "--server-cmd"

    monkeypatch.setenv(video_note.SERVER_CMD_ENV, f'"{other}"')
    assert video_note.resolve_server_cmd("")[2] == video_note.SERVER_CMD_ENV

    monkeypatch.delenv(video_note.SERVER_CMD_ENV)
    cmd_file.write_text(f'"{other}"', encoding="utf-8")
    assert video_note.resolve_server_cmd("")[2] == "上次记住的启动命令"


def test_resolve_server_cmd_skips_a_saved_path_that_no_longer_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """用户挪过 exe 是常事：文件不在就不能硬启动，得回退并说清是哪一条坏了。"""
    cmd_file = point_cmd_file_at_tmp(monkeypatch, tmp_path)
    cmd_file.write_text(f'"{tmp_path / "gone.exe"}"', encoding="utf-8")
    monkeypatch.delenv(video_note.SERVER_CMD_ENV, raising=False)
    monkeypatch.setattr(video_note, "repo_launcher_cmd", lambda: None)

    assert video_note.resolve_server_cmd("") is None


def test_resolve_server_cmd_falls_back_to_repo_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """从仓库里用技能的人（作者自己）不该被要求先填路径。"""
    point_cmd_file_at_tmp(monkeypatch, tmp_path)
    monkeypatch.delenv(video_note.SERVER_CMD_ENV, raising=False)

    argv, cwd, source = video_note.resolve_server_cmd("")

    assert source == "仓库内的 launcher.py"
    assert argv[0] == sys.executable and argv[1].endswith("launcher.py")
    assert (Path(cwd) / "backend").is_dir()


def test_server_cmd_cwd_only_follows_a_lone_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`python.exe launcher.py` 的工作目录不能跟着 exe 走。

    跟着走就变成了在 `.venv/Scripts` 里找 launcher.py——第一次真跑技能自启就是死在这，
    而且子进程的 stdout/stderr 都接去了空设备，一百二十秒里一句话都没留下。
    """
    point_cmd_file_at_tmp(monkeypatch, tmp_path)
    monkeypatch.delenv(video_note.SERVER_CMD_ENV, raising=False)
    scripts = tmp_path / "Scripts"
    python = make_fake_exe(scripts, name="python.exe")
    launcher_py = tmp_path / "launcher.py"
    launcher_py.write_text("", encoding="utf-8")

    assert video_note.resolve_server_cmd(f'"{python}" "{launcher_py}"')[1] == ""
    assert video_note.resolve_server_cmd(f'"{python}"')[1] == str(scripts)


def test_spawn_headless_service_passes_headless_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}

    class FakePopen:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakePopen()

    monkeypatch.setattr(video_note.subprocess, "Popen", fake_popen)

    assert video_note.spawn_headless_service(["C:/x/VideoToNo.exe"], str(tmp_path)) == 4242
    assert captured["cmd"] == [
        "C:/x/VideoToNo.exe",
        "--headless",
        "--idle-exit",
        str(video_note.HEADLESS_IDLE_SECONDS),
    ]
    # 后台进程不能继承脚本的管道：那样 agent 收流会被一个不退出的服务挂住
    for key in ("stdin", "stdout", "stderr"):
        assert captured["kwargs"][key] == video_note.subprocess.DEVNULL
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    if os.name == "nt":
        flags = captured["kwargs"]["creationflags"]
        assert flags & video_note.subprocess.DETACHED_PROCESS
        assert "start_new_session" not in captured["kwargs"]
    else:
        assert captured["kwargs"]["start_new_session"] is True


def test_find_service_reuses_a_running_service_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(video_note, "probe_service", lambda: "http://127.0.0.1:8000")

    def no_spawn(*args, **kwargs):
        raise AssertionError("服务已经在跑，不该再起一个")

    monkeypatch.setattr(video_note, "spawn_headless_service", no_spawn)

    assert video_note.find_service("") == "http://127.0.0.1:8000"


def test_find_service_starts_it_and_remembers_the_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd_file = point_cmd_file_at_tmp(monkeypatch, tmp_path)
    exe = make_fake_exe(tmp_path)
    spawned: list[tuple[list[str], str]] = []

    monkeypatch.setattr(video_note, "probe_service", lambda: None)
    monkeypatch.setattr(
        video_note,
        "spawn_headless_service",
        lambda argv, cwd: spawned.append((argv, cwd)) or 4242,
    )
    monkeypatch.setattr(video_note, "wait_for_service", lambda timeout: "http://127.0.0.1:8001")

    assert video_note.find_service(f'"{exe}"') == "http://127.0.0.1:8001"
    assert spawned == [([str(exe)], str(tmp_path))]
    # 给过一次就记住，下次甩链接不再需要用户提供任何东西
    assert cmd_file.read_text(encoding="utf-8") == f'"{exe}"'


def test_find_service_asks_for_the_exe_path_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    point_cmd_file_at_tmp(monkeypatch, tmp_path)
    monkeypatch.delenv(video_note.SERVER_CMD_ENV, raising=False)
    monkeypatch.setattr(video_note, "probe_service", lambda: None)
    monkeypatch.setattr(video_note, "repo_launcher_cmd", lambda: None)

    with pytest.raises(SystemExit):
        video_note.find_service("")

    text = capsys.readouterr().err
    assert "--server-cmd" in text and video_note.SERVER_CMD_ENV in text


def test_closed_port_probe_is_fast() -> None:
    """没监听的端口要立刻被拒，不能靠 HTTP 超时去撞。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]

    started = time.monotonic()
    assert video_note.port_is_listening(free_port) is False
    assert time.monotonic() - started < 0.6


def test_probe_service_does_not_http_a_closed_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """一轮扫描的耗时决定自启能不能成：对 20 个端口逐个撞满 1.5 秒要一分钟，
    而服务在这一分钟的间隙里就按闲置退掉了（真机上第一次跑自启就是这么失败的）。
    """
    monkeypatch.setattr(
        video_note, "port_is_listening", lambda port, timeout=0.3: port == 8003
    )
    asked: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"ok","service":"VideoToNo"}'

    def fake_urlopen(url, timeout):
        asked.append(url)
        if "8003" in url:
            return FakeResponse()
        raise AssertionError("没监听的端口不该发 HTTP 请求")

    monkeypatch.setattr(video_note.urllib.request, "urlopen", fake_urlopen)

    assert video_note.probe_service() == "http://127.0.0.1:8003"
    assert asked == ["http://127.0.0.1:8003/api/health"]


def test_describe_idle_reports_what_was_actually_sent() -> None:
    """用户自己给了 `--idle-exit` 就不能照本宣科说"15 分钟"。"""
    assert video_note.describe_idle(["x.exe", "--headless"]) == "闲置 15 分钟后自动退出"
    assert video_note.describe_idle(["x.exe", "--idle-exit", "25"]) == "闲置 25 秒后自动退出"
    assert video_note.describe_idle(["x.exe", "--idle-exit", "0"]) == "常驻运行，不会自动退出"


def test_headless_command_does_not_duplicate_user_flags() -> None:
    assert video_note.headless_command(["x.exe"]) == [
        "x.exe",
        "--headless",
        "--idle-exit",
        str(video_note.HEADLESS_IDLE_SECONDS),
    ]
    assert video_note.headless_command(["x.exe", "--headless", "--idle-exit", "30"]) == [
        "x.exe",
        "--headless",
        "--idle-exit",
        "30",
    ]
