import json
import logging
from pathlib import Path
import sys
from unittest.mock import Mock
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from v1_entitlements import admission_diagnostics, snapshot_gate
from shared_check_authority.core import AuthorityError

BASE, EXTRA, OTHER = ['00000000-0000-0000-0000-00000000000' + str(i) for i in (1, 2, 3)]
MARKER = 'private-identity-claim-payload-marker'


def original_gate():
    with zipfile.ZipFile(ROOT / 'tests/shared_check_authority/fixtures/dev-access-reader-8ecc.zip') as archive:
        source = archive.read('v1_entitlements/snapshot_gate.py')
    namespace = {'__name__': 'v1_entitlements.baseline_snapshot_gate', '__package__': 'v1_entitlements'}
    exec(compile(source, '<pinned-original-gate>', 'exec'), namespace)
    return namespace['trial_access_for_snapshot']


def event(subject=EXTRA):
    return {'routeKey': 'GET /v1/access', 'requestContext': {'http': {'method': 'GET'},
        'authorizer': {'jwt': {'claims': {'sub': subject, 'iss': 'https://cognito-idp.example.test/pool',
            'client_id': 'synthetic-client', 'scope': 'aws.cognito.signin.user.admin',
            'token_use': 'access', 'exp': 2000}}}}, 'body': MARKER}


def outcome(fn, value):
    try:
        return ('accepted', fn(value))
    except AuthorityError as error:
        return ('rejected', error.code)


@pytest.mark.parametrize('case,expected_branch', [
    ('base', None), ('extra', None), ('base_bad_extra', None),
    ('other', 'SUBJECT_NOT_ADMITTED'), ('expired', 'VERIFIED_CLAIMS'),
    ('bad_claim', 'VERIFIED_CLAIMS'), ('bad_base', 'BASE_GATE_CONFIGURATION'),
    ('bad_extra', 'EXTRA_GATE_CONFIGURATION'), ('bad_settings', 'CLAIM_CONFIGURATION'),
    ('bad_route', 'ROUTE_SHAPE'), ('non_dev', None),
])
def test_differential_admission_unchanged_and_fixed_failure_branch(case, expected_branch, monkeypatch, caplog):
    values = {'STAGE': 'dev', 'DEV_SUBJECT_ALLOWLIST_JSON': json.dumps([BASE]),
              'DEV_ACCESS_SNAPSHOT_EXTRA_SUBJECTS_JSON': json.dumps([EXTRA]),
              'COGNITO_ISSUER': 'https://cognito-idp.example.test/pool',
              'COGNITO_APP_CLIENT_ID': 'synthetic-client', 'COGNITO_REQUIRED_SCOPE': 'aws.cognito.signin.user.admin'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(snapshot_gate.time, 'time', lambda: 1000)
    value = event(BASE if case in ('base', 'base_bad_extra') else OTHER if case == 'other' else EXTRA)
    if case in ('bad_extra', 'base_bad_extra'):
        monkeypatch.setenv('DEV_ACCESS_SNAPSHOT_EXTRA_SUBJECTS_JSON', MARKER)
    if case == 'bad_base':
        monkeypatch.setenv('DEV_SUBJECT_ALLOWLIST_JSON', MARKER)
    if case == 'bad_settings':
        monkeypatch.setenv('COGNITO_REQUIRED_SCOPE', MARKER)
    if case == 'expired':
        value['requestContext']['authorizer']['jwt']['claims']['exp'] = 999
    if case == 'bad_claim':
        value['requestContext']['authorizer']['jwt']['claims']['client_id'] = MARKER
    if case == 'bad_route':
        value['routeKey'] = MARKER
    if case == 'non_dev':
        monkeypatch.setenv('STAGE', 'production')
    before = outcome(original_gate(), value)
    after = outcome(snapshot_gate.trial_access_for_snapshot, value)
    assert before == after
    if expected_branch:
        record = json.loads(caplog.records[-1].getMessage())
        assert record == {'event': 'dev_access_admission_failure', 'schemaVersion': 1, 'branch': expected_branch}
        assert before == ('rejected', 'ENGINEERING_ACCESS_UNAVAILABLE')
    else:
        assert not caplog.records
    assert MARKER not in caplog.text and EXTRA not in caplog.text


def test_logging_failure_cannot_change_rejection(monkeypatch):
    monkeypatch.setattr(logging.getLogger(admission_diagnostics.__name__), 'warning', Mock(side_effect=RuntimeError(MARKER)))
    monkeypatch.setenv('STAGE', 'dev')
    assert outcome(snapshot_gate.trial_access_for_snapshot, {}) == ('rejected', 'ENGINEERING_ACCESS_UNAVAILABLE')


@pytest.mark.parametrize('branch', [MARKER, [], None, True])
def test_unknown_branch_never_logs_input(branch, monkeypatch, caplog):
    monkeypatch.setenv('STAGE', 'dev')
    assert admission_diagnostics.record_failure(branch)
    assert json.loads(caplog.records[-1].getMessage())['branch'] == 'UNKNOWN'
    assert MARKER not in caplog.text
