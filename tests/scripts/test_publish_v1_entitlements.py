import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    'publish_v1_entitlements', Path(__file__).parents[2] / 'scripts/publish_v1_entitlements.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def package(tmp_path):
    body = b'fixture-entitlements'
    (tmp_path / 'v1_entitlements.zip').write_bytes(body)
    digest = hashlib.sha256(body).digest()
    (tmp_path / 'SHA256SUMS').write_text(digest.hex() + '  v1_entitlements.zip\n')
    return tmp_path, {
        'VersionId': 'version-1', 'ContentLength': len(body),
        'Metadata': {'sha256': digest.hex()},
        'ChecksumSHA256': base64.b64encode(digest).decode(),
    }


def test_publishes_only_entitlements_and_verifies_exact_current_version(package):
    path, expected = package
    calls = []
    def aws(region, operation, *args):
        calls.append((operation, args))
        if len(calls) == 1:
            raise module.MissingObject()
        return expected
    result = module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=aws)
    assert [item[0] for item in calls] == ['head-object', 'put-object', 'head-object']
    assert result['key'] == f"releases/{'a' * 40}/v1_entitlements.zip"
    assert result['sourceCodeHash'] == expected['ChecksumSHA256']
    assert calls[1][1][calls[1][1].index('--if-none-match') + 1] == '*'


def test_matching_artifact_is_reused_without_put(package):
    path, expected = package
    operations = []
    def aws(region, operation, *args):
        operations.append(operation)
        return expected
    module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=aws)
    assert operations == ['head-object', 'head-object']


def test_existing_different_artifact_is_never_overwritten(package):
    path, _ = package
    operations = []
    def aws(region, operation, *args):
        operations.append(operation)
        return {'VersionId': 'different'}
    with pytest.raises(ValueError, match='different content'):
        module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=aws)
    assert operations == ['head-object']


def test_corrupt_package_fails_before_cloud_call(package):
    path, _ = package
    (path / 'v1_entitlements.zip').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='checksum'):
        module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=lambda *args: (_ for _ in ()).throw(AssertionError()))


@pytest.mark.parametrize('changed', [
    {'VersionId': 'concurrent-replacement'},
    {'ContentLength': 1},
    {'ChecksumSHA256': 'wrong'},
])
def test_post_upload_replacement_or_mismatch_fails(package, changed):
    path, expected = package
    count = 0
    def aws(*args):
        nonlocal count
        count += 1
        if count == 1:
            raise module.MissingObject()
        if count == 2:
            return expected
        return {**expected, **changed}
    with pytest.raises(ValueError, match='verification'):
        module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=aws)
