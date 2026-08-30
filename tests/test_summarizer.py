from types import SimpleNamespace

import pytest

from backend.llm_summarizer import (
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
    NOTE_TAIL_CHARACTERS,
    TAIL_PATCH_MAX_GAP_SECONDS,
    TAIL_PATCH_MAX_INPUT_CHARACTERS,
    LLMSummarizer,
    PROVIDER_DEFAULTS,
)
from backend.transcript import TranscriptSegment, segments_to_prompt


def _stream_response(content: str | None, finish_reason: str = "stop"):
    """构造一个假的大模型流式响应（_complete 现按流式逐块读取）。"""
    async def gen():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=content),
                    finish_reason=finish_reason,
                )
            ]
        )

    return gen()


def test_note_prompt_forbids_invented_timestamps() -> None:
    prompt = LLMSummarizer._note_prompt(
        "测试视频",
        "[00:10-00:20] 原文",
        {"transcript_source": "platform_subtitle"},
    )
    assert "时间点只能取自材料" in prompt
    assert "[00:10-00:20]" in prompt


def test_note_prompt_allows_verified_metadata_without_forcing_a_template() -> None:
    prompt = LLMSummarizer._note_prompt(
        "测试视频",
        "[00:10-00:20] 原文",
        {
            "owner": "测试作者",
            "published_at": "2026-08-21",
            "duration_text": "00:30",
            "view_count": 123,
            "like_count": 4,
        },
        "faithful",
    )
    assert "发布时间：2026-08-21" in prompt
    assert "标题下自然带出材料中明确的作者、发布时间" in prompt
    assert "缺失的信息省略" in prompt


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


def test_note_prompt_requires_coverage_to_transcript_end() -> None:
    prompt = LLMSummarizer._note_prompt(
        "测试视频",
        "[09:40-09:51] 结尾内容",
        {},
        "faithful",
        "09:51",
    )
    assert "材料内容一直推进到时间点 09:51" in prompt
    assert "必须覆盖到该时间点" in prompt
    concise = LLMSummarizer._note_prompt(
        "测试视频", "[00:10] 内容", {}, "concise", "09:51"
    )
    assert "必须覆盖到该时间点" not in concise


def test_chunk_prompt_states_coverage_range() -> None:
    prompt = LLMSummarizer._chunk_prompt(
        "测试视频",
        2,
        3,
        [TranscriptSegment(540, 550, "前"), TranscriptSegment(580, 591, "后")],
    )
    assert "本片段覆盖时间轴 09:00 – 09:51" in prompt
    assert "不要丢掉结尾内容" in prompt


def test_tail_patch_prompt_contains_missing_material() -> None:
    prompt = LLMSummarizer._tail_patch_prompt(
        "测试视频",
        "# 视频笔记\n\n[00:01-07:23] 前半部分要点",
        [TranscriptSegment(443, 591, "最后一题的讲解")],
        "07:23",
        "09:51",
    )
    assert "07:23 – 09:51" in prompt
    assert "最后一题的讲解" in prompt
    assert "只输出补写的 Markdown 正文" in prompt


