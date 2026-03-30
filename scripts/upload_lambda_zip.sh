#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZIP_FILE="$ROOT_DIR/function.zip"
BUCKET_NAME="asecurityforall-dev-artifacts"
OBJECT_KEY="identity/post-confirmation/v1.0.0/function.zip"
AWS_REGION=""

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Uploads a Lambda ZIP artifact to S3 and prints metadata for deployment wiring.

Options:
  --file <zip>      ZIP file to upload (default: function.zip at repo root)
  --bucket <name>   Destination bucket (default: asecurityforall-dev-artifacts)
  --key <path>      Destination object key (default: identity/post-confirmation/v1.0.0/function.zip)
  --region <name>   AWS region override (optional)
  -h, --help        Show this help

Example:
  $(basename "$0") --file dist/post-confirmation.zip --bucket asecurityforall-dev-artifacts --key identity/post-confirmation/v1.0.0/function.zip --region us-east-1
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
    --file)
      ZIP_FILE="$2"
      shift 2
      ;;
    --bucket)
      BUCKET_NAME="$2"
      shift 2
      ;;
    --key)
      OBJECT_KEY="$2"
      shift 2
      ;;
    --region)
      AWS_REGION="$2"
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

require_cmd aws
ZIP_FILE="$(resolve_path "$ZIP_FILE")"

if [[ ! -f "$ZIP_FILE" ]]; then
  echo "Error: zip file not found: $ZIP_FILE" >&2
  exit 1
fi

PUT_ARGS=(s3api put-object --bucket "$BUCKET_NAME" --key "$OBJECT_KEY" --body "$ZIP_FILE")
if [[ -n "$AWS_REGION" ]]; then
  PUT_ARGS+=(--region "$AWS_REGION")
fi

log "Uploading artifact"
log "Bucket: $BUCKET_NAME"
log "Key: $OBJECT_KEY"

VERSION_ID="$(aws "${PUT_ARGS[@]}" --query 'VersionId' --output text)"

S3_URI="s3://$BUCKET_NAME/$OBJECT_KEY"
log "Upload complete"
log "S3 URI: $S3_URI"

if [[ -n "$VERSION_ID" && "$VERSION_ID" != "None" ]]; then
  log "Object VersionId: $VERSION_ID"
else
  log "Object VersionId: not returned (bucket versioning may be disabled)"
fi
