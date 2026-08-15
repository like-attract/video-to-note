"""从 Chrome/Edge 导出 B 站 cookie（仅 SESSDATA/bili_jct/buvid3/buvid4）。

用法: python scripts/read_bili_cookie.py
输出: 默认只打印掩码；--reveal 打印明文（仅本机调试用）
"""
import argparse
import base64
import ctypes
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BILI_NAMES = {"SESSDATA", "bili_jct", "buvid3", "buvid4"}
PROFILES = [
    (r"C:\Users\fight\AppData\Local\Google\Chrome\User Data", "Chrome"),
    (r"C:\Users\fight\AppData\Local\Microsoft\Edge\User Data", "Edge"),
]


def dpapi_decrypt(blob: bytes) -> bytes:
    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.c_void_p)]

    blob_in = DataBlob(len(blob), ctypes.cast(ctypes.create_string_buffer(blob), ctypes.c_void_p))
    blob_out = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("DPAPI 解密失败")
    data = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.LocalFree(blob_out.pbData)
    return data


def get_aes_key(user_data: Path) -> bytes | None:
    local_state = user_data / "Local State"
    if not local_state.is_file():
        return None
    state = json.loads(local_state.read_text(encoding="utf-8"))
    enc = base64.b64decode(state["os_crypt"]["encrypted_key"])
    if not enc.startswith(b"DPAPI"):
        return None
    return dpapi_decrypt(enc[5:])


def decrypt_value(value: bytes, key: bytes) -> str:
    if value[:3] in (b"v10", b"v20"):
        value = value[3:]
    nonce, ciphertext = value[:12], value[12:]
    return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")


def extract(user_data: Path, reveal: bool) -> dict[str, str]:
    cookies_db = user_data / "Default" / "Network" / "Cookies"
    if not cookies_db.is_file():
        return {}
    key = get_aes_key(user_data)
    if not key:
        return {}
    rows = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "Cookies"
            shutil.copy2(cookies_db, copy)
            con = sqlite3.connect(copy)
            rows = con.execute(
                "SELECT name, value FROM cookies WHERE host_key LIKE '%bilibili.com'"
            ).fetchall()
            con.close()
    except PermissionError:
        # 浏览器运行时锁定数据库：immutable 直读（无锁）
        uri = f"file:{cookies_db.as_posix()}?immutable=1"
        con = sqlite3.connect(uri, uri=True)
        rows = con.execute(
            "SELECT name, value FROM cookies WHERE host_key LIKE '%bilibili.com'"
        ).fetchall()
        con.close()
    found: dict[str, str] = {}
    for name, value in rows:
        if name not in BILI_NAMES or not value:
            continue
        try:
            plain = decrypt_value(value.encode(), key)
        except Exception:
            continue
        found[name] = plain
        if reveal:
            shown = plain if len(plain) <= 24 else f"{plain[:20]}...{plain[-4:]}"
            print(f"  {name} = {shown}")
        else:
            print(f"  {name} = {'*' * 8} ({len(plain)} chars)")
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reveal", action="store_true", help="打印明文（仅本机调试）")
    args = ap.parse_args()
    for path, name in PROFILES:
        user_data = Path(path)
        if not user_data.is_dir():
            continue
        print(f"[{name}]")
        found = extract(user_data, args.reveal)
        if "SESSDATA" in found:
            print(f"  -> 找到可用会话: {name}")
            return


if __name__ == "__main__":
    main()
