#!/usr/bin/env python3
"""Record whether a main push changes only the independently released resolver."""
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
RESOLVER_PREFIXES = ('src/url_redirect_resolver/', 'tests/url_redirect_resolver/',
                     'docs/url-resolver-', 'docs/url-redirect-resolver', 'scripts/url_resolver_')


def classify(paths):
    paths = list(paths)
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
