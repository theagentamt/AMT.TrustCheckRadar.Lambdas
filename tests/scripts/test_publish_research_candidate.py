import base64
import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location(
    'publish_research_candidate', Path(__file__).parents[2] / 'scripts/publish_research_candidate.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BUCKET = 'trustcheckradar-dev-107827791950-artifacts'
SHA = 'a' * 40


@pytest.fixture
def packages(tmp_path):
    for function in module.FUNCTIONS:
        with zipfile.ZipFile(tmp_path / (function + '.zip'), 'w') as archive:
            archive.writestr('app.py', 'def lambda_handler(event, context): return None\n')
    checksums(tmp_path)
    return tmp_path


def checksums(path):
    (path / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
        for p in sorted(path.glob('*.zip'))))


def cloud(path, *, existing=False, replace=False):
    calls = []
    heads = {}
    def call(region, operation, *args):
        calls.append((operation, args))
        key = args[args.index('--key') + 1]
        content = (path / key.split('/')[-1]).read_bytes()
        digest = hashlib.sha256(content).digest()
        response = {'VersionId': 'v1', 'ContentLength': len(content),
                    'Metadata': {'sha256': digest.hex()},
                    'ChecksumSHA256': base64.b64encode(digest).decode()}
        if operation == 'head-object':
            heads[key] = heads.get(key, 0) + 1
            if heads[key] == 1 and not existing:
                raise module.MissingObject()
            if replace and heads[key] > 1:
                response['VersionId'] = 'concurrent-replacement'
        return response
    return call, calls


def test_nine_versioned_puts_are_conditional_and_never_deploy(packages):
    call, calls = cloud(packages)
    result = module.publish(packages, BUCKET, SHA, 'us-east-1', call)
    assert len(result['artifacts']) == 9
    assert result['publicationComplete'] is True
    assert result['runtimeUpdated'] is False and result['activationApproved'] is False
    assert len(calls) == 27
    assert {op for op, _ in calls} == {'head-object', 'put-object'}
    for op, args in calls:
        assert args[args.index('--key') + 1].startswith('releases/' + SHA + '/')
        if op == 'put-object':
            assert args[args.index('--if-none-match') + 1] == '*'
    assert all(row['versionId'] == 'v1' for row in result['artifacts'])


def test_exact_retry_reuses_matching_versions_without_put(packages):
    call, calls = cloud(packages, existing=True)
    module.publish(packages, BUCKET, SHA, 'us-east-1', call)
    assert len(calls) == 18
    assert all(op == 'head-object' for op, _ in calls)


def test_concurrent_current_version_change_fails_closed(packages):
    call, calls = cloud(packages, replace=True)
    with pytest.raises(ValueError, match='verification'):
        module.publish(packages, BUCKET, SHA, 'us-east-1', call)
    assert len(calls) == 3


@pytest.mark.parametrize('response', [
    {'VersionId': 'v1', 'Metadata': {'sha256': 'other'}},
    {'VersionId': 'null'},
])
def test_existing_mismatched_content_never_overwritten(packages, response):
    calls = []
    def call(region, op, *args):
        calls.append(op)
        return response
    with pytest.raises(ValueError, match='different content'):
        module.publish(packages, BUCKET, SHA, 'us-east-1', call)
    assert calls == ['head-object']


@pytest.mark.parametrize('mutation', ['corrupt', 'extra', 'missing', 'duplicate_checksum', 'traversal', 'no_handler'])
def test_entire_scope_validated_before_any_aws_call(packages, mutation):
    first = packages / (module.FUNCTIONS[0] + '.zip')
    if mutation == 'corrupt':
        first.write_bytes(b'corrupt')
    elif mutation == 'extra':
        (packages / 'unreviewed.zip').write_bytes(b'content')
    elif mutation == 'missing':
        first.unlink()
    elif mutation == 'duplicate_checksum':
        manifest = packages / 'SHA256SUMS'
        manifest.write_text(manifest.read_text() * 2)
    else:
        with zipfile.ZipFile(first, 'w') as archive:
            archive.writestr('../app.py' if mutation == 'traversal' else 'other.py', 'pass')
            if mutation == 'traversal':
                archive.writestr('app.py', 'pass')
        checksums(packages)
    def no_cloud(*args):
        raise AssertionError('No AWS call allowed')
    with pytest.raises(ValueError):
        module.publish(packages, BUCKET, SHA, 'us-east-1', no_cloud)


@pytest.mark.parametrize('bucket,region,sha', [
    ('prod', 'us-east-1', SHA), (BUCKET, 'us-west-2', SHA),
    (BUCKET, 'us-east-1', 'release-V01'),
])
def test_wrong_target_or_unpinned_source_never_contacts_aws(packages, bucket, region, sha):
    def no_cloud(*args):
        raise AssertionError('No AWS call allowed')
    with pytest.raises(ValueError):
        module.publish(packages, bucket, sha, region, no_cloud)
