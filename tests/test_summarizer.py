import asyncio
import re
from types import SimpleNamespace

import pytest

from backend.llm_summarizer import (
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
    NOTE_BUDGET_MAX_TOKENS_MAX,
    NOTE_BUDGET_MAX_TOKENS_MIN,
    NOTE_SECTION_CHARACTERS,
    NOTE_TAIL_CHARACTERS,
    SECTION_MAX_TOKENS_MAX,
    SECTION_MAX_TOKENS_MIN,
    SECTION_WRITE_CONCURRENCY,
    THINKING_RESERVE_TOKENS,
    TAIL_PATCH_MAX_GAP_SECONDS,
    TAIL_PATCH_MAX_INPUT_CHARACTERS,
    LLMSummarizer,
    PROVIDER_DEFAULTS,
    mask_proxy_target,
)
from backend.transcript import TranscriptSegment, segments_to_prompt


def _stream_response(
    content: str | None, finish_reason: str = "stop", reasoning: str | None = None
):
    """构造一个假的大模型流式响应（_complete 现按流式逐块读取）。"""
    async def gen():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=content, reasoning_content=reasoning),
                    finish_reason=finish_reason,
                )
            ]
        )

    return gen()


@pytest.fixture(autouse=True)
def isolate_channel_caches(monkeypatch):
    """两条模块级通道缓存按生产语义会一直存活，测试里必须各自从空集开始。

    否则用例结果取决于执行顺序：前一个用例让某条配置"实测到过思考链"，后一个用例的
    额度就凭空多出预留（``_THINKING_SEEN_CHANNELS``），或被记走一个思考参数
    （``_REJECTED_REASONING_PARAMS``）。
    """
    from backend import llm_summarizer as module

    monkeypatch.setattr(module, "_REJECTED_REASONING_PARAMS", {})
    monkeypatch.setattr(module, "_THINKING_SEEN_CHANNELS", set())


def test_note_prompt_forbids_invented_timestamps() -> None:
    prompt = LLMSummarizer._note_prompt(
        "测试视频",
        "[00:10-00:20] 原文",
        {"transcript_source": "platform_subtitle"},
    )
    assert "时间点只能取自材料" in prompt
    assert "[00:10-00:20]" in prompt
    # 小标题必须带时间区间（用户反馈：成稿小标题后没时间轴，回溯不了）
    assert "「## 标题 [起点-终点]」" in prompt


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
    assert PROVIDER_DEFAULTS["deepseek"][1] == "deepseek-flash"
    assert PROVIDER_DEFAULTS["openai"][1] == "gpt-5.6-terra"
    assert PROVIDER_DEFAULTS["glm"][1] == "glm-4.7-flash"
    assert PROVIDER_DEFAULTS["qwen"][1] == "qwen3.7-plus"
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
    """detailed 允许省略时间戳；只要尾部内容在笔记里，就只告警、不补写。"""
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
    draft = (
        "# 视频笔记\n\n[00:01-04:00] 前半部分要点\n\n"
        "笔记最后也写了：结尾的题目讲解"
    )

    result = await summarizer._ensure_tail_coverage(
        "标题", draft, segments, "detailed", "max", None
    )

    assert result == draft
    assert calls == 0
    assert any("可能只是省略了时间戳" in warning for warning in summarizer.warnings)


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
    """笔记省略了时间戳（提示词允许这种写法）但内容写到了结尾，不该补写。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    async def complete(prompt, **kwargs):
        raise AssertionError("尾部内容已经在笔记里时不得触发补写请求")

    summarizer._complete = complete
    segments = [
        TranscriptSegment(index * 18.0, index * 18.0 + 18.0, f"第 {index} 句口播内容")
        for index in range(300)  # 约 90 分钟
    ]
    # 内容写到了最后（最后一句的原话就在笔记里），但时间戳只标到 12:00。
    draft = (
        "# 视频笔记\n\n[00:01-12:00] 前段要点\n\n"
        "后面每段都只写了文字，没再带时间点；最后讲到：第 299 句口播内容"
    )

    result = await summarizer._ensure_tail_coverage(
        "标题", draft, segments, "faithful", "max", None
    )

    assert result == draft
    assert segments[-1].end - 12 * 60 > TAIL_PATCH_MAX_GAP_SECONDS
    assert len(segments_to_prompt(segments)) > TAIL_PATCH_MAX_INPUT_CHARACTERS
    assert any("已跳过自动补写" in warning for warning in summarizer.warnings)
    assert not any("正在自动补写" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_large_missing_material_is_written_in_sections() -> None:
    """缺口大/材料多时必须拆段续写：单次请求有上限，且每一段都不能丢。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    prompts: list[str] = []

    async def complete(prompt, **kwargs):
        prompts.append(prompt)
        return "补写的结尾"

    summarizer._complete = complete
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

    assert prompts
    # 每一段提示词都受 NOTE_SECTION_CHARACTERS 约束，不再出现一次塞进全部缺失转录的请求
    assert all(len(prompt) < NOTE_SECTION_CHARACTERS * 2 for prompt in prompts)
    # 模型没按格式写时间戳时，代码把未覆盖的原文附进笔记：第 0 段到最后一段都不能丢
    assert "第0段" in result and "第29段" in result
    assert any("按段补写" in warning for warning in summarizer.warnings)
    assert result.startswith(draft)


@pytest.mark.asyncio
async def test_note_stopped_early_is_detected_by_missing_tail_content() -> None:
    """用户实测形状：63 分钟视频笔记停在 36:35、转写一直到 01:03:15。

    没有 finish_reason 也要能靠“尾部转录内容几乎没出现在笔记里”识别出真截断，
    且缺口超过 10 分钟时仍然补写，而不是像旧实现那样直接跳过。
    """
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    prompts: list[str] = []

    async def complete(prompt, **kwargs):
        prompts.append(prompt)
        return "## 补写的小节 [01:00:00-01:03:15]\n\n[01:03:15] 补写内容"

    summarizer._complete = complete
    segments = [
        TranscriptSegment(
            index * 60.0,
            (index + 1) * 60.0,
            f"第{index}分钟讲的是主题{index}的具体推导和例子",
        )
        for index in range(63)
    ]
    draft = (
        "# 视频笔记：《长视频》\n\n"
        "## 前半段 [00:00-36:35]\n\n[36:35] 前半段内容"
    )

    result = await summarizer._ensure_tail_coverage(
        "长视频", draft, segments, "faithful", "high", None
    )

    assert prompts, "真截断必须触发补写"
    assert 63 * 60 - (36 * 60 + 35) > TAIL_PATCH_MAX_GAP_SECONDS
    assert any("按段补写" in warning for warning in summarizer.warnings)
    assert "补写内容" in result
    assert result.startswith(draft)


