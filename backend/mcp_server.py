"""VideoToNo MCP Server：把「视频链接 → Markdown 笔记」能力暴露为 MCP 工具。

接入方式（两种传输，工具完全相同）：
- stdio：`python -m backend.mcp_server`，供 Codex CLI、Cherry Studio 等以本地命令方式接入；
- SSE：挂载在 FastAPI 的 `/mcp/sse`（见 main.py），Cherry Studio 以远程 URL 方式接入，
  无需本机 Python 环境（便携版 exe 用户推荐此方式）。

任务实际由本地后端进程执行，MCP 只是访问通道，因此从 UI、Cherry Studio 还是 Codex
提交，看到的都是同一份任务状态；Whisper 模型缓存、任务目录等全部复用本机资源。

LLM 配置与 B 站凭据可由后端统一保管（workspace/llm_config.json 与
bili_credentials.json）：调用 summarize_video 时省略 api_key / bilibili_* 参数
即自动使用已保存的配置，无需每次手动提供。
"""
from __future__ import annotations

import asyncio
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
        "（视频处理通常需要 1-10 分钟）。\n"
        "LLM 配置与 B 站凭据支持保存到本机（save_llm_config / save_bilibili_credentials），"
        "保存后调用 summarize_video 时无需再传 api_key 或 bilibili_* 参数；"
        "可通过 get_saved_config 查看保存状态。若用户已保存过配置，不要重复索要。"
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
    """stdio 模式后端：通过 HTTP 调用本地 VideoToNo（带端口自动发现）。"""

    def __init__(self, base_url: str = "", transport: Any = None) -> None:
        self.base_url = (base_url or DEFAULT_BACKEND_URL).rstrip("/")
        # trust_env=False：本地回环服务不走系统代理（代理会拦截 localhost 请求）
        self._client = httpx.Client(
            timeout=HTTP_TIMEOUT, transport=transport, trust_env=False
        )

    async def start_summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._request, "POST", "/api/summarize", json=payload
        )

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._request, "GET", f"/api/task/{task_id}")

    async def whisper_models(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._request, "GET", "/api/whisper-models")

    async def get_llm_config(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._request, "GET", "/api/llm-config")

    async def save_llm_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._request, "POST", "/api/llm-config", json=config
        )

    async def get_bili_credentials(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._request, "GET", "/api/bili-credentials")

    async def save_bili_credentials(self, credentials: dict[str, str]) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._request, "POST", "/api/bili-credentials", json=credentials
        )

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
            response = httpx.get(f"{url}/api/health", timeout=1.0, trust_env=False)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return url
        except Exception:
            continue
    return current


class InProcessBackend:
    """SSE 挂载在 FastAPI 进程内时使用：直接调用同进程端点函数。

    HTTP 自调会被 uvicorn 的 SSE 长连接占用事件循环而互相等待（自调死锁），
    因此挂载模式不走 HTTP，改为进程内调用。任务状态天然与网页端一致。
    """

    async def start_summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .main import SummarizeRequest, start_summarize

        request = SummarizeRequest(**payload)
        return await start_summarize(request)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        from .main import get_task_status

        return await get_task_status(task_id)

    async def whisper_models(self) -> dict[str, Any]:
        from .main import whisper_models_status

        return await whisper_models_status()

    async def get_llm_config(self) -> dict[str, Any]:
        from .main import get_llm_config

        return await get_llm_config()

    async def save_llm_config(self, config: dict[str, Any]) -> dict[str, Any]:
        from .main import LLMConfigPayload, save_llm_config

        return await save_llm_config(LLMConfigPayload(**config))

    async def get_bili_credentials(self) -> dict[str, Any]:
        from .main import get_bili_credentials

        return await get_bili_credentials()

    async def save_bili_credentials(self, credentials: dict[str, str]) -> dict[str, Any]:
        from .main import BiliCredentialsPayload, save_bili_credentials

        return await save_bili_credentials(BiliCredentialsPayload(**credentials))


