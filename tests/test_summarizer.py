from types import SimpleNamespace

import pytest

from backend.llm_summarizer import (
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
    LLMSummarizer,
    PROVIDER_DEFAULTS,
)
from backend.transcript import TranscriptSegment


def test_note_prompt_forbids_invented_timestamps() -> None:
    prompt = LLMSummarizer._note_prompt(
        "测试视频",
        "[00:10-00:20] 原文",
        {"transcript_source": "platform_subtitle"},
    )
    assert "时间点只能取自材料" in prompt
    assert "[00:10-00:20]" in prompt


def test_chunk_prompt_contains_segment_timestamps() -> None:
    prompt = LLMSummarizer._chunk_prompt(
        "测试视频", 1, 2, [TranscriptSegment(30, 45, "关键论点")]
    )
    assert "[00:30-00:45] 关键论点" in prompt


def test_provider_defaults_use_current_model_families() -> None:
    assert PROVIDER_DEFAULTS["deepseek"][1] == "deepseek-v4-flash"
    assert PROVIDER_DEFAULTS["openai"][1] == "gpt-5.6-terra"
    assert PROVIDER_DEFAULTS["glm"][1] == "glm-4.5-flash"
    assert PROVIDER_DEFAULTS["moonshot"][1] == "kimi-k3"


def test_llm_client_uses_bounded_timeout_without_hidden_retries() -> None:
    assert LLM_TIMEOUT_SECONDS == 300
    assert LLM_MAX_RETRIES == 0


def test_note_and_analysis_prompts_are_focused() -> None:
    prompt = LLMSummarizer._note_prompt(
        "测试反讽视频",
        "[00:10-00:20] 字面上表示工作十分轻松",
        {},
        "detailed",
    )
    analysis = LLMSummarizer._analysis_prompt("测试反讽视频", "初稿")
    assert "Markdown 脚注" in prompt
    assert "原文如此" in prompt
    assert "不必凑固定模板" in prompt
    assert "模型补充，未联网核验" in analysis
    assert "点评对象是视频里的内容" in analysis
    assert "不要评价笔记的组织结构" in analysis
    assert "语气中性自然" in analysis


def test_reasoning_effort_maps_by_provider() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-v4-flash"
    summarizer.base_url = "https://api.deepseek.com"
    deepseek_request = {}
    summarizer._apply_reasoning(deepseek_request, "max")
    assert deepseek_request["reasoning_effort"] == "max"
    assert deepseek_request["extra_body"]["thinking"]["type"] == "enabled"
    default_request = {}
    summarizer._apply_reasoning(default_request, "auto")
    assert default_request["reasoning_effort"] == "high"
    assert default_request["extra_body"]["thinking"]["type"] == "enabled"

    summarizer.model_type = "openai"
    summarizer.model = "gpt-5.6-terra"
    summarizer.base_url = "https://api.openai.com/v1"
    openai_request = {}
    summarizer._apply_reasoning(openai_request, "max")
    assert openai_request["reasoning_effort"] == "xhigh"

    summarizer.model_type = "glm"
    glm_request = {}
    summarizer._apply_reasoning(glm_request, "off")
    assert glm_request["extra_body"]["thinking"]["type"] == "disabled"


@pytest.mark.asyncio
async def test_short_transcript_uses_one_call_or_two_focused_calls() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    calls: list[str] = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        calls.append(effort)
        return "## 点评与分析" if "点评与分析" in prompt else "# 视频笔记"

    summarizer._complete = complete
    segments = [TranscriptSegment(0, 10, "一段简短转录")]

    faithful = await summarizer.generate_summary("标题", segments, style="faithful")
    assert faithful == "# 视频笔记"
    assert calls == ["auto"]

    calls.clear()
    detailed = await summarizer.generate_summary("标题", segments, style="detailed")
    assert detailed == "# 视频笔记\n\n## 点评与分析"
    assert calls == ["auto", "auto"]


@pytest.mark.asyncio
async def test_long_transcript_uses_hierarchical_reduction_and_reports_progress() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    calls: list[str] = []
    progress: list[int] = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        calls.append(prompt)
        if "这是第" in prompt and "个连续片段" in prompt:
            return "片段材料" * 1_500
        if "这是长视频内容的第" in prompt:
            return "归并材料" * 150
        return "# 长视频笔记"

    def report(value, message):
        progress.append(value)

    summarizer._complete = complete
    segments = [
        TranscriptSegment(index, index + 1, f"第{index}段" + "内容" * 4_500)
        for index in range(4)
    ]

    result = await summarizer.generate_summary(
        "长视频", segments, style="faithful", progress_callback=report
    )

    assert result == "# 长视频笔记"
    assert sum("归并" in prompt for prompt in calls) >= 2
    assert progress == sorted(progress)
    assert progress[-1] == 99


@pytest.mark.asyncio
async def test_gpt5_chat_completion_uses_supported_token_parameter() -> None:
    captured: dict = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="摘要"))]
        )

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "openai"
    summarizer.model = "gpt-5.6-terra"
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await summarizer._complete("测试", 800) == "摘要"
    assert captured["max_completion_tokens"] == 800
    assert "max_tokens" not in captured
    assert "temperature" not in captured


@pytest.mark.asyncio
async def test_custom_deepseek_uses_explicit_high_thinking_with_safe_output_budget() -> None:
    captured: dict = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="摘要"))]
        )

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = "deepseek-ai/DeepSeek-R1"
    summarizer.base_url = "https://gateway.example/v1"
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await summarizer._complete("测试", 800, "auto") == "摘要"
    assert captured["max_tokens"] == 12_000
    assert "temperature" not in captured
    assert captured["reasoning_effort"] == "high"
    assert captured["extra_body"]["thinking"]["type"] == "enabled"

    captured.clear()
    assert await summarizer._complete("测试", 4_600, "auto") == "摘要"
    assert captured["max_tokens"] == 12_000


def test_auto_keeps_model_default_across_generation_stages() -> None:
    assert LLMSummarizer._stage_effort("auto", "concise", "notes") == "auto"
    assert LLMSummarizer._stage_effort("auto", "detailed", "analysis") == "auto"


def test_unknown_custom_provider_does_not_receive_private_reasoning_fields() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = "provider-model-alias"
    summarizer.base_url = "https://gateway.example/v1"
    summarizer.warnings = []
    request = {}

    summarizer._apply_reasoning(request, "max")

    assert request == {}
    assert summarizer.warnings == [
        "custom 未配置通用推理强度映射，已使用模型默认设置"
    ]


@pytest.mark.asyncio
async def test_empty_thinking_response_retries_with_thinking_disabled() -> None:
    requests: list[dict] = []
    progress: list[tuple[int, str]] = []

    async def create(**kwargs):
        requests.append(kwargs)
        content = None if len(requests) == 1 else "重试成功"
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content), finish_reason="length"
                )
            ]
        )

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "glm"
    summarizer.model = "glm-4.5-flash"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    def report(value: int, message: str) -> None:
        progress.append((value, message))

    assert (
        await summarizer._complete(
            "测试",
            800,
            "high",
            progress_callback=report,
            progress=93,
            stage="正在补充点评与分析",
        )
        == "重试成功"
    )
    assert requests[0]["extra_body"]["thinking"]["type"] == "enabled"
    assert requests[1]["extra_body"]["thinking"]["type"] == "disabled"
    assert progress == [
        (93, "正在补充点评与分析：模型首次未返回正文，已关闭深度思考并重试")
    ]
    assert summarizer.warnings == [
        "正在补充点评与分析：模型首次未返回正文，已关闭深度思考并重试"
    ]
