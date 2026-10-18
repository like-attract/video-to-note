"""前端结构约束测试。

这里盯的都是历史上只能靠线上事故发现的那类问题：漏 bump `?v=` 让浏览器拿旧 JS
配新字段（所有模型显示"未缓存"）、HTML 改了 id 而 JS 还在绑旧 id（按钮"点了没反应"）、
以及把密钥写进浏览器存储。没有引入 JS 测试框架，这几条用源码结构就能钉住。
"""
from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SCRIPT = (FRONTEND / "script.js").read_text(encoding="utf-8")
PAGE = (FRONTEND / "index.html").read_text(encoding="utf-8")

BOUND_ID = re.compile(r"bindListener\('([A-Za-z][A-Za-z0-9]*)'")
DECLARED_ID = re.compile(r'id="([A-Za-z][A-Za-z0-9]*)"')
SCRIPT_TAG = re.compile(r'<script src="script\.js\?v=(\d{8}-\d+)">')

# 旧版一个设置一个散装键，其中 llm_model_id 被所有 Provider 共用，
# 正是"切到自定义接口时填进 DeepSeek 模型名"的根因；迁移后不得再写回。
RETIRED_LOOSE_KEYS = (
    "llm_provider",
    "llm_model_id",
    "custom_base_url",
    "custom_model_name",
    "theme",
)


def test_all_browser_storage_writes_go_through_one_gate() -> None:
    assert SCRIPT.count("localStorage.setItem(") == 1
    gate = SCRIPT.split("function persistPrefs()", 1)[1].split("\n}", 1)[0]
    assert "localStorage.setItem(" in gate


def test_retired_loose_preference_keys_are_never_written() -> None:
    for key in RETIRED_LOOSE_KEYS:
        assert f"setItem('{key}'" not in SCRIPT


def test_script_tag_carries_a_cache_version() -> None:
    assert SCRIPT_TAG.search(PAGE), "script.js 必须带 ?v=YYYYMMDD-N（DEVELOPMENT.md 前端约定）"


def test_note_images_are_resolved_on_every_output_path() -> None:
    """截图只在预览里改路径、导出全是坏链，就是 1.4.1 修的那个问题。"""
    assert "(?:frames|images)" in SCRIPT, "预览要同时认 frames 与历史笔记里的 images"

    download_summary = SCRIPT.split("async function downloadSummary(", 1)[1].split("\n}\n", 1)[0]
    assert "inlineServedImages(" in download_summary, "HTML 导出必须内联截图"

    markdown_download = SCRIPT.split(
        "async function downloadMarkdownFile(", 1
    )[1].split("\n}\n", 1)[0]
    assert "serverFilename(" in markdown_download, "带截图时后端发的是 zip，后缀不能写死"


def test_browser_side_downloads_are_named_after_the_note() -> None:
    """txt / html / json / png / srt 由浏览器自己拼文件名，只能取后端下发的下载名。

    写死 `video_summary_时间戳` 时，同一视频逐 P 导出的几份纯文本除了时间戳
    全都一样，用户根本分不清哪份是哪个 P。
    """
    assert "function noteFileName(" in SCRIPT
    for name in (
        "downloadSummary",
        "downloadSrtFile",
        "exportSummaryImage",
        "downloadCanvasPages",
    ):
        body = SCRIPT.split(f"function {name}(", 1)[1].split("\n}\n", 1)[0]
        assert "noteFileName(" in body, f"{name} 里的下载没取后端下发的文件名"
    assert SCRIPT.count("task.download_name") == 2, "打开任务与轮询完成两条路都要带上下载名"


def test_bili_credentials_card_tells_the_machine_store_truth() -> None:
    """B 站凭据现在扫码即自动存本机：卡片不能再写「只保留到页面关闭」，判「有没有凭据」
    也不能只看输入框——本机存过时后端会自己兜住那份，页面不该再劝人重扫。

    群测原话是「能不能让登录态保存久一点」，而本机那份一直是长期加密存的，缺的只是
    网页端从来没写入、也从来没显示。
    """
    assert "凭据只保留到页面关闭" not in PAGE, "这句承诺已经过期，会把自动落盘藏起来"
    body = SCRIPT.split("function hasBiliCredentials(", 1)[1].split("\n}\n", 1)[0]
    assert "biliSavedMask" in body, "本机存过凭据时不能判成「没有凭据」"
    assert "refreshBiliSavedState();" in SCRIPT, "开页面没读本机凭据状态，提示条会一直劝人扫码"
    assert "biliClearSavedBtn" in PAGE, "自动落盘就得同时给一条清除入口"


