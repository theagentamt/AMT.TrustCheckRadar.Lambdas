import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('publish_url_resolver', Path(__file__).parents[2] / 'scripts/publish_url_resolver.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def package(tmp_path):
    (tmp_path / 'url_redirect_resolver.zip').write_bytes(b'fixture-resolver')
    digest = hashlib.sha256(b'fixture-resolver').digest()
    (tmp_path / 'SHA256SUMS').write_text(digest.hex() + '  url_redirect_resolver.zip\n')
    return tmp_path, {'VersionId': 'version-1', 'ContentLength': len(b'fixture-resolver'), 'Metadata': {'sha256': digest.hex()}, 'ChecksumSHA256': base64.b64encode(digest).decode()}


def test_publishes_only_resolver_conditionally_and_verifies_exact_version(package):
    path, expected = package
    calls = []
    def aws(region, operation, *args):
        calls.append((operation, args))
        if len(calls) == 1:
            raise module.MissingObject()
        return expected
    result = module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=aws)
    assert [call[0] for call in calls] == ['head-object', 'put-object', 'head-object']
    assert calls[1][1][calls[1][1].index('--if-none-match') + 1] == '*'
    assert '--version-id' not in calls[2][1]
    assert result['versionId'] == 'version-1'
    assert result['key'] == 'releases/' + 'a' * 40 + '/url_redirect_resolver.zip'
    assert result['sourceCodeHash'] == expected['ChecksumSHA256']


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


def test_corrupt_package_fails_before_any_cloud_call(package):
    path, _ = package
    (path / 'url_redirect_resolver.zip').write_bytes(b'tampered')
    def aws(*args):
        raise AssertionError('AWS must not be contacted')
    with pytest.raises(ValueError, match='checksum'):
        module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=aws)


def test_missing_version_is_rejected(package):
    path, expected = package
    with pytest.raises(ValueError, match='versioned'):
        module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=lambda *args: {**expected, 'VersionId': 'null'})


def test_post_upload_checksum_mismatch_fails(package):
    path, expected = package
    count = 0
    def aws(*args):
        nonlocal count
        count += 1
        if count == 1:
            raise module.MissingObject()
        if count == 2:
            return expected
        return {**expected, 'ChecksumSHA256': 'wrong'}
    with pytest.raises(ValueError, match='verification'):
        module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=aws)


@pytest.mark.parametrize('changed', [
    {'VersionId': 'concurrent-replacement'},
    {'ContentLength': 1},
])
def test_current_object_replacement_or_size_mismatch_fails_without_new_permission(package, changed):
    path, expected = package
    calls = []
    def aws(region, operation, *args):
        calls.append(args)
        if len(calls) == 1:
            raise module.MissingObject()
        if len(calls) == 2:
            return expected
        return {**expected, **changed}
    with pytest.raises(ValueError, match='verification'):
        module.publish(path, 'bucket', 'a' * 40, 'us-east-1', call=aws)
    assert all('--version-id' not in args for args in calls)
