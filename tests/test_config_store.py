"""config_store 测试：按接口地址保管的 API Key、旧配置迁移与 B 站凭据。

重点是几条安全性质：Key 只可能被取回给保存它的那个端点、对外视图永远不含密钥、
迁移只有在确认密钥已加密落盘后才抹掉明文。
"""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from backend import main, secret_box
from backend.config_store import (
    BILI_CREDENTIALS_FILE,
    LLM_CONFIG_FILE,
    LLM_KEYS_FILE,
    ConfigStore,
)

DEEPSEEK = "https://api.deepseek.com"
CORP = "https://gateway.corp.example/v1"


def test_put_and_resolve_key_by_endpoint(tmp_path) -> None:
    store = ConfigStore(tmp_path)

    assert store.list_keys() == []
    entry = store.put_key(
        provider="deepseek", base_url=DEEPSEEK, api_key="sk-123456", model="deepseek-v4-flash"
    )

    assert entry["has_key"] is True
    assert entry["api_key_masked"] == "sk-1****"
    assert entry["model"] == "deepseek-v4-flash"
    resolved = store.resolve_stored_key(model_type="deepseek", base_url=DEEPSEEK)
    assert resolved["api_key"] == "sk-123456"
    assert resolved["reason"] == ""


def test_endpoint_identity_ignores_case_slash_and_default_port(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.put_key(provider="custom", base_url="https://Gateway.Corp.example:443/v1/", api_key="sk-x")

    assert store.resolve_stored_key(model_type="custom", base_url=CORP)["api_key"] == "sk-x"


def test_builtin_provider_without_base_url_matches_official_host(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.put_key(provider="qwen", base_url="", api_key="sk-q")

    assert store.resolve_stored_key(model_type="qwen", base_url=None)["api_key"] == "sk-q"


def test_key_never_crosses_endpoints(tmp_path) -> None:
    """整案的核心性质：换了端点就不能借用本机另一把 Key，哪怕 Provider 名字看起来更"像"。"""
    store = ConfigStore(tmp_path)
    store.put_key(provider="deepseek", base_url=DEEPSEEK, api_key="sk-deepseek")

    other_host = store.resolve_stored_key(model_type="glm", base_url=CORP)
    same_provider_other_host = store.resolve_stored_key(model_type="deepseek", base_url=CORP)

    assert other_host["api_key"] == "" and other_host["reason"] == "no_saved_key"
    assert same_provider_other_host["api_key"] == ""
    assert same_provider_other_host["reason"] == "no_saved_key"


def test_unidentifiable_endpoint_never_resolves_a_key(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.put_key(provider="deepseek", base_url=DEEPSEEK, api_key="sk-x")

    assert store.resolve_stored_key(model_type="custom", base_url="")["reason"] == "unknown_endpoint"


def test_put_key_validates_input(tmp_path) -> None:
    store = ConfigStore(tmp_path)

    with pytest.raises(ValueError, match="API Key 为空"):
        store.put_key(provider="deepseek", base_url=DEEPSEEK, api_key="   ")
    with pytest.raises(ValueError, match="接口地址"):
        store.put_key(provider="custom", base_url="", api_key="sk-x")
    assert store.list_keys() == []


def _field_names(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _field_names(item)
    elif isinstance(value, list):
        for item in value:
            yield from _field_names(item)


def test_listed_view_and_file_never_expose_the_secret(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.put_key(provider="deepseek", base_url=DEEPSEEK, api_key="sk-super-secret-value")

    listed = store.list_keys()
    assert "sk-super-secret-value" not in json.dumps(listed)
    assert "api_key" not in set(_field_names(listed))
    assert "sk-super-secret-value" not in (tmp_path / LLM_KEYS_FILE).read_text(encoding="utf-8")


def test_same_host_is_updated_not_duplicated(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.put_key(provider="custom", base_url=CORP, api_key="sk-old", label="公司代理")
    store.put_key(provider="custom", base_url=CORP, api_key="sk-new", model="kimi-k2.6")

    entries = store.list_keys()
    assert len(entries) == 1
    assert entries[0]["label"] == "公司代理"
    assert entries[0]["model"] == "kimi-k2.6"
    assert store.resolve_stored_key(model_type="custom", base_url=CORP)["api_key"] == "sk-new"


def test_duplicate_hosts_in_a_hand_edited_file_collapse(tmp_path) -> None:
    (tmp_path / LLM_KEYS_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {"id": "e-1", "host": "a.com", "base_url": "https://a.com", "provider": "custom"},
                    {"id": "e-2", "host": "a.com", "base_url": "https://a.com", "provider": "custom"},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert len(ConfigStore(tmp_path).list_keys()) == 1


def test_delete_key_only_removes_the_named_endpoint(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    store.put_key(provider="deepseek", base_url=DEEPSEEK, api_key="sk-d")
    store.put_key(provider="custom", base_url=CORP, api_key="sk-c")

    assert store.delete_key(base_url=DEEPSEEK, model_type="deepseek") is True
    assert store.resolve_stored_key(model_type="deepseek", base_url=DEEPSEEK)["api_key"] == ""
    assert store.resolve_stored_key(model_type="custom", base_url=CORP)["api_key"] == "sk-c"
    assert store.delete_key(base_url="https://nothing.example", model_type="custom") is False


def test_corrupt_keys_file_is_reported_never_reset(tmp_path) -> None:
    """损坏时只报错：静默重置会把用户所有已保存的 Key 抹掉，而且他不知道为什么。"""
    (tmp_path / LLM_KEYS_FILE).write_text("{broken", encoding="utf-8")
    store = ConfigStore(tmp_path)

    assert store.keys_are_corrupt() is True
    assert store.list_keys() == []
    assert store.resolve_stored_key(model_type="deepseek")["reason"] == "corrupt_keys_file"
    with pytest.raises(ValueError, match="损坏"):
        store.put_key(provider="deepseek", base_url=DEEPSEEK, api_key="sk-x")
    assert (tmp_path / LLM_KEYS_FILE).read_text(encoding="utf-8") == "{broken"


def test_undecryptable_key_reports_reason(tmp_path, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise secret_box.SecretBoxError("undecryptable", "来自其他机器")

    store = ConfigStore(tmp_path)
    store.put_key(provider="deepseek", base_url=DEEPSEEK, api_key="sk-x")
    monkeypatch.setattr(secret_box, "unprotect", refuse)

    resolved = store.resolve_stored_key(model_type="deepseek", base_url=DEEPSEEK)

    assert resolved["api_key"] == ""
    assert resolved["reason"] == "undecryptable"
    # 列表仍要能显示"这里有一把自己解不开的 Key"，否则用户无从下手。
    assert store.list_keys()[0]["has_key"] is True


def test_legacy_config_migrates_then_scrubs_plaintext_key(tmp_path) -> None:
    (tmp_path / LLM_CONFIG_FILE).write_text(
        json.dumps({"model_type": "deepseek", "api_key": "sk-legacy123", "model": "deepseek-v4-flash"}),
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path)

    entries = store.list_keys()
    assert len(entries) == 1 and entries[0]["has_key"] is True
    assert store.resolve_stored_key(model_type="deepseek")["api_key"] == "sk-legacy123"

    scrubbed = json.loads((tmp_path / LLM_CONFIG_FILE).read_text(encoding="utf-8"))
    assert "api_key" not in scrubbed
    assert scrubbed["model_type"] == "deepseek"  # 文件与设置保留为面包屑，不静默删除
    assert "sk-legacy123" not in (tmp_path / LLM_CONFIG_FILE).read_text(encoding="utf-8")


def test_migration_never_drops_key_when_encryption_fails(tmp_path, monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise secret_box.SecretBoxError("dpapi_failed", "平台加密不可用")

    monkeypatch.setattr(secret_box, "protect", explode)
    (tmp_path / LLM_CONFIG_FILE).write_text(
        json.dumps({"model_type": "deepseek", "api_key": "sk-legacy123"}), encoding="utf-8"
    )
    store = ConfigStore(tmp_path)

    assert store.list_keys() == []
    assert "sk-legacy123" in (tmp_path / LLM_CONFIG_FILE).read_text(encoding="utf-8")
    assert not (tmp_path / LLM_KEYS_FILE).exists()


def test_migration_imports_settings_without_a_key(tmp_path) -> None:
    """旧版以"有没有 api_key"为读取门槛，导致无 Key 的旧配置连设置都不生效。"""
    (tmp_path / LLM_CONFIG_FILE).write_text(
        json.dumps({"model_type": "glm", "model": "glm-5.2"}), encoding="utf-8"
    )
    store = ConfigStore(tmp_path)

    entries = store.list_keys()
    assert entries[0]["model"] == "glm-5.2"
    assert entries[0]["has_key"] is False
    assert store.resolve_stored_key(model_type="glm")["reason"] == "no_saved_key"


def test_corrupt_legacy_config_is_ignored(tmp_path) -> None:
    (tmp_path / LLM_CONFIG_FILE).write_text("{broken", encoding="utf-8")

    store = ConfigStore(tmp_path)

    assert store.list_keys() == []
    assert store.keys_are_corrupt() is False


def test_stored_envelope_is_self_describing(tmp_path) -> None:
    """信封自带算法标识：同一格式能同时容纳 DPAPI 与明文回退，跨平台读取时能明确归类失败原因。"""
    store = ConfigStore(tmp_path)
    store.put_key(provider="deepseek", base_url=DEEPSEEK, api_key="sk-envelope-check")

    record = json.loads((tmp_path / LLM_KEYS_FILE).read_text(encoding="utf-8"))
    key = record["entries"][0]["key"]
    assert key["alg"] == secret_box.storage_backend()
    payload = base64.b64decode(key["ciphertext"])
    if key["alg"] == secret_box.ALG_PLAIN:
        assert payload == b"sk-envelope-check"
    else:
        assert b"sk-envelope-check" not in payload


def test_bili_credentials_roundtrip_and_clear(tmp_path) -> None:
    store = ConfigStore(tmp_path)

    assert store.load_bili_credentials() is None
    store.save_bili_credentials({"sessdata": "s1", "bili_jct": "j1", "buvid3": "b1"})
    assert store.load_bili_credentials() == {"sessdata": "s1", "bili_jct": "j1", "buvid3": "b1"}

    store.clear_bili_credentials()
    assert store.load_bili_credentials() is None
    (tmp_path / BILI_CREDENTIALS_FILE).write_text('{"sessdata": ""}', encoding="utf-8")
    assert store.load_bili_credentials() is None


def test_llm_key_endpoints_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    client = TestClient(main.app)

    empty = client.get("/api/llm-keys")
    assert empty.status_code == 200
    assert empty.json()["entries"] == []
    assert empty.json()["storage"]["algorithm"] in {secret_box.ALG_DPAPI, secret_box.ALG_PLAIN}

    saved = client.put(
        "/api/llm-keys",
        json={"provider": "custom", "base_url": CORP, "api_key": "sk-abc123", "label": "公司代理"},
    )
    assert saved.status_code == 200
    assert saved.json()["saved"] is True
    assert saved.json()["api_key_masked"] == "sk-a****"

    listed = client.get("/api/llm-keys").json()["entries"]
    assert [entry["label"] for entry in listed] == ["公司代理"]
    assert all("api_key" not in entry for entry in listed)

    matched = client.get("/api/llm-keys", params={"base_url": CORP, "provider": "custom"}).json()
    assert matched["match"]["saved"] is True
    missing = client.get("/api/llm-keys", params={"base_url": DEEPSEEK, "provider": "deepseek"}).json()
    assert missing["match"]["saved"] is False

    assert client.delete("/api/llm-keys", params={"base_url": CORP}).json() == {"removed": True}
    assert client.get("/api/llm-keys").json()["entries"] == []


def test_put_key_rejects_blank_secret(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    client = TestClient(main.app)

    response = client.put("/api/llm-keys", json={"provider": "deepseek", "base_url": DEEPSEEK, "api_key": " "})

    assert response.status_code == 400
    assert "API Key" in response.json()["detail"]


def test_legacy_llm_config_endpoints_still_answer_for_mcp(monkeypatch, tmp_path) -> None:
    """MCP 的老调用形状（保存一份、之后免传 Key）必须继续可用。"""
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    client = TestClient(main.app)

    assert client.get("/api/llm-config").json() == {"saved": False}

    saved = client.post(
        "/api/llm-config",
        json={"model_type": "qwen", "api_key": "sk-abc", "model": "qwen3.7-plus"},
    )
    assert saved.status_code == 200
    assert saved.json()["saved"] is True

    loaded = client.get("/api/llm-config").json()
    assert loaded["saved"] is True
    assert loaded["model_type"] == "qwen"
    assert loaded["api_key_masked"] == "sk-a****"
    assert "api_key" not in loaded

    assert client.delete("/api/llm-config").json()["saved"] is False
    assert client.get("/api/llm-config").json() == {"saved": False}


def test_bili_credentials_endpoints(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    client = TestClient(main.app)

    assert client.get("/api/bili-credentials").json() == {"saved": False}
    saved_bili = client.post(
        "/api/bili-credentials", json={"sessdata": "s2", "bili_jct": "j2", "buvid3": "b2"}
    )
    assert saved_bili.status_code == 200
    assert client.get("/api/bili-credentials").json() == {
        "saved": True,
        "sessdata_masked": "s2****",
        "has_bili_jct": True,
        "has_buvid3": True,
    }
    assert client.delete("/api/bili-credentials").json() == {"saved": False}
    assert client.get("/api/bili-credentials").json() == {"saved": False}
