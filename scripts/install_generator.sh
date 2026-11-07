#!/usr/bin/env bash
# Install vietnh1009/ASCII-generator for astrbot_plugin_ascii.
#
# Usage:
#   PYTHON_BIN=/opt/AstrBot/venv/bin/python ./install_generator.sh [target_dir] [--apply-patch]
#
# This script only creates files inside a NEW directory. It never deletes,
# overwrites, or modifies anything that already exists on the machine.

set -euo pipefail

UPSTREAM="https://github.com/vietnh1009/ASCII-generator.git"
TARGET="${1:-/opt/ASCII-generator}"
APPLY_PATCH="${2:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="$SCRIPT_DIR/../patches/pillow12-getsize.patch"

if [ -e "$TARGET" ]; then
  echo "ERROR: '$TARGET' already exists. Pick a different directory or manage it manually." >&2
  exit 1
fi

case "$PYTHON_BIN" in
  /*) ;;
  *) PYTHON_BIN="$(command -v "$PYTHON_BIN")" ;;
esac

echo "==> Cloning $UPSTREAM (sparse checkout, demo/ skipped) into $TARGET"
git clone --depth 1 --filter=blob:none --sparse "$UPSTREAM" "$TARGET"
git -C "$TARGET" sparse-checkout set --no-cone \
  '/*.py' '/README.md' '/LICENSE' '/fonts/DejaVuSansMono-Bold.ttf'

if [ "$APPLY_PATCH" = "--apply-patch" ]; then
  if [ ! -f "$PATCH_FILE" ]; then
    echo "ERROR: patch file not found: $PATCH_FILE" >&2
    exit 1
  fi
  echo "==> Applying the Pillow >= 10 compatibility patch"
  git -C "$TARGET" apply "$PATCH_FILE"
else
  echo "==> Patch skipped. On Pillow >= 10, apply patches/pillow12-getsize.patch manually."
fi

echo "==> Checking Python dependencies with $PYTHON_BIN"
"$PYTHON_BIN" -c "import numpy, cv2, PIL; print('numpy', numpy.__version__, '| opencv', cv2.__version__, '| Pillow', PIL.__version__)"

echo "==> Checking required upstream files"
test -f "$TARGET/img2img.py"
test -f "$TARGET/img2img_color.py"
test -f "$TARGET/fonts/DejaVuSansMono-Bold.ttf"

cat <<EOF

Done. Now open the AstrBot WebUI and set:
  generator_dir = $TARGET
  python_path   = $PYTHON_BIN
Save, then reload the plugin. See docs/SETUP.md for the full checklist.
EOF
