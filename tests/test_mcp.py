"""MCP server 测试：工具参数构建、保存配置回退、错误处理与端口发现。"""
import httpx
import pytest

from backend import mcp_server


class FakeBackend:
    def __init__(self, response=None, captured=None, llm_config=None, bili_config=None):
        self.response = response or {"ok": True, "task_id": "task-1", "reused_task_id": None}
        self.captured = captured or {}
        self.llm_config = llm_config or {"saved": False}
        self.bili_config = bili_config or {"saved": False}
        self.saved_llm = None
        self.saved_bili = None

    async def start_summarize(self, payload):
        self.captured["payload"] = payload
        return self.response

    async def get_task(self, task_id):
        return {"status": "completed", "result": {"title": "T", "markdown": "# 笔记"}}

    async def whisper_models(self):
        return {"models": [{"id": "base", "status": "cached"}]}

    async def get_llm_config(self):
        return self.llm_config

    async def save_llm_config(self, config):
        self.saved_llm = config
        return {"saved": True}

    async def get_bili_credentials(self):
        return self.bili_config

    async def save_bili_credentials(self, credentials):
        self.saved_bili = credentials
        return {"saved": True}


SAVED_LLM = {"saved": True, "model_type": "deepseek", "api_key": "sk-saved-key"}
SAVED_BILI = {"saved": True, "sessdata": "sess-saved", "bili_jct": "jct", "buvid3": "buvid"}


