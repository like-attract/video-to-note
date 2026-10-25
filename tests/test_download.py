import urllib.error
from pathlib import Path

import pytest

from backend.download import download_to_file


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def read(self, size: int) -> bytes:
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_download_reports_progress_and_verifies_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"x" * 2_000
    monkeypatch.setattr(
        "backend.download.urllib.request.urlopen",
        lambda *args, **kwargs: _Response(200, body, {"Content-Length": str(len(body))}),
    )
    progress: list[tuple[int, int]] = []
    target = tmp_path / "model.bin"

    ok, error = download_to_file(
        "https://example.com/model.bin",
        target,
        {},
        progress_callback=lambda received, total: progress.append((received, total)),
    )

    assert ok is True and error is None
    assert target.read_bytes() == body
    assert progress[-1] == (len(body), len(body))


def test_download_resumes_truncated_file_with_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连接中途断掉：保留 .part，下一次带 Range 续传，不从头下载。"""
    full = b"x" * 1_000
    calls: list[str | None] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001
        calls.append(request.headers.get("Range"))
        if len(calls) == 1:
            return _Response(200, full[:500], {"Content-Length": "1000"})
        return _Response(206, full[500:], {"Content-Length": "500"})

    monkeypatch.setattr("backend.download.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("backend.download.time.sleep", lambda _seconds: None)
    target = tmp_path / "model.bin"

    ok, error = download_to_file("https://example.com/model.bin", target, {})

    assert ok is True and error is None
    assert calls == [None, "bytes=500-"]
    assert target.read_bytes() == full


def test_download_restarts_when_range_not_satisfiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """遗留 .part 比服务端文件还长（换了 revision）：416 后丢掉重下。"""
    part = tmp_path / "model.bin.part"
    part.write_bytes(b"old" * 100)
    calls: list[str | None] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001
        calls.append(request.headers.get("Range"))
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 416, "Range Not Satisfiable", {}, None)
        return _Response(200, b"fresh", {"Content-Length": "5"})

    monkeypatch.setattr("backend.download.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("backend.download.time.sleep", lambda _seconds: None)
    target = tmp_path / "model.bin"

    ok, error = download_to_file("https://example.com/model.bin", target, {})

    assert ok is True and error is None
    assert calls[0] == "bytes=300-"
    assert target.read_bytes() == b"fresh"
