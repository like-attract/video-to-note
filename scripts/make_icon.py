"""从 sources/icon.png 生成多尺寸 sources/icon.ico。

用法：python scripts/make_icon.py [源PNG] [输出ICO]
默认：sources/icon.png -> sources/icon.ico

Windows 资源管理器按显示尺寸取 ico 内对应帧；只放一帧 256x256 时，
小图标（桌面/任务栏）会从 256 大幅缩放，透明边缘容易渲染出白边。
因此生成标准多尺寸集合（16/24/32/48/64/128/256），全部保留 alpha：
- 256px 帧：PNG 编码（ICO 规范支持）
- 小尺寸帧：32bit BMP（带 alpha 通道）

注：Pillow 的 ICO 保存不支持多帧（sizes 参数已失效），故手动构造。
"""
from __future__ import annotations

import struct
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = BASE_DIR / "sources" / "icon.png"
DEFAULT_TARGET = BASE_DIR / "sources" / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _encode_bmp32(frame: Image.Image) -> bytes:
    """RGBA 图像 -> 32bit BMP 像素数据（BITMAPINFOHEADER + 自下而上 BGRA，无 AND mask）。"""
    width, height = frame.size
    header = struct.pack(
        "<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, width * height * 4, 0, 0, 0, 0
    )
    pixels = bytearray(frame.tobytes())  # RGBA 顺序
    for index in range(0, len(pixels), 4):
        pixels[index], pixels[index + 2] = pixels[index + 2], pixels[index]  # RGBA -> BGRA
    # BMP 要求自下而上行序，而 frame.tobytes() 是自上而下；不翻转图标会上下颠倒
    stride = width * 4
    pixels = b"".join(
        bytes(pixels[row * stride:(row + 1) * stride]) for row in range(height - 1, -1, -1)
    )
    return header + pixels


def _encode_png(frame: Image.Image) -> bytes:
    buffer = BytesIO()
    frame.save(buffer, format="PNG")
    return buffer.getvalue()


def build_ico(frames: list[tuple[int, Image.Image]]) -> bytes:
    encoded: list[tuple[str, bytes]] = []
    for size, frame in frames:
        encoded.append(("png" if size >= 256 else "bmp", _encode_png(frame) if size >= 256 else _encode_bmp32(frame)))

    count = len(encoded)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries: list[bytes] = []
    for (_, data), (size, _) in zip(encoded, frames):
        dimension = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    return header + b"".join(entries) + b"".join(data for _, data in encoded)


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TARGET
    if not source.is_file():
        raise SystemExit(f"找不到源图标: {source}")

    image = Image.open(source).convert("RGBA")
    frames = [(size, image.resize((size, size), Image.Resampling.LANCZOS)) for size in SIZES]
    target.write_bytes(build_ico(frames))
    print(f"已生成: {target}（{len(SIZES)} 个尺寸: 16/24/32/48/64/128/256）")


if __name__ == "__main__":
    main()
