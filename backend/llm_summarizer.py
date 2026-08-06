from __future__ import annotations

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

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=120)
        self.model_type = model_type
        self.model = model
        self.warnings: list[str] = []

    async def generate_summary(
        self,
        title: str,
        segments: Sequence[TranscriptSegment],
        metadata: dict[str, Any] | None = None,
        style: str = "detailed",
        reasoning_effort: str = "auto",
    ) -> str:
        if not segments:
            raise ValueError("Cannot summarize an empty transcript")
        if style not in {"detailed", "faithful", "concise"}:
            raise ValueError(f"Unsupported summary style: {style}")
        if reasoning_effort not in {"auto", "off", "low", "medium", "high", "max"}:
            raise ValueError(f"Unsupported reasoning effort: {reasoning_effort}")

        chunks = chunk_segments(segments)
        if len(chunks) == 1:
            source = segments_to_prompt(chunks[0])
        else:
            condensed_chunks = []
            for index, chunk in enumerate(chunks, start=1):
                condensed_chunks.append(
                    await self._complete(
                        self._chunk_prompt(title, index, len(chunks), chunk),
                        max_tokens=2_200,
                        effort=self._stage_effort(reasoning_effort, style, "notes"),
                    )
                )
            source = "\n\n".join(
                f"### 片段 {index}\n{note}"
                for index, note in enumerate(condensed_chunks, start=1)
            )

        max_tokens = {"detailed": 4_600, "faithful": 4_600, "concise": 2_400}[style]
        draft = await self._complete(
            self._note_prompt(title, source, metadata or {}, style),
            max_tokens=max_tokens,
            effort=self._stage_effort(reasoning_effort, style, "notes"),
        )
        if style == "detailed":
            analysis = await self._complete(
                self._analysis_prompt(title, draft),
                max_tokens=3_200,
                effort=self._stage_effort(reasoning_effort, style, "analysis"),
            )
            draft = f"{draft.rstrip()}\n\n{analysis.lstrip()}"
        return self._strip_code_fence(draft)

    @staticmethod
    def _stage_effort(selected: str, style: str, stage: str) -> str:
        if selected != "auto":
            return selected
        if style == "concise":
            return "off"
        return "max" if stage == "analysis" else "high"

    async def _complete(
        self,
        prompt: str,
        max_tokens: int,
        effort: str = "auto",
        retry_empty: bool = True,
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
            request.update(temperature=0.2, max_tokens=max_tokens)
        self._apply_reasoning(request, effort)

        response = await self.client.chat.completions.create(**request)
        content = response.choices[0].message.content if response.choices else None
        if not content:
            if retry_empty and effort != "off":
                self.warnings.append("模型未返回正文，已关闭深度思考自动重试一次")
                return await self._complete(prompt, max_tokens, "off", retry_empty=False)
            finish_reason = response.choices[0].finish_reason if response.choices else "unknown"
            raise RuntimeError(
                f"模型未返回正文（finish_reason={finish_reason}）。"
                "可尝试关闭深度思考、缩短转录或更换模型。"
            )
        return content.strip()

    def _apply_reasoning(self, request: dict[str, Any], effort: str) -> None:
        if effort == "auto":
            return
        enabled = effort != "off"
        if self.model_type == "deepseek":
            request.pop("temperature", None)
            request["extra_body"] = {"thinking": {"type": "enabled" if enabled else "disabled"}}
            if enabled:
                request["reasoning_effort"] = "max" if effort == "max" else "high"
        elif self.model_type == "glm":
            request["extra_body"] = {"thinking": {"type": "enabled" if enabled else "disabled"}}
        elif self.model_type == "qwen":
            if enabled:
                request["reasoning_effort"] = effort
            else:
                request["extra_body"] = {"enable_thinking": False}
        elif self.model_type == "openai":
            request["reasoning_effort"] = {
                "off": "none",
                "max": "xhigh",
            }.get(effort, effort)
        elif self.model_type == "custom":
            request["reasoning_effort"] = "none" if effort == "off" else effort
        elif enabled:
            self.warnings.append(
                f"{self.model_type} 未配置通用推理强度映射，已使用模型默认设置"
            )

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
    def _note_prompt(
        title: str,
        source: str,
        metadata: dict[str, Any],
        style: str = "detailed",
    ) -> str:
        owner = metadata.get("owner") or "未知"
        duration = metadata.get("duration_text") or "未知"
        source_label = metadata.get("transcript_source") or "语音转写"
        if style == "concise":
            task = (
                "写一份精炼的 Markdown 摘要，说明视频谈了什么、作者的主要观点，"
                "并列出少量最有用的时间点。"
            )
        else:
            task = (
                "写一份翔实、自然的 Markdown 视频笔记。完整复原内容脉络、具体例子、"
                "核心观点及其依据，并在有帮助时加入关键时间点。不要加入外部知识或评价。"
            )
        return f"""{task}

视频标题：{title}
作者：{owner}
时长：{duration}
文字来源：{source_label}

以准确理解语境和作者立场为先。时间点只能取自材料。可在上下文支持时直接修正明显的口误、
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
