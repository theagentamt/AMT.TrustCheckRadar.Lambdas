#!/usr/bin/env python3
"""Publish one verified resolver ZIP. Never updates Lambda functions or aliases."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess


class MissingObject(Exception):
    pass


def aws(region, operation, *args):
    result = subprocess.run(['aws', '--region', region, 's3api', operation, *args, '--output', 'json'], capture_output=True, text=True)
    if result.returncode:
        if operation == 'head-object' and '(404)' in result.stderr:
            raise MissingObject()
        raise RuntimeError('AWS artifact operation failed: ' + operation)
    return json.loads(result.stdout)


def publish(dist, bucket, release, region, call=aws):
    if not re.fullmatch('[0-9a-f]{40}', release):
        raise ValueError('Release must be the full source commit SHA')
    artifact = Path(dist) / 'url_redirect_resolver.zip'
    digest = hashlib.sha256(artifact.read_bytes()).digest()
    sha = digest.hex()
    encoded = base64.b64encode(digest).decode()
    entries = [line.split('  ', 1) for line in (Path(dist) / 'SHA256SUMS').read_text().splitlines()]
    matches = [row[0] for row in entries if len(row) == 2 and row[1] == artifact.name]
    if matches != [sha]:
        raise ValueError('Resolver checksum manifest mismatch')
    key = f'releases/{release}/url_redirect_resolver.zip'
    try:
        existing = call(region, 'head-object', '--bucket', bucket, '--key', key, '--checksum-mode', 'ENABLED')
        if existing.get('Metadata', {}).get('sha256') != sha or existing.get('ChecksumSHA256') != encoded:
            raise ValueError('Immutable artifact already exists with different content')
        version = existing.get('VersionId')
    except MissingObject:
        response = call(region, 'put-object', '--bucket', bucket, '--key', key, '--body', str(artifact),
                        '--content-type', 'application/zip', '--metadata', f'sha256={sha},target-runtime=python3.14,target-architecture=arm64',
                        '--checksum-sha256', encoded, '--if-none-match', '*')
        version = response.get('VersionId')
    if not isinstance(version, str) or not version or version == 'null':
        raise ValueError('A versioned artifact is required')
    # Existing publisher role has GetObject, not GetObjectVersion. Inspect the
    # current object and require that it is still the exact version just chosen.
    # A concurrent replacement fails closed instead of widening IAM.
    verified = call(region, 'head-object', '--bucket', bucket, '--key', key, '--checksum-mode', 'ENABLED')
    if verified.get('VersionId') != version or verified.get('ChecksumSHA256') != encoded or verified.get('Metadata', {}).get('sha256') != sha or verified.get('ContentLength') != artifact.stat().st_size:
        raise ValueError('Published artifact verification failed')
    return {'bucket': bucket, 'key': key, 'versionId': version, 'sha256': sha, 'sourceCodeHash': encoded, 'runtime': 'python3.14', 'architecture': 'arm64'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist-dir', required=True)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--release', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    record = publish(args.dist_dir, args.bucket, args.release, args.region)
    Path(args.output).write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record))


if __name__ == '__main__':
    main()
