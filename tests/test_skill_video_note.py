"""Agent Skill 脚本的凭据处理测试。

`skills/video-to-note/scripts/video_note.py` 不在包里（没有可导入的包路径），
所以用 importlib 按文件加载——这个脚本是发给用户机器跑的，Key 的来路和出口
必须钉住：命令行明文要能换成环境变量/stdin，任何回显凭据的文本都要先脱敏。
"""
from __future__ import annotations

import importlib.util
import io
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
        '"authorization": Bearer Q29uZ3JldGVzczEyMzQ1Ng, "note": "' + plain + '"}'
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
