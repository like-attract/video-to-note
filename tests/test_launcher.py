import json
from pathlib import Path

import launcher


def test_version_tuple_accepts_release_tags() -> None:
    assert launcher.version_tuple("v1.1.4") == (1, 1, 4)
    assert launcher.version_tuple("1.2.0-rc1") == (1, 2, 0)
    assert launcher.version_tuple("invalid") == (0,)


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
        lambda: {"tag_name": "v1.2.0", "html_url": "https://github.com/example/release"},
    )
    monkeypatch.setattr(launcher, "show_update_message", lambda *args, **kwargs: 6)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)

    launcher.check_for_updates()

    assert opened == ["https://github.com/example/release"]