@pytest.mark.asyncio
async def test_summarize_video_builds_payload_with_optional_fields(monkeypatch) -> None:
    fake = FakeBackend()
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    result = await mcp_server.summarize_video(
        "https://www.bilibili.com/video/BV1xx",
        api_key="sk-test",
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


@pytest.mark.asyncio
async def test_summarize_video_omits_optional_keys_when_unset(monkeypatch) -> None:
    fake = FakeBackend()
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    await mcp_server.summarize_video("https://www.youtube.com/watch?v=abc", api_key="sk-test")

    payload = fake.captured["payload"]
    assert "base_url" not in payload["llm_config"]
    assert "model" not in payload["llm_config"]
    assert "bilibili_cookie" not in payload


@pytest.mark.asyncio
async def test_summarize_video_raises_when_backend_misses_task_id(monkeypatch) -> None:
    fake = FakeBackend(response={"ok": False})
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    with pytest.raises(RuntimeError, match="未返回任务 ID"):
        await mcp_server.summarize_video("https://www.bilibili.com/video/BV1xx", api_key="sk-test")


@pytest.mark.asyncio
async def test_summarize_video_uses_saved_llm_config_when_api_key_omitted(
    monkeypatch,
) -> None:
    fake = FakeBackend(llm_config=SAVED_LLM, bili_config=SAVED_BILI)
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    await mcp_server.summarize_video("https://www.bilibili.com/video/BV1xx")

    payload = fake.captured["payload"]
    assert payload["llm_config"] == {"model_type": "deepseek", "api_key": "sk-saved-key"}
    assert payload["bilibili_cookie"] == {
        "sessdata": "sess-saved",
        "bili_jct": "jct",
        "buvid3": "buvid",
    }


@pytest.mark.asyncio
async def test_summarize_video_explicit_args_override_saved_config(monkeypatch) -> None:
    fake = FakeBackend(llm_config=SAVED_LLM)
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    await mcp_server.summarize_video(
        "https://www.bilibili.com/video/BV1xx", model_type="glm", model="glm-5.2"
    )

    payload = fake.captured["payload"]
    assert payload["llm_config"] == {
        "model_type": "glm",
        "api_key": "sk-saved-key",
        "model": "glm-5.2",
    }


@pytest.mark.asyncio
async def test_summarize_video_raises_when_no_key_and_nothing_saved(monkeypatch) -> None:
    fake = FakeBackend()
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    with pytest.raises(RuntimeError, match="save_llm_config"):
        await mcp_server.summarize_video("https://www.bilibili.com/video/BV1xx")


@pytest.mark.asyncio
async def test_save_tools_store_config_and_get_saved_config_masks_secrets(
    monkeypatch,
) -> None:
    fake = FakeBackend(llm_config=SAVED_LLM, bili_config=SAVED_BILI)
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    saved = await mcp_server.save_llm_config("sk-new", model_type="qwen", model="qwen3.7-plus")
    assert saved["saved"] is True
    assert fake.saved_llm == {
        "model_type": "qwen",
        "api_key": "sk-new",
        "model": "qwen3.7-plus",
    }

    saved_bili = await mcp_server.save_bilibili_credentials("sess-1", "jct-1", "buvid-1")
    assert saved_bili["saved"] is True
    assert fake.saved_bili == {"sessdata": "sess-1", "bili_jct": "jct-1", "buvid3": "buvid-1"}

    status = await mcp_server.get_saved_config()
    assert status["llm_config"]["api_key_masked"] == "sk-s****"
    assert status["bilibili_credentials"]["sessdata_masked"] == "sess****"


class WaitingBackend(FakeBackend):
    def __init__(self, terminal_status="completed", calls_to_terminal=3):
        super().__init__()
        self.calls = 0
        self.terminal_status = terminal_status
        self.calls_to_terminal = calls_to_terminal

    async def get_task(self, task_id):
        self.calls += 1
        if self.calls < self.calls_to_terminal:
            return {"status": "processing", "progress": 30}
        return {
            "status": self.terminal_status,
            "result": {"title": "T", "markdown": "# 笔记"},
        }


@pytest.mark.asyncio
async def test_wait_for_task_polls_until_terminal(monkeypatch) -> None:
    fake = WaitingBackend()
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    result = await mcp_server.wait_for_task("task-1", timeout_seconds=10)
    assert result["status"] == "completed"
    assert result["result"]["markdown"] == "# 笔记"
    assert result["waited_seconds"] > 0
    assert fake.calls == 3


@pytest.mark.asyncio
async def test_wait_for_task_times_out_with_hint(monkeypatch) -> None:
    fake = WaitingBackend(calls_to_terminal=999)
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    result = await mcp_server.wait_for_task("task-1", timeout_seconds=5)
    assert result["status"] == "processing"
    assert "再次调用 wait_for_task" in result["hint"]


@pytest.mark.asyncio
async def test_wait_for_task_strips_markdown_when_requested(monkeypatch) -> None:
    fake = WaitingBackend()
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    result = await mcp_server.wait_for_task("task-1", timeout_seconds=10, include_markdown=False)
    assert result["status"] == "completed"
    assert "markdown" not in result["result"]


@pytest.mark.asyncio
async def test_get_task_status_strips_markdown_when_requested(monkeypatch) -> None:
    fake = FakeBackend()
    monkeypatch.setattr(mcp_server, "_get_backend", lambda: fake)

    summary = await mcp_server.get_task_status("task-1", include_markdown=False)
    assert summary["result"]["title"] == "T"
    assert "markdown" not in summary["result"]

    full = await mcp_server.get_task_status("task-1")
    assert full["result"]["markdown"] == "# 笔记"


def test_backend_client_parses_api_error_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "不支持的 Whisper 模型"})

    client = mcp_server.BackendClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(RuntimeError, match="不支持的 Whisper 模型"):
        client._request("POST", "/api/summarize", json={})


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

    result = client._request("POST", "/api/summarize", json={})
    assert result["task_id"] == "found"
    assert healthy_port["port"] == 8005
    assert client.base_url == "http://127.0.0.1:8005"


def test_backend_client_raises_friendly_error_when_unreachable(monkeypatch) -> None:
    def failing_request(self, method, url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.Client, "request", failing_request)
    client = mcp_server.BackendClient(base_url="http://127.0.0.1:8000")

    with pytest.raises(RuntimeError, match="请先启动服务"):
        client._request("GET", "/api/task/task-1")


def test_mcp_endpoint_mounted_on_fastapi_app() -> None:
    from starlette.routing import Mount

    from backend import main

    mounts = [route for route in main.app.routes if isinstance(route, Mount)]
    assert any(getattr(route, "path", "") == "/mcp" for route in mounts)