def test_srt_export_is_rendered_by_the_backend() -> None:
    """字幕必须由后端从分段渲染，前端不许拿手里那份 markdown 自己拼。

    `transcript.md` 与 `currentMarkdown` 里的时间戳是整秒，前端现拼 SRT 会把每条
    字幕的毫秒抹平——Issue #4 报告人点名"写错了在播放器里表现为字幕整体偏移"，
    而分段只有后端那份带毫秒精度。
    """
    assert '<option value="srt">' in PAGE, "导出菜单里没有字幕这一项"
    assert "format === 'srt'" in SCRIPT, "菜单选了字幕却没走字幕那条路"
    body = SCRIPT.split("function downloadSrtFile(", 1)[1].split("\n}\n", 1)[0]
    assert "output_format=srt" in body
    assert "currentMarkdown" not in body, "SRT 不能由前端那份整秒 markdown 现拼"


def test_completed_notice_leads_with_the_titled_file() -> None:
    """群测原话"能不能用视频标题命名，现在都叫 transcript.md"——根因不是命名没做，是主行
    摆的是 UUID 任务目录，用户点进去只看到固定名的中间产物，于是以为那就是产物名。

    有归档时主行必须是带标题的归档文件；任务目录降成补充信息（音频与抽帧在里面，不能不告诉
    用户），归档失败时退回原样。
    """
    body = SCRIPT.split("async function showResult(", 1)[1].split("\n}\n", 1)[0]
    assert "archivedPath || outputDirectory" in body, "主行没优先取归档文件"
    assert "taskDirNotice" in body, "任务目录被整条删掉了（音频与抽帧在里面）"
    assert "outputPathLabel" in body, "主行换了含义，引导语必须跟着变，否则读起来是错的"


def test_retired_effort_option_is_mapped_not_silently_reset() -> None:
    """下拉里去了「最大」这一档，但存过它的浏览器偏好不能静默变成"自动"。

    那个 select 的值就是提交给后端的 reasoning_effort，静默改档等于替用户决定跑什么。
    偏好迁移与读取显示两处都要映射，只写一处就会退回 auto——第一版就漏了读取那处，
    是浏览器里点出来的。
    """
    assert "next.global.reasoning_effort === 'max'" in SCRIPT, "迁移路径没映射 max"
    assert "prefs.global.reasoning_effort === 'max'" in SCRIPT, "读取显示路径没映射 max"
    assert '<option value="max">' not in PAGE, "「最大」不该再出现在下拉里"


def test_every_bound_element_id_exists_in_the_page() -> None:
    missing = sorted(set(BOUND_ID.findall(SCRIPT)) - set(DECLARED_ID.findall(PAGE)))
    assert not missing, f"script.js 绑定了页面里不存在的元素：{missing}"


def _builtin_providers() -> dict[str, dict[str, str]]:
    config = SCRIPT.split("const PROVIDER_CONFIG = ", 1)[1]
    found: dict[str, dict[str, str]] = {}
    for provider in ("deepseek", "openai", "glm", "qwen", "moonshot"):
        section = config.split(f"\n    {provider}: {{", 1)[1].split("\n    }},", 1)[0]
        found[provider] = {
            "baseUrl": re.search(r"baseUrl: '([^']*)'", section).group(1),
            "defaultModel": re.search(r"defaultModel: '([^']*)'", section).group(1),
        }
    return found


def test_builtin_provider_defaults_agree_between_front_and_back() -> None:
    """前端下拉的默认档/地址必须与后端 ``PROVIDER_DEFAULTS`` 是同一个值。

    两处各写一份（前端要显示名字，后端要兜住不带 model 的请求，例如 MCP 与 skill），
    换模型时只改一边就会出现"页面上选的是新模型、实际请求打到旧 ID"这种没人看得见
    的错——1.4.2 换 DeepSeek/GLM/Qwen 档位时就是这个形状。
    """
    from backend.llm_summarizer import PROVIDER_DEFAULTS

    for provider, fields in _builtin_providers().items():
        base_url, model = PROVIDER_DEFAULTS[provider]
        assert fields["baseUrl"] == base_url, f"{provider} 接口地址两边不一致"
        assert fields["defaultModel"] == model, (
            f"{provider} 默认模型两边不一致：前端 {fields['defaultModel']} "
            f"≠ 后端 {model}"
        )
