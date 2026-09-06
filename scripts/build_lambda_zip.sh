#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/dist"
PYTHON_VERSION="3.13"
LAMBDA_ARCH="arm64"
SELECTED_FUNCTION=""
BUILD_ALL=false
SKIP_DEPENDENCIES=false

FUNCTIONS=(
  age_attestation
  campaign_cluster_aggregator
  campaign_deletion_bridge
  campaign_lifecycle
  campaign_observation_publisher
  campaign_review
  conversation_analysis
  device_registration
  device_recovery
  entitlement_snapshot
  purchase_handoff
  web_risk_communication
  post_confirmation
)

usage() {
  cat <<'USAGE'
Usage: build_lambda_zip.sh (--all | --function <name>) [options]

Build deterministic AWS Lambda ZIP packages with app.py at the archive root.

Options:
  --all                    Build every Lambda artifact.
  --function <name>        Build one function from src/<name>.
  --output-dir <dir>       Artifact directory (default: dist).
  --python-version <ver>   Lambda Python version (default: 3.13).
  --arch <arch>            arm64 or x86_64 (default: arm64).
  --skip-dependencies      Package source only; intended for local validation.
  -h, --help               Show this help.
USAGE
}

fail() {
  echo "Error: $*" >&2
  exit 2
}

resolve_path() {
  if [[ "$1" = /* ]]; then
    printf '%s\n' "$1"
  else
    printf '%s\n' "$ROOT_DIR/$1"
  fi
}

platform_for_arch() {
  case "$1" in
    arm64) printf '%s\n' "manylinux2014_aarch64" ;;
    x86_64) printf '%s\n' "manylinux2014_x86_64" ;;
    *) fail "unsupported architecture '$1'; use arm64 or x86_64" ;;
  esac
}

is_known_function() {
  local candidate="$1"
  local function_name
  for function_name in "${FUNCTIONS[@]}"; do
    [[ "$function_name" == "$candidate" ]] && return 0
  done
  return 1
}

needs_shared_entitlements() {
  case "$1" in
    conversation_analysis|entitlement_snapshot|purchase_handoff) return 0 ;;
    *) return 1 ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      BUILD_ALL=true
      shift
      ;;
    --function)
      [[ $# -ge 2 ]] || fail "--function requires a value"
      SELECTED_FUNCTION="$2"
      shift 2
      ;;
    --output-dir)
      [[ $# -ge 2 ]] || fail "--output-dir requires a value"
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --python-version)
      [[ $# -ge 2 ]] || fail "--python-version requires a value"
      PYTHON_VERSION="$2"
      shift 2
      ;;
    --arch)
      [[ $# -ge 2 ]] || fail "--arch requires a value"
      LAMBDA_ARCH="$2"
      shift 2
      ;;
    --skip-dependencies)
      SKIP_DEPENDENCIES=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) fail "unknown argument '$1'" ;;
  esac
done

if [[ "$BUILD_ALL" == true && -n "$SELECTED_FUNCTION" ]] || [[ "$BUILD_ALL" == false && -z "$SELECTED_FUNCTION" ]]; then
  fail "choose exactly one of --all or --function <name>"
fi

if [[ -n "$SELECTED_FUNCTION" ]] && ! is_known_function "$SELECTED_FUNCTION"; then
  fail "unknown function '$SELECTED_FUNCTION'"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
TARGET_PLATFORM="$(platform_for_arch "$LAMBDA_ARCH")"
OUTPUT_DIR="$(resolve_path "$OUTPUT_DIR")"
mkdir -p "$OUTPUT_DIR"

build_function() {
  local function_name="$1"
  local source_dir="$ROOT_DIR/src/$function_name"
  local build_dir="$ROOT_DIR/.build/$function_name"
  local output_zip="$OUTPUT_DIR/$function_name.zip"
  local requirements_file="$source_dir/requirements.txt"

  [[ -d "$source_dir" ]] || fail "source directory not found: $source_dir"
  [[ -f "$source_dir/app.py" ]] || fail "handler not found: $source_dir/app.py"

  echo "Packaging $function_name"
  rm -rf "$build_dir"
  mkdir -p "$build_dir"
  cp -R "$source_dir"/. "$build_dir/"

  if needs_shared_entitlements "$function_name"; then
    cp -R "$ROOT_DIR/src/shared_entitlements" "$build_dir/shared_entitlements"
  fi

  if [[ -f "$requirements_file" && "$SKIP_DEPENDENCIES" == false ]]; then
    python3 -m pip install \
      --disable-pip-version-check \
      --no-compile \
      --target "$build_dir" \
      --requirement "$requirements_file" \
      --platform "$TARGET_PLATFORM" \
      --implementation cp \
      --python-version "$PYTHON_VERSION" \
      --only-binary=:all:
  fi

  find "$build_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
  find "$build_dir" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
  rm -f "$build_dir/requirements.txt" "$output_zip"

  python3 - "$build_dir" "$output_zip" <<'PY'
from pathlib import Path
import stat
import sys
import zipfile

build_dir = Path(sys.argv[1]).resolve()
output_zip = Path(sys.argv[2]).resolve()

with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(item for item in build_dir.rglob("*") if item.is_file()):
        relative_path = path.relative_to(build_dir).as_posix()
        info = zipfile.ZipInfo(relative_path, date_time=(1980, 1, 1, 0, 0, 0))
        mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
        info.external_attr = (stat.S_IFREG | mode) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with path.open("rb") as source:
            archive.writestr(info, source.read(), compresslevel=9)
PY

  python3 - "$output_zip" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    names = archive.namelist()
    if "app.py" not in names:
        raise SystemExit(f"{sys.argv[1]} does not contain app.py at its root")
    bad = [name for name in names if "__pycache__" in name or name.endswith((".pyc", ".pyo"))]
    if bad:
        raise SystemExit(f"{sys.argv[1]} contains cache files: {bad}")
PY

  python3 - "$output_zip" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys

artifact = Path(sys.argv[1])
print(f"{sha256(artifact.read_bytes()).hexdigest()}  {artifact}")
PY
}

if [[ "$BUILD_ALL" == true ]]; then
  for function_name in "${FUNCTIONS[@]}"; do
    build_function "$function_name"
  done
else
  build_function "$SELECTED_FUNCTION"
fi
