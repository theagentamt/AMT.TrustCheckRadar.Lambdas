#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/dist"
PYTHON_VERSION=""
LAMBDA_ARCH="arm64"
SELECTED_FUNCTION=""
BUILD_ALL=false
SKIP_DEPENDENCIES=false
CONTRACT_VERSION="1.0.0"

FUNCTIONS=(
  account_data_api
  account_export_api
  age_attestation
  campaign_cluster_aggregator
  campaign_deletion_bridge
  campaign_lifecycle
  campaign_observation_publisher
  campaign_participation
  campaign_review
  campaign_trends
  conversation_analysis
  message_consumer
  message_evaluator
  recovery_consumer
  result_feedback
  recovery_evaluator
  device_registration
  device_recovery
  entitlement_snapshot
  history_account_deletion_bridge
  history_lifecycle
  history_mutation_api
  history_read_api
  purchase_handoff
  url_redirect_resolver
  url_assessment
  url_consumer
  url_lease_recovery
  v1_entitlements
  v1_authority_deletion
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
  --python-version <ver>   Override target Python (resolver/assessment: 3.14; others: 3.13).
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
    campaign_participation|conversation_analysis|entitlement_snapshot|purchase_handoff) return 0 ;;
    *) return 1 ;;
  esac
}

needs_shared_campaign_contracts() {
  case "$1" in
    campaign_cluster_aggregator|campaign_observation_publisher|conversation_analysis) return 0 ;;
    *) return 1 ;;
  esac
}

