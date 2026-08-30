"""本地配置存储：按接口地址保管的模型 API Key 与 B 站凭据。

服务于 MCP 客户端与网页端「保存到本机」：调用方无需每次提供 API Key，
由后端统一保管；B 站凭据同理。

文件位置：
- workspace/llm_keys.json          按接口地址（host + path）保管的 API Key，
                                   Windows 下用 DPAPI 加密，其他平台明文并如实上报存储方式
- workspace/llm_config.json        旧版单份明文配置：仅作一次性迁移的来源，迁移后只留设置与说明
- workspace/bili_credentials.json  B 站凭据（SESSDATA / bili_jct / buvid3）

三个文件都在工作目录（workspace/）下，已被 .gitignore 整目录排除，不会进入 Git 仓库；
请在开发中保持该约定，不要把它们放到仓库目录内。

安全约定：Key 以"接口地址"为身份保管，只有请求的目标地址与保存时一致才会复用，
因此切换 Provider / 换网关时不可能把 A 站点的 Key 发给 B 站点。地址无法识别
（空、非 http/https）时一律拒绝复用。所有对外读取只返回掩码，绝不返回密钥原文。
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import secret_box
from .llm_summarizer import default_base_url, normalize_endpoint_host

LOGGER = logging.getLogger(__name__)

LLM_KEYS_FILE = "llm_keys.json"
LLM_CONFIG_FILE = "llm_config.json"
BILI_CREDENTIALS_FILE = "bili_credentials.json"
KEYS_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConfigStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        # 现有调用点都在同一事件循环上、方法内部无 await，事实上已串行；
        # 这把锁是为了让"读-改-写"在任何调用方式下都成立。
        self._lock = threading.RLock()

    def _read(self, filename: str) -> dict[str, Any] | None:
        path = self.workspace / filename
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write(self, filename: str, payload: dict[str, Any]) -> None:
        path = self.workspace / filename
        self.workspace.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)

    def _clear(self, filename: str) -> None:
        try:
            (self.workspace / filename).unlink()
        except FileNotFoundError:
            pass

    def _entropy_path(self) -> Path:
        return self.workspace / secret_box.ENTROPY_FILE_NAME

    # ---- 按接口地址保管的 API Key ----

    def keys_are_corrupt(self) -> bool:
        """密钥文件存在但解析不了。损坏时只报错，绝不自动重置（那会静默清空用户的 Key）。"""
        _, corrupt = self._read_keys()
        return corrupt

    def list_keys(self) -> list[dict[str, Any]]:
        """公开视图，不含任何密钥材料（掩码是保存时就写死的）。"""
        entries, _ = self._read_keys()
        return [self._public(entry) for entry in entries]

    def most_recent_entry(self) -> dict[str, Any] | None:
        entries, _ = self._read_keys()
        if not entries:
            return None
        return self._public(max(entries, key=lambda item: str(item.get("updated_at") or "")))

    def put_key(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        label: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """把某个接口地址的 Key 加密保存到本机。"""
        secret = (api_key or "").strip()
        if not secret:
            raise ValueError("API Key 为空，未保存到本机")
        identity = self._endpoint(provider, base_url)
        envelope = secret_box.protect(secret, entropy_file=self._entropy_path())
        envelope["masked"] = secret_box.mask_secret(secret)
        with self._lock:
            entries, corrupt = self._read_keys()
            if corrupt:
                raise ValueError("本机密钥文件已损坏，请先清除本机配置后重试")
            public = self._upsert(entries, identity=identity, label=label, model=model, envelope=envelope)
            self._write(LLM_KEYS_FILE, {"version": KEYS_SCHEMA_VERSION, "entries": entries})
        return public

    def delete_key(self, *, base_url: str | None = None, model_type: str = "") -> bool:
        identity_host = normalize_endpoint_host(base_url or "", model_type)
        if not identity_host:
            return False
        with self._lock:
            entries, corrupt = self._read_keys()
            if corrupt:
                return False
            remaining = [entry for entry in entries if entry.get("host") != identity_host]
            if len(remaining) == len(entries):
                return False
            self._write(LLM_KEYS_FILE, {"version": KEYS_SCHEMA_VERSION, "entries": remaining})
        return True

    def resolve_stored_key(self, *, model_type: str = "", base_url: str | None = None) -> dict[str, Any]:
        """按接口地址取回本机 Key；解不到时返回 ``reason`` 说明原因，绝不退回到别的地址的 Key。"""
        host = normalize_endpoint_host(base_url or "", model_type)
        if not host:
            return {"api_key": "", "reason": "unknown_endpoint"}
        entries, corrupt = self._read_keys()
        if corrupt:
            return {"api_key": "", "reason": "corrupt_keys_file"}
        entry = _entry_by_host(entries, host)
        if not entry or not isinstance(entry.get("key"), dict):
            return {"api_key": "", "reason": "no_saved_key", "host": host}
        try:
            api_key = secret_box.unprotect(entry["key"], entropy_file=self._entropy_path())
        except secret_box.SecretBoxError as error:
            LOGGER.warning("本机 Key 解密失败（%s）：%s", error.code, error)
            return {
                "api_key": "",
                "reason": error.code,
                "message": str(error),
                "host": host,
                "entry": self._public(entry),
            }
        return {"api_key": api_key, "reason": "", "host": host, "entry": self._public(entry)}

    @staticmethod
    def _endpoint(provider: str, base_url: str) -> dict[str, str]:
        provider_slug = (provider or "").strip().lower()
        url = (base_url or "").strip() or default_base_url(provider_slug)
        host = normalize_endpoint_host(url, provider_slug)
        if not host:
            raise ValueError("接口地址无法识别，未保存到本机")
        return {"provider": provider_slug, "base_url": url, "host": host}

    def _upsert(
        self,
        entries: list[dict[str, Any]],
        *,
        identity: dict[str, str],
        label: str | None,
        model: str | None,
        envelope: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = _now()
        entry = _entry_by_host(entries, identity["host"])
        if entry is None:
            entry = {
                "id": f"e-{uuid.uuid4().hex[:8]}",
                "host": identity["host"],
                "created_at": now,
            }
            entries.append(entry)
        entry["provider"] = identity["provider"] or str(entry.get("provider") or "")
        entry["base_url"] = identity["base_url"] or str(entry.get("base_url") or "")
        entry["label"] = (label or "").strip() or str(entry.get("label") or identity["host"])
        if model is not None:
            entry["model"] = str(model).strip() or None
        if envelope is not None:
            entry["key"] = envelope
        entry["updated_at"] = now
        return self._public(entry)

    def _read_keys(self) -> tuple[list[dict[str, Any]], bool]:
        path = self.workspace / LLM_KEYS_FILE
        if not path.is_file():
            return self._import_legacy_config(), False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], True
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            return [], True
        entries = [entry for entry in payload["entries"] if _is_usable_entry(entry)]
        return _dedupe(entries), False

    def _public(self, entry: dict[str, Any]) -> dict[str, Any]:
        key = entry.get("key") if isinstance(entry.get("key"), dict) else {}
        return {
            "id": str(entry.get("id") or ""),
            "host": str(entry.get("host") or ""),
            "base_url": str(entry.get("base_url") or ""),
            "provider": str(entry.get("provider") or ""),
            "label": str(entry.get("label") or entry.get("host") or ""),
            "model": entry.get("model") or None,
            "has_key": bool(key),
            "api_key_masked": str(key.get("masked") or ""),
            "key_algorithm": str(key.get("alg") or ""),
            "key_saved_at": str(key.get("saved_at") or ""),
            "updated_at": str(entry.get("updated_at") or ""),
        }

    # ---- 旧版单份配置：一次性迁移 ----

    def _import_legacy_config(self) -> list[dict[str, Any]]:
        """一次性迁移旧版明文配置：能加密才抹掉明文，加密失败就什么都不动。"""
        legacy = self._read(LLM_CONFIG_FILE)
        if not legacy:
            return []
        try:
            identity = self._endpoint(
                str(legacy.get("model_type") or ""), str(legacy.get("base_url") or "")
            )
        except ValueError:
            return []
        api_key = str(legacy.get("api_key") or "").strip()
        envelope: dict[str, Any] | None = None
        if api_key:
            try:
                envelope = secret_box.protect(api_key, entropy_file=self._entropy_path())
                envelope["masked"] = secret_box.mask_secret(api_key)
            except secret_box.SecretBoxError as error:
                LOGGER.warning("旧配置密钥加密失败，保留 %s 不迁移：%s", LLM_CONFIG_FILE, error)
                return []
        entries: list[dict[str, Any]] = []
        self._upsert(
            entries,
            identity=identity,
            label="已迁移配置",
            model=legacy.get("model"),
            envelope=envelope,
        )
        self._write(LLM_KEYS_FILE, {"version": KEYS_SCHEMA_VERSION, "entries": entries})
        if envelope is not None and not self._legacy_key_moved(identity["host"], api_key):
            # 新文件没读回同一把 Key 就不碰旧文件：宁可留一份明文，也不能丢用户的 Key。
            LOGGER.warning("迁移校验未通过，保留 %s 原文", LLM_CONFIG_FILE)
            return entries
        if envelope is not None:
            self._scrub_legacy(legacy)
        return entries

    def _legacy_key_moved(self, host: str, api_key: str) -> bool:
        entries, corrupt = self._read_keys()
        if corrupt:
            return False
        entry = _entry_by_host(entries, host)
        if not entry or not isinstance(entry.get("key"), dict):
            return False
        try:
            return secret_box.unprotect(entry["key"], entropy_file=self._entropy_path()) == api_key
        except secret_box.SecretBoxError:
            return False

    def _scrub_legacy(self, legacy: dict[str, Any]) -> None:
        scrubbed = {key: value for key, value in legacy.items() if key != "api_key"}
        scrubbed["note"] = f"api_key 已迁移并加密保存到 {LLM_KEYS_FILE}（本文件不再含密钥）"
        self._write(LLM_CONFIG_FILE, scrubbed)

    # ---- B 站凭据 ----

    def load_bili_credentials(self) -> dict[str, str] | None:
        payload = self._read(BILI_CREDENTIALS_FILE)
        if not payload or not payload.get("sessdata"):
            return None
        return {
            "sessdata": str(payload["sessdata"]),
            "bili_jct": str(payload.get("bili_jct") or ""),
            "buvid3": str(payload.get("buvid3") or ""),
        }

    def save_bili_credentials(self, credentials: dict[str, str]) -> None:
        self._write(BILI_CREDENTIALS_FILE, credentials)

    def clear_bili_credentials(self) -> None:
        self._clear(BILI_CREDENTIALS_FILE)


def _is_usable_entry(entry: Any) -> bool:
    return isinstance(entry, dict) and bool(str(entry.get("host") or "")) and bool(str(entry.get("id") or ""))


def _dedupe(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 host 只保留最后写入的一条：手工编辑或目录合并都不应产生歧义匹配。"""
    by_host: dict[str, dict[str, Any]] = {}
    for entry in entries:
        by_host[str(entry["host"])] = entry
    return list(by_host.values())


def _entry_by_host(entries: list[dict[str, Any]], host: str) -> dict[str, Any] | None:
    for entry in entries:
        if str(entry.get("host") or "") == host:
            return entry
    return None
