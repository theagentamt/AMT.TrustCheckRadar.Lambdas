"""Real local RSA signatures + Moto DynamoDB; injected resource identity/Cognito.

No deployed IAM authentication, signer ownership procedure, or cloud acceptance.
"""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from moto import mock_aws
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from support_account_deletion import app, contract as C, runtime as R
from shared_account_finalization.service import REQUIRED_COMPONENTS

NOW = 1800000000
SUBJECT = '01993d31-cafe-7abc-8abc-0123456789ab'
OTHER = '01993d31-cafe-7abc-8abc-0123456789ac'
ACCOUNT = '123456789012'


@pytest.fixture(scope='module')
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


class Fixture:
    def __init__(self, key):
        self.key, self.clock, self.budget = key, NOW, 30000
        self.calls, self.write_count = [], 0
        self.before_tx = lambda: None
        self.after_tx = lambda: None
        self.before_verify = lambda: None
        self.c = {
            'environment': 'dev', 'accountId': ACCOUNT, 'region': 'us-east-1',
            'functionArn': f'arn:aws:lambda:us-east-1:{ACCOUNT}:function:trustcheckradar-dev-support-account-deletion',
            'apiId': 'abcdefghij', 'stage': 'dev',
            'operatorRoleArn': f'arn:aws:iam::{ACCOUNT}:role/VerifiedSupport',
            'operatorRoleId': 'AROA' + 'A' * 17,
            'kmsKeyArn': f'arn:aws:kms:us-east-1:{ACCOUNT}:key/' + str(uuid4()),
            'generation': str(uuid4()), 'verificationPolicySha256': 'a' * 64,
            'readinessSha256': 'b' * 64, 'maximumVerificationAgeSeconds': 120,
            'cognitoPoolId': 'us-east-1_example', 'usersTable': 'trustcheckradar-dev-users',
            'usersTableId': str(uuid4()), 'ledgerTable': 'trustcheckradar-dev-deletion-ledger',
            'ledgerTableId': str(uuid4()), 'inventoryManifestSha256': 'c' * 64,
            'inventoryRevision': 1, 'allowedSubjects': [SUBJECT],
        }
        self.context = SimpleNamespace(invoked_function_arn=self.c['functionArn'],
                                       get_remaining_time_in_millis=lambda: self.budget)
        self.resource = boto3.resource('dynamodb', region_name='us-east-1')
        self.raw = boto3.client('dynamodb', region_name='us-east-1')
        for name in ('usersTable', 'ledgerTable'):
            self.resource.create_table(TableName=self.c[name], BillingMode='PAY_PER_REQUEST',
                KeySchema=[{'AttributeName': 'PK', 'KeyType': 'HASH'}, {'AttributeName': 'SK', 'KeyType': 'RANGE'}],
                AttributeDefinitions=[{'AttributeName': 'PK', 'AttributeType': 'S'}, {'AttributeName': 'SK', 'AttributeType': 'S'}])
        self.users, self.ledger = [self.resource.Table(self.c[name]) for name in ('usersTable', 'ledgerTable')]
        self.profile = {'PK': 'USER#' + SUBJECT, 'SK': 'PROFILE', 'sub': SUBJECT,
                        'status': 'ACTIVE', 'email': 'synthetic@example.invalid', 'given_name': 'Test',
                        'family_name': 'Qualification', 'ageVerified': True, 'updatedAt': 'fixture-time'}
        self.users.put_item(Item=self.profile)
        self.users.put_item(Item={'PK': 'USER#' + OTHER, 'SK': 'PROFILE', 'sub': OTHER, 'status': 'ACTIVE'})
        self.inventory = {'PK': 'INVENTORY#dev', 'SK': 'ACCOUNT_DATA_INVENTORY',
            'recordType': 'ACCOUNT_DATA_INVENTORY', 'schemaVersion': 1, 'revision': 1,
            'environment': 'dev', 'coverage': 'VERIFIED_COMPLETE', 'manifestSha256': 'c' * 64,
            'requiredComponents': list(REQUIRED_COMPONENTS), 'usernameIsSubVerified': True,
            'approvedAtEpoch': NOW - 500}
        self.ledger.put_item(Item=self.inventory)
        self.record = {k: self.c[k] for k in ('environment', 'accountId', 'region', 'functionArn',
            'apiId', 'stage', 'operatorRoleArn', 'operatorRoleId', 'generation', 'verificationPolicySha256',
            'readinessSha256', 'cognitoPoolId')}
        self.record.update(schemaVersion=1, purpose=C.PURPOSE, route=C.ROUTE, subject=SUBJECT,
            operationId=str(uuid4()), profileSha256=C.profile_hash(self.profile),
            requestReceivedAtEpoch=NOW - 60, ownershipVerifiedAtEpoch=NOW - 20,
            issuedAtEpoch=NOW - 10, expiresAtEpoch=NOW + 90)

    def event(self):
        signature = self.key.sign(C.canonical(self.record), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
        return {'version': '2.0', 'routeKey': C.ROUTE, 'rawPath': '/support/account-deletion',
            'rawQueryString': '', 'isBase64Encoded': False,
            'requestContext': {'apiId': self.c['apiId'], 'accountId': ACCOUNT,
                'stage': 'dev', 'routeKey': C.ROUTE, 'http': {'method': 'POST'},
                'authorizer': {'iam': {'accountId': ACCOUNT,
                    'userArn': f'arn:aws:sts::{ACCOUNT}:assumed-role/VerifiedSupport/operator-session',
                    'userId': self.c['operatorRoleId'] + ':operator-session'}}},
            'body': C.canonical({'record': self.record, 'signature': base64.b64encode(signature).decode()}).decode()}

    def verify(self, **kw):
        self.calls.append('verify'); self.before_verify()
        assert kw['KeyId'] == self.c['kmsKeyArn'] and kw['SigningAlgorithm'] == C.ALGORITHM and kw['MessageType'] == 'RAW'
        self.key.public_key().verify(kw['Signature'], kw['Message'],
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
        return {'SignatureValid': True, 'KeyId': kw['KeyId'], 'SigningAlgorithm': C.ALGORITHM}

    def describe_table(self, **kw):
        self.calls.append('describe_table')
        key = 'usersTable' if kw['TableName'] == self.c['usersTable'] else 'ledgerTable'
        return {'Table': {'TableId': self.c[key + 'Id'], 'TableStatus': 'ACTIVE',
            'TableArn': f'arn:aws:dynamodb:us-east-1:{ACCOUNT}:table/' + self.c[key]}}

    def get_item(self, **kw):
        self.calls.append('get_item'); return self.raw.get_item(**kw)

    def query(self, **kw):
        self.calls.append('query'); return self.raw.query(**kw)

    def transact_write_items(self, **kw):
        self.calls.append('transaction'); self.write_count += 1; self.before_tx()
        result = self.raw.transact_write_items(**kw)
        self.after_tx()
        return result

    def admin_get_user(self, **kw):
        self.calls.append('admin_get_user')
        assert kw == {'UserPoolId': self.c['cognitoPoolId'], 'Username': SUBJECT}
        return {'Username': SUBJECT, 'UserAttributes': [{'Name': k, 'Value': self.profile[k]}
                for k in ('sub', 'email', 'given_name', 'family_name')]}

    def run(self, event=None):
        return R.execute(event or self.event(), self.context, config=self.c,
            clients={k: self for k in ('kms', 'dynamodb', 'cognito-idp')}, now=lambda: self.clock)

    def rows(self):
        return [sorted(t.scan(ConsistentRead=True)['Items'], key=lambda r: (r['PK'], r['SK']))
                for t in (self.users, self.ledger)]

    def command(self):
        return self.ledger.get_item(Key={'PK': 'ACCOUNT#' + SUBJECT, 'SK': 'ACCOUNT_DELETION'}, ConsistentRead=True).get('Item')


@pytest.fixture
def f(key, monkeypatch):
    monkeypatch.setenv('SUPPORT_ACCOUNT_DELETION_ENABLED', 'true')
    with mock_aws():
        yield Fixture(key)


def test_disabled_before_runtime_import_or_sdk(monkeypatch):
    monkeypatch.delenv('SUPPORT_ACCOUNT_DELETION_ENABLED', raising=False)
    monkeypatch.setattr(R, 'execute', lambda *a: pytest.fail('disabled runtime reached'))
    assert app.lambda_handler({'Records': []}, None)['statusCode'] == 503


@pytest.mark.parametrize('status', ['ACTIVE', 'PENDING_AGE_GATE'])
def test_actual_admission_atomic_job_fence_and_read_only_replay(f, status):
    f.profile['status'] = status; f.users.put_item(Item=f.profile)
    f.record['profileSha256'] = C.profile_hash(f.profile)
    before_other = f.rows()[0][1]
    assert f.run()['completionVerified'] is False
    command = f.command()
    assert command['operationId'] == f.record['operationId'] and command['occurredAtEpoch'] == NOW
    assert command['deleteByEpoch'] == NOW + 86400
    rows = f.rows()
    assert any(r['SK'].startswith('CAMPAIGN_RECOVERY#') for r in rows[1])
    control = next(r for r in rows[1] if r['SK'] == 'CAMPAIGN_RECOVERY_CONTROL')
    assert control['pendingJobs'] == 1 and control['state'] == 'OPEN'
    assert not any(r['SK'].startswith('ACCOUNT_DELETION#') for r in rows[1])
    assert before_other in rows[0]
    assert f.run()['admission'] == 'ACCEPTED' and f.rows() == rows and f.write_count == 1


@pytest.mark.parametrize('field,value', [
    ('purpose', 'different'), ('subject', OTHER), ('generation', '00000000-0000-4000-8000-000000000001'),
    ('schemaVersion', True), ('ownershipVerifiedAtEpoch', True), ('expiresAtEpoch', NOW),
    ('issuedAtEpoch', NOW + 1), ('requestReceivedAtEpoch', NOW),
    ('expiresAtEpoch', NOW + 1000), ('profileSha256', '0' * 64),
    ('readinessSha256', '0' * 64), ('verificationPolicySha256', '0' * 64),
])
def test_invalid_or_stale_proof_no_mutation(f, field, value):
    before = f.rows(); f.record[field] = value
    with pytest.raises(Exception): f.run()
    assert f.rows() == before and f.write_count == 0


@pytest.mark.parametrize('kind', ['signature', 'duplicate', 'unknown', 'noncanonical', 'jwt', 'principal', 'route', 'function'])
def test_bad_signature_or_transport_never_admits(f, kind):
    event = f.event(); before = f.rows()
    if kind == 'signature':
        body = json.loads(event['body']); body['signature'] = base64.b64encode(b'0' * 384).decode(); event['body'] = C.canonical(body).decode()
    elif kind == 'duplicate': event['body'] = event['body'][:-1] + ',"signature":""}'
    elif kind == 'unknown':
        body = json.loads(event['body']); body['record']['email'] = 'not-allowed'; event['body'] = C.canonical(body).decode()
    elif kind == 'noncanonical': event['body'] = ' ' + event['body']
    elif kind == 'jwt': event['requestContext']['authorizer'] = {'jwt': {'claims': {'sub': SUBJECT}}}
    elif kind == 'principal': event['requestContext']['authorizer']['iam']['userId'] = 'other:operator-session'
    elif kind == 'route': event['requestContext']['apiId'] = 'different'
    elif kind == 'function': f.context.invoked_function_arn += ':different'
    with pytest.raises(Exception): f.run(event)
    assert f.rows() == before and f.write_count == 0


def test_signature_expiry_and_generation_checked_before_replay(f):
    f.run(); before = f.rows()
    f.clock = f.record['expiresAtEpoch']
    with pytest.raises(C.Unavailable): f.run()
    f.clock = NOW; f.c['generation'] = str(uuid4())
    with pytest.raises(C.Unavailable): f.run()
    assert f.rows() == before and f.write_count == 1


@pytest.mark.parametrize('race', ['profile', 'optional_profile_field', 'inventory'])
def test_real_transaction_race_rolls_back_entire_admission(f, race):
    def mutate():
        if race in ('profile', 'optional_profile_field'):
            profile = deepcopy(f.profile)
            profile['email' if race == 'profile' else 'phone_number'] = 'replacement'
            f.users.put_item(Item=profile)
        else:
            item = deepcopy(f.inventory); item['revision'] = 2; f.ledger.put_item(Item=item)
    f.before_tx = mutate
    with pytest.raises(Exception): f.run()
    assert f.command() is None
    assert not any(r['PK'].startswith('ACCOUNT#') for r in f.rows()[1])
    assert f.users.get_item(Key={'PK': 'USER#' + SUBJECT, 'SK': 'PROFILE'})['Item']['status'] == 'ACTIVE'


def test_committed_lost_ack_is_reconciled_without_second_write(f):
    def lost(): raise TimeoutError('synthetic-response-lost')
    f.after_tx = lost
    assert f.run()['admission'] == 'ACCEPTED' and f.write_count == 1
    before = f.rows()
    assert f.run()['admission'] == 'ACCEPTED' and f.rows() == before and f.write_count == 1


@pytest.mark.parametrize('change', ['expired', 'budget', 'backward'])
def test_post_signature_time_or_budget_drop_stops_next_sdk(f, change):
    def mutate():
        if change == 'expired': f.clock = f.record['expiresAtEpoch']
        elif change == 'budget': f.budget = 5999
        else: f.clock = NOW - 1
    f.before_verify = mutate
    with pytest.raises(C.Unavailable): f.run()
    assert f.calls == ['verify'] and f.write_count == 0


def test_terminal_ack_does_not_require_or_recreate_deleted_profile(f):
    f.run(); command = f.command()
    command.update(status='COMPLETE', eventType='account.deletion.completed', completedAtEpoch=NOW,
                   retainUntilEpoch=NOW + 120 * 86400)
    f.ledger.put_item(Item=command)
    f.users.delete_item(Key={'PK': 'USER#' + SUBJECT, 'SK': 'PROFILE'})
    f.calls.clear(); before = f.rows()
    assert f.run() == {'schemaVersion': 1, 'operationId': f.record['operationId'], 'admission': 'ACCEPTED', 'completionVerified': False}
    assert 'admin_get_user' not in f.calls and f.rows() == before and f.write_count == 1


def test_different_existing_operation_is_not_success(f):
    f.run(); before = f.rows(); f.record['operationId'] = str(uuid4())
    with pytest.raises(C.Unavailable): f.run()
    assert f.rows() == before and f.write_count == 1


def test_older_mail_request_with_fresh_verification_is_eligible(f):
    f.record['requestReceivedAtEpoch'] = NOW - 86400
    assert f.run()['admission'] == 'ACCEPTED'


@pytest.mark.parametrize('drop', ['expiry', 'budget'])
def test_expiry_during_committed_transaction_stays_unconfirmed_and_does_not_retry(f, drop):
    f.after_tx = lambda: setattr(f, 'clock' if drop == 'expiry' else 'budget',
                                 f.record['expiresAtEpoch'] if drop == 'expiry' else 5999)
    with pytest.raises(C.Unavailable): f.run()
    assert f.command()['operationId'] == f.record['operationId'] and f.write_count == 1
