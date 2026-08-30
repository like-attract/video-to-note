from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Sequence

from .transcript import (
    TranscriptSegment,
    chunk_segments,
    format_timestamp,
    segments_to_prompt,
)


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
# auto 档在 DeepSeek 兼容通道按笔记风格解析出的默认推理档位。
# 档位不影响 token 单价（思考链按输出 token 计费），只影响思考量，
# 因此默认拉高换内容完整性；非 DeepSeek 通道保持模型默认不注入参数。
STYLE_DEFAULT_EFFORT = {"detailed": "max", "faithful": "max", "concise": "high"}
# 笔记末尾时间戳落后转写结尾超过该秒数视为丢尾。
TAIL_GAP_SECONDS = 60.0
# 补尾时附带给模型参考的已有笔记结尾长度。
NOTE_TAIL_CHARACTERS = 600
TAIL_PATCH_MAX_TOKENS = 1_600
# 补写结尾的三道护栏。成稿提示词明确允许“不必每段都加时间戳”，所以“笔记末尾时间戳
# 落后于转写结尾”既可能是真的丢尾，也可能只是省略了时间戳。不加护栏时，后者会把
# 整段剩余转录塞进一次 max 思考档的补写请求（几十分钟无任何进度反馈，表现为“卡住不动”，
# 而 detailed 只记告警不发请求，所以只有“详细复原”会卡）。
# 缺口超过该秒数视为“省略时间戳”而不是丢尾，只告警不补写。
TAIL_PATCH_MAX_GAP_SECONDS = 600.0
# 补写材料字符上限，超过时只保留最靠近结尾的部分。
TAIL_PATCH_MAX_INPUT_CHARACTERS = 4_000
# 补写请求的整体耗时上限：超时则保留原笔记，绝不让任务停在补写阶段不放。
TAIL_PATCH_TIMEOUT_SECONDS = 240.0
# 流式读取心跳间隔：把“模型仍在输出”反映到任务进度上，避免长时间同一句话看起来像死锁。
LLM_HEARTBEAT_SECONDS = 20.0
# 匹配笔记中的 [MM:SS]、[MM:SS-MM:SS]、[HH:MM:SS] 等时间戳（起点与区间终点都计入）。
# 分钟位允许 1~3 位：长视频模型常把 1 小时 45 分写成 [105:30]，识别不到会被误判成丢尾。
_NOTE_TIMESTAMP_RE = re.compile(
    r"\[(\d{1,3}:\d{2}(?::\d{2})?)(?:\s*[-–—~]\s*(\d{1,3}:\d{2}(?::\d{2})?))?\]"
)
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
        notes_effort = self._stage_effort(reasoning_effort, style, "notes")
        coverage_end = format_timestamp(segments[-1].end)
        draft_progress = 78
        draft_stage = "正在生成完整笔记"
        await self._report_progress(progress_callback, draft_progress, draft_stage)
        draft = await self._complete(
            self._note_prompt(title, source, metadata or {}, style, coverage_end),
            max_tokens=max_tokens,
            effort=notes_effort,
            progress_callback=progress_callback,
            progress=draft_progress,
            stage=draft_stage,
            should_abort=should_abort,
        )
        draft = await self._ensure_tail_coverage(
            title,
            draft,
            segments,
            style,
            notes_effort,
            progress_callback,
            should_abort,
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

    async def _ensure_tail_coverage(
        self,
        title: str,
        draft: str,
        segments: Sequence[TranscriptSegment],
        style: str,
        effort: str,
        progress_callback: ProgressCallback | None,
        should_abort: Callable[[], bool] | None = None,
    ) -> str:
        """核对笔记是否推进到转写结尾，尽力弥补生成时丢失的尾部内容。

        - concise 摘要允许只挑要点，不做核对；
        - faithful（详细复原）要求完整覆盖：确实丢尾时自动补写一次结尾；
        - detailed 的笔记允许按需省略时间戳，自动补写容易误伤，只记录告警。

        补写受三道护栏约束（缺口上限 / 材料字符上限 / 整体耗时上限）：成稿提示词允许
        “不必每段都加时间戳”，长视频里只标前半段时间戳很常见，此时“末尾时间戳落后”
        并不代表丢尾。没有护栏时会自动发一次覆盖剩余全部转录的 max 思考档请求，几十分钟
        不返回进度，表现为“详细复原卡在生成阶段不动，而详细+点评正常”（用户反馈）。
        """
        if style == "concise" or not segments:
            return draft
        coverage_end = segments[-1].end
        note_end = self._max_note_timestamp(draft)
        if note_end is None:
            self.warnings.append(
                "笔记中没有可识别的时间戳，无法自动核对是否覆盖到 "
                f"{format_timestamp(coverage_end)}"
            )
            return draft
        gap = coverage_end - note_end
        if gap <= TAIL_GAP_SECONDS:
            return draft
        missing = [segment for segment in segments if segment.start >= note_end - 5]
        if not missing:
            return draft
        message = (
            "检测到笔记内容停在 "
            f"{format_timestamp(note_end)}，未覆盖转写结尾 {format_timestamp(coverage_end)}"
        )
        if style == "detailed":
            self.warnings.append(
                message + "；也可能只是笔记省略了时间戳；建议改用“详细复原”风格或更高推理强度重新生成"
            )
            return draft
        # 护栏一：缺口过大时不是“丢尾”，补写等于把剩余转录重写一遍，只告警。
        if gap > TAIL_PATCH_MAX_GAP_SECONDS:
            self.warnings.append(
                message
                + f"；缺口约 {gap / 60:.0f} 分钟，更像笔记省略了时间戳而不是真的丢尾，"
                "已跳过自动补写（强行补写会把剩余转录重写一遍，长时间出不来结果）"
            )
            return draft
        # 护栏二：材料只保留预算内最靠近结尾的一段，保证补写请求规模有上限。
        material = self._tail_patch_material(missing)
        missing_start = format_timestamp(material[0].start)
        scope_note = "" if material is missing else "（缺口较大，只补写最靠近结尾的一段）"
        self.warnings.append(
            message + f"，正在自动补写 {missing_start} 之后的内容{scope_note}"
        )
        await self._report_progress(progress_callback, 88, "正在补写笔记缺失的结尾内容")
        try:
            # 护栏三：补写本身有整体耗时上限，超时保留原笔记，不停在补写阶段不放。
            patch = await asyncio.wait_for(
                self._complete(
                    self._tail_patch_prompt(
                        title, draft, material, missing_start, format_timestamp(coverage_end)
                    ),
                    max_tokens=TAIL_PATCH_MAX_TOKENS,
                    effort=effort,
                    progress_callback=progress_callback,
                    progress=88,
                    stage="补写结尾",
                    should_abort=should_abort,
                ),
                TAIL_PATCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self.warnings.append(
                f"补写结尾超过 {TAIL_PATCH_TIMEOUT_SECONDS:.0f} 秒仍未完成，已跳过并保留原笔记"
                "（模型响应过慢，可降低推理强度后重新生成）"
            )
            await self._report_progress(progress_callback, 91, "补写结尾超时，已保留原笔记")
            return draft
        return f"{draft.rstrip()}\n\n### 补遗（{missing_start} 之后）\n\n{patch.strip()}"

    @staticmethod
    def _tail_patch_material(
        segments: Sequence[TranscriptSegment],
    ) -> Sequence[TranscriptSegment]:
        """从缺口尾部往前取材料，保证补写请求的输入规模有上限。"""
        if len(segments) == 1:
            return segments
        kept: list[TranscriptSegment] = []
        used = 0
        for segment in reversed(segments):
            # 与 segments_to_prompt 一致：每行另有时间戳前缀与换行开销。
            line_characters = len(segment.text) + 33
            if kept and used + line_characters > TAIL_PATCH_MAX_INPUT_CHARACTERS:
                break
            kept.append(segment)
            used += line_characters
        kept.reverse()
        return kept if len(kept) < len(segments) else segments

    @staticmethod
    def _parse_clock(value: str) -> float:
        """把 [HH:MM:SS] / [MM:SS] 转成秒；两段且分钟超过 99 时按累计分钟处理。"""
        parts = value.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        return int(parts[0]) * 60 + float(parts[1])

    @staticmethod
    def _max_note_timestamp(text: str) -> float | None:
        seconds: list[float] = []
        for match in _NOTE_TIMESTAMP_RE.finditer(text):
            seconds.append(LLMSummarizer._parse_clock(match.group(1)))
            if match.group(2):
                seconds.append(LLMSummarizer._parse_clock(match.group(2)))
        return max(seconds) if seconds else None

    @staticmethod
    def _tail_patch_prompt(
        title: str,
        draft: str,
        missing_segments: Sequence[TranscriptSegment],
        missing_start: str,
        coverage_end: str,
    ) -> str:
        material = segments_to_prompt(missing_segments)
        note_tail = draft[-NOTE_TAIL_CHARACTERS:]
        return f"""视频《{title}》的笔记在 {missing_start} 之前已经写好，但遗漏了视频最后一段内容。

请根据下面的结尾部分转录补写这一部分的笔记：
- 与已有笔记保持一致的结构、语气和时间戳格式（[MM:SS] 或 [起点-终点]）
- 忠实转录内容，不补充外部知识，不做评价
- 只输出补写的 Markdown 正文，不要重复已有笔记，不要输出标题或解释

已有笔记的结尾（仅供衔接参考）：
{note_tail}

缺失部分的转录（{missing_start} – {coverage_end}）：
{material}"""

    @staticmethod
    async def _report_progress(
        callback: ProgressCallback | None, progress: int, message: str
    ) -> None:
        if callback is None:
            return
        result = callback(progress, message)
        if inspect.isawaitable(result):
            await result

    def _stage_effort(self, selected: str, style: str, stage: str) -> str:
        """解析单个生成阶段实际生效的推理强度。

        - 用户显式选择（off/high/max）永远优先；
        - auto 在 DeepSeek 兼容通道（含 custom 中识别出的 DeepSeek 模型）按
          笔记风格给默认档：detailed/faithful → max，concise → high；
        - 其余通道返回 auto，保持模型默认，不注入私有参数。
        stage 参数保留给将来按阶段（成稿/点评）差异化档位使用。
        """
        if selected in {"off", "high", "max"}:
            return selected
        if self._uses_deepseek_compatibility():
            return STYLE_DEFAULT_EFFORT.get(style, "high")
        return "auto"

    def describe_effort(self, reasoning_effort: str, style: str) -> str:
        """返回用于任务日志的推理设置说明（auto 解析成实际档位）。"""
        if reasoning_effort in {"off", "high", "max"}:
            return reasoning_effort
        if self._uses_deepseek_compatibility():
            resolved = STYLE_DEFAULT_EFFORT.get(style, "high")
            return f"auto（DeepSeek 通道按风格默认：{resolved}）"
        return "auto（使用模型默认）"

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
        content_characters = 0
        reasoning_characters = 0
        next_beat = time.monotonic() + LLM_HEARTBEAT_SECONDS
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
                    content_characters += len(content)
                elif delta is not None:
                    reasoning_characters += self._reasoning_characters(delta)
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                # 心跳：深度思考阶段正文前只有 reasoning 增量，若不回报进度，
                # 前端与“真卡死”无法区分（本次故障的表现就是“卡住不动”）。
                if progress_callback is not None and time.monotonic() >= next_beat:
                    next_beat = time.monotonic() + LLM_HEARTBEAT_SECONDS
                    await self._report_progress(
                        progress_callback,
                        progress,
                        self._stream_status_message(
                            stage, content_characters, reasoning_characters
                        ),
                    )
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

    @staticmethod
    def _reasoning_characters(delta: Any) -> int:
        """取一个流式块里 reasoning/思考链增量的长度（不同通道字段名不一致）。"""
        values = [getattr(delta, name, None) for name in ("reasoning_content", "reasoning")]
        extra = getattr(delta, "model_extra", None)
        if isinstance(extra, dict):
            values.extend(extra.get(name) for name in ("reasoning_content", "reasoning"))
        return max((len(value) for value in values if isinstance(value, str)), default=0)

    @staticmethod
    def _stream_status_message(
        stage: str, content_characters: int, reasoning_characters: int
    ) -> str:
        if content_characters:
            return f"{stage}：模型已输出 {content_characters} 字"
        if reasoning_characters:
            return f"{stage}：模型正在深度思考（已累计 {reasoning_characters} 字）"
        return f"{stage}：等待模型返回首个数据块"

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
        first_ts = format_timestamp(segments[0].start)
        last_ts = format_timestamp(segments[-1].end)
        return f"""视频标题：{title}
这是第 {index}/{total} 个连续片段。

请忠实压缩这个片段，供后续撰写完整笔记使用。保留重要事实、例子、论证关系和原有时间点；
本片段覆盖时间轴 {first_ts} – {last_ts}，区间末尾的话题同样重要，压缩时不要丢掉结尾内容；
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
        coverage_end: str | None = None,
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
            coverage_hint = ""
        else:
            task = (
                "写一份翔实、自然的 Markdown 视频笔记。完整复原内容脉络、具体例子、"
                "核心观点及其依据，并在有帮助时加入关键时间点。不要加入外部知识或评价。"
            )
            timestamp_hint = (
                "时间点写在段落开头，写成 [MM:SS] 或 [起点-终点]；"
                "仅在有助于定位时使用，不必每段都加；但结尾一小节必须带一个接近材料末尾的时间点，"
                "以便核对是否写到视频最后。"
            )
            coverage_hint = (
                f"材料内容一直推进到时间点 {coverage_end}；笔记必须覆盖到该时间点，"
                "收尾前先自查是否漏掉了材料末尾的话题。"
                if coverage_end
                else ""
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

以准确理解语境和作者立场为先。{metadata_hint}时间点只能取自材料。{coverage_hint}{timestamp_hint}可在上下文支持时直接修正明显的口误、
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
