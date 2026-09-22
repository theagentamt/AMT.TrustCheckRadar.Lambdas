#!/usr/bin/env python3
"""Build evidence and publish only the nine reviewed Dev candidate packages.

No Lambda/IAM/DynamoDB operation exists here. Publication is not activation.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

FUNCTIONS = (
    'campaign_cluster_aggregator', 'campaign_deletion_bridge', 'campaign_lifecycle',
    'campaign_observation_publisher', 'campaign_participation', 'conversation_analysis',
    'entitlement_snapshot', 'purchase_handoff', 'web_risk_communication',
)


class MissingObject(Exception):
    pass


def aws(region, operation, *args):
    result = subprocess.run(['aws', '--region', region, 's3api', operation, *args,
                             '--output', 'json'], capture_output=True, text=True)
    if result.returncode:
        if operation == 'head-object' and '(404)' in result.stderr:
            raise MissingObject()
        raise RuntimeError('Artifact operation failed: ' + operation)
    return json.loads(result.stdout)


def inspect_packages(dist, source_sha):
    if not re.fullmatch('[0-9a-f]{40}', source_sha):
        raise ValueError('Exact source SHA required')
    dist = Path(dist)
    expected = {name + '.zip' for name in FUNCTIONS}
    if {p.name for p in dist.glob('*.zip')} != expected:
        raise ValueError('Exactly the nine candidate packages are required')
    entries = {}
    for line in (dist / 'SHA256SUMS').read_text().splitlines():
        digest, separator, name = line.partition('  ')
        if not separator or name in entries or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid checksum manifest')
        entries[name] = digest
    if set(entries) != expected:
        raise ValueError('Checksum scope does not match candidate scope')
    artifacts = []
    for function in FUNCTIONS:
        path = dist / (function + '.zip')
        content = path.read_bytes()
        digest = hashlib.sha256(content).digest()
        if entries[path.name] != digest.hex():
            raise ValueError('Package checksum mismatch')
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if 'app.py' not in names or len(names) != len(set(names)):
                raise ValueError('Invalid handler or duplicate archive entries')
            if any(name.startswith('/') or '..' in Path(name).parts for name in names):
                raise ValueError('Invalid archive path')
            dependencies = sorted(name.split('/')[0] for name in names
                                  if name.endswith('.dist-info/METADATA'))
        artifacts.append({'function': function, 'artifact': path.name,
                          'sha256': digest.hex(), 'sourceCodeHash': base64.b64encode(digest).decode(),
                          'bytes': len(content), 'runtime': 'python3.14', 'architecture': 'arm64',
                          'handler': 'app.lambda_handler', 'bundledDependencies': dependencies})
    return {'schemaVersion': 1, 'scope': 'research_candidate', 'sourceSha': source_sha,
            'environment': 'dev', 'runtimeUpdated': False, 'activationApproved': False,
            'artifacts': artifacts}


def smoke(dist):
    if sys.version_info[:2] != (3, 14):
        raise ValueError('Offline package check requires Python 3.14')
    for function in FUNCTIONS:
        path = Path(dist).resolve() / (function + '.zip')
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.endswith('.py'):
                    compile(archive.read(name), name, 'exec')
        env = dict(os.environ, AWS_ACCESS_KEY_ID='synthetic', AWS_SECRET_ACCESS_KEY='synthetic',
                   AWS_EC2_METADATA_DISABLED='true', AWS_DEFAULT_REGION='us-east-1')
        code = 'import sys; sys.path.insert(0,sys.argv[1]); import app; assert callable(app.lambda_handler)'
        subprocess.run([sys.executable, '-c', code, str(path)], env=env, check=True)
        print(function + ': offline compile/import passed; no invocation')


def publish(dist, bucket, source_sha, region, call=aws):
    if region != 'us-east-1' or bucket != 'trustcheckradar-dev-107827791950-artifacts':
        raise ValueError('Publication is restricted to the approved Dev artifact bucket')
    manifest = inspect_packages(dist, source_sha)  # Validate all bytes before the first AWS call.
    records = []
    for artifact in manifest['artifacts']:
        key = f"releases/{source_sha}/{artifact['artifact']}"
        checksum = artifact['sourceCodeHash']
        sha = artifact['sha256']
        try:
            existing = call(region, 'head-object', '--bucket', bucket, '--key', key,
                            '--checksum-mode', 'ENABLED')
            if existing.get('Metadata', {}).get('sha256') != sha or existing.get('ChecksumSHA256') != checksum:
                raise ValueError('Immutable artifact already exists with different content')
            version = existing.get('VersionId')
        except MissingObject:
            response = call(region, 'put-object', '--bucket', bucket, '--key', key,
                            '--body', str(Path(dist) / artifact['artifact']),
                            '--content-type', 'application/zip',
                            '--metadata', f'sha256={sha},source-sha={source_sha},target-runtime=python3.14,target-architecture=arm64',
                            '--checksum-sha256', checksum, '--if-none-match', '*')
            version = response.get('VersionId')
        if not isinstance(version, str) or not version or version == 'null':
            raise ValueError('Versioned artifact required')
        verified = call(region, 'head-object', '--bucket', bucket, '--key', key,
                        '--checksum-mode', 'ENABLED')
        if (verified.get('VersionId') != version or verified.get('ChecksumSHA256') != checksum
                or verified.get('Metadata', {}).get('sha256') != sha
                or verified.get('ContentLength') != artifact['bytes']):
            raise ValueError('Published artifact verification failed')
        records.append(dict(artifact, bucket=bucket, key=key, versionId=version))
    return dict(manifest, artifacts=records, publicationComplete=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist-dir', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--bucket')
    parser.add_argument('--region')
    args = parser.parse_args()
    manifest = inspect_packages(args.dist_dir, args.source_sha)
    if args.smoke:
        smoke(args.dist_dir)
    if args.publish:
        manifest = publish(args.dist_dir, args.bucket, args.source_sha, args.region)
    Path(args.output).write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
