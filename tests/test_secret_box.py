"""secret_box 测试：跨平台可验证的部分（信封格式、回退、写后校验、盐值文件）。

DPAPI 本身只在 Windows 上存在，Linux CI 跑不到，因此这里全部用例都强制走明文回退路径，
把"回退路径与加密路径共用同一套信封与校验逻辑"这件事钉住；DPAPI 真实往返只在
Windows 上跑，见文件末尾两个 skipif 用例与 DEVELOPMENT.md 的手工烟测。
"""
from __future__ import annotations

import base64
import sys

import pytest

from backend import secret_box
from backend.secret_box import ALG_DPAPI, ALG_PLAIN, SecretBoxError


@pytest.fixture(autouse=True)
def plaintext_backend(monkeypatch) -> None:
    """用例结论必须与运行平台无关：默认强制明文回退。"""
    monkeypatch.setattr(secret_box, "_STORAGE_BACKEND", ALG_PLAIN)


def test_protect_and_unprotect_roundtrip(tmp_path) -> None:
    envelope = secret_box.protect("sk-test-1234", entropy_file=tmp_path / "entropy.bin")

    assert envelope["alg"] == ALG_PLAIN
    assert envelope["saved_at"]
    assert secret_box.unprotect(envelope, entropy_file=tmp_path / "entropy.bin") == "sk-test-1234"


def test_plaintext_fallback_is_obviously_insecure(tmp_path) -> None:
    """回退路径不假装安全：密文就是明文本身，界面必须据此提示"本机不支持加密"。"""
    envelope = secret_box.protect("sk-leak", entropy_file=tmp_path / "entropy.bin")

    assert base64.b64decode(envelope["ciphertext"]) == b"sk-leak"


def test_empty_key_is_rejected(tmp_path) -> None:
    with pytest.raises(SecretBoxError) as error:
        secret_box.protect("   ", entropy_file=tmp_path / "entropy.bin")

    assert error.value.code == "empty_secret"


def test_unknown_algorithm_is_rejected(tmp_path) -> None:
    envelope = {"alg": "aes-but-not-really", "ciphertext": base64.b64encode(b"x").decode()}

    with pytest.raises(SecretBoxError) as error:
        secret_box.unprotect(envelope, entropy_file=tmp_path / "entropy.bin")

    assert error.value.code == "unsupported_alg"


def test_dpapi_envelope_cannot_be_read_off_windows(tmp_path) -> None:
    """跨机器/跨平台读到的 DPAPI 信封必须报"解不开"，而不是返回乱码当密钥用。"""
    envelope = {"alg": ALG_DPAPI, "ciphertext": base64.b64encode(b"not-really").decode()}

    with pytest.raises(SecretBoxError) as error:
        secret_box.unprotect(envelope, entropy_file=tmp_path / "entropy.bin")

    assert error.value.code == "undecryptable"


def test_malformed_envelope_is_rejected(tmp_path) -> None:
    entropy = tmp_path / "entropy.bin"

    with pytest.raises(SecretBoxError) as missing:
        secret_box.unprotect({"alg": ALG_PLAIN, "ciphertext": ""}, entropy_file=entropy)
    with pytest.raises(SecretBoxError) as broken:
        secret_box.unprotect({"alg": ALG_PLAIN, "ciphertext": "!!!"}, entropy_file=entropy)

    assert missing.value.code == "malformed"
    assert broken.value.code == "malformed"


def test_write_time_verification_refuses_bad_envelope(tmp_path, monkeypatch) -> None:
    """加密原语"看起来成功但回读不一致"时，绝不能拿坏信封覆盖掉旧 Key。"""
    monkeypatch.setattr(secret_box, "unprotect", lambda *_args, **_kwargs: "wrong-value")

    with pytest.raises(SecretBoxError) as error:
        secret_box.protect("sk-keep-me", entropy_file=tmp_path / "entropy.bin")

    assert error.value.code == "verify_failed"


def test_verification_reports_unreadable_envelope(tmp_path, monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise SecretBoxError("dpapi_failed", "boom")

    monkeypatch.setattr(secret_box, "unprotect", explode)

    with pytest.raises(SecretBoxError) as error:
        secret_box.protect("sk-keep-me", entropy_file=tmp_path / "entropy.bin")

    assert error.value.code == "verify_failed"


def test_entropy_file_is_created_once_and_reused(tmp_path) -> None:
    path = tmp_path / secret_box.ENTROPY_FILE_NAME

    first = secret_box._read_entropy(path)
    second = secret_box._read_entropy(path)

    assert len(first) == secret_box.ENTROPY_BYTES
    assert first == second == path.read_bytes()


def test_truncated_entropy_file_is_never_rotated(tmp_path) -> None:
    """静默重建盐值 = 已保存的 Key 全部作废，用户只会看到"Key 突然解不开"。"""
    path = tmp_path / secret_box.ENTROPY_FILE_NAME
    path.write_bytes(b"short")

    with pytest.raises(SecretBoxError) as error:
        secret_box._read_entropy(path)

    assert error.value.code == "entropy_invalid"


def test_detect_backend_respects_disable_switch(monkeypatch) -> None:
    monkeypatch.setattr(secret_box, "IS_WINDOWS", True)
    monkeypatch.setenv(secret_box.DISABLE_DPAPI_ENV, "1")

    assert secret_box._detect_backend() == ALG_PLAIN


def test_detect_backend_stays_plain_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(secret_box, "IS_WINDOWS", False)
    monkeypatch.delenv(secret_box.DISABLE_DPAPI_ENV, raising=False)

    assert secret_box._detect_backend() == ALG_PLAIN


def test_mask_secret_keeps_existing_format() -> None:
    assert secret_box.mask_secret("sk-abcdef123456") == "sk-a****"
    assert secret_box.mask_secret("") == ""


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 只在 Windows 上存在")
def test_dpapi_roundtrip_on_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(secret_box, "_STORAGE_BACKEND", ALG_DPAPI)

    envelope = secret_box.protect("sk-windows-secret", entropy_file=tmp_path / "entropy.bin")

    assert envelope["alg"] == ALG_DPAPI
    assert secret_box.unprotect(envelope, entropy_file=tmp_path / "entropy.bin") == (
        "sk-windows-secret"
    )
    assert base64.b64decode(envelope["ciphertext"]) != b"sk-windows-secret"


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 只在 Windows 上存在")
def test_dpapi_key_does_not_survive_a_different_entropy(tmp_path, monkeypatch) -> None:
    """换掉盐值文件（等价于把配置拷到另一台机器）后必须解不开，而不是解出垃圾。"""
    monkeypatch.setattr(secret_box, "_STORAGE_BACKEND", ALG_DPAPI)
    entropy = tmp_path / "a.bin"

    envelope = secret_box.protect("sk-portable", entropy_file=entropy)
    (tmp_path / "b.bin").write_bytes(entropy.read_bytes()[::-1])

    with pytest.raises(SecretBoxError):
        secret_box.unprotect(envelope, entropy_file=tmp_path / "b.bin")
