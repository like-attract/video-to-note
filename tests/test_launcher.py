import json

import launcher


def test_version_tuple_accepts_release_tags() -> None:
    assert launcher.version_tuple("v1.1.4") == (1, 1, 4)
    assert launcher.version_tuple("1.2.0-rc1") == (1, 2, 0)
    assert launcher.version_tuple("invalid") == (0,)


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