needs_shared_history() {
  case "$1" in
    account_data_api|conversation_analysis|device_recovery|device_registration|history_lifecycle|history_mutation_api|history_read_api) return 0 ;;
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
  local python_version="${PYTHON_VERSION:-3.13}"
  if [[ -z "$PYTHON_VERSION" && ( "$function_name" == "account_export_api" || "$function_name" == "account_data_api" || "$function_name" == "result_feedback" || "$function_name" == "recovery_consumer" || "$function_name" == "recovery_evaluator" || "$function_name" == "message_consumer" || "$function_name" == "message_evaluator" || "$function_name" == "url_redirect_resolver" || "$function_name" == "url_assessment" || "$function_name" == "url_consumer" || "$function_name" == "url_lease_recovery" || "$function_name" == "v1_entitlements" || "$function_name" == "v1_authority_deletion" ) ]]; then
    python_version="3.14"
  fi

  [[ -d "$source_dir" ]] || fail "source directory not found: $source_dir"
  [[ -f "$source_dir/app.py" ]] || fail "handler not found: $source_dir/app.py"

  echo "Packaging $function_name (Python $python_version, $LAMBDA_ARCH)"
  rm -rf "$build_dir"
  mkdir -p "$build_dir"
  cp -R "$source_dir"/. "$build_dir/"

  if [[ "$function_name" == "url_consumer" || "$function_name" == "url_lease_recovery" || "$function_name" == "v1_entitlements" || "$function_name" == "v1_authority_deletion" ]]; then
    cp -R "$ROOT_DIR/src/shared_check_authority" "$build_dir/shared_check_authority"
    cp -R "$ROOT_DIR/src/shared_history" "$build_dir/shared_history"
  fi
  if [[ "$function_name" == "v1_entitlements" || "$function_name" == "v1_authority_deletion" ]]; then
    mkdir -p "$build_dir/$function_name"
    cp -R "$source_dir"/. "$build_dir/$function_name/"
  fi
  if [[ "$function_name" == "url_consumer" ]]; then
    cp -R "$ROOT_DIR/contracts/url-assessment/v1-draft" "$build_dir/public_contract"
    cp -R "$ROOT_DIR/contracts/url-consumer/1.0.0-candidate.1" "$build_dir/transport_contract"
  fi

  if [[ "$function_name" == "message_consumer" || "$function_name" == "message_evaluator" ]]; then
    mkdir -p "$build_dir/$function_name" "$build_dir/url_redirect_resolver"
    cp -R "$source_dir"/. "$build_dir/$function_name/"
    printf 'from %s.app import lambda_handler\n' "$function_name" > "$build_dir/app.py"
    cp -R "$ROOT_DIR/src/shared_message_contract" "$build_dir/shared_message_contract"
    cp "$ROOT_DIR/src/url_redirect_resolver/resolver.py" "$build_dir/url_redirect_resolver/"
    cp -R "$ROOT_DIR/contracts/url-assessment/v1-draft" "$build_dir/shared_message_contract/url_contract"
    if [[ "$function_name" == "message_evaluator" ]]; then
      cp -R "$ROOT_DIR/contracts/message-consumer/1.0.0-candidate.2" "$build_dir/message_evaluator/ai_contract"
    fi
    if [[ "$function_name" == "message_consumer" ]]; then
      cp -R "$ROOT_DIR/src/shared_check_authority" "$build_dir/shared_check_authority"
      cp -R "$ROOT_DIR/src/shared_history" "$build_dir/shared_history"
      mkdir -p "$build_dir/message_evaluator" "$build_dir/url_consumer"
      cp "$ROOT_DIR/src/message_evaluator/policy.py" "$ROOT_DIR/src/message_evaluator/policy_v2.py" "$ROOT_DIR/src/message_evaluator/coverage.py" "$build_dir/message_evaluator/"
      cp "$ROOT_DIR/src/url_consumer/service.py" "$build_dir/url_consumer/"
      cp -R "$ROOT_DIR/contracts/message-consumer/1.0.0-candidate.1" "$build_dir/message_consumer/contract"
      cp -R "$ROOT_DIR/contracts/message-consumer/1.0.0-candidate.2" "$build_dir/message_consumer/contract_v2"
    fi
  fi

  if [[ "$function_name" == "recovery_consumer" || "$function_name" == "recovery_evaluator" ]]; then
    mkdir -p "$build_dir/$function_name" "$build_dir/url_redirect_resolver" "$build_dir/message_evaluator"
    cp -R "$source_dir"/. "$build_dir/$function_name/"
    printf 'from %s.app import lambda_handler\n' "$function_name" > "$build_dir/app.py"
    cp -R "$ROOT_DIR/src/shared_recovery_contract" "$build_dir/shared_recovery_contract"
    cp -R "$ROOT_DIR/contracts/recovery-playbook/1.0" "$build_dir/shared_recovery_contract/playbook"
    cp -R "$ROOT_DIR/src/shared_message_contract" "$build_dir/shared_message_contract"
    cp "$ROOT_DIR/src/url_redirect_resolver/resolver.py" "$build_dir/url_redirect_resolver/"
    cp -R "$ROOT_DIR/contracts/url-assessment/v1-draft" "$build_dir/shared_message_contract/url_contract"
    if [[ "$function_name" == "recovery_consumer" ]]; then
      cp -R "$ROOT_DIR/src/shared_check_authority" "$build_dir/shared_check_authority"
      cp -R "$ROOT_DIR/src/shared_history" "$build_dir/shared_history"
      mkdir -p "$build_dir/url_consumer"
      cp "$ROOT_DIR/src/url_consumer/service.py" "$build_dir/url_consumer/"
      cp -R "$ROOT_DIR/contracts/recovery-consumer/1.0.0-candidate.1" "$build_dir/recovery_consumer/contract"
    else
      cp "$ROOT_DIR/src/message_evaluator/proposer.py" "$build_dir/message_evaluator/"
    fi
  fi
  if [[ "$function_name" == "result_feedback" ]]; then
    mkdir -p "$build_dir/result_feedback" "$build_dir/url_redirect_resolver"
    cp -R "$source_dir"/. "$build_dir/result_feedback/"
    printf 'from result_feedback.app import lambda_handler\n' > "$build_dir/app.py"
    cp -R "$ROOT_DIR/src/shared_check_authority" "$build_dir/shared_check_authority"
    cp -R "$ROOT_DIR/src/shared_history" "$build_dir/shared_history"
    cp -R "$ROOT_DIR/src/shared_message_contract" "$build_dir/shared_message_contract"
    cp "$ROOT_DIR/src/url_redirect_resolver/resolver.py" "$build_dir/url_redirect_resolver/"
    cp -R "$ROOT_DIR/contracts/url-assessment/v1-draft" "$build_dir/shared_message_contract/url_contract"
    cp -R "$ROOT_DIR/contracts/result-feedback/1.0.0-candidate.1" "$build_dir/result_feedback/contract"
  fi
  if [[ "$function_name" == "account_export_api" ]]; then
    mkdir -p "$build_dir/account_export_api"
    cp -R "$source_dir"/. "$build_dir/account_export_api/"
    printf 'from account_export_api.app import lambda_handler\n' > "$build_dir/app.py"
    cp -R "$ROOT_DIR/src/shared_check_authority" "$build_dir/shared_check_authority"
    cp -R "$ROOT_DIR/src/shared_history" "$build_dir/shared_history"
    cp -R "$ROOT_DIR/src/shared_message_contract" "$build_dir/shared_message_contract"
  fi
  # Every shared-authority consumer can recover a recovery lease without the
  # model/parser/bundle modules. Keep this lightweight dependency in old workers.
  if [[ -d "$build_dir/shared_check_authority" && ! -d "$build_dir/shared_recovery_contract" ]]; then
    mkdir -p "$build_dir/shared_recovery_contract"
    cp "$ROOT_DIR/src/shared_recovery_contract/__init__.py" "$ROOT_DIR/src/shared_recovery_contract/constants.py" "$ROOT_DIR/src/shared_recovery_contract/usage.py" "$build_dir/shared_recovery_contract/"
  fi

  if [[ "$function_name" == "url_assessment" ]]; then
    mkdir -p "$build_dir/url_redirect_resolver"
    cp "$ROOT_DIR/src/url_redirect_resolver/resolver.py" "$build_dir/url_redirect_resolver/"
  fi

  if needs_shared_entitlements "$function_name"; then
    cp -R "$ROOT_DIR/src/shared_entitlements" "$build_dir/shared_entitlements"
  fi
  if [[ "$function_name" == "purchase_handoff" || "$function_name" == "account_data_api" || "$function_name" == "account_export_api" ]]; then
    cp -R "$ROOT_DIR/src/shared_purchase_ownership" "$build_dir/shared_purchase_ownership"
  fi

  if [[ "$function_name" == "campaign_observation_publisher" || "$function_name" == "campaign_cluster_aggregator" || "$function_name" == "campaign_deletion_bridge" || "$function_name" == "campaign_lifecycle" ]]; then
    cp -R "$ROOT_DIR/src/shared_campaign_locators" "$build_dir/shared_campaign_locators"
  fi
  if needs_shared_campaign_contracts "$function_name"; then
    cp -R "$ROOT_DIR/src/shared_campaign_contracts" "$build_dir/shared_campaign_contracts"
  fi

  if [[ "$function_name" == "account_data_api" || "$function_name" == "v1_authority_deletion" ]]; then
    cp -R "$ROOT_DIR/src/shared_account_finalization" "$build_dir/shared_account_finalization"
  fi

  if needs_shared_history "$function_name"; then
    cp -R "$ROOT_DIR/src/shared_history" "$build_dir/shared_history"
  fi

  if [[ -f "$requirements_file" && "$SKIP_DEPENDENCIES" == false ]]; then
    python3 -m pip install \
      --disable-pip-version-check \
      --no-compile \
      --target "$build_dir" \
      --requirement "$requirements_file" \
      --platform "$TARGET_PLATFORM" \
      --implementation cp \
      --python-version "$python_version" \
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

