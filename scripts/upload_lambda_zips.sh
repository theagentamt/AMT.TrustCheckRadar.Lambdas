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
OPTIONAL_FUNCTIONS=(campaign_cluster_aggregator campaign_deletion_bridge campaign_lifecycle campaign_observation_publisher campaign_participation campaign_review campaign_trends device_recovery web_risk_communication)

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
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

DIST_DIR="$(resolve_path "$DIST_DIR")"
FUNCTIONS=("${REQUIRED_FUNCTIONS[@]}")
if [[ "$INCLUDE_OPTIONAL" == true ]]; then
  FUNCTIONS+=("${OPTIONAL_FUNCTIONS[@]}")
fi

for function_name in "${FUNCTIONS[@]}"; do
  artifact="$DIST_DIR/$function_name.zip"
  [[ -f "$artifact" ]] || fail "missing artifact: $artifact"
done

CHECKSUM_MANIFEST="$DIST_DIR/SHA256SUMS"
[[ -f "$CHECKSUM_MANIFEST" ]] || fail "missing checksum manifest: $CHECKSUM_MANIFEST"

python3 - "$DIST_DIR" "${FUNCTIONS[@]}" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys

dist_dir = Path(sys.argv[1])
function_names = sys.argv[2:]
entries = {}
for line in (dist_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
    digest, separator, name = line.partition("  ")
    if not separator or len(digest) != 64 or not name:
        raise SystemExit(f"invalid SHA256SUMS entry: {line!r}")
    entries[name] = digest

for function_name in function_names:
    name = f"{function_name}.zip"
    artifact = dist_dir / name
    expected = entries.get(name)
    actual = sha256(artifact.read_bytes()).hexdigest()
    if expected is None:
        raise SystemExit(f"missing checksum for {name}")
    if actual != expected:
        raise SystemExit(f"checksum mismatch for {name}")
PY

upload_immutable() {
  local source_path="$1"
  local object_key="$2"
  local digest="$3"
  local content_type="${4:-application/zip}"
  local existing_digest=""
  local head_args=(s3api head-object --bucket "$BUCKET_NAME" --key "$object_key")
  local put_args=(s3api put-object --bucket "$BUCKET_NAME" --key "$object_key" --body "$source_path" --content-type "$content_type" --metadata "sha256=$digest" --if-none-match '*')

  if [[ -n "$AWS_REGION" ]]; then
    head_args+=(--region "$AWS_REGION")
    put_args+=(--region "$AWS_REGION")
  fi

  if existing_digest="$(aws "${head_args[@]}" --query 'Metadata.sha256' --output text 2>/dev/null)"; then
    if [[ "$existing_digest" == "$digest" ]]; then
      echo "Already published with matching checksum: s3://$BUCKET_NAME/$object_key"
      return 0
    fi
    fail "immutable object already exists with a different or missing checksum: s3://$BUCKET_NAME/$object_key"
  fi

  echo "Uploading s3://$BUCKET_NAME/$object_key"
  aws "${put_args[@]}" --query '{ETag:ETag,VersionId:VersionId}' --output json
}

for function_name in "${FUNCTIONS[@]}"; do
  artifact="$DIST_DIR/$function_name.zip"
  object_key="releases/$RELEASE_ID/$function_name.zip"
  artifact_digest="$(awk -v name="$function_name.zip" '$2 == name { print $1 }' "$CHECKSUM_MANIFEST")"
  upload_immutable "$artifact" "$object_key" "$artifact_digest"
done

manifest_key="releases/$RELEASE_ID/SHA256SUMS"
manifest_digest="$(python3 -c 'from hashlib import sha256; from pathlib import Path; import sys; print(sha256(Path(sys.argv[1]).read_bytes()).hexdigest())' "$CHECKSUM_MANIFEST")"
upload_immutable "$CHECKSUM_MANIFEST" "$manifest_key" "$manifest_digest" "text/plain"