@pytest.mark.asyncio
async def test_truncation_by_output_limit_is_continued_for_detailed_style() -> None:
    """finish_reason=length 是确定的截断证据；detailed 也补写，不再只告警。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    prompts: list[str] = []

    async def complete(prompt, **kwargs):
        prompts.append(prompt)
        return "## 补写 [03:00:00-03:10:00]\n\n[03:10:00] 详细复原的结尾"

    summarizer._complete = complete
    segments = [
        TranscriptSegment(
            index * 300.0,
            (index + 1) * 300.0,
            f"第{index}段课程内容，包含一个完整例子。",
        )
        for index in range(38)
    ]
    draft = "# 视频笔记\n\n[00:00-01:00:00] 只写到了这里"

    result = await summarizer._ensure_tail_coverage(
        "课程", draft, segments, "detailed", "high", None,
        truncated_by_length=True,
    )

    assert prompts
    assert "详细复原的结尾" in result
    assert any("按段补写" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_tail_patch_starts_after_last_covered_content_not_heading_end() -> None:
    """标题写到 10:02、正文只写到 09:34 时，必须从 09:35 补，而不是从 10:02 补。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    prompts: list[str] = []

    async def complete(prompt, **kwargs):
        prompts.append(prompt)
        return "## 补写 [09:35-11:12]\n\n[11:12] 补写内容"

    summarizer._complete = complete
    segments = [
        TranscriptSegment(0, 300, "开场介绍和第一部分的完整内容。"),
        TranscriptSegment(300, 564.52, "中间几层的内容。"),
        TranscriptSegment(
            564.52,
            574.94,
            "写完了，这三层还差最后一步把它们组装起来。现在各层还只通过抽象打交道，"
            "agent service只认识model client和session store，不知道有anthropic client。",
        ),
        TranscriptSegment(
            575.08,
            579.24,
            "和jsonl session store需要有一个地方，把具体的类创建起来接到一起。",
        ),
        TranscriptSegment(
            579.97,
            590.40,
            "这个地方就叫组装根，也是启动服务的地方，它是整个系统里唯一知道所有具体类的地方，"
            "以后换模型换存储、换入口，换任何具体实现。",
        ),
        TranscriptSegment(
            590.63,
            602.04,
            "都只要改这一个文件。比如说我们想从claude模型换成gpt模型，就只需要修改这一行。"
            "最后的目录差不多长这样，于是我们就完成了设计。",
        ),
        TranscriptSegment(
            602.34,
            620.0,
            "都放进和ai的对话框里，让他帮你实现。别忘了加上之前提过的tdd的策略，"
            "让他写好测试再实现。",
        ),
        TranscriptSegment(
            620.0,
            672.7,
            "看完这个视频，你应该学会了一套设计思路。感谢您的观看，我们下次再见。",
        ),
    ]
    draft = (
        "# 视频笔记：《Agent 架构》\n\n"
        "## 组装根：唯一知道具体类的地方 [09:24-10:02]\n\n"
        "写完了，这三层还差最后一步把它们组装起来。现在各层还只通过抽象打交道，"
        "`AgentService` 只认识 `ModelClient` 和 `SessionStore`，不知道有 `An"
    )

    result = await summarizer._ensure_tail_coverage(
        "Agent 架构", draft, segments, "detailed", "high", None,
        truncated_by_length=True,
    )

    assert prompts
    assert "缺失部分的转录（09:35 – 11:12）" in prompts[0]
    assert "这个地方就叫组装根" in prompts[0]
    assert "### 补遗（09:35 之后）" in result
    assert any("正文实际只写到 09:34" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_common_phrase_in_body_does_not_hide_missing_tail() -> None:
    """通用短语在前文出现过，不能据此判定尾部已覆盖；没有 finish_reason 也要补。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    prompts: list[str] = []

    async def complete(prompt, **kwargs):
        prompts.append(prompt)
        return "## 补写 [10:02-11:12]\n\n[11:12] 补写的结尾"

    summarizer._complete = complete
    segments = [
        TranscriptSegment(0, 300, "前面介绍应用层定义接口，然后在适配层实现。"),
        TranscriptSegment(564.52, 574.94, "组装根之前的内容。"),
        TranscriptSegment(
            602.34,
            612.60,
            "都放进和ai的对话框里，让他帮你实现。别忘了加上之前提过的tdd的策略，"
            "让他写好测试再实现。",
        ),
        TranscriptSegment(
            612.68,
            672.70,
            "看完这个视频，你应该学会了一套设计思路，然后在适配层实现。",
        ),
    ]
    draft = (
        "# 视频笔记：《Agent 架构》\n\n"
        "## 架构 [09:24-10:02]\n\n"
        "前面提过在应用层定义接口，然后在适配层实现。"
    )

    result = await summarizer._ensure_tail_coverage(
        "Agent 架构", draft, segments, "detailed", "high", None
    )

    assert prompts, "通用短语不能把真截断判成已覆盖"
    assert "补写的结尾" in result
    assert any("正在自动补写" in warning for warning in summarizer.warnings)


def test_final_note_budget_grows_with_material_and_has_a_ceiling() -> None:
    assert LLMSummarizer._final_note_max_tokens("字" * 1_000, "faithful") == 4_600
    assert LLMSummarizer._final_note_max_tokens("字" * 1_000, "concise") == 2_400
    middle = LLMSummarizer._final_note_max_tokens("字" * 12_000, "faithful")
    assert 4_600 < middle < 8_000
    assert LLMSummarizer._final_note_max_tokens("字" * 50_000, "faithful") == 8_000


@pytest.mark.asyncio
async def test_complete_records_finish_reason_for_tail_detection() -> None:
    async def create(**kwargs):
        return _stream_response("短正文", finish_reason="length")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = "some-model"
    summarizer.base_url = "https://gateway.example/v1"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await summarizer._complete("测试", 800) == "短正文"
    assert summarizer._last_finish_reason == "length"
    assert summarizer._last_call_stats == {
        "stage": "模型调用",
        "max_tokens": 800,
        "finish_reason": "length",
        "reasoning_characters": 0,
        "content_characters": 3,
    }


@pytest.mark.asyncio
async def test_condensed_pipeline_recovers_a_length_truncated_draft() -> None:
    """完整链路回归：63 分钟转录的成稿被额度截断在 36:35，尾部必须续写出来。"""
    segments = [
        TranscriptSegment(
            index * 60.0,
            (index + 1) * 60.0,
            (f"第{index}分钟讲的是主题{index}的定义、推导和具体例子。" * 5),
        )
        for index in range(63)
    ]
    final_tokens: list[int] = []

    async def complete(prompt, max_tokens, effort="auto", **kwargs):
        if "个连续片段" in prompt:
            return "片段材料" * 1_500
        if "写一份翔实、自然的 Markdown 视频笔记" in prompt:
            final_tokens.append(max_tokens)
            summarizer._last_finish_reason = "length"
            summarizer._last_call_stats = {
                "stage": "正在生成完整笔记",
                "max_tokens": 8000,
                "finish_reason": "length",
                "reasoning_characters": 1234,
                "content_characters": 567,
            }
            return (
                "# 视频笔记：《长视频》\n\n"
                "## 前半段 [00:00-36:35]\n\n"
                + "\n".join(
                    f"[{index:02d}:00] 第{index}分钟讲的是主题{index}的定义、推导和具体例子。"
                    for index in range(36)
                )
            )
        if "补写第" in prompt:
            return "## 补写 [01:00:00-01:03:00]\n\n[01:03:00] 第62分钟的结尾内容。"
        raise AssertionError(f"未预期的请求：{prompt[:60]}")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    summarizer._complete = complete

    result = await summarizer.generate_summary("长视频", segments, style="faithful")

    assert final_tokens and final_tokens[0] > 4_600, "成稿额度没有随材料放大"
    assert "第62分钟的结尾内容" in result
    assert any("按段补写" in warning for warning in summarizer.warnings)
    assert any(
        "成稿被输出额度截断" in warning and "max_tokens=8000" in warning
        for warning in summarizer.warnings
    )
    assert all(warning.find("已跳过自动补写") == -1 for warning in summarizer.warnings)


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
async def test_below_threshold_still_uses_hierarchical_reduction() -> None:
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
    # 21,012 净字数：低于 LONG_TRANSCRIPT_CHARACTERS，但逐段压缩后仍超过 14,000 字预算
    segments = [
        TranscriptSegment(index, index + 1, f"第{index}段" + "内容" * 3_500)
        for index in range(3)
    ]

    result = await summarizer.generate_summary(
        "长视频", segments, style="faithful", progress_callback=report
    )

    assert result == "# 长视频笔记"
    assert sum("归并" in prompt for prompt in calls) >= 2
    assert not any("这是全片第" in prompt for prompt in calls)
    assert progress == sorted(progress)
    assert progress[-1] == 99


def test_note_budget_scales_with_material_and_keeps_thinking_room() -> None:
    """回归（ollama gemma 用户 40 分钟视频实测）：恒定 1200 的压缩额度连思考链都装不下
    ——一次请求思考 4505 字 / 正文 0 / finish_reason=length，整条任务死在 10%。"""
    # 最坏情况：一个 9000 预算的块里只有约 1700 字真语音（whisper 碎段把预算吃在时间戳上）
    assert LLMSummarizer._note_budget_max_tokens(1_700) > 1_200
    # 大块给更多额度，不再"对大块不够、对小块过宽"
    assert LLMSummarizer._note_budget_max_tokens(9_000) > (
        LLMSummarizer._note_budget_max_tokens(1_700)
    )
    assert LLMSummarizer._note_budget_max_tokens(0) == NOTE_BUDGET_MAX_TOKENS_MIN
    assert LLMSummarizer._note_budget_max_tokens(40_000) == NOTE_BUDGET_MAX_TOKENS_MAX


@pytest.mark.asyncio
async def test_chunk_compression_failure_falls_back_to_the_raw_transcript() -> None:
    """一次分片压缩失败不该判死整条任务：该块原文直接交给成稿，内容一条不丢。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    prompts: list[str] = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        prompts.append(prompt)
        if "这是第" in prompt and "个连续片段" in prompt:
            if "第 1/3 个连续片段" in prompt:
                raise RuntimeError("模型未返回正文（下发 max_tokens=1200、思考档=off）")
            return "压缩稿"
        return "# 视频笔记"

    summarizer._complete = complete
    segments = [
        TranscriptSegment(index, index + 1, f"第{index}段" + "内容" * 3_500)
        for index in range(3)
    ]

    result = await summarizer.generate_summary("标题", segments, style="faithful")

    assert result == "# 视频笔记"
    draft_prompt = next(prompt for prompt in prompts if "材料：" in prompt)
    assert "第0段" in draft_prompt, "失败那一段的原文必须仍然交给成稿"
    assert "压缩稿" in draft_prompt, "其余片段照常使用压缩结果"
    assert any("未能整理" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_chunk_compression_cancellation_still_propagates() -> None:
    """兜底是 ``except Exception``：用户取消（BaseException）必须原样抛出去，
    否则会变成"取消不掉"，后台还在继续砸请求。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        raise asyncio.CancelledError("任务已取消")

    summarizer._complete = complete
    segments = [
        TranscriptSegment(index, index + 1, f"第{index}段" + "内容" * 3_500)
        for index in range(3)
    ]

    with pytest.raises(asyncio.CancelledError):
        await summarizer.generate_summary("标题", segments, style="faithful")


@pytest.mark.asyncio
async def test_reduce_failure_stops_at_the_current_level() -> None:
    """归并失败就停在当前层，把未归并的片段笔记交给成稿，而不是让写了大半的任务死掉。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    prompts: list[str] = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        prompts.append(prompt)
        if "这是第" in prompt and "个连续片段" in prompt:
            return "片段材料" * 1_500  # 三份 6000 字，归并一定会触发
        if "这是长视频内容的第" in prompt:
            raise RuntimeError("模型未返回正文")
        return "# 长视频笔记"

    summarizer._complete = complete
    segments = [
        TranscriptSegment(index, index + 1, f"第{index}段" + "内容" * 3_500)
        for index in range(3)
    ]

    result = await summarizer.generate_summary("标题", segments, style="faithful")

    assert result == "# 长视频笔记"
    draft_prompt = next(prompt for prompt in prompts if "材料：" in prompt)
    assert "片段材料" in draft_prompt
    assert any("已停止归并" in warning for warning in summarizer.warnings)


_SECTION_RANGE_RE = re.compile(r"覆盖时间轴 (\S+?) – (\S+?)。")
_SECTION_COVERS_RE = re.compile(r"（覆盖到 (\S+?)）")


def _section_end(prompt: str) -> str:
    """从提示词里取该段覆盖到的时间点——提示词句式是管线与测试之间的契约。"""
    first_draft = _SECTION_RANGE_RE.search(prompt)
    if first_draft:
        return first_draft.group(2)
    return _SECTION_COVERS_RE.search(prompt).group(1)


def _section_index(prompt: str) -> int:
    return int(re.search(r"这是全片第 (\d+)/", prompt).group(1))


def _sectioned_transcript(sections: int = 4) -> list[TranscriptSegment]:
    """净字数超过阈值、且刚好切成 ``sections`` 段的碎行转录。"""
    lines_per_section = NOTE_SECTION_CHARACTERS // 100
    count = lines_per_section * sections
    return [
        TranscriptSegment(index * 3.0, index * 3.0 + 2.0, "字" * 97 + f"{index:03d}")
        for index in range(count)
    ]


def _summarizer_with(fake_complete) -> tuple[LLMSummarizer, list]:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    calls: list[tuple[str, int, str]] = []

    async def wrapped(prompt, max_tokens, effort="auto", **kwargs):
        calls.append((prompt, max_tokens, effort))
        return await fake_complete(prompt, max_tokens, effort)

    summarizer._complete = wrapped
    return summarizer, calls


def test_section_stage_effort_disables_thinking_in_auto_mode() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-v4-flash"
    summarizer.base_url = "https://api.deepseek.com"

    # 逐段直写是局部任务：实测 high 档每段 1 万多字思考只换 1 千字正文，auto 下直接关思考
    assert summarizer._stage_effort("auto", "faithful", "section") == "off"
    # 而中短路径的压缩与成稿走 STYLE_DEFAULT_EFFORT（2026-10-03 三档对照后从 off 换成 high）
    assert summarizer._stage_effort("auto", "faithful", "notes") == "high"
    # 用户显式选择仍然优先
    assert summarizer._stage_effort("max", "faithful", "section") == "max"
    assert summarizer._stage_effort("off", "faithful", "section") == "off"


@pytest.mark.asyncio
async def test_long_transcript_writes_sections_without_merging() -> None:
    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            return "这份笔记覆盖四段内容。"
        if "这是全片第" in prompt:
            return f"## 小节{_section_index(prompt)}\n\n[{_section_end(prompt)}] 写到结尾"
        return "# 视频笔记"

    summarizer, calls = _summarizer_with(fake)
    progress: list[int] = []

    result = await summarizer.generate_summary(
        "长视频",
        _sectioned_transcript(),
        style="faithful",
        progress_callback=lambda value, message: progress.append(value),
    )

    assert not any("归并" in prompt for prompt, *_ in calls)
    assert sum("这是全片第" in prompt for prompt, *_ in calls) == 4
    assert result.startswith("# 视频笔记：《长视频》")
    assert "这份笔记覆盖四段内容。" in result
    # 目录由代码生成：区间端点必须落在整片时间轴上
    toc = result.split("## 本片目录")[1].split("## 小节")[0]
    assert "`00:00-" in toc and "写到结尾" in result.split("## 小节4")[1]
    assert progress == sorted(progress)
    assert progress[-1] == 99


def test_section_output_budget_scales_with_section_size() -> None:
    tiny = [TranscriptSegment(0, 1, "字" * 200)]
    small = [TranscriptSegment(0, 1, "字" * 3_000)]
    large = [TranscriptSegment(0, 1, "字" * 6_000)]
    huge = [TranscriptSegment(0, 1, "字" * 40_000)]

    assert LLMSummarizer._section_max_tokens(tiny) == SECTION_MAX_TOKENS_MIN
    assert LLMSummarizer._section_max_tokens(huge) == SECTION_MAX_TOKENS_MAX
    # 落在上下限之间时，额度与这一段要记录的字数成正比
    assert LLMSummarizer._section_max_tokens(large) == 2 * (
        LLMSummarizer._section_max_tokens(small)
    )
    assert SECTION_MAX_TOKENS_MIN < LLMSummarizer._section_max_tokens(
        small
    ) < SECTION_MAX_TOKENS_MAX


def test_section_prompt_asks_for_timestamped_headings() -> None:
    prompt = LLMSummarizer._section_note_prompt(
        "测试视频",
        1,
        4,
        [TranscriptSegment(0, 60, "内容"), TranscriptSegment(60, 120, "内容")],
        "",
    )
    # 用户反馈：小标题带时间更好读（放在标题文字之后）；文档级标题仍统一由代码写在最前
    assert "「## 标题 [起点-终点]」" in prompt
    assert "不要输出 # 开头的文档标题" in prompt


def test_section_prompt_includes_brief_and_math_delimiters() -> None:
    prompt = LLMSummarizer._section_note_prompt(
        "测试视频",
        1,
        4,
        [TranscriptSegment(0, 60, "内容")],
        "",
        brief="视频背景资料（来自平台简介）：\n简介：线性代数课程",
    )
    assert "线性代数课程" in prompt
    # 多数 Markdown 渲染器只认 $ 系定界符，\( \) / \[ \] 会渲染成裸反斜杠
    assert "不要用 \\(...\\) 或 \\[...\\]" in prompt

    bare = LLMSummarizer._section_note_prompt(
        "测试视频", 1, 4, [TranscriptSegment(0, 60, "内容")], ""
    )
    assert "视频背景资料" not in bare


def test_context_brief_uses_video_description_only() -> None:
    brief = LLMSummarizer._context_brief({"description": "宋浩老师的线性代数课程"})
    assert "简介：宋浩老师的线性代数课程" in brief
    # 热评多为玩梗，刻意不采集：即使元数据里带了也不进提示词
    assert (
        LLMSummarizer._context_brief(
            {"description": "线代课程", "hot_comments": ["前排"]}
        ).count("前排")
        == 0
    )
    # 缺失或空白简介时不输出空壳背景块
    assert LLMSummarizer._context_brief({"description": "  "}) == ""
    assert LLMSummarizer._context_brief({}) == ""


def test_math_delimiters_are_normalized_for_markdown_renderers() -> None:
    raw = (
        "行内 \\(a_{11}\\) 与显示公式：\n\n"
        "\\[\n(\\lambda-1)(\\lambda+4)-2\\times 3=0\n\\]\n\n"
        "```python\nliteral \\(x\\) stays\n```\n"
    )
    normalized = LLMSummarizer._normalize_math_delimiters(raw)
    assert "$a_{11}$" in normalized
    assert "$$\n(\\lambda-1)(\\lambda+4)-2\\times 3=0\n$$" in normalized
    # 代码块内是字面文本，不能被改写
    assert "literal \\(x\\) stays" in normalized


def test_toc_heading_drops_the_trailing_timestamp() -> None:
    assert (
        LLMSummarizer._section_heading("## 主存编址 [01:02-03:04]\n\n正文") == "主存编址"
    )
    # 只剥时钟样式，标题里正常的方括号内容要留着
    assert LLMSummarizer._section_heading("## 重点 [务必注意]") == "重点 [务必注意]"
    assert LLMSummarizer._section_heading("## 没有时间的标题") == "没有时间的标题"
    assert LLMSummarizer._section_heading("整段没有二级标题") == ""


@pytest.mark.asyncio
async def test_stopped_section_is_continued_from_its_last_timestamp() -> None:
    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            return "概览"
        if "还没写完" in prompt:
            return f"[{_section_end(prompt)}] 续写把剩下的讲完了"
        return f"## 小节{_section_index(prompt)}\n\n[00:03] 只写了开头"

    summarizer, calls = _summarizer_with(fake)
    result = await summarizer.generate_summary(
        "长视频", _sectioned_transcript(), style="faithful"
    )

    continuations = [
        effort for prompt, _tokens, effort in calls if "还没写完" in prompt
    ]
    assert len(continuations) == 4
    # 思考已在首写时做过，续写不再注入思考参数（off 会被某些兼容网关翻译成
    # reasoning_effort=none 并被拒，auto 才是最不会把任务打翻的写法）
    assert set(continuations) == {"auto"}
    assert result.count("续写把剩下的讲完了") == 4
    assert summarizer.warnings == []


@pytest.mark.asyncio
async def test_continuation_failure_keeps_the_section_already_written() -> None:
    """续写阶段报错不得打死整条任务：首写已产出的内容必须留在笔记里。"""

    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            return "概览"
        if "还没写完" in prompt:
            raise RuntimeError("通道把 reasoning_effort 的值拒了")
        return f"## 小节{_section_index(prompt)}\n\n[00:03] 首写只到这里"

    summarizer, calls = _summarizer_with(fake)
    result = await summarizer.generate_summary(
        "长视频", _sectioned_transcript(), style="faithful"
    )

    assert "## 小节1" in result and "## 小节4" in result
    assert sum("还没写完" in prompt for prompt, *_ in calls) >= 4
    assert len(summarizer.warnings) == 4
    assert "补写结尾失败" in summarizer.warnings[0]
    # 首写没推进到区间末尾，未覆盖的原文必须补上
    assert "### 未能整理的原文" in result


@pytest.mark.asyncio
async def test_section_falls_back_to_raw_transcript_instead_of_losing_content() -> None:
    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            return "概览"
        if "这是全片第" in prompt:
            return f"## 小节{_section_index(prompt)}\n\n整段都没有时间戳"
        if "还没写完" in prompt:
            return "仍然没有时间戳"
        return "其他"

    summarizer, _ = _summarizer_with(fake)
    segments = _sectioned_transcript()
    result = await summarizer.generate_summary("长视频", segments, style="faithful")

    assert result.count("### 未能整理的原文") == 4
    assert len(summarizer.warnings) == 4
    assert "多次整理后仍未写到本段结尾" in summarizer.warnings[0]
    # 内容一条都不能少：每段原文都必须在笔记里出现
    for segment in segments:
        assert segment.text in result


@pytest.mark.asyncio
async def test_model_error_in_one_section_does_not_fail_the_task() -> None:
    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            return "概览"
        if "这是全片第" in prompt:
            if _section_index(prompt) == 2:
                raise RuntimeError("模型未返回正文")
            return f"## 小节{_section_index(prompt)}\n\n[{_section_end(prompt)}] 正常"
        return "其他"

    summarizer, _ = _summarizer_with(fake)
    result = await summarizer.generate_summary("长视频", _sectioned_transcript(), style="faithful")

    assert "## 小节1" in result and "## 小节4" in result
    assert "### 未能整理的原文" in result
    assert "模型调用失败" in summarizer.warnings[0]


@pytest.mark.asyncio
async def test_rate_limited_section_backs_off_before_retrying() -> None:
    """限流不能立刻再砸一次请求：先退避，否则整段直接退化成内嵌原文。"""
    import backend.llm_summarizer as module

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    original_sleep = asyncio.sleep
    asyncio.sleep = fake_sleep
    attempts: dict[int, int] = {}

    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            return "概览"
        if "这是全片第" not in prompt:
            return "其他"
        index = _section_index(prompt)
        attempts[index] = attempts.get(index, 0) + 1
        if index == 2 and attempts[index] == 1:
            raise RuntimeError(
                "Error code: 429 - {'error': {'code': 'insufficient_quota',"
                " 'message': 'You exceeded your current quota'}}"
            )
        return f"## 小节{index}\n\n[{_section_end(prompt)}] 内容{index}"

    try:
        summarizer, _ = _summarizer_with(fake)
        result = await summarizer.generate_summary(
            "长视频", _sectioned_transcript(), style="faithful"
        )
    finally:
        asyncio.sleep = original_sleep

    # 只有撞限流那一次等待，非限流失败不白等
    assert sleeps == [module.SECTION_RATE_LIMIT_BACKOFF_SECONDS]
    assert "内容2" in result
    assert module.RAW_FALLBACK_HEADING not in result  # 退避后重试成功，没落到兜底


@pytest.mark.asyncio
async def test_backoff_tolerates_a_sync_progress_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退避提示不得直接 await 回调：调用方传同步函数也得活。"""

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", no_wait)
    seen: list[str] = []

    def sync_report(value: int, message: str) -> None:
        seen.append(message)

    calls: list[str] = []

    async def fake(prompt, max_tokens, effort="auto", **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("Error code: 429 - We have to rate limit you")
        return f"## 小节1\n\n[{_section_end(prompt)}] 重试后写成"

    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    summarizer._complete = fake
    section = [
        TranscriptSegment(index * 60.0, index * 60.0 + 59.0, "字" * 100)
        for index in range(3)
    ]

    piece = await summarizer._write_one_section(
        "长视频", 1, 1, section, "", "high", "auto", sync_report, None
    )

    assert any("遇到限流" in message for message in seen)
    assert piece.endswith("重试后写成")
    assert summarizer.warnings == []


@pytest.mark.asyncio
async def test_concurrent_completion_order_does_not_change_assembly() -> None:
    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            return "概览"
        if "这是全片第" in prompt:
            index = _section_index(prompt)
            await asyncio.sleep((5 - index) * 0.02)  # 后面的段先完成
            return f"## 小节{index}\n\n[{_section_end(prompt)}] 内容{index}"
        return "其他"

    summarizer, _ = _summarizer_with(fake)
    result = await summarizer.generate_summary("长视频", _sectioned_transcript(4), style="faithful")

    positions = [result.index(f"内容{index}") for index in range(1, 5)]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_section_writing_stays_within_the_concurrency_limit() -> None:
    inflight = {"now": 0, "peak": 0}

    async def fake(prompt, max_tokens, effort):
        if "这是全片第" in prompt:
            inflight["now"] += 1
            inflight["peak"] = max(inflight["peak"], inflight["now"])
            await asyncio.sleep(0.02)
            inflight["now"] -= 1
            return f"## 小节{_section_index(prompt)}\n\n[{_section_end(prompt)}] 内容"
        if "各段标题与时间范围" not in prompt:
            return "其他"
        return "概览"

    summarizer, _ = _summarizer_with(fake)
    await summarizer.generate_summary("长视频", _sectioned_transcript(6), style="faithful")

    assert inflight["peak"] == SECTION_WRITE_CONCURRENCY


@pytest.mark.asyncio
async def test_cancellation_inside_a_section_is_not_swallowed_by_the_fallback() -> None:
    async def fake(prompt, max_tokens, effort):
        if "这是全片第" in prompt and _section_index(prompt) == 2:
            raise asyncio.CancelledError("任务已取消")
        if "这是全片第" in prompt:
            await asyncio.sleep(0.05)
            return f"## 小节\n\n[{_section_end(prompt)}] 内容"
        return "概览"

    summarizer, _ = _summarizer_with(fake)
    with pytest.raises(asyncio.CancelledError):
        await summarizer.generate_summary("长视频", _sectioned_transcript(), style="faithful")


@pytest.mark.asyncio
async def test_detailed_analysis_sees_the_whole_assembled_note() -> None:
    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            return "概览"
        if "点评对象是视频里的内容" in prompt:
            return "## 点评与分析\n\n点评正文"
        return f"## 小节{_section_index(prompt)}\n\n[{_section_end(prompt)}] 内容{_section_index(prompt)}"

    summarizer, calls = _summarizer_with(fake)
    result = await summarizer.generate_summary("长视频", _sectioned_transcript(), style="detailed")

    analysis_prompt = next(prompt for prompt, *_ in calls if "点评对象是视频里的内容" in prompt)
    assert "内容1" in analysis_prompt and "内容4" in analysis_prompt
    assert result.endswith("点评正文")
    # 点评必须是最后一个调用
    assert "点评对象是视频里的内容" in calls[-1][0]


@pytest.mark.asyncio
async def test_explicit_effort_is_respected_by_mechanical_stages() -> None:
    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            return "概览"
        if "还没写完" in prompt:
            return f"[{_section_end(prompt)}] 续写"
        return f"## 小节\n\n[00:03] 只写了开头"

    summarizer, calls = _summarizer_with(fake)
    await summarizer.generate_summary(
        "长视频", _sectioned_transcript(), style="faithful", reasoning_effort="max"
    )

    mechanical = [
        effort
        for prompt, _tokens, effort in calls
        if "还没写完" in prompt or "各段标题与时间范围" in prompt
    ]
    # 4 次段内续写 + 1 次概览
    assert len(mechanical) == 5
    # 用户显式选了档位，机械阶段也照它的选择下发；而「最大」在入口就被折成「高」
    # （界面上已去掉这一档，实测它对笔记是净伤害），所以这里收到的是 high 不是 max
    assert set(mechanical) == {"high"}


@pytest.mark.asyncio
async def test_overview_failure_keeps_the_note_body() -> None:
    async def fake(prompt, max_tokens, effort):
        if "各段标题与时间范围" in prompt:
            raise RuntimeError("模型未返回正文")
        return f"## 小节{_section_index(prompt)}\n\n[{_section_end(prompt)}] 内容"

    summarizer, _ = _summarizer_with(fake)
    result = await summarizer.generate_summary("长视频", _sectioned_transcript(), style="faithful")

    assert "## 小节1" in result and "## 本片目录" in result
    assert "内容概览生成失败，已跳过" in summarizer.warnings[0]


@pytest.mark.asyncio
async def test_concise_style_keeps_the_condensed_path() -> None:
    async def fake(prompt, max_tokens, effort):
        if "这是第" in prompt and "个连续片段" in prompt:
            return "片段材料" * 1_500
        return "# 精简摘要"

    summarizer, calls = _summarizer_with(fake)
    result = await summarizer.generate_summary(
        "长视频", _sectioned_transcript(), style="concise"
    )

    assert result == "# 精简摘要"
    assert not any("这是全片第" in prompt for prompt, *_ in calls)


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
    assert captured["max_tokens"] == 16_000
    assert "temperature" not in captured
    # 非官方通道不得收到私有 thinking 字段
    assert captured["reasoning_effort"] == "high"
    assert "extra_body" not in captured

    captured.clear()
    assert await summarizer._complete("测试", 4_600, "max") == "摘要"
    assert captured["max_tokens"] == 23_000


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


def test_official_deepseek_gets_32k_high_budget_while_compat_gets_16k() -> None:
    official = object.__new__(LLMSummarizer)
    official.model_type = "deepseek"
    official.model = "deepseek-flash"
    official.base_url = "https://api.deepseek.com"
    official.warnings = []
    assert official._build_request("测试", 4_600, "high")["max_tokens"] == 32_000
    assert official._build_request("测试", 4_600, "off")["max_tokens"] == 4_600
    # max 档取 max(正文×5, 官方高额度)，23K 小于 32K
    assert official._build_request("测试", 4_600, "max")["max_tokens"] == 32_000

    compat = object.__new__(LLMSummarizer)
    compat.model_type = "custom"
    compat.model = "DeepSeek-V4-Flash"
    compat.base_url = "https://gateway.example/v1"
    compat.warnings = []
    assert compat._build_request("测试", 4_600, "high")["max_tokens"] == 16_000
    # 兼容通道的 max 仍按正文×5 抬高，16K 不参与放大
    assert compat._build_request("测试", 4_600, "max")["max_tokens"] == 23_000


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
    assert request["max_tokens"] == 16_000
    # 同一配置的新实例也从缓存里读到该结论
    fresh = object.__new__(LLMSummarizer)
    fresh.model_type = "custom"
    fresh.model = "deepseek-ai/DeepSeek-V4"
    fresh.base_url = "https://gateway.example/v1"
    fresh.warnings = []
    assert "reasoning_effort" not in fresh._build_request("测试", 800, "high")


@pytest.mark.asyncio
async def test_rejected_reasoning_effort_value_degrades_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只否认取值的 400 也要降级，不能把任务判失败。

    ModelScope 这类兼容网关不认关思考用的 ``none``，报错既没有 ``param`` 字段也不是
    ``is not supported`` 句式（``'reasoning_effort' must be one of: 'low', ...``）。
    """
    import backend.llm_summarizer as module

    monkeypatch.setattr(module, "_REJECTED_REASONING_PARAMS", {})
    attempts: list[dict] = []

    class RejectValue(Exception):
        status_code = 400

        def __init__(self, name: str):
            super().__init__(
                f"Error code: 400 - {{'error': {{'code': 'invalid_value', 'message': "
                f"'{name}' must be one of: 'low', 'medium', 'high', 'xhigh', 'max', "
                f"'param': None}}}}"
            )

    async def create(**kwargs):
        attempts.append(kwargs)
        if len(attempts) <= 2:
            raise RejectValue("reasoning_effort" if len(attempts) == 1 else "reasoning")
        return _stream_response("摘要")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = "deepseek-ai/DeepSeek-V4"
    summarizer.base_url = "https://api-inference.modelscope.cn/v1"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await summarizer._complete("测试", 800, "off") == "摘要"
    # flat reasoning_effort=none → 嵌套 reasoning.effort → 两个都被拒后干脆不注入
    assert len(attempts) == 3
    assert "reasoning_effort" not in attempts[2]
    assert "extra_body" not in attempts[2]
    assert any("不支持关闭深度思考" in warning for warning in summarizer.warnings)


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
    assert attempts[0]["max_tokens"] == 23_000  # max 档 ×5 抬高后的预算
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
    assert [a.get("max_tokens") for a in attempts] == [16_000, 4_600, None]
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
    assert summarizer._stage_effort("auto", "detailed", "notes") == "high"
    assert summarizer._stage_effort("auto", "faithful", "notes") == "high"
    assert summarizer._stage_effort("auto", "concise", "notes") == "high"
    # 用户显式选择永远优先
    assert summarizer._stage_effort("off", "detailed", "notes") == "off"
    assert summarizer._stage_effort("high", "detailed", "analysis") == "high"

    # custom 通道识别出 DeepSeek 模型时同样吃到风格默认（所以默认换成 high 也覆盖到
    # ModelScope 这类免费代理——那条通道有长思考流被掐断的旧记录，是这次改动的已知风险）
    summarizer.model_type = "custom"
    summarizer.model = "deepseek-ai/DeepSeek-V4-Flash-0731"
    summarizer.base_url = "https://api-inference.modelscope.cn/v1/"
    assert summarizer._stage_effort("auto", "detailed", "notes") == "high"

    # 非 DeepSeek 通道保持模型默认，不注入私有参数
    summarizer.model_type = "custom"
    summarizer.model = "provider-model-alias"
    summarizer.base_url = "https://gateway.example/v1"
    assert summarizer._stage_effort("auto", "detailed", "analysis") == "auto"


@pytest.mark.asyncio
async def test_retired_max_effort_folds_into_high_instead_of_failing() -> None:
    """界面上已经没有「最大」这一档，但存过它的旧档案、MCP 与 Skill 仍会传 max 进来。
    在这里报错等于让老用户一点就失败，所以折成 high 继续跑。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-flash"
    summarizer.base_url = "https://api.deepseek.com"
    seen: list[str] = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        seen.append(effort)
        return "# 视频笔记"

    summarizer._complete = complete
    await summarizer.generate_summary(
        "标题", [TranscriptSegment(0, 10, "一段简短转录")],
        style="faithful", reasoning_effort="max",
    )

    assert seen and set(seen) == {"high"}


def test_timestamp_ranges_are_normalized_to_hyphens() -> None:
    """模型（尤其开思考那几档）爱把标题区间写成 [00:00 – 00:33]，与代码生成的目录和
    提示词用的半角 - 不一致；同一次运行内部一致、跨次不一致，收尾统一掉。"""
    text = (
        "## 开场 [00:00 – 00:33]\n\n见 [01:00 — 02:00]，也见 03:00–04:00 与 a–b\n\n"
        "## 下一节 [05:00-06:00]"
    )
    out = LLMSummarizer._normalize_timestamp_delimiters(text)
    assert "[00:00-00:33]" in out and "[01:00-02:00]" in out and "03:00-04:00" in out
    assert "[05:00-06:00]" in out
    assert "a–b" in out, "不是「时间:时间」形状的破折号不该被动"


def test_describe_effort_reports_effective_level() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-v4-flash"
    summarizer.base_url = "https://api.deepseek.com"
    assert summarizer.describe_effort("max", "detailed") == "high"
    assert summarizer.describe_effort("auto", "detailed") == (
        "auto（本机按 high，长视频逐段直写为 off 档）"
    )
    # 这句以前写死"默认关思考"，默认值一改就成了假话——而它是用户在任务记录里
    # 唯一能看到的依据，必须跟着实际档位走

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
    expected_warning = (
        "正在补充点评与分析：模型未返回正文（下发 max_tokens=800、思考档=high，"
        "实际收到 思考 0 字 / 正文 0 字，finish_reason=length），"
        "多为思考链吃满输出额度后流被截断，已关闭深度思考并重试"
    )
    assert progress == [(93, expected_warning)]
    assert summarizer.warnings == [expected_warning]


def _deepseek_offline(create) -> LLMSummarizer:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "deepseek"
    summarizer.model = "deepseek-flash"
    summarizer.base_url = "https://api.deepseek.com"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return summarizer


@pytest.mark.asyncio
async def test_channel_ignoring_thinking_off_says_so_instead_of_repeating_advice() -> None:
    """1.4.2 群测实测：off 档下发后仍累计 6473 字思考，4600 额度吃满、正文一个字没出。

    这句报错以前固定写着"可尝试关闭深度思考"——而他的档位正是关着的，于是照着它反复
    重投、重登，白绕一圈。
    """
    requests: list[dict] = []

    async def create(**kwargs):
        requests.append(kwargs)
        return _stream_response(None, "length", reasoning="思" * 6473)

    summarizer = _deepseek_offline(create)
    with pytest.raises(RuntimeError) as raised:
        await summarizer._complete("测试", 4_600, "off", stage="正在生成完整笔记")

    text = str(raised.value)
    assert "该通道没有响应关思考的请求" in text
    assert "可尝试关闭深度思考" not in text
    assert "思考 6473 字 / 正文 0 字" in text
    assert "max_tokens=4600" in text
    assert len(requests) == 1  # 已经是 off，不该再白跑一次"关掉思考"的重试


@pytest.mark.asyncio
async def test_thinking_leak_is_reported_once_even_when_the_note_succeeds() -> None:
    """成功出稿也要提一句这条通道的开关是空的，别等长正文被噎死才发现。"""

    async def create(**kwargs):
        return _stream_response("正文", "stop", reasoning="思考链")

    summarizer = _deepseek_offline(create)
    for _ in range(3):  # 逐段直写会调几十次，告警只该说一句
        assert await summarizer._complete("测试", 4_600, "off") == "正文"

    assert len(summarizer.warnings) == 1
    assert "没有响应「关闭深度思考」" in summarizer.warnings[0]


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


@pytest.mark.asyncio
async def test_dropped_stream_retries_once_when_nothing_received() -> None:
    """长流被网关掐断且正文为 0 时自动重投一次；已收到部分正文时不重投。"""
    attempts: list = []

    async def create(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:

            async def broken():
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=None, reasoning_content="思考中"),
                            finish_reason=None,
                        )
                    ]
                )
                raise RuntimeError(
                    "peer closed connection without sending complete message body "
                    "(incomplete chunked read)"
                )

            return broken()
        return _stream_response("重试成功")

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "glm"
    summarizer.model = "glm-4.5-flash"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert await summarizer._complete("测试", 800, "high") == "重试成功"
    assert len(attempts) == 2
    assert any("连接中断" in warning and "自动重试一次" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_dropped_stream_with_partial_content_is_not_retried() -> None:
    async def create(**kwargs):
        async def half_then_broken():
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="半截正文"), finish_reason=None
                    )
                ]
            )
            raise RuntimeError("peer closed connection")

        return half_then_broken()

    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "glm"
    summarizer.model = "glm-4.5-flash"
    summarizer.warnings = []
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(RuntimeError, match="peer closed"):
        await summarizer._complete("测试", 800, "high")
    assert summarizer.warnings == []


# ------------------------------------------------- 测试连接的绕代理重试（1.4.2「重启电脑才行」悬案的对症路）


def test_mask_proxy_target_hides_userinfo() -> None:
    """诊断信息允许出现"代理指向哪"，绝不允许带出代理的账号段。"""
    assert mask_proxy_target("http://user:supersecret@127.0.0.1:7890") == "http://***@127.0.0.1:7890"
    assert mask_proxy_target("socks5://127.0.0.1:7891") == "socks5://127.0.0.1:7891"
    assert mask_proxy_target("") == ""


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


def _connection_error():
    import httpx
    import openai

    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.example.test/v1"))


@pytest.mark.asyncio
async def test_test_connection_retries_direct_when_env_proxy_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """走环境代理连不上时自动直连重试；两条路都要在消息里说清。"""
    attempts: list[str] = []
    created: list[str] = []

    class FakeCompletions:
        def __init__(self, tag: str) -> None:
            self._tag = tag

        async def create(self, **kwargs):
            attempts.append(self._tag)
            if self._tag == "proxied":
                raise _connection_error()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))]
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            tag = "direct" if created else "proxied"
            created.append(tag)
            self.chat = SimpleNamespace(completions=FakeCompletions(tag))

        async def close(self) -> None:
            pass

    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

    summarizer = LLMSummarizer(model_type="deepseek", api_key="sk-test")
    ok, message, _latency = await summarizer.test_connection()

    assert ok is True
    assert attempts == ["proxied", "direct"]
    assert "直连成功" in message
    assert "HTTPS_PROXY=" in message


@pytest.mark.asyncio
async def test_test_connection_direct_retry_also_failing_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """绕开代理也连不上时，把"整条网络都不通"说出口，别只报代理。"""
    attempts: list[int] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            attempts.append(1)
            raise _connection_error()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self) -> None:
            pass

    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

    summarizer = LLMSummarizer(model_type="deepseek", api_key="sk-test")
    ok, message, _latency = await summarizer.test_connection()

    assert ok is False
    assert len(attempts) == 2
    assert "绕开代理直连也一样失败" in message


@pytest.mark.asyncio
async def test_test_connection_skips_retry_without_env_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有环境代理就没有"换条路"可言：失败直接返回，不建第二个客户端。"""
    created: list[int] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            raise _connection_error()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            created.append(1)
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self) -> None:
            pass

    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

    summarizer = LLMSummarizer(model_type="deepseek", api_key="sk-test")
    ok, _message, _latency = await summarizer.test_connection()

    assert ok is False
    assert len(created) == 1


@pytest.mark.asyncio
async def test_test_connection_does_not_retry_on_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """401 这类与代理无关的失败不再重试，免得把"Key 不对"搅成网络问题。"""
    attempts: list[int] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            attempts.append(1)
            raise RuntimeError("HTTP 401")

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self) -> None:
            pass

    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

    summarizer = LLMSummarizer(model_type="deepseek", api_key="sk-test")
    ok, _message, _latency = await summarizer.test_connection()

    assert ok is False
    assert len(attempts) == 1