package_campaign_contracts() {
  local source_dir="$ROOT_DIR/contracts/campaign/v1"
  local output_zip="$OUTPUT_DIR/campaign-contracts-$CONTRACT_VERSION.zip"
  [[ -f "$source_dir/contract-set.json" ]] || fail "campaign contract source not found: $source_dir"
  rm -f "$output_zip"
  python3 - "$source_dir" "$output_zip" <<'PY'
from hashlib import sha256
from pathlib import Path
import stat
import sys
import zipfile

source_dir = Path(sys.argv[1]).resolve()
output_zip = Path(sys.argv[2]).resolve()
files = sorted(path for path in source_dir.rglob("*") if path.is_file())
manifest = "".join(
    f"{sha256(path.read_bytes()).hexdigest()}  {path.relative_to(source_dir).as_posix()}\n"
    for path in files
)

with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in files:
        relative_path = path.relative_to(source_dir).as_posix()
        info = zipfile.ZipInfo(relative_path, date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, path.read_bytes(), compresslevel=9)
    info = zipfile.ZipInfo("SHA256SUMS", date_time=(1980, 1, 1, 0, 0, 0))
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, manifest.encode("utf-8"), compresslevel=9)

print(f"{sha256(output_zip.read_bytes()).hexdigest()}  {output_zip}")
PY
}

