#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
BUCKET_NAME=""
RELEASE_ID=""
AWS_REGION=""
INCLUDE_OPTIONAL=true

REQUIRED_FUNCTIONS=(
  age_attestation
  conversation_analysis
  device_registration
  purchase_handoff
  entitlement_snapshot
  post_confirmation
)
OPTIONAL_FUNCTIONS=(campaign_observation_publisher device_recovery web_risk_communication)

usage() {
  cat <<'USAGE'
Usage: upload_lambda_zips.sh --bucket <name> --release <id> [options]

Uploads immutable Lambda packages to releases/<release-id>/<function>.zip.

Options:
  --dist-dir <dir>            Package directory (default: dist).
  --bucket <name>             Foundation artifact bucket (required).
  --release <id>              Immutable release ID (required).
  --region <name>             AWS region override.
  --include-optional <bool>   Include optional functions (default: true).
  -h, --help                  Show this help.
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dist-dir)
      [[ $# -ge 2 ]] || fail "--dist-dir requires a value"
      DIST_DIR="$2"
      shift 2
      ;;
    --bucket)
      [[ $# -ge 2 ]] || fail "--bucket requires a value"
      BUCKET_NAME="$2"
      shift 2
      ;;
    --release)
      [[ $# -ge 2 ]] || fail "--release requires a value"
      RELEASE_ID="$2"
      shift 2
      ;;
    --region)
      [[ $# -ge 2 ]] || fail "--region requires a value"
      AWS_REGION="$2"
      shift 2
      ;;
    --include-optional)
      [[ $# -ge 2 ]] || fail "--include-optional requires a value"
      INCLUDE_OPTIONAL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) fail "unknown argument '$1'" ;;
  esac
done

[[ -n "$BUCKET_NAME" ]] || fail "--bucket is required"
[[ "$RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail "--release must use only letters, digits, dots, underscores, and hyphens"
[[ "$INCLUDE_OPTIONAL" == true || "$INCLUDE_OPTIONAL" == false ]] || fail "--include-optional must be true or false"
command -v aws >/dev/null 2>&1 || fail "aws CLI is required"

DIST_DIR="$(resolve_path "$DIST_DIR")"
FUNCTIONS=("${REQUIRED_FUNCTIONS[@]}")
if [[ "$INCLUDE_OPTIONAL" == true ]]; then
  FUNCTIONS+=("${OPTIONAL_FUNCTIONS[@]}")
fi

for function_name in "${FUNCTIONS[@]}"; do
  artifact="$DIST_DIR/$function_name.zip"
  [[ -f "$artifact" ]] || fail "missing artifact: $artifact"
done

for function_name in "${FUNCTIONS[@]}"; do
  artifact="$DIST_DIR/$function_name.zip"
  object_key="releases/$RELEASE_ID/$function_name.zip"
  put_args=(s3api put-object --bucket "$BUCKET_NAME" --key "$object_key" --body "$artifact" --if-none-match '*')
  if [[ -n "$AWS_REGION" ]]; then
    put_args+=(--region "$AWS_REGION")
  fi

  echo "Uploading s3://$BUCKET_NAME/$object_key"
  aws "${put_args[@]}" --query '{ETag:ETag,VersionId:VersionId}' --output json
done