# ---------------------------------------------------------------------------
# 思考链吃满输出额度（本地 ollama 用户 2026-10-03 的实测日志：一次压缩请求
# 思考 4505 字 / 正文 0 / finish_reason=length，整条任务死在 10%）
# ---------------------------------------------------------------------------


def _ollama_like(model: str = "gemma4:e4b") -> LLMSummarizer:
    """一条"本程序没有思考开关"的通道：custom + 非 DeepSeek 模型名 + 本机地址。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.model_type = "custom"
    summarizer.model = model
    summarizer.base_url = "http://127.0.0.1:11434/v1"
    summarizer.warnings = []
    return summarizer


def _fake_client(summarizer: LLMSummarizer, create) -> None:
    summarizer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def test_reserve_waits_for_evidence_and_never_overrides_degradation() -> None:
    from backend import llm_summarizer as module

    summarizer = _ollama_like()
    assert not summarizer._controls_thinking()
    assert summarizer._build_request("p", 1_600, "auto")["max_tokens"] == 1_600
    # 换档对这条通道既不下发思考参数也不改变额度——所以失败文案不该劝人换档
    assert summarizer._build_request("p", 1_600, "high")["max_tokens"] == 1_600

    module._THINKING_SEEN_CHANNELS.add(summarizer._param_cache_key())
    assert summarizer._build_request("p", 1_600, "auto")["max_tokens"] == (
        1_600 + THINKING_RESERVE_TOKENS
    )
    # 降级到 requested 时不叠加预留：那条路正是网关嫌额度太大才退回来的
    assert summarizer._build_request("p", 1_600, "auto", budget="requested")["max_tokens"] == 1_600


@pytest.mark.asyncio
async def test_stream_records_that_the_channel_actually_thinks() -> None:
    async def create(**kwargs):
        return _stream_response("正文", reasoning="思考" * 100)

    summarizer = _ollama_like()
    _fake_client(summarizer, create)

    assert not summarizer.thinking_seen
    assert await summarizer._complete("测试", 1_600, "off") == "正文"
    assert summarizer.thinking_seen
    assert any("改档位也不会变" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_empty_content_advice_stops_offering_a_no_op_knob() -> None:
    """回归：1.4.2 那句"请改用「高」或「最大」档"是给 DeepSeek 兼容通道写的，
    在 ollama 上换档既不下发思考参数也不抬高额度，照做只会原地重投。"""

    async def create(**kwargs):
        return _stream_response(None, "length", reasoning="思考" * 2_000)

    summarizer = _ollama_like()
    _fake_client(summarizer, create)

    with pytest.raises(RuntimeError) as excinfo:
        await summarizer._complete("测试", 1_200, "off")

    assert "没有这条通道的思考开关" in str(excinfo.value)
    assert "请改用「高」" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_tail_patch_failure_keeps_the_finished_draft() -> None:
    """补写只是可选增强：只 catch TimeoutError 时，一次「模型未返回正文」会把已经
    写完整的笔记一起判死。用户实测同一 18 分钟视频「详细复原」失败、「精简摘要」通过，
    差别正是 concise 在函数开头就跳过了补写。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        raise RuntimeError("模型未返回正文（下发 max_tokens=1600、思考档=off）")

    summarizer._complete = complete
    # 缺口 299 秒：在 TAIL_PATCH_MAX_GAP_SECONDS 之内，确实会发出补写请求
    segments = [
        TranscriptSegment(index * 60.0, index * 60.0 + 59.0, f"第{index}段内容")
        for index in range(8)
    ]
    draft = "## 小节 [00:00-02:00]\n\n已经写好的正文"

    result = await summarizer._ensure_tail_coverage(
        "标题", draft, segments, "faithful", "off", None, None
    )

    assert result == draft
    assert any("补写结尾失败" in warning for warning in summarizer.warnings)


