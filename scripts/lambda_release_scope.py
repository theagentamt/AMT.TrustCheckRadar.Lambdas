#!/usr/bin/env python3
"""Classify a main push for resolver, contract-only, or broad artifact publication."""
import argparse
import json
import re
import subprocess
from pathlib import Path

SHARED_REVIEWED_PATHS = {
    '.github/workflows/ci.yml', '.github/workflows/publish.yml',
    'README.md', 'requirements-dev.txt', 'lambda_deployment_dependency_contract.md',
    'scripts/build_lambda_zip.sh', 'scripts/upload_lambda_zips.sh',
    'scripts/lambda_release_scope.py', 'scripts/publish_url_resolver.py',
    'tests/scripts/test_lambda_release_scope.py', 'tests/scripts/test_publish_url_resolver.py',
    'docs/GITHUB_PUBLISHING.md',
}
MESSAGE_PREFIXES = ('src/message_consumer/', 'src/message_evaluator/', 'src/shared_message_contract/', 'tests/message_consumer/', 'tests/message_evaluator/', 'contracts/message-consumer/', 'docs/sec229-message-', 'docs/message-consumer-')
AUTHORITY_PREFIXES = MESSAGE_PREFIXES + ('src/shared_check_authority/', 'tests/shared_check_authority/', 'docs/v1-check-authority',
    'src/url_consumer/', 'src/url_lease_recovery/', 'src/v1_entitlements/', 'src/v1_authority_deletion/',
    'contracts/url-consumer/', 'contracts/v1-access/', 'docs/v1-entitlement-', 'docs/url-consumer-',
    'src/url_assessment/', 'tests/url_assessment/')
AUTHORITY_SUPPORT_PATHS = {
    'scripts/url_consumer_engineering_smoke.py', 'tests/scripts/test_url_consumer_engineering_smoke.py',
    'src/account_data_api/config.py', 'src/account_data_api/service.py',
    'tests/account_data_api/test_service.py', 'tests/account_data_api/test_handler.py',
    '.github/workflows/ci.yml', '.github/workflows/publish.yml',
    'scripts/lambda_release_scope.py', 'tests/scripts/test_lambda_release_scope.py',
    'docs/GITHUB_PUBLISHING.md', 'scripts/build_lambda_zip.sh', 'docs/atcr120-next-message-binding-slice.md',
    'tests/scripts/authority_release_paths.json', 'scripts/verify_v1_packages.py', 'tests/contracts/test_v1_access_contract.py', 'tests/contracts/test_url_consumer_transport.py',
}
ASSESSMENT_PREFIXES = ('src/url_assessment/', 'tests/url_assessment/', 'docs/url-assessment-', 'scripts/url_assessment_')
ASSESSMENT_SUPPORT_PATHS = {
    '.github/workflows/ci.yml', '.github/workflows/publish.yml',
    'scripts/build_lambda_zip.sh', 'scripts/lambda_release_scope.py',
    'tests/scripts/test_lambda_release_scope.py', 'docs/GITHUB_PUBLISHING.md',
}
CONTRACT_PREFIXES = ('contracts/url-assessment/v1-draft/', 'tests/url_assessment_contracts/')
CONTRACT_SUPPORT_PATHS = {
    '.github/workflows/publish.yml', 'scripts/lambda_release_scope.py',
    'tests/scripts/test_lambda_release_scope.py', 'docs/GITHUB_PUBLISHING.md',
}
RESOLVER_PREFIXES = ('src/url_redirect_resolver/', 'tests/url_redirect_resolver/',
                     'docs/url-resolver-', 'docs/url-redirect-resolver', 'scripts/url_resolver_')


def classify(paths):
    paths = list(paths)
    authority = lambda path: path.startswith(AUTHORITY_PREFIXES)
    if paths and any(path.startswith(MESSAGE_PREFIXES + ('src/shared_check_authority/', 'src/url_consumer/', 'src/url_lease_recovery/', 'src/v1_entitlements/', 'src/v1_authority_deletion/', 'contracts/url-consumer/', 'contracts/v1-access/')) for path in paths) and all(
        authority(path) or path in AUTHORITY_SUPPORT_PATHS for path in paths
    ):
        return 'authority_manual'
    assessment = lambda path: path.startswith(ASSESSMENT_PREFIXES)
    if paths and any(assessment(path) for path in paths) and all(
        assessment(path) or path in ASSESSMENT_SUPPORT_PATHS for path in paths
    ):
        return 'assessment_manual'
    contract = lambda path: path.startswith(CONTRACT_PREFIXES)
    if paths and any(contract(path) for path in paths) and all(
        contract(path) or path in CONTRACT_SUPPORT_PATHS for path in paths
    ):
        return 'contracts'
    resolver = lambda path: path.startswith(RESOLVER_PREFIXES)
    if paths and any(resolver(path) for path in paths) and all(
        resolver(path) or path in SHARED_REVIEWED_PATHS for path in paths
    ):
        return 'resolver'
    return 'all'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    for revision in (args.base, args.head):
        if not re.fullmatch('[0-9a-f]{40}', revision) or revision == '0' * 40:
            parser.error('Expected nonzero full Git commit IDs')
    subprocess.run(['git', 'merge-base', '--is-ancestor', args.base, args.head], check=True)
    diff = subprocess.check_output(['git', 'diff', '--name-only', '-z', args.base, args.head])
    paths = diff.decode().rstrip('\0').split('\0') if diff else []
    manifest = {'schemaVersion': 1, 'baseSha': args.base, 'headSha': args.head, 'scope': classify(paths)}
    Path(args.output).write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
