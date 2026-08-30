"""本机密钥的静态加密：Windows DPAPI + 本机盐值，非 Windows 回退明文。

只加密密钥本身，不加密整份配置文档：provider / base_url / model 不是秘密
（它们本来就会写进任务日志），这样换机器导致密钥解不开时，用户看到的仍然是
有名字的接口列表，只有 Key 变红，而不是整页空白。

威胁模型（诚实版）：DPAPI 绑定当前 Windows 用户，再叠一个只存在于本机的随机盐值文件，
把威胁门槛从"以同一用户身份运行的任意进程"抬高到"能读取 workspace 目录的进程"。
它**不**防御：同用户下同时读到两个文件的恶意进程，以及你把整个 workspace 交给别人。
需要真正隔离时，请不要保存 Key，改用页面临时输入。

非 Windows（开发机 / CI）没有 DPAPI，回退明文保存并通过 `is_secure_storage()`
如实上报，由界面和文档标注"本机不支持加密"。
"""
from __future__ import annotations

import base64
import ctypes
import logging
import os
import secrets
import sys
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

ALG_DPAPI = "dpapi-cryptprotect-v1"
ALG_PLAIN = "plain-v1"
ENTROPY_FILE_NAME = ".profile_entropy.bin"
ENTROPY_MIN_BYTES = 16
ENTROPY_BYTES = 32
# 杀软/企业策略干扰 DPAPI 时的逃生口；命名对齐仓库既有的 VIDEOTONOTES_* 变量。
DISABLE_DPAPI_ENV = "VIDEOTONOTES_DISABLE_DPAPI"
IS_WINDOWS = sys.platform == "win32"
TRUTHY = {"1", "true", "yes", "on"}


