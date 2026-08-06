"""本地配置文件存储：LLM 配置与 B 站凭据。

服务于 MCP 客户端与未来的手机 APP / 小程序：调用方无需每次提供 API Key 或
B 站凭据，由后端统一保管（明文保存在工作目录，仅本机可读，chmod 600）。

文件位置：
- workspace/llm_config.json      大模型服务配置（provider/model/api_key）
- workspace/bili_credentials.json B 站凭据（SESSDATA / bili_jct / buvid3）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LLM_CONFIG_FILE = "llm_config.json"
BILI_CREDENTIALS_FILE = "bili_credentials.json"


class ConfigStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

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
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _clear(self, filename: str) -> None:
        try:
            (self.workspace / filename).unlink()
        except FileNotFoundError:
            pass

    # ---- LLM 配置 ----

    def load_llm_config(self) -> dict[str, Any] | None:
        payload = self._read(LLM_CONFIG_FILE)
        if not payload or not payload.get("api_key"):
            return None
        return payload

    def save_llm_config(self, config: dict[str, Any]) -> None:
        self._write(LLM_CONFIG_FILE, config)

    def clear_llm_config(self) -> None:
        self._clear(LLM_CONFIG_FILE)

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
