#!/usr/bin/env bash
# VideoToNo macOS 构建脚本（需在 macOS 上运行，PyInstaller 不支持交叉编译）
# 用法: bash scripts/build_mac.sh
# 产物: dist/VideoToNo.app（可右键打包成 dmg 分发）
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" --version >/dev/null 2>&1; then
    echo "未找到 python3，请先安装 Python 3.11: https://www.python.org/downloads/macos/"
    exit 1
fi

echo "==> 安装构建依赖（pyinstaller / pystray）"
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet pyinstaller
"$PYTHON" -m pip install --quiet -r backend/requirements.txt

echo "==> 读取版本号"
VERSION_LINE=$(grep -m1 '^VERSION = ' launcher.py)
VERSION=${VERSION_LINE#VERSION = \"}
VERSION=${VERSION%\"}
echo "    版本: $VERSION"

echo "==> PyInstaller 构建（--windowed，无控制台窗口）"
"$PYTHON" -m PyInstaller --noconfirm --clean --onefile --windowed \
    --name "VideoToNo" \
    --add-data "frontend:frontend" \
    --add-data "sources/icon.png:sources" \
    --collect-data faster_whisper \
    --icon "sources/icon.png" \
    --exclude-module tkinter \
    launcher.py

TARGET="dist/VideoToNo-${VERSION}-macos.app"
rm -rf "$TARGET"
mv "dist/VideoToNo.app" "$TARGET" 2>/dev/null || mv "dist/VideoToNo" "$TARGET"

echo ""
echo "构建完成: $TARGET"
echo "分发建议: 右键 $TARGET -> 压缩，或使用 create-dmg 打包"
echo "注意: 首次启动时 macOS 会提示“无法验证开发者”，需在 系统设置->隐私与安全性 中允许；"
echo "      或执行: xattr -dr com.apple.quarantine \"$TARGET\""