@pytest.mark.asyncio
async def test_tail_patch_cancellation_still_propagates() -> None:
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        raise asyncio.CancelledError("任务已取消")

    summarizer._complete = complete
    segments = [
        TranscriptSegment(index * 60.0, index * 60.0 + 59.0, f"第{index}段内容")
        for index in range(8)
    ]

    with pytest.raises(asyncio.CancelledError):
        await summarizer._ensure_tail_coverage(
            "标题", "## 小节 [00:00-02:00]\n\n正文", segments, "faithful", "off", None, None
        )


@pytest.mark.asyncio
async def test_short_transcript_is_not_split_by_timestamp_overhead() -> None:
    """碎 ASR 行的时间戳开销曾把 7 分钟的视频切成多块，每块都要押一次有损压缩调用。
    先合并成 ~48 字长行再切，这个规模就该是一次成稿、零压缩调用。"""
    summarizer = object.__new__(LLMSummarizer)
    summarizer.warnings = []
    prompts: list[str] = []

    async def complete(prompt, max_tokens, effort="auto", retry_empty=True, **kwargs):
        prompts.append(prompt)
        return "# 视频笔记"

    summarizer._complete = complete
    # 300 段 × 8 字：净字数只有 2400，但逐行摊上 32 字开销就是 12000，超过 9000 预算
    segments = [
        TranscriptSegment(index * 1.5, index * 1.5 + 1.4, "字" * 8) for index in range(300)
    ]

    await summarizer.generate_summary("标题", segments, style="faithful")

    assert not any("个连续片段" in prompt for prompt in prompts), "不该再发分片压缩请求"
    assert len(prompts) == 1
