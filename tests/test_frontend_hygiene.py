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


def test_every_bound_element_id_exists_in_the_page() -> None:
    missing = sorted(set(BOUND_ID.findall(SCRIPT)) - set(DECLARED_ID.findall(PAGE)))
    assert not missing, f"script.js 绑定了页面里不存在的元素：{missing}"