_backend: BackendClient | InProcessBackend | None = None


def use_http_backend(base_url: str = "") -> None:
    """stdio 模式：通过 HTTP 调用本地后端（支持端口自动发现）。"""
    global _backend
    _backend = BackendClient(base_url or os.getenv("VIDEOTONOTES_BACKEND_URL", ""))


def use_in_process_backend() -> None:
    """SSE 挂载模式：由 main.py 调用，直接使用同进程端点函数。"""
    global _backend
    _backend = InProcessBackend()


def _get_backend() -> BackendClient | InProcessBackend:
    global _backend
    if _backend is None:
        use_http_backend()
    return _backend


async def _resolve_llm_config(
    backend: BackendClient | InProcessBackend,
    api_key: str | None,
    model_type: str | None,
    model: str | None,
    base_url: str | None,
) -> dict[str, Any]:
    """api_key 缺省时使用本机已保存的 LLM 配置；显式参数可覆盖已保存值。"""
    if api_key:
        config: dict[str, Any] = {"model_type": model_type or "deepseek", "api_key": api_key}
        if base_url:
            config["base_url"] = base_url
        if model:
            config["model"] = model
        return config
    saved = await backend.get_llm_config()
    if not saved.get("saved") or not saved.get("api_key"):
        raise RuntimeError(
            "未提供 api_key，且本机未保存 LLM 配置。"
            "请先调用 save_llm_config 保存配置，或在调用中传入 api_key。"
        )
    config = {
        "model_type": saved.get("model_type") or "deepseek",
        "api_key": saved["api_key"],
    }
    if saved.get("base_url"):
        config["base_url"] = saved["base_url"]
    if saved.get("model"):
        config["model"] = saved["model"]
    if model_type:
        config["model_type"] = model_type
    if base_url:
        config["base_url"] = base_url
    if model:
        config["model"] = model
    return config


async def _resolve_bili_cookie(
    backend: BackendClient | InProcessBackend,
    sessdata: str | None,
    bili_jct: str | None,
    buvid3: str | None,
) -> dict[str, str] | None:
    """显式传入的凭据优先；否则使用本机已保存的 B 站凭据。"""
    if sessdata or bili_jct or buvid3:
        return {
            "sessdata": sessdata or "",
            "bili_jct": bili_jct or "",
            "buvid3": buvid3 or "",
        }
    saved = await backend.get_bili_credentials()
    if saved.get("saved"):
        return {
            "sessdata": str(saved.get("sessdata") or ""),
            "bili_jct": str(saved.get("bili_jct") or ""),
            "buvid3": str(saved.get("buvid3") or ""),
        }
    return None