package_history_contracts() {
  local source_dir="$ROOT_DIR/contracts/history/v1"
  local output_zip="$OUTPUT_DIR/history-contracts-$CONTRACT_VERSION.zip"
  [[ -f "$source_dir/contract-set.json" ]] || fail "history contract source not found: $source_dir"
  rm -f "$output_zip"
  python3 - "$source_dir" "$output_zip" <<'PY'
from hashlib import sha256
from pathlib import Path
import stat
import sys
import zipfile

source_dir = Path(sys.argv[1]).resolve()
output_zip = Path(sys.argv[2]).resolve()
files = sorted(path for path in source_dir.rglob("*") if path.is_file())
manifest = "".join(
    f"{sha256(path.read_bytes()).hexdigest()}  {path.relative_to(source_dir).as_posix()}\n"
    for path in files
)
with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in files:
        relative_path = path.relative_to(source_dir).as_posix()
        info = zipfile.ZipInfo(relative_path, date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, path.read_bytes(), compresslevel=9)
    info = zipfile.ZipInfo("SHA256SUMS", date_time=(1980, 1, 1, 0, 0, 0))
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, manifest.encode("utf-8"), compresslevel=9)
print(f"{sha256(output_zip.read_bytes()).hexdigest()}  {output_zip}")
PY
}

package_device_recovery_contracts() {
  local source_dir="$ROOT_DIR/contracts/device-recovery/v1"
  local output_zip="$OUTPUT_DIR/device-recovery-contracts-$CONTRACT_VERSION.zip"
  [[ -f "$source_dir/contract-set.json" ]] || fail "device recovery contract source not found: $source_dir"
  rm -f "$output_zip"
  python3 - "$source_dir" "$output_zip" <<'PY'
from hashlib import sha256
from pathlib import Path
import stat
import sys
import zipfile

source_dir = Path(sys.argv[1]).resolve()
output_zip = Path(sys.argv[2]).resolve()
files = sorted(path for path in source_dir.rglob("*") if path.is_file())
manifest = "".join(
    f"{sha256(path.read_bytes()).hexdigest()}  {path.relative_to(source_dir).as_posix()}\n"
    for path in files
)
with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in files:
        relative_path = path.relative_to(source_dir).as_posix()
        info = zipfile.ZipInfo(relative_path, date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, path.read_bytes(), compresslevel=9)
    info = zipfile.ZipInfo("SHA256SUMS", date_time=(1980, 1, 1, 0, 0, 0))
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, manifest.encode("utf-8"), compresslevel=9)
print(f"{sha256(output_zip.read_bytes()).hexdigest()}  {output_zip}")
PY
}

if [[ "$BUILD_ALL" == true ]]; then
  for function_name in "${FUNCTIONS[@]}"; do
    build_function "$function_name"
  done
  package_campaign_contracts
  package_history_contracts
  package_device_recovery_contracts

  python3 - "$OUTPUT_DIR" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys

output_dir = Path(sys.argv[1])
artifacts = sorted(output_dir.glob("*.zip"))
manifest = "".join(
    f"{sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}\n"
    for artifact in artifacts
)
(output_dir / "SHA256SUMS").write_text(manifest, encoding="utf-8")
PY
else
  build_function "$SELECTED_FUNCTION"
fi