@pytest.mark.asyncio
async def test_faithful_missing_tail_gets_patched() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    patch_prompts: list[str] = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        patch_prompts.append(prompt)
        return "补写的结尾内容"

    summarizer._complete = complete
    segments = [
        TranscriptSegment(0, 300, "前半部分"),
        TranscriptSegment(300, 591, "结尾的题目讲解"),
    ]
    draft = "# 视频笔记\n\n[00:01-04:00] 前半部分要点"

    result = await summarizer._ensure_tail_coverage(
        "标题", draft, segments, "faithful", "max", None
    )

    assert "### 补遗（05:00 之后）" in result
    assert "补写的结尾内容" in result
    assert result.startswith(draft.rstrip())
    assert len(patch_prompts) == 1
    assert "结尾的题目讲解" in patch_prompts[0]
    assert any("未覆盖转写结尾" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_detailed_missing_tail_only_warns_without_extra_calls() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    calls = 0

    async def complete(prompt, **kwargs):
        nonlocal calls
        calls += 1
        return "不应被调用"

    summarizer._complete = complete
    segments = [
        TranscriptSegment(0, 300, "前半部分"),
        TranscriptSegment(300, 591, "结尾的题目讲解"),
    ]
    draft = "# 视频笔记\n\n[00:01-04:00] 前半部分要点"

    result = await summarizer._ensure_tail_coverage(
        "标题", draft, segments, "detailed", "max", None
    )

    assert result == draft
    assert calls == 0
    assert any("建议改用“详细复原”风格" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_tail_check_skipped_for_concise_and_full_coverage() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    async def complete(prompt, **kwargs):
        raise AssertionError("concise 与覆盖完整时不应触发补写调用")

    summarizer._complete = complete
    segments = [
        TranscriptSegment(0, 300, "前半部分"),
        TranscriptSegment(300, 591, "结尾的题目讲解"),
    ]
    draft = "# 视频笔记\n\n[00:01-04:00] 前半部分要点"

    concise = await summarizer._ensure_tail_coverage(
        "标题", draft, segments, "concise", "high", None
    )
    assert concise == draft

    complete_draft = "# 视频笔记\n\n[00:01-04:00] 要点\n\n[09:50-09:51] 收尾"
    covered = await summarizer._ensure_tail_coverage(
        "标题", complete_draft, segments, "faithful", "max", None
    )
    assert covered == complete_draft
    assert summarizer.warnings == []


@pytest.mark.asyncio
async def test_large_gap_skips_faithful_patch_call() -> None:
    """笔记省略了时间戳（提示词允许这种写法）不应被当成丢尾而发出超大补写请求。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    async def complete(prompt, **kwargs):
        raise AssertionError("缺口过大时不得触发补写请求（会长时间无响应）")

    summarizer._complete = complete
    segments = [
        TranscriptSegment(index * 18.0, index * 18.0 + 18.0, f"第 {index} 句口播内容")
        for index in range(300)  # 约 90 分钟
    ]
    # 内容写到了最后，但时间戳只标到 12:00。
    draft = "# 视频笔记\n\n[00:01-12:00] 前段要点\n\n后面每段都只写了文字，没再带时间点"

    result = await summarizer._ensure_tail_coverage(
        "标题", draft, segments, "faithful", "max", None
    )

    assert result == draft
    assert segments[-1].end - 12 * 60 > TAIL_PATCH_MAX_GAP_SECONDS
    assert len(segments_to_prompt(segments)) > TAIL_PATCH_MAX_INPUT_CHARACTERS
    assert any("已跳过自动补写" in warning for warning in summarizer.warnings)
    assert not any("正在自动补写" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_faithful_patch_material_is_capped_to_the_tail() -> None:
    """缺口在补写范围内时，补写输入规模也要有上限，并保留最靠近结尾的材料。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    prompts: list[str] = []

    async def complete(prompt, **kwargs):
        prompts.append(prompt)
        return "补写的结尾"

    summarizer._complete = complete
    # 恰好 10 分钟缺口（不超过补写范围），但句子密集，整段材料超出输入预算。
    segments = [
        TranscriptSegment(
            1_800 + index * 20.0,
            1_820 + index * 20.0,
            f"第{index}段 " + "结尾处的详细讲解。" * 16,
        )
        for index in range(30)
    ]
    assert len(segments_to_prompt(segments)) > TAIL_PATCH_MAX_INPUT_CHARACTERS
    draft = "# 视频笔记\n\n[25:00-30:00] 前面已经写过的内容"

    result = await summarizer._ensure_tail_coverage(
        "标题", draft, segments, "faithful", "high", None
    )

    assert len(prompts) == 1
    assert "第0段" not in prompts[0]
    assert "第29段" in prompts[0]
    assert len(prompts[0]) < TAIL_PATCH_MAX_INPUT_CHARACTERS + NOTE_TAIL_CHARACTERS + 400
    assert "补遗" in result
    assert any("只补写最靠近结尾的一段" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_faithful_patch_timeout_keeps_original_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补写请求卡住时必须在上限内返回原笔记，不能停在“补写结尾”不放。"""
    import asyncio

    import backend.llm_summarizer as module

    monkeypatch.setattr(module, "TAIL_PATCH_TIMEOUT_SECONDS", 0.05)
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    async def complete(prompt, **kwargs):
        await asyncio.sleep(30)
        return "永远来不及返回的补写内容"

    summarizer._complete = complete
    segments = [
        TranscriptSegment(0, 300, "前半部分"),
        TranscriptSegment(300, 591, "结尾的题目讲解"),
    ]
    draft = "# 视频笔记\n\n[00:01-04:00] 前半部分要点"

    result = await asyncio.wait_for(
        summarizer._ensure_tail_coverage(
            "标题", draft, segments, "faithful", "max", None
        ),
        timeout=5,
    )

    assert result == draft
    assert any("已跳过并保留原笔记" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_faithful_patch_cancellation_still_propagates() -> None:
    import asyncio

    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    async def complete(prompt, **kwargs):
        raise asyncio.CancelledError("任务已取消")

    summarizer._complete = complete
    segments = [
        TranscriptSegment(0, 300, "前半部分"),
        TranscriptSegment(300, 591, "结尾的题目讲解"),
    ]

    with pytest.raises(asyncio.CancelledError):
        await summarizer._ensure_tail_coverage(
            "标题", "# 笔记\n\n[00:01-04:00] 要点", segments, "faithful", "max", None
        )


@pytest.mark.asyncio
async def test_complete_reports_stream_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    """深度思考阶段（只有 reasoning 增量）也要周期上报进度，否则与卡死无法区分。"""
    import backend.llm_summarizer as module

    monkeypatch.setattr(module, "LLM_HEARTBEAT_SECONDS", 0)

    async def create(**kwargs):
        async def gen():
            for index in range(3):
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None, reasoning_content="思考" * 10
                            ),
                            finish_reason=None,
                        )
                    ]
                )
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="正文"), finish_reason="stop"
                    )
                ]
            )

        return gen()

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "glm"
    summarizer.model = "glm-4.5-flash"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    progress: list[tuple[int, str]] = []

    def report(value: int, message: str) -> None:
        progress.append((value, message))

    assert (
        await summarizer._complete(
            "测试", 800, "high", progress_callback=report, progress=88, stage="补写结尾"
        )
        == "正文"
    )
    assert any("正在深度思考" in message for _value, message in progress)


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
        return _stream_response("摘要")

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
        return _stream_response("摘要")

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


def test_auto_resolves_style_default_only_on_deepseek_compatible_channels() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-v4-flash"
    summarizer.base_url = "https://api.deepseek.com"
    assert summarizer._stage_effort("auto", "detailed", "notes") == "max"
    assert summarizer._stage_effort("auto", "faithful", "notes") == "max"
    assert summarizer._stage_effort("auto", "concise", "notes") == "high"
    # 用户显式选择永远优先
    assert summarizer._stage_effort("off", "detailed", "notes") == "off"
    assert summarizer._stage_effort("high", "detailed", "analysis") == "high"

    # custom 通道识别出 DeepSeek 模型时同样吃到风格默认
    summarizer.model_type = "custom"
    summarizer.model = "deepseek-ai/DeepSeek-V4-Flash-0731"
    summarizer.base_url = "https://api-inference.modelscope.cn/v1/"
    assert summarizer._stage_effort("auto", "detailed", "notes") == "max"

    # 非 DeepSeek 通道保持模型默认，不注入私有参数
    summarizer.model_type = "custom"
    summarizer.model = "provider-model-alias"
    summarizer.base_url = "https://gateway.example/v1"
    assert summarizer._stage_effort("auto", "detailed", "analysis") == "auto"


def test_describe_effort_reports_effective_level() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-v4-flash"
    summarizer.base_url = "https://api.deepseek.com"
    assert summarizer.describe_effort("max", "detailed") == "max"
    assert (
        summarizer.describe_effort("auto", "detailed")
        == "auto（DeepSeek 通道按风格默认：max）"
    )

    summarizer.model_type = "openai"
    summarizer.model = "gpt-5.6-terra"
    summarizer.base_url = "https://api.openai.com/v1"
    assert summarizer.describe_effort("auto", "detailed") == "auto（使用模型默认）"


def test_max_note_timestamp_parses_common_formats() -> None:
    parse = LLMSummarizer._max_note_timestamp
    assert parse("无时间戳的笔记") is None
    assert parse("[07:23] 内容") == 443.0
    assert parse("开头 [00:10-00:20]，收尾 [09:45-09:51]") == 591.0
    assert parse("[1:02:03] 长视频") == 3723.0
    # 长视频常用的累计分钟写法：识别不到会被当成丢尾，触发多余的补写
    assert parse("[99:30] 写法") == 5970.0
    assert parse("[105:30-106:02] 收尾") == 6362.0
    assert parse("发布于 [2024:01] 不是时间戳") is None


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
        if len(requests) == 1:
            return _stream_response(None, "length")
        return _stream_response("重试成功")

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


@pytest.mark.asyncio
async def test_complete_aborts_before_call_when_cancelled() -> None:
    called = []

    async def create(**kwargs):
        called.append(kwargs)
        return _stream_response("摘要")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "glm"
    summarizer.model = "glm-4.5-flash"
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await summarizer._complete("测试", 800, "high", should_abort=lambda: True)
    assert called == []


@pytest.mark.asyncio
async def test_complete_aborts_mid_stream_when_cancelled() -> None:
    import asyncio

    async def create(**kwargs):
        async def gen():
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="前"), finish_reason=None)]
            )
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="后"), finish_reason="stop")]
            )

        return gen()

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "glm"
    summarizer.model = "glm-4.5-flash"
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    # 第一个块后置位 should_abort：第二个块读取前应中断
    state = {"called": 0}

    def should_abort():
        state["called"] += 1
        return state["called"] >= 2

    with pytest.raises(asyncio.CancelledError):
        await summarizer._complete("测试", 800, "high", should_abort=should_abort)


@pytest.mark.asyncio
async def test_generate_summary_aborts_immediately_when_cancelled() -> None:
    import asyncio

    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    segments = [TranscriptSegment(0, 5, "一段内容")]

    with pytest.raises(asyncio.CancelledError):
        await summarizer.generate_summary(
            "标题", segments, should_abort=lambda: True
        )
