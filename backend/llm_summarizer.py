from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Sequence

from .transcript import TranscriptSegment, chunk_segments, segments_to_prompt


PROVIDER_DEFAULTS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-v4-flash"),
    "openai": ("https://api.openai.com/v1", "gpt-5.6-terra"),
    "openai_gpt4": ("https://api.openai.com/v1", "gpt-4o"),
    "openai_gpt35": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "qwen": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-plus",
    ),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4.5-flash"),
    "moonshot": ("https://api.moonshot.cn/v1", "kimi-k3"),
}

SUMMARY_CHUNK_CHARACTERS = 9_000
MERGE_INPUT_CHARACTERS = 14_000
LLM_TIMEOUT_SECONDS = 300
LLM_MAX_RETRIES = 0
DEEPSEEK_HIGH_TOKEN_BUDGET = 12_000
ProgressCallback = Callable[[int, str], Awaitable[None] | None]


class LLMSummarizer:
    def __init__(
        self,
        model_type: str,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("An API key is required")
        if model_type == "custom":
            if not base_url or not model:
                raise ValueError("Custom providers require base_url and model")
        else:
            if model_type not in PROVIDER_DEFAULTS:
                raise ValueError(f"Unsupported model provider: {model_type}")
            default_base_url, default_model = PROVIDER_DEFAULTS[model_type]
            base_url = base_url or default_base_url
            model = model or default_model

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai is not installed. Run pip install -r backend/requirements.txt"
            ) from exc

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )
        self.model_type = model_type
        self.base_url = base_url or ""
        self.model = model
        self.warnings: list[str] = []

    async def test_connection(self, timeout_seconds: float = 20.0) -> tuple[bool, str, float]:
        """轻量连通性测试：发一个极小请求，返回 (是否成功, 可读消息, 耗时秒)。"""
        import time

        start = time.monotonic()
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=8,
                timeout=timeout_seconds,
            )
            latency = time.monotonic() - start
            reply = (response.choices[0].message.content or "").strip()
            if reply:
                return True, f"连接成功（{latency * 1000:.0f} ms）模型响应：{reply[:40]}", latency
            return True, f"连接成功（{latency * 1000:.0f} ms）", latency
        except Exception as exc:
            latency = time.monotonic() - start
            return False, self._describe_llm_error(exc, latency), latency

    @staticmethod
    def _describe_llm_error(exc: Exception, latency: float) -> str:
        """把连接异常翻译成用户可读的诊断信息（区分 key 错误 / URL 错误 / 其他）。"""
        try:
            from openai import (
                APIConnectionError,
                APITimeoutError,
                AuthenticationError,
                BadRequestError,
                NotFoundError,
                PermissionDeniedError,
                RateLimitError,
            )
        except ImportError:
            message = (
                f"连接失败（{latency * 1000:.0f} ms）：{type(exc).__name__}: {exc}"
            )
            return message

        elapsed_ms = f"{latency * 1000:.0f} ms"
        if isinstance(exc, AuthenticationError):
            return (
                f"连接失败（HTTP 401，{elapsed_ms}）：API Key 无效或已过期，请检查密钥"
            )
        if isinstance(exc, PermissionDeniedError):
            return (
                f"连接失败（HTTP 403，{elapsed_ms}）：API Key 无权限访问该模型，"
                f"请检查密钥权限或模型名称"
            )
        if isinstance(exc, NotFoundError):
            return (
                f"连接失败（HTTP 404，{elapsed_ms}）：接口/模型不存在——大概率是 "
                f"Base URL 或模型名填错了"
            )
        if isinstance(exc, BadRequestError):
            return (
                f"连接失败（HTTP 400，{elapsed_ms}）：请求被拒绝——通常是模型名不识别"
                f"或参数不受支持（{exc}）"
            )
        if isinstance(exc, RateLimitError):
            return (
                f"连接失败（HTTP 429，{elapsed_ms}）：请求频率超限或余额不足，请稍后重试"
            )
        if isinstance(exc, APITimeoutError):
            return (
                f"连接失败（{elapsed_ms}）：请求超时——服务无响应，或网络不通"
            )
        if isinstance(exc, APIConnectionError):
            hint = "Base URL 填错或网络无法访问该地址"
            if "resolve" in str(exc).lower() or "dns" in str(exc).lower():
                hint = "域名解析失败——Base URL 填错或网络不通"
            return (
                f"连接失败（{elapsed_ms}）：{hint}（{type(exc).__name__}: {exc}）"
            )
        detail = getattr(exc, "status_code", None)
        if detail:
            return f"连接失败（HTTP {detail}，{elapsed_ms}）：{exc}"
        return f"连接失败（{elapsed_ms}）：{type(exc).__name__}: {exc}"

    async def generate_summary(
        self,
        title: str,
        segments: Sequence[TranscriptSegment],
        metadata: dict[str, Any] | None = None,
        style: str = "detailed",
        reasoning_effort: str = "auto",
        progress_callback: ProgressCallback | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> str:
        if not segments:
            raise ValueError("Cannot summarize an empty transcript")
        if style not in {"detailed", "faithful", "concise"}:
            raise ValueError(f"Unsupported summary style: {style}")
        if reasoning_effort not in {"auto", "off", "high", "max"}:
            raise ValueError(f"Unsupported reasoning effort: {reasoning_effort}")
        if should_abort is not None and should_abort():
            raise asyncio.CancelledError("任务已取消")

        await self._report_progress(progress_callback, 2, "正在分析转录内容")
        chunks = chunk_segments(segments, max_characters=SUMMARY_CHUNK_CHARACTERS)
        if len(chunks) == 1:
            source = segments_to_prompt(chunks[0])
        else:
            condensed_chunks: list[str] = []
            for index, chunk in enumerate(chunks, start=1):
                if should_abort is not None and should_abort():
                    raise asyncio.CancelledError("任务已取消")
                chunk_progress = 5 + int(52 * (index - 1) / len(chunks))
                chunk_stage = f"正在整理第 {index}/{len(chunks)} 个转录片段"
                await self._report_progress(
                    progress_callback,
                    chunk_progress,
                    chunk_stage,
                )
                condensed_chunks.append(
                    await self._complete(
                        self._chunk_prompt(title, index, len(chunks), chunk),
                        max_tokens=1_200,
                        effort=self._stage_effort(reasoning_effort, style, "notes"),
                        progress_callback=progress_callback,
                        progress=chunk_progress,
                        stage=chunk_stage,
                        should_abort=should_abort,
                    )
                )
            await self._report_progress(
                progress_callback, 60, f"已整理 {len(chunks)} 个转录片段"
            )
            condensed_chunks = await self._reduce_chunks(
                title,
                condensed_chunks,
                reasoning_effort,
                style,
                progress_callback,
                should_abort,
            )
            source = "\n\n".join(
                f"### 片段 {index}\n{note}"
                for index, note in enumerate(condensed_chunks, start=1)
            )

        max_tokens = {"detailed": 4_600, "faithful": 4_600, "concise": 2_400}[style]
        draft_progress = 78
        draft_stage = "正在生成完整笔记"
        await self._report_progress(progress_callback, draft_progress, draft_stage)
        draft = await self._complete(
            self._note_prompt(title, source, metadata or {}, style),
            max_tokens=max_tokens,
            effort=self._stage_effort(reasoning_effort, style, "notes"),
            progress_callback=progress_callback,
            progress=draft_progress,
            stage=draft_stage,
            should_abort=should_abort,
        )
        await self._report_progress(progress_callback, 91, "完整笔记初稿已生成")
        if style == "detailed":
            analysis_progress = 93
            analysis_stage = "正在补充点评与分析"
            await self._report_progress(progress_callback, analysis_progress, analysis_stage)
            analysis = await self._complete(
                self._analysis_prompt(title, draft),
                max_tokens=3_200,
                effort=self._stage_effort(reasoning_effort, style, "analysis"),
                progress_callback=progress_callback,
                progress=analysis_progress,
                stage=analysis_stage,
                should_abort=should_abort,
            )
            draft = f"{draft.rstrip()}\n\n{analysis.lstrip()}"
        await self._report_progress(progress_callback, 99, "正在保存笔记")
        return self._strip_code_fence(draft)

    async def _reduce_chunks(
        self,
        title: str,
        notes: list[str],
        reasoning_effort: str,
        style: str,
        progress_callback: ProgressCallback | None,
        should_abort: Callable[[], bool] | None = None,
    ) -> list[str]:
        level = 0
        while sum(len(note) for note in notes) > MERGE_INPUT_CHARACTERS:
            if should_abort is not None and should_abort():
                raise asyncio.CancelledError("任务已取消")
            groups = self._group_notes(notes, MERGE_INPUT_CHARACTERS)
            if len(groups) >= len(notes):
                break
            level += 1
            merged: list[str] = []
            for index, group in enumerate(groups, start=1):
                if should_abort is not None and should_abort():
                    raise asyncio.CancelledError("任务已取消")
                merge_progress = min(75, 61 + level * 4 + int(4 * index / len(groups)))
                merge_stage = f"正在归并第 {level} 层内容 {index}/{len(groups)} 组"
                await self._report_progress(
                    progress_callback,
                    merge_progress,
                    merge_stage,
                )
                merged.append(
                    await self._complete(
                        self._merge_prompt(title, level, index, len(groups), group),
                        max_tokens=1_400,
                        effort=self._stage_effort(reasoning_effort, style, "notes"),
                        progress_callback=progress_callback,
                        progress=merge_progress,
                        stage=merge_stage,
                        should_abort=should_abort,
                    )
                )
            notes = merged
        return notes

    @staticmethod
    def _group_notes(notes: Sequence[str], max_characters: int) -> list[list[str]]:
        groups: list[list[str]] = []
        current: list[str] = []
        size = 0
        for note in notes:
            if current and size + len(note) > max_characters:
                groups.append(current)
                current = []
                size = 0
            current.append(note)
            size += len(note)
        if current:
            groups.append(current)
        return groups

    @staticmethod
    async def _report_progress(
        callback: ProgressCallback | None, progress: int, message: str
    ) -> None:
        if callback is None:
            return
        result = callback(progress, message)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _stage_effort(selected: str, style: str, stage: str) -> str:
        # auto means provider/model default and must not inject private parameters.
        return selected if selected in {"off", "high", "max"} else "auto"

    async def _complete(
        self,
        prompt: str,
        max_tokens: int,
        effort: str = "auto",
        retry_empty: bool = True,
        progress_callback: ProgressCallback | None = None,
        progress: int = 0,
        stage: str = "模型调用",
        should_abort: Callable[[], bool] | None = None,
    ) -> str:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是视频笔记编辑。忠实理解语境与作者意图，允许结合上下文"
                        "保守修正明显的口误、笔误和语音转写错误。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        if self.model_type == "openai" and self.model.startswith("gpt-5"):
            request["max_completion_tokens"] = max_tokens
        else:
            request["max_tokens"] = (
                max(max_tokens * 3, DEEPSEEK_HIGH_TOKEN_BUDGET)
                if self._uses_deepseek_compatibility() and effort == "max"
                else DEEPSEEK_HIGH_TOKEN_BUDGET
                if self._uses_deepseek_compatibility() and effort != "off"
                else max_tokens
            )
        self._apply_reasoning(request, effort)

        if should_abort is not None and should_abort():
            raise asyncio.CancelledError("任务已取消")

        # 流式读取：既能在模型开始返回后逐块更新，也能在取消时立即中止本次请求
        stream = await self.client.chat.completions.create(**request, stream=True)
        parts: list[str] = []
        finish_reason = "unknown"
        try:
            async for chunk in stream:
                if should_abort is not None and should_abort():
                    raise asyncio.CancelledError("任务已取消")
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    parts.append(content)
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                closed = close()
                if inspect.isawaitable(closed):
                    await closed

        content = "".join(parts)
        if not content:
            if retry_empty and effort != "off" and self._can_control_reasoning():
                warning = f"{stage}：模型首次未返回正文，已关闭深度思考并重试"
                self.warnings.append(warning)
                await self._report_progress(progress_callback, progress, warning)
                return await self._complete(
                    prompt,
                    max_tokens,
                    "off",
                    retry_empty=False,
                    progress_callback=progress_callback,
                    progress=progress,
                    stage=stage,
                    should_abort=should_abort,
                )
            raise RuntimeError(
                f"模型未返回正文（finish_reason={finish_reason}）。"
                "可尝试关闭深度思考、缩短转录或更换模型。"
            )
        return content.strip()

    def _apply_reasoning(self, request: dict[str, Any], effort: str) -> None:
        if effort == "auto" and not self._uses_deepseek_compatibility():
            return
        enabled = effort != "off"
        if self._uses_deepseek_compatibility():
            request["extra_body"] = {"thinking": {"type": "enabled" if enabled else "disabled"}}
            if enabled:
                request["reasoning_effort"] = "max" if effort == "max" else "high"
        elif self.model_type == "glm":
            request["extra_body"] = {"thinking": {"type": "enabled" if enabled else "disabled"}}
        elif self.model_type == "qwen":
            request["extra_body"] = {"enable_thinking": enabled}
        elif self.model_type == "openai":
            request["reasoning_effort"] = {
                "off": "none",
                "max": "xhigh",
            }.get(effort, effort)
        elif enabled:
            self.warnings.append(
                f"{self.model_type} 未配置通用推理强度映射，已使用模型默认设置"
            )

    def _uses_deepseek_compatibility(self) -> bool:
        provider = str(getattr(self, "model_type", "")).lower()
        model = str(getattr(self, "model", "")).lower()
        base_url = str(getattr(self, "base_url", "")).lower()
        return provider == "deepseek" or "deepseek" in model or "deepseek" in base_url

    def _can_control_reasoning(self) -> bool:
        return self._uses_deepseek_compatibility() or self.model_type in {
            "openai", "glm", "qwen"
        }

    @staticmethod
    def _chunk_prompt(
        title: str,
        index: int,
        total: int,
        segments: Sequence[TranscriptSegment],
    ) -> str:
        return f"""视频标题：{title}
这是第 {index}/{total} 个连续片段。

请忠实压缩这个片段，供后续撰写完整笔记使用。保留重要事实、例子、论证关系和原有时间点；
结合上下文理解表达意图，不做评价，不补充外部知识。

转录：
{segments_to_prompt(segments)}"""

    @staticmethod
    def _merge_prompt(
        title: str,
        level: int,
        index: int,
        total: int,
        notes: Sequence[str],
    ) -> str:
        material = "\n\n".join(notes)
        return f"""视频标题：{title}
这是长视频内容的第 {level} 层归并，第 {index}/{total} 组。

请合并以下连续片段笔记，去除重复但保留观点、依据、例子、时间点和前后关系。
不要评价，不补充外部知识，只输出供最终成稿使用的连续材料。

片段笔记：
{material}"""

    @staticmethod
    def _note_prompt(
        title: str,
        source: str,
        metadata: dict[str, Any],
        style: str = "detailed",
    ) -> str:
        owner = metadata.get("owner") or "未知"
        published_at = metadata.get("published_at") or "未知"
        duration = metadata.get("duration_text") or "未知"
        view_count = metadata.get("view_count") or 0
        like_count = metadata.get("like_count") or 0
        source_label = metadata.get("transcript_source") or "语音转写"
        if style == "concise":
            task = (
                "写一份精炼的 Markdown 摘要，说明视频谈了什么、作者的主要观点，"
                "并列出少量最有用的时间点。"
            )
            timestamp_hint = ""
        else:
            task = (
                "写一份翔实、自然的 Markdown 视频笔记。完整复原内容脉络、具体例子、"
                "核心观点及其依据，并在有帮助时加入关键时间点。不要加入外部知识或评价。"
            )
            timestamp_hint = (
                "时间点写在段落开头，写成 [MM:SS] 或 [起点-终点]；"
                "仅在有助于定位时使用，不必每段都加。"
            )
        metadata_hint = (
            "如果有助于读者快速了解视频，可在标题下自然带出材料中明确的作者、发布时间、"
            "时长或播放信息；缺失的信息省略，不要猜测，也不必为了满足格式强行补齐。"
            "应用会在标题下补充一行可验证的元信息，正文无需重复同一组信息。"
        )
        return f"""{task}

视频标题：{title}
作者：{owner}
发布时间：{published_at}
时长：{duration}
播放：{view_count}
点赞：{like_count}
文字来源：{source_label}

以准确理解语境和作者立场为先。{metadata_hint}时间点只能取自材料。{timestamp_hint}可在上下文支持时直接修正明显的口误、
笔误或转写错误；只有歧义会影响结论且无法可靠判断时，才在正文采用最可能的解释，并在文末用
Markdown 脚注集中说明。不要在正文反复插入“原文如此”或“疑为转写错误”。
结构按内容自然组织，不必凑固定模板。标题使用：# 视频笔记：《{title}》

材料：
{source}"""

    @staticmethod
    def _analysis_prompt(title: str, draft: str) -> str:
        return f"""请针对这份视频笔记所对应的视频内容本身，补写一段独立的点评与分析，只输出可直接追加到笔记后的 Markdown。

点评对象是视频里的内容，而不是笔记的写法：作者的核心观点与立场是否成立、论据与例证是否充分、论证逻辑是否严密、结论是否可靠，以及内容的信息价值、适用场景和值得进一步思考的问题。可以补充有助于理解的稳定背景知识，但必须标明“模型补充，未联网核验”，不得把它写成视频原话或已核实事实。语气中性自然，服务于理解内容，避免复述已有笔记；不要评价笔记的组织结构、文笔或排版。

标题：{title}

已有笔记：
{draft}"""

    @staticmethod
    def _strip_code_fence(value: str) -> str:
        if value.startswith("```markdown") and value.endswith("```"):
            return value[len("```markdown") : -3].strip()
        if value.startswith("```") and value.endswith("```"):
            return value[3:-3].strip()
        return value
