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
    # 不读全局“被拒参数”缓存，只看映射规则本身
    summarizer._rejected_params = set()

    # 官方 DeepSeek：开思考只用标准参数，只有“关掉思考”才需要私有 thinking
    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-v4-flash"
    summarizer.base_url = "https://api.deepseek.com"
    deepseek_request = {}
    summarizer._apply_reasoning(deepseek_request, "max")
    assert deepseek_request["reasoning_effort"] == "max"
    assert "extra_body" not in deepseek_request
    off_request = {}
    summarizer._apply_reasoning(off_request, "off")
    assert off_request["extra_body"]["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in off_request
    # auto 不注入任何思考参数（风格默认在 _stage_effort 里已解析掉）
    default_request = {}
    summarizer._apply_reasoning(default_request, "auto")
    assert default_request == {}

    # 第三方网关托管的 deepseek 模型：只发标准参数，不发私有 thinking
    # （v1.2.3 用户反馈：custom 通道下发 thinking 会被直接 400 拒掉）
    summarizer.model_type = "custom"
    summarizer.model = "deepseek-ai/DeepSeek-V4-Flash"
    summarizer.base_url = "https://api-inference.modelscope.cn/v1/"
    gateway_request = {}
    summarizer._apply_reasoning(gateway_request, "max")
    assert gateway_request["reasoning_effort"] == "max"
    assert "extra_body" not in gateway_request
    gateway_off = {}
    summarizer._apply_reasoning(gateway_off, "off")
    assert gateway_off["reasoning_effort"] == "none"
    assert "extra_body" not in gateway_off
    # flat 写法被拒后改试嵌套 reasoning.effort（报错提示里并列给了这两种）
    summarizer._rejected_params = {"reasoning_effort"}
    nested = {}
    summarizer._apply_reasoning(nested, "high")
    assert nested == {"extra_body": {"reasoning": {"effort": "high"}}}
    # 两种写法都被拒后不再猜，只记一条告警
    summarizer._rejected_params = {"thinking", "reasoning_effort", "reasoning"}
    given_up = {}
    summarizer._apply_reasoning(given_up, "high")
    assert given_up == {}
    assert any("不支持思考强度参数" in warning for warning in summarizer.warnings)
    summarizer._rejected_params = set()

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
async def test_custom_deepseek_uses_standard_param_with_safe_output_budget() -> None:
    captured: dict = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return _stream_response("摘要")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = "deepseek-ai/DeepSeek-R1"
    summarizer.base_url = "https://gateway.example/v1"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await summarizer._complete("测试", 800, "high") == "摘要"
    assert captured["max_tokens"] == 12_000
    assert "temperature" not in captured
    # 非官方通道不得收到私有 thinking 字段
    assert captured["reasoning_effort"] == "high"
    assert "extra_body" not in captured

    captured.clear()
    assert await summarizer._complete("测试", 4_600, "max") == "摘要"
    assert captured["max_tokens"] == 13_800


def test_is_official_deepseek_only_matches_deepseek_endpoints() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-v4-flash"
    summarizer.base_url = "https://api.deepseek.com"
    assert summarizer._is_official_deepseek()

    summarizer.model_type = "custom"
    summarizer.base_url = "https://api.deepseek.com/v1"
    assert summarizer._is_official_deepseek()

    summarizer.base_url = "https://api-inference.modelscope.cn/v1/"
    assert not summarizer._is_official_deepseek()
    summarizer.base_url = "https://notdeepseek.example.com/v1"
    assert not summarizer._is_official_deepseek()


def test_env_declared_rejected_params_skip_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    """逃生口：用环境变量声明通道不支持的思考参数（对齐 pi/pivi 的 compat 能力声明）。"""
    monkeypatch.setenv("VIDEOTONOTES_REJECTED_LLM_PARAMS", "thinking, reasoning_effort, reasoning , bogus")
    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-v4-flash"
    summarizer.base_url = "https://api.deepseek.com"
    summarizer.warnings = []

    assert summarizer.rejected_params == {"thinking", "reasoning_effort", "reasoning"}
    for effort in ("max", "high"):
        request = {}
        summarizer._apply_reasoning(request, effort)
        assert request == {}
    off_request = {}
    summarizer._apply_reasoning(off_request, "off")
    assert off_request == {}
    assert any("不支持思考强度参数" in warning for warning in summarizer.warnings)
    assert summarizer._can_disable_thinking() is False


@pytest.mark.asyncio
async def test_rejected_thinking_param_retries_without_it_and_remembers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """官方通道也开始拒 thinking 时：去掉参数重试，不直接判任务失败。"""
    import backend.llm_summarizer as module

    monkeypatch.setattr(module, "_REJECTED_REASONING_PARAMS", {})
    attempts: list[dict] = []

    class RejectThinking(Exception):
        status_code = 400
        body = {"error": {"message": "\"thinking\" is not supported", "param": "thinking",
                          "code": "unsupported_parameter"}}

        def __str__(self):
            return (
                "Error code: 400 - {'error': {'message': '\"thinking\" is not supported "
                "on /v1/chat/completions', 'type': 'invalid_request_error', "
                "'param': 'thinking', 'code': 'unsupported_parameter'}}"
            )

    async def create(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise RejectThinking()
        return _stream_response("摘要")

    def build():
        summarizer = object.__new__(LLMSummarizer)
        summarizer.model_type = "deepseek"
        summarizer.model = "deepseek-v4-flash"
        summarizer.base_url = "https://api.deepseek.com"
        summarizer.warnings = []
        summarizer.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        return summarizer

    summarizer = build()
    progress: list[tuple[int, str]] = []

    async def report(value: int, message: str) -> None:
        progress.append((value, message))

    # 官方通道只有“关思考”会用到私有 thinking，就从这个入口验证降级
    assert (
        await summarizer._complete(
            "测试", 800, "off", progress_callback=report, progress=78, stage="成稿"
        )
        == "摘要"
    )
    assert attempts[0]["extra_body"]["thinking"]["type"] == "disabled"
    assert "extra_body" not in attempts[1]
    assert any("不支持 thinking 参数" in message for _value, message in progress)
    # 同配置的新实例不再下发被拒参数（不会每个阶段重吃一次 400）
    fresh = build()
    request = fresh._build_request("测试", 800, "off")
    assert "extra_body" not in request
    assert request["reasoning_effort"] == "none"
    # 还能用标准写法关思考，所以空正文重试仍可做
    assert fresh._can_disable_thinking()


@pytest.mark.asyncio
async def test_rejected_reasoning_effort_falls_back_to_model_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.llm_summarizer as module

    monkeypatch.setattr(module, "_REJECTED_REASONING_PARAMS", {})
    attempts: list[dict] = []

    class RejectEffort(Exception):
        status_code = 400
        body = {"error": {"message": "unsupported parameter", "param": "reasoning_effort"}}

        def __str__(self):
            return "Error code: 400 - invalid reasoning_effort: unsupported parameter"

    async def create(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise RejectEffort()
        return _stream_response("摘要")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = "deepseek-ai/DeepSeek-V4"
    summarizer.base_url = "https://gateway.example/v1"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await summarizer._complete("测试", 800, "high") == "摘要"
    assert len(attempts) == 2
    assert "reasoning_effort" not in attempts[1]
    # 下一级：嵌套 reasoning.effort（经 extra_body 合并进 body）
    assert attempts[1]["extra_body"]["reasoning"] == {"effort": "high"}
    # 后续阶段不再重复下发 flat 参数
    request = summarizer._build_request("测试", 800, "high")
    assert "reasoning_effort" not in request
    assert request["max_tokens"] == 12_000
    # 同一配置的新实例也从缓存里读到该结论
    fresh = object.__new__(LLMSummarizer)
    fresh.model_type = "custom"
    fresh.model = "deepseek-ai/DeepSeek-V4"
    fresh.base_url = "https://gateway.example/v1"
    fresh.warnings = []
    assert "reasoning_effort" not in fresh._build_request("测试", 800, "high")


@pytest.mark.asyncio
async def test_reasoning_effort_ladder_flat_then_nested_then_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flat reasoning_effort → 嵌套 reasoning.effort → 不注入，逐档降级。"""
    import backend.llm_summarizer as module

    monkeypatch.setattr(module, "_REJECTED_REASONING_PARAMS", {})
    attempts: list[dict] = []

    class RejectParam(Exception):
        status_code = 400

        def __init__(self, param: str):
            super().__init__(f'Error code: 400 - "{param}" is not supported')
            self.body = {"error": {"message": f"{param} unsupported", "param": param}}

    async def create(**kwargs):
        attempts.append(dict(kwargs))
        extra = kwargs.get("extra_body") or {}
        if "reasoning_effort" in kwargs:
            raise RejectParam("reasoning_effort")
        if isinstance(extra, dict) and "reasoning" in extra:
            raise RejectParam("reasoning")
        return _stream_response("摘要")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = "deepseek-ai/DeepSeek-V4"
    summarizer.base_url = "https://gateway.example/v1"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await summarizer._complete("测试", 800, "high") == "摘要"
    assert len(attempts) == 3
    assert attempts[0]["reasoning_effort"] == "high"
    assert attempts[1]["extra_body"]["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in attempts[2]
    assert "extra_body" not in attempts[2]
    assert summarizer.rejected_params == {"reasoning_effort", "reasoning"}
    assert any("不支持思考强度参数" in warning for warning in summarizer.warnings)
    assert not summarizer._can_disable_thinking()


@pytest.mark.asyncio
async def test_unsupported_max_tokens_degrades_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第三方网关的输出额度上限常低于我们给思考档抬高的预算。"""
    import backend.llm_summarizer as module

    monkeypatch.setattr(module, "_REJECTED_REASONING_PARAMS", {})
    attempts: list[dict] = []

    class RejectTokens(Exception):
        status_code = 400

        def __init__(self, message: str):
            super().__init__(message)
            self.body = {
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "param": "max_tokens",
                    "code": "unsupported_parameter",
                }
            }

    async def create(**kwargs):
        attempts.append(dict(kwargs))
        if kwargs.get("max_tokens", 0) > 8_000:
            raise RejectTokens(
                "Error code: 400 - 'max_tokens' is not supported with this model "
                "(limit 8192)"
            )
        return _stream_response("摘要")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = "DeepSeek-V4-Flash"
    summarizer.base_url = "https://developer.amd.com.cn/radeon/v1"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await summarizer._complete("测试", 4_600, "max") == "摘要"
    assert len(attempts) == 2
    assert attempts[0]["max_tokens"] == 13_800  # max 档抬高后的预算
    assert attempts[1]["max_tokens"] == 4_600  # 退回调用方额度
    # 思考参数不受影响，仍然带着
    assert attempts[1]["reasoning_effort"] == "max"
    assert any("不支持 max_tokens 参数" in warning for warning in summarizer.warnings)
    assert summarizer.rejected_params == set()  # 额度问题不该被记成“参数不支持”

    # 额度参数彻底不支持时，改为不下发
    attempts.clear()

    async def create_always_reject(**kwargs):
        attempts.append(dict(kwargs))
        if "max_tokens" in kwargs:
            raise RejectTokens("Error code: 400 - 'max_tokens' is not supported")
        return _stream_response("摘要")

    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_always_reject))
    )
    summarizer.warnings = []
    assert await summarizer._complete("测试", 4_600, "high") == "摘要"
    assert [a.get("max_tokens") for a in attempts] == [12_000, 4_600, None]
    assert any("已不再下发输出额度上限" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_other_400_errors_still_fail_the_call() -> None:

    async def create(**kwargs):
        raise RuntimeError("Error code: 400 - {'error': {'message': 'maximum context length exceeded'}}")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = "deepseek-ai/DeepSeek-V4"
    summarizer.base_url = "https://gateway.example/v1"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(RuntimeError, match="maximum context length"):
        await summarizer._complete("测试", 800, "high")


def test_rejected_request_params_only_reports_sent_params() -> None:
    """拒绝提示里会同时推荐另一个参数，不得把推荐项也当成被拒参数。"""

    class FakeError(Exception):
        status_code = 400
        body = {
            "error": {
                "message": (
                    '"thinking" is not supported on /v1/chat/completions and was '
                    'not applied. Use "reasoning_effort" (or "reasoning.effort") '
                    "to control thinking."
                ),
                "type": "invalid_request_error",
                "param": "thinking",
                "code": "unsupported_parameter",
            }
        }

        def __str__(self):
            return (
                "Error code: 400 - {'error': {'message': '\"thinking\" is not supported "
                "on /v1/chat/completions and was not applied. Use \"reasoning_effort\" "
                "(or \"reasoning.effort\") to control thinking.', "
                "'type': 'invalid_request_error', 'param': 'thinking', "
                "'code': 'unsupported_parameter'}}"
            )

    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    request = {"model": "m", "reasoning_effort": "max", "extra_body": {"thinking": {"type": "enabled"}}}
    assert summarizer._rejected_request_params(FakeError(), request) == ("thinking",)

    # 本次请求没下发 thinking 时不应误判可重试
    assert summarizer._rejected_request_params(FakeError(), {"model": "m"}) == ()

    other = RuntimeError("Error code: 400 - {'error': {'message': 'invalid model'}}")
    assert summarizer._rejected_request_params(other, request) == ()


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
