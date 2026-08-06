"""config_store 测试：LLM 配置与 B 站凭据的本地持久化。"""
import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.config_store import ConfigStore


def test_config_store_roundtrip_and_clear(tmp_path) -> None:
    store = ConfigStore(tmp_path)

    assert store.load_llm_config() is None
    assert store.load_bili_credentials() is None

    store.save_llm_config({"model_type": "deepseek", "api_key": "sk-123"})
    store.save_bili_credentials({"sessdata": "s1", "bili_jct": "j1", "buvid3": "b1"})

    assert store.load_llm_config() == {"model_type": "deepseek", "api_key": "sk-123"}
    assert store.load_bili_credentials() == {"sessdata": "s1", "bili_jct": "j1", "buvid3": "b1"}

    store.clear_llm_config()
    store.clear_bili_credentials()
    assert store.load_llm_config() is None
    assert store.load_bili_credentials() is None


def test_config_store_ignores_corrupt_or_empty_files(tmp_path) -> None:
    store = ConfigStore(tmp_path)
    (tmp_path / "llm_config.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "bili_credentials.json").write_text('{"sessdata": ""}', encoding="utf-8")

    assert store.load_llm_config() is None
    assert store.load_bili_credentials() is None


def test_api_endpoints_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "WORKSPACE_DIR", tmp_path)
    from backend.main import config_store

    monkeypatch.setattr(main, "config_store", ConfigStore(tmp_path))
    client = TestClient(main.app)

    assert client.get("/api/llm-config").json() == {"saved": False}
    assert client.get("/api/bili-credentials").json() == {"saved": False}

    saved = client.post(
        "/api/llm-config",
        json={"model_type": "qwen", "api_key": "sk-abc", "model": "qwen3.7-plus"},
    )
    assert saved.status_code == 200
    assert saved.json()["saved"] is True

    loaded = client.get("/api/llm-config").json()
    assert loaded["saved"] is True
    assert loaded["model_type"] == "qwen"
    assert loaded["api_key"] == "sk-abc"

    saved_bili = client.post(
        "/api/bili-credentials", json={"sessdata": "s2", "bili_jct": "j2", "buvid3": "b2"}
    )
    assert saved_bili.status_code == 200
    loaded_bili = client.get("/api/bili-credentials").json()
    assert loaded_bili == {"saved": True, "sessdata": "s2", "bili_jct": "j2", "buvid3": "b2"}

    assert client.delete("/api/llm-config").json() == {"saved": False}
    assert client.delete("/api/bili-credentials").json() == {"saved": False}
    assert client.get("/api/llm-config").json() == {"saved": False}
