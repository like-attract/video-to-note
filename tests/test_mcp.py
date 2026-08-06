"""MCP server 测试：工具参数构建、后端客户端错误处理与端口发现。"""
import httpx
import pytest

from backend import mcp_server


class FakeClient:
    def __init__(self, response=None, captured=None):
        self.response = response or {"ok": True, "task_id": "task-1", "reused_task_id": None}
        self.captured = captured or {}

    def start_summarize(self, payload):
        self.captured["payload"] = payload
        return self.response

    def get_task(self, task_id):
        return {"status": "completed", "result": {"title": "T", "markdown": "# 笔记"}}

    def whisper_models(self):
        return {"models": [{"id": "base", "status": "cached"}]}


def test_summarize_video_builds_payload_with_optional_fields(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(mcp_server, "_get_client", lambda: fake)

    result = mcp_server.summarize_video(
        "https://www.bilibili.com/video/BV1xx",
        "sk-test",
        model_type="custom",
        model="my-model",
        base_url="https://api.example.com/v1",
        summary_style="concise",
        whisper_model="small",
        bilibili_sessdata="sess",
        bilibili_bili_jct="jct",
        bilibili_buvid3="buvid",
    )

    assert result["ok"] is True
    assert result["task_id"] == "task-1"
    payload = fake.captured["payload"]
    assert payload["video_url"] == "https://www.bilibili.com/video/BV1xx"
    assert payload["llm_config"] == {
        "model_type": "custom",
        "api_key": "sk-test",
        "base_url": "https://api.example.com/v1",
        "model": "my-model",
    }
    assert payload["summary_style"] == "concise"
    assert payload["whisper_model"] == "small"
    assert payload["bilibili_cookie"] == {
        "sessdata": "sess",
        "bili_jct": "jct",
        "buvid3": "buvid",
    }


def test_summarize_video_omits_optional_keys_when_unset(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(mcp_server, "_get_client", lambda: fake)

    mcp_server.summarize_video("https://www.youtube.com/watch?v=abc", "sk-test")

    payload = fake.captured["payload"]
    assert "base_url" not in payload["llm_config"]
    assert "model" not in payload["llm_config"]
    assert "bilibili_cookie" not in payload


def test_summarize_video_raises_when_backend_misses_task_id(monkeypatch) -> None:
    fake = FakeClient(response={"ok": False})
    monkeypatch.setattr(mcp_server, "_get_client", lambda: fake)

    with pytest.raises(RuntimeError, match="未返回任务 ID"):
        mcp_server.summarize_video("https://www.bilibili.com/video/BV1xx", "sk-test")


def test_get_task_status_strips_markdown_when_requested(monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr(mcp_server, "_get_client", lambda: fake)

    summary = mcp_server.get_task_status("task-1", include_markdown=False)
    assert summary["result"]["title"] == "T"
    assert "markdown" not in summary["result"]

    full = mcp_server.get_task_status("task-1")
    assert full["result"]["markdown"] == "# 笔记"


def test_backend_client_parses_api_error_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "不支持的 Whisper 模型"})

    client = mcp_server.BackendClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(RuntimeError, match="不支持的 Whisper 模型"):
        client.start_summarize({"video_url": "x", "llm_config": {"model_type": "deepseek"}})


def test_backend_client_falls_back_to_scanned_port(monkeypatch) -> None:
    """默认端口连不上时，自动扫描 8000-8019 找到实际运行的服务。"""
    healthy_port = {"port": None}

    def request(self, method, url, **kwargs):
        parsed = httpx.URL(str(url))
        if parsed.path == "/api/health":
            if parsed.port == 8005:
                healthy_port["port"] = parsed.port
                return httpx.Response(200, json={"status": "ok"})
            raise httpx.ConnectError("refused")
        if parsed.port == 8005:
            return httpx.Response(200, json={"task_id": "found"})
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.Client, "request", request)
    client = mcp_server.BackendClient(base_url="http://127.0.0.1:8000")

    result = client.start_summarize({"video_url": "x"})
    assert result["task_id"] == "found"
    assert healthy_port["port"] == 8005
    assert client.base_url == "http://127.0.0.1:8005"


def test_backend_client_raises_friendly_error_when_unreachable(monkeypatch) -> None:
    def failing_request(self, method, path, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.Client, "request", failing_request)
    client = mcp_server.BackendClient(base_url="http://127.0.0.1:8000")

    with pytest.raises(RuntimeError, match="请先启动服务"):
        client.get_task("task-1")


def test_mcp_endpoint_mounted_on_fastapi_app() -> None:
    from starlette.routing import Mount

    from backend import main

    mounts = [route for route in main.app.routes if isinstance(route, Mount)]
    assert any(getattr(route, "path", "") == "/mcp" for route in mounts)
