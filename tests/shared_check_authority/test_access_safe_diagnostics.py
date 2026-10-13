import hashlib
import importlib.util
import io
import json
import logging
from pathlib import Path
import sys
import zipfile
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from shared_check_authority.core import AuthorityError
from v1_entitlements import diagnostics
from botocore.exceptions import ClientError

spec = importlib.util.spec_from_file_location('diagnostic_package', ROOT / 'scripts/prepare_access_snapshot_diagnostics.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
BASELINE = (ROOT / 'tests/shared_check_authority/fixtures/dev-access-reader-8ecc.zip').read_bytes()
CLASSIFIER = (ROOT / 'src/v1_entitlements/diagnostics.py').read_bytes()
GET = {'routeKey': 'GET /v1/access', 'requestContext': {'http': {'method': 'GET'}}}
PRIVATE_MARKER = 'test-private-identity-token-payload-marker'


def test_exact_deployed_overlay_reproducible_and_preserves_unrelated_entries():
    first = package.build(BASELINE, CLASSIFIER)
    second = package.build(BASELINE, CLASSIFIER)
    assert first == second
    assert hashlib.sha256(first).hexdigest() == package.EXPECTED_SHA256
    with zipfile.ZipFile(io.BytesIO(BASELINE)) as old, zipfile.ZipFile(io.BytesIO(first)) as new:
        assert set(new.namelist()) - set(old.namelist()) == {package.DIAGNOSTICS}
        assert all(new.read(name) == old.read(name) for name in old.namelist() if name != package.APP)
        assert new.read('app.py') == old.read('app.py')
        for name in old.namelist():
            previous, actual = old.getinfo(name), new.getinfo(name)
            for field in ('date_time', 'compress_type', 'external_attr', 'extra', 'comment', 'create_system'):
                assert getattr(previous, field) == getattr(actual, field)


@pytest.mark.parametrize('bad', [b'', BASELINE[:-1], BASELINE + b'changed'])
def test_baseline_changes_fail_closed(bad):
    with pytest.raises(ValueError):
        package.build(bad, CLASSIFIER)


def test_classifier_changes_fail_expected_package_pin():
    with pytest.raises(ValueError):
        package.build(BASELINE, CLASSIFIER + b'\n# changed\n')


def test_handler_structure_preserved_and_response_callback_guarded():
    with zipfile.ZipFile(io.BytesIO(BASELINE)) as old:
        source = package.instrument(old.read(package.APP)).decode()
    assert 'pass  # Diagnostics must never alter the response or trigger a retry.' in source
    assert "trial_activation_permitted = trial_access_for_snapshot(event)" in source
    assert "trial_activation_permitted=trial_activation_permitted" in source


def test_suppressed_sdk_context_records_only_fixed_classifications(caplog):
    try:
        try:
            raise ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': PRIVATE_MARKER}}, 'GetSecretValue')
        except ClientError:
            raise AuthorityError('KEY_INVENTORY_UNAVAILABLE') from None
    except AuthorityError as error:
        with caplog.at_level(logging.WARNING):
            assert diagnostics.record_get_failure(GET, error)
    value = json.loads(caplog.records[-1].getMessage())
    assert value['providerCode'] == 'AccessDeniedException'
    assert value['authorityCode'] == 'KEY_INVENTORY_UNAVAILABLE'
    assert value['causeCategory'] == 'AWS_CLIENT'
    assert PRIVATE_MARKER not in caplog.text
    assert set(value) == {'event', 'schemaVersion', 'stage', 'authorityCode', 'providerCode', 'causeCategory'}


@pytest.mark.parametrize('event', [None, {}, {'routeKey': 'POST /v1/access/trial', 'requestContext': {'http': {'method': 'POST'}}}, {'routeKey': 'GET /v1/access', 'requestContext': {'http': {'method': 'POST'}}}])
def test_non_get_failure_does_not_log(event, caplog):
    assert diagnostics.record_get_failure(event, RuntimeError(PRIVATE_MARKER)) is False
    assert not caplog.records


def test_unknown_codes_messages_and_cycle_never_expose_marker():
    error = AuthorityError(PRIVATE_MARKER)
    error.__context__ = error
    value = diagnostics.classify(error)
    assert value['authorityCode'] == 'UNKNOWN'
    assert PRIVATE_MARKER not in json.dumps(value)
    provider = ClientError({'Error': {'Code': PRIVATE_MARKER, 'Message': PRIVATE_MARKER}}, 'GetItem')
    assert diagnostics.classify(provider)['providerCode'] == 'NONE'


def test_logging_failure_cannot_escape_handler():
    with patch.object(logging.getLogger(diagnostics.__name__), 'warning', side_effect=RuntimeError(PRIVATE_MARKER)):
        assert diagnostics.record_get_failure(GET, RuntimeError(PRIVATE_MARKER)) is False