class SecretBoxError(RuntimeError):
    """加密层能明确归类的失败，`code` 供上层映射成用户可读提示。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def storage_backend() -> str:
    return _STORAGE_BACKEND


def is_secure_storage() -> bool:
    return _STORAGE_BACKEND == ALG_DPAPI


def mask_secret(value: str) -> str:
    """只暴露前 4 个字符，与既有的 `/api/llm-config` 掩码格式保持一致。"""
    if not value:
        return ""
    return f"{value[:4]}****"


def protect(plaintext: str, *, entropy_file: Path) -> dict[str, Any]:
    if not plaintext.strip():
        raise SecretBoxError("empty_secret", "API Key 为空，未写入本机")
    data = plaintext.encode("utf-8")
    if storage_backend() == ALG_DPAPI:
        entropy = _read_entropy(entropy_file)
        envelope: dict[str, Any] = {
            "alg": ALG_DPAPI,
            "ciphertext": base64.b64encode(_dpapi_protect(data, entropy)).decode("ascii"),
        }
    else:
        envelope = {"alg": ALG_PLAIN, "ciphertext": base64.b64encode(data).decode("ascii")}
    envelope["saved_at"] = datetime.now(timezone.utc).isoformat()
    # 写后即读：密文损坏或平台异常在保存当场就暴露，绝不拿一个解不开的信封覆盖旧 Key。
    try:
        verified = unprotect(envelope, entropy_file=entropy_file)
    except SecretBoxError as exc:
        raise SecretBoxError("verify_failed", f"本机加密结果无法回读（{exc}），已保留原有 Key") from exc
    if verified != plaintext:
        raise SecretBoxError("verify_failed", "本机加密结果校验不一致，已保留原有 Key")
    return envelope


def unprotect(envelope: dict[str, Any], *, entropy_file: Path) -> str:
    algorithm = str(envelope.get("alg") or "")
    raw = str(envelope.get("ciphertext") or "")
    if not raw:
        raise SecretBoxError("malformed", "密钥记录缺少密文")
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception as exc:  # binascii.Error 等多种子类型，统一归类
        raise SecretBoxError("malformed", "密钥记录格式无法解析") from exc
    if algorithm == ALG_PLAIN:
        return _decode(data)
    if algorithm == ALG_DPAPI:
        if storage_backend() != ALG_DPAPI:
            raise SecretBoxError(
                "undecryptable",
                "该 Key 由 Windows 机器加密，当前系统无法解密，请重新填写",
            )
        return _decode(_dpapi_unprotect(data, _read_entropy(entropy_file)))
    raise SecretBoxError("unsupported_alg", f"未知的密钥加密方式：{algorithm}")


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretBoxError("undecryptable", "密钥无法解密（可能来自其他机器或 Windows 账户）") from exc


def _read_entropy(path: Path) -> bytes:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        data = b""
    except OSError as exc:
        raise SecretBoxError("entropy_unavailable", f"无法读取本机加密盐值：{exc}") from exc
    if data:
        if len(data) < ENTROPY_MIN_BYTES:
            # 盐值被截断时绝不静默重新生成：那会让已保存的密钥全部作废，
            # 而用户看到的只是"Key 突然解不开"。
            raise SecretBoxError("entropy_invalid", "本机加密盐值文件已损坏，请删除后重新保存 Key")
        return data
    data = secrets.token_bytes(ENTROPY_BYTES)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)
    except OSError as exc:
        raise SecretBoxError("entropy_unavailable", f"无法创建本机加密盐值：{exc}") from exc
    return data


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


_CRYPTPROTECT_UI_FORBIDDEN = 0x01
# 刻意不加 CRYPTPROTECT_LOCAL_MACHINE：机器级作用域会让同机任意用户（含 SYSTEM
# 上的服务）都能解密，共享工作站上等于没有保护。代价是 Key 不随 workspace 搬家。
_CRYPTPROTECT_FLAGS = wintypes.DWORD(_CRYPTPROTECT_UI_FORBIDDEN)


def _make_blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    # 必须同时返回 buffer：ctypes 的缓冲区一旦被回收，blob 里的指针就悬空。
    return blob, buffer


def _dpapi() -> Any:
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    # 不显式声明 argtypes/restype 时，ctypes 会按 int 推断，x64 上 DWORD 长度会被
    # 静默截断——这是 DPAPI-via-ctypes 最经典的坑。
    pointer_signature = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.argtypes = pointer_signature
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = pointer_signature
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    return crypt32


def _dpapi_call(function: Any, data: bytes, entropy: bytes) -> bytes:
    blob, _input_guard = _make_blob(data)
    secret, _entropy_guard = _make_blob(entropy)
    output = _DataBlob()
    ok = function(ctypes.byref(blob), "VideoToNo", ctypes.byref(secret), None, None, _CRYPTPROTECT_FLAGS, ctypes.byref(output))
    if not ok:
        raise SecretBoxError("dpapi_failed", f"Windows 加密调用失败（错误码 {ctypes.GetLastError()}）")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        # 输出缓冲区由系统分配，必须用 LocalFree 归还；用错分配器会破坏堆。
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(output.pbData)


def _dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    return _dpapi_call(_dpapi().CryptProtectData, data, entropy)


def _dpapi_unprotect(data: bytes, entropy: bytes) -> bytes:
    return _dpapi_call(_dpapi().CryptUnprotectData, data, entropy)


def _detect_backend() -> str:
    if not IS_WINDOWS:
        return ALG_PLAIN
    if os.environ.get(DISABLE_DPAPI_ENV, "").strip().lower() in TRUTHY:
        LOGGER.warning("已通过 %s 关闭 DPAPI，本机 API Key 将以明文保存", DISABLE_DPAPI_ENV)
        return ALG_PLAIN
    try:
        canary = "videotonotes-probe"
        entropy = secrets.token_bytes(ENTROPY_BYTES)
        sealed = _dpapi_protect(canary.encode("utf-8"), entropy)
        if _dpapi_unprotect(sealed, entropy).decode("utf-8") != canary:
            raise SecretBoxError("verify_failed", "DPAPI 自检未通过")
    except Exception as exc:  # 任何平台异常都退回明文，不能让配置页打不开
        LOGGER.warning("DPAPI 不可用，本机 API Key 将以明文保存：%s", exc)
        return ALG_PLAIN
    return ALG_DPAPI


# 进程内探测一次：避免一半记录加密、一半明文。
_STORAGE_BACKEND = _detect_backend()