@mcp.tool()
async def summarize_video(
    video_url: str,
    api_key: str | None = None,
    model_type: str | None = None,
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
        api_key: 大模型 API Key。可省略：省略时使用本机已保存的配置（save_llm_config）。
        model_type: 模型服务商（deepseek/openai/glm/qwen/moonshot/custom）；省略时用已保存配置。
        model: 模型 ID；省略时用已保存配置或服务商默认模型。
        base_url: 自定义 OpenAI 兼容接口地址（custom 必填）。
        summary_style: detailed=详细笔记+点评分析 / faithful=详细复原 / concise=精简摘要。
        reasoning_effort: auto / off / low / medium / high / max。
        whisper_model: 无字幕时的转写模型（tiny/base/small/medium/large-v3/turbo）。
        use_gpu: 是否启用 NVIDIA GPU 转写。
        include_screenshots: 是否附带定时截图。
        screenshot_interval: 截图间隔秒数（5-300）。
        prefer_subtitles: 优先使用平台字幕（默认开启）。
        processing_mode: reuse=复用同链接已有转录 / restart=从头处理。
        bilibili_sessdata/bilibili_bili_jct/bilibili_buvid3: B 站凭据（可省略，
            省略时使用已保存凭据；未保存也能处理，只是可能读不到 AI 字幕）。
    """
    backend = _get_backend()
    llm_config = await _resolve_llm_config(backend, api_key, model_type, model, base_url)

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
    bili_cookie = await _resolve_bili_cookie(
        backend, bilibili_sessdata, bilibili_bili_jct, bilibili_buvid3
    )
    if bili_cookie:
        payload["bilibili_cookie"] = bili_cookie

    data = await backend.start_summarize(payload)
    task_id = data.get("task_id")
    if not task_id:
        raise RuntimeError(f"后端未返回任务 ID：{data}")
    hint = f"任务已提交：{task_id}"
    if data.get("reused_task_id"):
        hint += f"（复用任务 {data['reused_task_id']} 的转录）"
    return {"ok": True, "task_id": task_id, "hint": hint + "，请用 get_task_status 轮询进度。"}


@mcp.tool()
async def get_task_status(task_id: str, include_markdown: bool = True) -> dict[str, Any]:
    """查询视频总结任务的进度与结果。

    Args:
        task_id: summarize_video 返回的任务 ID。
        include_markdown: 为 False 时省略笔记正文，只返回状态概要（减少输出量）。
    """
    data = await _get_backend().get_task(task_id)
    if not include_markdown and data.get("result") and "markdown" in data["result"]:
        data = {
            **data,
            "result": {key: value for key, value in data["result"].items() if key != "markdown"},
        }
    return data


@mcp.tool()
async def list_whisper_models() -> dict[str, Any]:
    """列出 Whisper 语音模型在本机的缓存状态（cached 已缓存 / incomplete 不完整 / missing 未下载）。"""
    return await _get_backend().whisper_models()


@mcp.tool()
async def save_llm_config(
    api_key: str,
    model_type: str = "deepseek",
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """把大模型配置保存到本机（明文写入工作目录，仅本机可读，供 MCP 客户端复用）。

    保存后调用 summarize_video 时无需再传 api_key / model_type / model。
    """
    config: dict[str, Any] = {"model_type": model_type, "api_key": api_key}
    if model:
        config["model"] = model
    if base_url:
        config["base_url"] = base_url
    await _get_backend().save_llm_config(config)
    return {
        "saved": True,
        "model_type": model_type,
        "model": model,
        "hint": "已保存到本机。之后调用 summarize_video 可省略 api_key。",
    }


@mcp.tool()
async def save_bilibili_credentials(
    sessdata: str,
    bili_jct: str = "",
    buvid3: str = "",
) -> dict[str, Any]:
    """把 B 站访问凭据保存到本机（明文写入工作目录，仅本机可读）。

    保存后处理 B 站视频会自动携带这些凭据（优先使用 AI 字幕），无需每次传入。
    凭据保存在后端工作目录，注意不要分享该文件。
    """
    await _get_backend().save_bili_credentials(
        {"sessdata": sessdata, "bili_jct": bili_jct, "buvid3": buvid3}
    )
    return {"saved": True, "hint": "已保存。之后处理 B 站视频将自动使用这些凭据。"}


@mcp.tool()
async def get_saved_config() -> dict[str, Any]:
    """查看本机已保存的 LLM 配置与 B 站凭据状态（敏感信息脱敏显示）。"""
    llm = await _get_backend().get_llm_config()
    bili = await _get_backend().get_bili_credentials()
    return {
        "llm_config": {
            "saved": bool(llm.get("saved")),
            "model_type": llm.get("model_type"),
            "model": llm.get("model"),
            "api_key_masked": (
                str(llm.get("api_key", ""))[:4] + "****" if llm.get("saved") else None
            ),
        },
        "bilibili_credentials": {
            "saved": bool(bili.get("saved")),
            "sessdata_masked": (
                str(bili.get("sessdata", ""))[:4] + "****" if bili.get("saved") else None
            ),
        },
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
