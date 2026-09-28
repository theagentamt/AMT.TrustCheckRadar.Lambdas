"""Human attestation CLI with real local RSA/Moto; no actual mailbox/IAM proof."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from moto import mock_aws
import pytest

ROOT = Path(__file__).resolve().parents[2]
fixture_spec = importlib.util.spec_from_file_location('support_verifier_admission_fixture',
    Path(__file__).with_name('test_admission_dynamodb.py'))
fixture_module = importlib.util.module_from_spec(fixture_spec); fixture_spec.loader.exec_module(fixture_module)
Fixture, key, NOW, SUBJECT, ACCOUNT, C = [getattr(fixture_module, name)
    for name in ('Fixture', 'key', 'NOW', 'SUBJECT', 'ACCOUNT', 'C')]
spec = importlib.util.spec_from_file_location('support_verifier_cli', ROOT / 'scripts/support_deletion_verifier.py')
V = importlib.util.module_from_spec(spec); spec.loader.exec_module(V)


@pytest.fixture(autouse=True)
def enabled_candidate(monkeypatch):
    monkeypatch.setenv('SUPPORT_ACCOUNT_DELETION_ENABLED', 'true')


class Setup:
    def __init__(self, key, tmp_path):
        self.f = Fixture(key)
        self.p = dict(schemaVersion=1, method='trusted-human-shared-inbox-challenge-reply-v1',
            signerRoleArn=f'arn:aws:iam::{ACCOUNT}:role/SupportVerifier', signerRoleId='AROA' + 'B' * 17,
            custodyReference=str(uuid4()), auditLocation='same-support-case', retentionAfterResolutionDays=30,
            roleSeparation='same-owner-separate-restricted-roles', challengeValiditySeconds=86400,
            maximumReplyAgeSeconds=86400, capabilityValiditySeconds=120)
        self.f.c['verificationPolicySha256'] = V.digest(self.p)
        self.case = str(uuid4()); self.directory = tmp_path / self.case
        self.directory.mkdir(mode=0o700)
        self.signs = 0; self.after_sign = lambda: None
        self.identity = dict(Account=ACCOUNT, Arn=f'arn:aws:sts::{ACCOUNT}:assumed-role/SupportVerifier/session',
            UserId=self.p['signerRoleId'] + ':session')
        self.v = V.Verifier(self.f.c, self.p,
            dict(sts=self, kms=self, dynamodb=self.f, **{'cognito-idp': self.f}), now=lambda: self.f.clock)

    def get_caller_identity(self):
        return self.identity

    def describe_key(self, **kw):
        assert kw == {'KeyId': self.f.c['kmsKeyArn']}
        return {'KeyMetadata': dict(Arn=self.f.c['kmsKeyArn'], KeySpec='RSA_3072', KeyUsage='SIGN_VERIFY',
            KeyState='Enabled', Origin='AWS_KMS', KeyManager='CUSTOMER', MultiRegion=False,
            SigningAlgorithms=[C.ALGORITHM])}

    def sign(self, **kw):
        self.signs += 1
        assert kw['KeyId'] == self.f.c['kmsKeyArn'] and kw['MessageType'] == 'RAW' and kw['SigningAlgorithm'] == C.ALGORITHM
        signature = self.f.key.sign(kw['Message'], padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
        self.after_sign()
        return dict(KeyId=self.f.c['kmsKeyArn'], SigningAlgorithm=C.ALGORITHM, Signature=signature)

    def prepare(self):
        result = self.v.prepare(SUBJECT, self.case, NOW - 86400 * 7, self.directory / 'challenge.json')
        assert result == {'schemaVersion': 1, 'stage': 'CHALLENGE_PREPARED', 'emailSent': False}
        self.challenge = V.private_read(self.directory / 'challenge.json')
        self.a = dict(schemaVersion=1, caseReference=self.case, challengeSha256=V.digest(self.challenge),
            returnedChallenge=self.challenge['challenge'], replyReceivedAtEpoch=NOW,
            ownershipVerifiedAtEpoch=NOW, reviewedActualSharedInboxReply=True, explicitDeletionConfirmed=True)
        self.intent = self.directory / (self.challenge['operationId'] + '.sign-intent.json')
        self.output = self.directory / (self.challenge['operationId'] + '.capability.json')
        return self

    def issue(self):
        return self.v.sign(self.challenge, self.a, self.intent, self.output)


@mock_aws
def test_actual_sign_to_existing_admission_and_exact_replay(key, tmp_path):
    s = Setup(key, tmp_path).prepare(); before = s.f.rows()
    assert s.issue()['stage'] == 'CAPABILITY_WRITTEN'
    assert s.f.rows() == before and s.signs == 1
    body = V.private_read(s.output); s.f.record = body['record']
    event = s.f.event(); event['body'] = C.canonical(body).decode()
    accepted = s.f.run(event)
    assert accepted['admission'] == 'ACCEPTED' and not accepted['completionVerified']
    rows = s.f.rows()
    assert s.f.run(event) == accepted and s.f.rows() == rows and s.f.write_count == 1
    assert s.f.record['requestReceivedAtEpoch'] == NOW - 86400 * 7
    assert set(V.private_read(s.intent)) == {'schemaVersion', 'caseReference', 'operationId', 'verifier', 'confirmationTime', 'outcome'}
    assert s.output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('change', [
    lambda s: s.identity.update(Arn=f'arn:aws:sts::{ACCOUNT}:assumed-role/VerifiedSupport/session'),
    lambda s: s.identity.update(UserId='AROA' + 'C' * 17 + ':session'),
    lambda s: s.a.update(explicitDeletionConfirmed=False),
    lambda s: s.a.update(reviewedActualSharedInboxReply='true'),
    lambda s: s.a.update(returnedChallenge='x' * 43),
    lambda s: s.a.update(challengeSha256='0' * 64),
    lambda s: s.a.update(ownershipVerifiedAtEpoch=True),
    lambda s: s.a.update(replyReceivedAtEpoch=NOW - 1),
    lambda s: s.a.update(extra='unknown'),
    lambda s: s.challenge.update(configurationSha256='0' * 64),
    lambda s: s.challenge.update(expiresAtEpoch=NOW + 86401),
    lambda s: setattr(s.f, 'clock', NOW + 120),
])
@mock_aws
def test_sign_refuses_without_signing_or_writing_accounts(key, tmp_path, change):
    s = Setup(key, tmp_path).prepare(); before = s.f.rows(); change(s)
    with pytest.raises(C.Unavailable): s.issue()
    assert s.signs == 0 and not s.output.exists() and s.f.rows() == before


@pytest.mark.parametrize('field,value', [('retentionAfterResolutionDays', 120),
    ('auditLocation', 'separate-audit'), ('roleSeparation', 'two-person'),
    ('challengeValiditySeconds', 86401), ('capabilityValiditySeconds', 301),
    ('maximumReplyAgeSeconds', True)])
@mock_aws
def test_policy_no_implicit_defaults_or_retention_expansion(key, tmp_path, field, value):
    s = Setup(key, tmp_path); s.p[field] = value
    s.f.c['verificationPolicySha256'] = V.digest(s.p)
    with pytest.raises(C.Unavailable): V.policy(s.f.c, s.p)


@pytest.mark.parametrize('mode', ['profile', 'email_unverified', 'inventory', 'existing_command'])
@mock_aws
def test_changed_binding_refuses_before_sign(key, tmp_path, mode):
    s = Setup(key, tmp_path).prepare()
    if mode == 'profile':
        s.f.profile['given_name'] = 'Changed'; s.f.users.put_item(Item=s.f.profile)
    elif mode == 'email_unverified':
        original = s.f.admin_get_user
        def unverified(**kw):
            response = original(**kw)
            for a in response['UserAttributes']:
                if a['Name'] == 'email_verified': a['Value'] = 'false'
            return response
        s.f.admin_get_user = unverified
    elif mode == 'inventory':
        s.f.inventory['revision'] = 2; s.f.ledger.put_item(Item=s.f.inventory)
    else:
        s.f.ledger.put_item(Item={'PK': 'ACCOUNT#' + SUBJECT, 'SK': 'ACCOUNT_DELETION'})
    before = s.f.rows()
    with pytest.raises(Exception): s.issue()
    assert s.signs == 0 and s.f.rows() == before and not s.intent.exists()


@pytest.mark.parametrize('mode', ['lost_ack', 'expired', 'binding_changed'])
@mock_aws
def test_ambiguous_or_late_sign_preserves_intent_no_retry(key, tmp_path, mode):
    s = Setup(key, tmp_path).prepare()
    def after():
        if mode == 'lost_ack': raise TimeoutError('provider-secret-must-not-print')
        if mode == 'expired': s.f.clock += 120
        if mode == 'binding_changed':
            s.f.profile['given_name'] = 'Changed'; s.f.users.put_item(Item=s.f.profile)
    s.after_sign = after
    with pytest.raises(Exception): s.issue()
    assert s.intent.exists() and not s.output.exists() and s.signs == 1
    s.f.clock = NOW; s.after_sign = lambda: None
    s.f.profile['given_name'] = 'Test'; s.f.users.put_item(Item=s.f.profile)
    s.v = V.Verifier(s.f.c, s.p, s.v.clients, now=lambda: s.f.clock)
    with pytest.raises(FileExistsError): s.issue()
    assert s.signs == 1


@mock_aws
def test_prepare_exclusive_no_email_and_no_replace(key, tmp_path):
    s = Setup(key, tmp_path).prepare(); first = (s.directory / 'challenge.json').read_bytes()
    with pytest.raises(FileExistsError): s.v.prepare(SUBJECT, s.case, NOW, s.directory / 'challenge.json')
    assert (s.directory / 'challenge.json').read_bytes() == first and not s.f.write_count and not s.signs


def test_private_files_reject_duplicate_noncanonical_symlink_and_permissions(tmp_path):
    path = tmp_path / 'input.json'
    for raw in (b'{"a":1,"a":2}', b'{ "a":1}', b'{"a":NaN}'):
        path.write_bytes(raw); path.chmod(0o600)
        with pytest.raises(Exception): V.private_read(path)
    path.write_bytes(b'{"a":1}'); path.chmod(0o644)
    with pytest.raises(C.Unavailable): V.private_read(path)
    alias = tmp_path / 'alias'; alias.symlink_to(path)
    with pytest.raises(OSError): V.private_read(alias)


@mock_aws
def test_cli_default_offline_without_sdk_and_content_free(key, tmp_path):
    s = Setup(key, tmp_path)
    config = s.directory / 'config.json'; reviewed = s.directory / 'policy.json'
    V.exclusive(config, s.f.c); V.exclusive(reviewed, s.p)
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/support_deletion_verifier.py'),
        '--configuration', str(config), '--policy', str(reviewed)], capture_output=True, text=True,
        env={**os.environ, 'AWS_EC2_METADATA_DISABLED': 'true'})
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == {'schemaVersion': 1, 'stage': 'OFFLINE_INPUTS_VALID', 'policyApprovalInferred': False}
    assert SUBJECT not in result.stdout and s.p['signerRoleArn'] not in result.stdout


@mock_aws
def test_admission_rechecks_verified_email_first_request_only(key, tmp_path):
    s = Setup(key, tmp_path).prepare(); s.issue()
    body = V.private_read(s.output); s.f.record = body['record']
    event = s.f.event(); event['body'] = C.canonical(body).decode()
    original = s.f.admin_get_user
    def missing_verified(**kw):
        result = original(**kw)
        result['UserAttributes'] = [a for a in result['UserAttributes'] if a['Name'] != 'email_verified']
        return result
    s.f.admin_get_user = missing_verified
    before = s.f.rows()
    with pytest.raises(C.Unavailable): s.f.run(event)
    assert s.f.rows() == before and s.f.write_count == 0
    s.f.admin_get_user = original; s.f.run(event)
    s.f.admin_get_user = lambda **kw: pytest.fail('authorized original replay must not need deleted profile/identity')
    assert s.f.run(event)['admission'] == 'ACCEPTED' and s.f.write_count == 1


@pytest.mark.parametrize('field,value', [('KeySpec', 'RSA_2048'), ('KeyState', 'Disabled'),
    ('MultiRegion', True), ('Arn', 'arn:aws:kms:us-east-1:000000000000:key/foreign')])
@mock_aws
def test_wrong_key_metadata_never_creates_sign_intent(key, tmp_path, field, value):
    s = Setup(key, tmp_path).prepare(); original = s.describe_key
    def altered(**kw):
        result = original(**kw); result['KeyMetadata'][field] = value; return result
    s.describe_key = altered
    with pytest.raises(C.Unavailable): s.issue()
    assert s.signs == 0 and not s.intent.exists()


@mock_aws
def test_output_failure_preserves_attempt_and_forbids_second_sign(key, tmp_path, monkeypatch):
    s = Setup(key, tmp_path).prepare(); original = V.exclusive
    def fail_output(path, value):
        if Path(path) == s.output: raise OSError('disk failure')
        return original(path, value)
    monkeypatch.setattr(V, 'exclusive', fail_output)
    with pytest.raises(OSError): s.issue()
    assert s.signs == 1 and s.intent.exists() and not s.output.exists()
    monkeypatch.setattr(V, 'exclusive', original)
    with pytest.raises(FileExistsError): s.issue()
    assert s.signs == 1


@pytest.mark.parametrize('failed_call', [1, 2])
@mock_aws
def test_intent_fsync_failure_never_signs_and_keeps_attempt(key, tmp_path, monkeypatch, failed_call):
    s = Setup(key, tmp_path).prepare()
    original = V.os.fsync; calls = []
    def failure(fd):
        calls.append(fd)
        if len(calls) == failed_call: raise OSError('fsync failure')
        return original(fd)
    with monkeypatch.context() as patch:
        patch.setattr(V.os, 'fsync', failure)
        with pytest.raises(OSError): s.issue()
    assert s.intent.exists() and not s.output.exists() and s.signs == 0
    with pytest.raises(FileExistsError): s.issue()
    assert s.signs == 0
