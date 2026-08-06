"""VideoToNo MCP Server：把「视频链接 → Markdown 笔记」能力暴露为 MCP 工具。

接入方式（两种传输，工具完全相同）：
- stdio：`python -m backend.mcp_server`，供 Codex CLI、Cherry Studio 等以本地命令方式接入；
- SSE：挂载在 FastAPI 的 `/mcp/sse`（见 main.py），Cherry Studio 以远程 URL 方式接入，
  无需本机 Python 环境（便携版 exe 用户推荐此方式）。

工具通过 HTTP 调用本地 VideoToNo 后端（默认 http://127.0.0.1:8000，可被
VIDEOTONOTES_BACKEND_URL 覆盖）。任务实际由后端进程执行，MCP 只是访问通道，
因此无论从 UI、Cherry Studio 还是 Codex 提交，看到的都是同一份任务状态。
后端未启动时，工具会自动扫描 8000-8019 端口寻找正在运行的服务，找不到则给出提示。
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
PORT_SCAN_RANGE = 20
HTTP_TIMEOUT = httpx.Timeout(20.0)

mcp = FastMCP(
    "VideoToNo",
    instructions=(
        "VideoToNo 本地视频笔记服务：输入 B 站 / YouTube 等视频链接，返回带时间轴的 "
        "Markdown 视频笔记。提交后用 get_task_status 轮询，直到 status 为 completed "
        "（视频处理通常需要 1-10 分钟）。API Key 请向用户询问，且不要在对话中复述。"
    ),
)


def _extract_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    return str(
        data.get("detail") or data.get("error") or data.get("message") or response.text[:300]
    )


class BackendClient:
    """本地 VideoToNo 后端的薄 HTTP 客户端，带端口自动发现。"""

    def __init__(self, base_url: str = "", transport: Any = None) -> None:
        self.base_url = (base_url or DEFAULT_BACKEND_URL).rstrip("/")
        self._client = httpx.Client(timeout=HTTP_TIMEOUT, transport=transport)

    def start_summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/summarize", json=payload)

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/task/{task_id}")

    def whisper_models(self) -> dict[str, Any]:
        return self._request("GET", "/api/whisper-models")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            discovered = _discover_backend_url(self.base_url)
            if discovered == self.base_url:
                raise RuntimeError(
                    f"无法连接本地 VideoToNo 服务（{self.base_url}）。"
                    "请先启动服务（python launcher.py 或 start.ps1）后再试。"
                ) from exc
            # 端口扫描找到实际运行的服务（launcher 会在 8000-8019 之间选端口）
            self.base_url = discovered
            return self._request(method, path, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(
                f"VideoToNo 后端返回错误（HTTP {response.status_code}）：{_extract_detail(response)}"
            )
        return response.json()


def _discover_backend_url(current: str) -> str:
    """在当前地址连不上时，扫描 8000-8019 寻找正在运行的 VideoToNo 服务。"""
    for port in range(8000, 8000 + PORT_SCAN_RANGE):
        url = f"http://127.0.0.1:{port}"
        if url == current:
            continue
        try:
            response = httpx.get(f"{url}/api/health", timeout=1.0)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return url
        except Exception:
            continue
    return current


def configure_backend_url(base_url: str) -> None:
    """由 main.py 在挂载 SSE 端点时注入实际端口，保证自调命中当前服务。"""
    global _client
    _client = BackendClient(base_url)


def _get_client() -> BackendClient:
    global _client
    if _client is None:
        _client = BackendClient(os.getenv("VIDEOTONOTES_BACKEND_URL", ""))
    return _client


_client: BackendClient | None = None


@mcp.tool()
def summarize_video(
    video_url: str,
    api_key: str,
    model_type: str = "deepseek",
    model: str | None = None,
    base_url: str | None = None,
    summary_style: str = "detailed",
    reasoning_effort: str = "auto",
    whisper_model: str = "base",
    use_gpu: bool = False,
    include_screenshots: bool = False,
    screenshot_interval: int = 30,
    prefer_subtitles: bool = True,
    processing_mode: str = "reuse",
    bilibili_sessdata: str | None = None,
    bilibili_bili_jct: str | None = None,
    bilibili_buvid3: str | None = None,
) -> dict[str, Any]:
    """提交视频总结任务（异步执行，立即返回任务 ID，用 get_task_status 轮询）。

    Args:
        video_url: B 站 / YouTube 等视频链接，必填。
        api_key: 大模型服务 API Key，必填（不会保存，仅随本次任务提交）。
        model_type: 模型服务商，deepseek / openai / glm / qwen / moonshot / custom。
        model: 模型 ID；不填则使用所选服务商的默认模型。
        base_url: 自定义 OpenAI 兼容接口地址（model_type=custom 时必填）。
        summary_style: detailed=详细笔记+点评分析 / faithful=详细复原（仅内容）/ concise=精简摘要。
        reasoning_effort: auto / off / low / medium / high / max。
        whisper_model: 无字幕时的语音转写模型，tiny / base / small / medium / large-v3 / turbo。
        use_gpu: 是否启用 NVIDIA GPU 转写。
        include_screenshots: 是否附带定时截图。
        screenshot_interval: 截图间隔秒数（5-300）。
        prefer_subtitles: 优先使用平台字幕（默认开启）。
        processing_mode: reuse=复用同链接已有转录 / restart=从头处理。
        bilibili_sessdata/bilibili_bili_jct/bilibili_buvid3: B 站访问凭据（可选，AI 字幕需要）。
    """
    llm_config: dict[str, Any] = {
        "model_type": model_type,
        "api_key": api_key,
    }
    if base_url:
        llm_config["base_url"] = base_url
    if model:
        llm_config["model"] = model

    payload: dict[str, Any] = {
        "video_url": video_url,
        "processing_mode": processing_mode,
        "summary_style": summary_style,
        "reasoning_effort": reasoning_effort,
        "prefer_subtitles": prefer_subtitles,
        "include_screenshots": include_screenshots,
        "screenshot_interval": screenshot_interval,
        "whisper_model": whisper_model,
        "use_gpu": use_gpu,
        "llm_config": llm_config,
    }
    if bilibili_sessdata or bilibili_bili_jct or bilibili_buvid3:
        payload["bilibili_cookie"] = {
            "sessdata": bilibili_sessdata or "",
            "bili_jct": bilibili_bili_jct or "",
            "buvid3": bilibili_buvid3 or "",
        }

    data = _get_client().start_summarize(payload)
    task_id = data.get("task_id")
    if not task_id:
        raise RuntimeError(f"后端未返回任务 ID：{data}")
    hint = f"任务已提交：{task_id}"
    if data.get("reused_task_id"):
        hint += f"（复用任务 {data['reused_task_id']} 的转录）"
    return {"ok": True, "task_id": task_id, "hint": hint + "，请用 get_task_status 轮询进度。"}


@mcp.tool()
def get_task_status(task_id: str, include_markdown: bool = True) -> dict[str, Any]:
    """查询视频总结任务的进度与结果。

    Args:
        task_id: summarize_video 返回的任务 ID。
        include_markdown: 为 False 时省略笔记正文，只返回状态概要（减少输出量）。
    """
    data = _get_client().get_task(task_id)
    if not include_markdown and data.get("result") and "markdown" in data["result"]:
        data = {
            **data,
            "result": {key: value for key, value in data["result"].items() if key != "markdown"},
        }
    return data


@mcp.tool()
def list_whisper_models() -> dict[str, Any]:
    """列出 Whisper 语音模型在本机的缓存状态（cached 已缓存 / incomplete 不完整 / missing 未下载）。"""
    return _get_client().whisper_models()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
