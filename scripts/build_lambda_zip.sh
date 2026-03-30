#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAMBDA_SRC_DIR="$ROOT_DIR/src/incognito_write"
REQUIREMENTS_FILE=""
OUTPUT_ZIP="$ROOT_DIR/function.zip"
PYTHON_VERSION="3.12"
LAMBDA_ARCH="arm64"
BUILD_DIR="$ROOT_DIR/.build/lambda_package"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Builds a Lambda ZIP artifact from Python source, installing Linux-compatible
wheels so builds done on macOS are safe for AWS Lambda runtime.

Options:
  --lambda-src <dir>       Lambda source directory (default: src/incognito_write)
  --requirements <file>    requirements.txt path (default: auto-detect in lambda source)
  --output <zip>           Output zip path (default: function.zip at repo root)
  --python-version <ver>   Python runtime version, e.g. 3.12 (default: 3.12)
  --arch <arch>            Lambda architecture: arm64 or x86_64 (default: arm64)
  -h, --help               Show this help

Example:
  $(basename "$0") --lambda-src src/incognito_write --output dist/incognito-write.zip --arch arm64
USAGE
}

log() {
  printf '\n[%s] %s\n' "$(date +"%Y-%m-%d %H:%M:%S")" "$*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: required command not found: $1" >&2
    exit 1
  fi
}

platform_for_arch() {
  case "$1" in
    arm64) echo "manylinux2014_aarch64" ;;
    x86_64) echo "manylinux2014_x86_64" ;;
    *)
      echo "Error: unsupported arch '$1'. Use arm64 or x86_64." >&2
      exit 1
      ;;
  esac
}

resolve_path() {
  local input="$1"
  if [[ "$input" = /* ]]; then
    printf '%s\n' "$input"
  else
    printf '%s\n' "$ROOT_DIR/$input"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lambda-src)
      LAMBDA_SRC_DIR="$2"
      shift 2
      ;;
    --requirements)
      REQUIREMENTS_FILE="$2"
      shift 2
      ;;
    --output)
      OUTPUT_ZIP="$2"
      shift 2
      ;;
    --python-version)
      PYTHON_VERSION="$2"
      shift 2
      ;;
    --arch)
      LAMBDA_ARCH="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_cmd python3
LAMBDA_SRC_DIR="$(resolve_path "$LAMBDA_SRC_DIR")"
OUTPUT_ZIP="$(resolve_path "$OUTPUT_ZIP")"

if [[ ! -d "$LAMBDA_SRC_DIR" ]]; then
  echo "Error: lambda source directory not found: $LAMBDA_SRC_DIR" >&2
  exit 1
fi

if [[ -z "$REQUIREMENTS_FILE" ]] && [[ -f "$LAMBDA_SRC_DIR/requirements.txt" ]]; then
  REQUIREMENTS_FILE="$LAMBDA_SRC_DIR/requirements.txt"
fi

if [[ -n "$REQUIREMENTS_FILE" ]]; then
  REQUIREMENTS_FILE="$(resolve_path "$REQUIREMENTS_FILE")"
fi

TARGET_PLATFORM="$(platform_for_arch "$LAMBDA_ARCH")"

log "Preparing build directory: $BUILD_DIR"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

log "Copying source files from $LAMBDA_SRC_DIR"
cp -R "$LAMBDA_SRC_DIR"/. "$BUILD_DIR/"

log "Removing local caches"
find "$BUILD_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$BUILD_DIR" -type f -name "*.pyc" -delete

if [[ -n "$REQUIREMENTS_FILE" ]]; then
  if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    echo "Error: requirements file not found: $REQUIREMENTS_FILE" >&2
    exit 1
  fi

  log "Installing Linux-compatible dependencies for Lambda"
  log "Platform: $TARGET_PLATFORM | Python: $PYTHON_VERSION | Arch: $LAMBDA_ARCH"

  python3 -m pip install \
    --upgrade \
    --target "$BUILD_DIR" \
    --requirement "$REQUIREMENTS_FILE" \
    --platform "$TARGET_PLATFORM" \
    --implementation cp \
    --python-version "$PYTHON_VERSION" \
    --only-binary=:all:
fi

log "Creating ZIP artifact: $OUTPUT_ZIP"
mkdir -p "$(dirname "$OUTPUT_ZIP")"
rm -f "$OUTPUT_ZIP"

python3 - <<PY
import os
import zipfile

build_dir = os.path.abspath("$BUILD_DIR")
out_zip = os.path.abspath("$OUTPUT_ZIP")

with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, _, files in os.walk(build_dir):
        for f in files:
            full_path = os.path.join(root, f)
            arcname = os.path.relpath(full_path, build_dir)
            zf.write(full_path, arcname)

print(out_zip)
PY

log "Build complete"
log "Artifact: $OUTPUT_ZIP"
