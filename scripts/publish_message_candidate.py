#!/usr/bin/env python3
"""Validate and immutably publish the two governed-message Dev artifacts.

This tool can write versioned objects to the approved Dev artifact bucket. It has
no Lambda, IAM, API Gateway, DynamoDB, secret, or activation operation.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import zipfile


FUNCTIONS = ("message_consumer", "message_evaluator")
BUCKET = "trustcheckradar-dev-107827791950-artifacts"
REGION = "us-east-1"


class MissingObject(Exception):
    pass


def aws(region, operation, *args):
    result = subprocess.run(
        ["aws", "--region", region, "s3api", operation, *args, "--output", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        if operation == "head-object" and "(404)" in result.stderr:
            raise MissingObject()
        raise RuntimeError("Artifact operation failed: " + operation)
    return json.loads(result.stdout)


def inspect_packages(dist, source_sha):
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Exact source SHA required")
    dist = Path(dist)
    expected = {name + ".zip" for name in FUNCTIONS}
    if {path.name for path in dist.glob("*.zip")} != expected:
        raise ValueError("Exactly the two governed-message packages are required")

    entries = {}
    for line in (dist / "SHA256SUMS").read_text().splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or name in entries or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid checksum manifest")
        entries[name] = digest
    if set(entries) != expected:
        raise ValueError("Checksum scope does not match governed-message scope")

    artifacts = []
    for function in FUNCTIONS:
        path = dist / (function + ".zip")
        content = path.read_bytes()
        digest = hashlib.sha256(content).digest()
        if entries[path.name] != digest.hex():
            raise ValueError("Package checksum mismatch")
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "app.py" not in names or len(names) != len(set(names)):
                raise ValueError("Invalid handler or duplicate archive entries")
            if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                raise ValueError("Invalid archive path")
            dependencies = sorted(
                name.split("/")[0]
                for name in names
                if name.endswith(".dist-info/METADATA")
            )
        artifacts.append(
            {
                "function": function,
                "artifact": path.name,
                "sha256": digest.hex(),
                "sourceCodeHash": base64.b64encode(digest).decode(),
                "bytes": len(content),
                "runtime": "python3.14",
                "architecture": "arm64",
                "handler": f"{function}.app.lambda_handler",
                "bundledDependencies": dependencies,
            }
        )
    return {
        "schemaVersion": 1,
        "scope": "message_candidate",
        "sourceSha": source_sha,
        "environment": "dev",
        "runtimeUpdated": False,
        "routesUpdated": False,
        "activationApproved": False,
        "artifacts": artifacts,
    }


def smoke(dist):
    if sys.version_info[:2] != (3, 14) or platform.system() != "Linux" or platform.machine() not in ("aarch64", "arm64"):
        raise ValueError("Offline package import requires Linux ARM64 Python 3.14")
    for function in FUNCTIONS:
        path = Path(dist).resolve() / (function + ".zip")
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.endswith(".py"):
                    compile(archive.read(name), name, "exec")
        env = dict(
            os.environ,
            AWS_ACCESS_KEY_ID="synthetic",
            AWS_SECRET_ACCESS_KEY="synthetic",
            AWS_EC2_METADATA_DISABLED="true",
            AWS_DEFAULT_REGION=REGION,
            STAGE="dev",
            MESSAGE_CONSUMER_ENABLED="false",
            MESSAGE_EVALUATOR_ENABLED="false",
        )
        code = (
            "import json,sys; sys.path.insert(0,sys.argv[1]); import app; "
            "result=app.lambda_handler({},None); "
            "assert (result.get('enabled') is False) if 'enabled' in result else "
            "(result.get('statusCode')==503 and json.loads(result['body'])['errorCode']=='SERVICE_NOT_ENABLED')"
        )
        subprocess.run([sys.executable, "-c", code, str(path)], env=env, check=True)
        print(function + ": offline compile/import and disabled-handler smoke passed")


def publish(dist, bucket, source_sha, region, call=aws):
    if region != REGION or bucket != BUCKET:
        raise ValueError("Publication is restricted to the approved Dev artifact bucket")
    manifest = inspect_packages(dist, source_sha)
    records = []
    for artifact in manifest["artifacts"]:
        key = f"releases/{source_sha}/{artifact['artifact']}"
        checksum = artifact["sourceCodeHash"]
        sha = artifact["sha256"]
        try:
            existing = call(region, "head-object", "--bucket", bucket, "--key", key)
            if (
                existing.get("ChecksumSHA256") != checksum
                or existing.get("Metadata", {}).get("sha256") != sha
                or existing.get("ContentLength") != artifact["bytes"]
                or existing.get("VersionId") in (None, "null")
            ):
                raise ValueError("Immutable artifact already exists with different content")
        except MissingObject:
            call(
                region,
                "put-object",
                "--bucket",
                bucket,
                "--key",
                key,
                "--body",
                str(Path(dist) / artifact["artifact"]),
                "--checksum-algorithm",
                "SHA256",
                "--checksum-sha256",
                checksum,
                "--metadata",
                "sha256=" + sha,
                "--if-none-match",
                "*",
            )
        verified = call(region, "head-object", "--bucket", bucket, "--key", key)
        version = verified.get("VersionId")
        if version in (None, "null"):
            raise ValueError("Versioned artifact required")
        if (
            verified.get("ChecksumSHA256") != checksum
            or verified.get("Metadata", {}).get("sha256") != sha
            or verified.get("ContentLength") != artifact["bytes"]
        ):
            raise ValueError("Published artifact verification failed")
        records.append(dict(artifact, bucket=bucket, key=key, versionId=version))
    return dict(manifest, artifacts=records, publicationComplete=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--bucket")
    parser.add_argument("--region")
    args = parser.parse_args()
    result = inspect_packages(args.dist_dir, args.source_sha)
    if args.smoke:
        smoke(args.dist_dir)
    if args.publish:
        if not args.bucket or not args.region:
            parser.error("--publish requires --bucket and --region")
        result = publish(args.dist_dir, args.bucket, args.source_sha, args.region)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
