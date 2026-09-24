"""Isolated real-SDK transactions with synthetic data; no live AWS calls."""
import os
import importlib.util
import json
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run separately with the real SDK/Moto environment', allow_module_level=True)

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

SUB = 'synthetic-profile-owner'
SENSITIVE = 'synthetic-private@example.invalid TOKEN-DO-NOT-LOG'
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    monkeypatch.setenv('USERS_TABLE_NAME', 'users')
    monkeypatch.setenv('DELETION_LEDGER_TABLE_NAME', 'ledger')
    with mock_aws():
        ddb = boto3.resource('dynamodb', region_name='us-east-1')
        for name in ('users', 'ledger'):
            ddb.create_table(TableName=name, BillingMode='PAY_PER_REQUEST',
                KeySchema=[{'AttributeName': k, 'KeyType': t} for k, t in [('PK','HASH'),('SK','RANGE')]],
                AttributeDefinitions=[{'AttributeName': k, 'AttributeType':'S'} for k in ('PK','SK')])
        apps = {}
        for name in ('post_confirmation', 'age_attestation'):
            spec = importlib.util.spec_from_file_location('qualified_' + name, ROOT / 'src' / name / 'app.py')
            app = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(app)
            apps[name] = app
        yield SimpleNamespace(ddb=ddb, post=apps['post_confirmation'], age=apps['age_attestation'])


def event():
    return {'request': {'userAttributes': {'sub': SUB, 'email': 'synthetic@example.invalid', 'custom:over_18':'true'}}, 'response': {}}


def profile(w):
    return w.ddb.Table('users').get_item(Key={'PK':'USER#'+SUB,'SK':'PROFILE'}, ConsistentRead=True).get('Item')


def fence(w, state='REQUESTED'):
    w.ddb.Table('ledger').put_item(Item={'PK':'ACCOUNT#'+SUB,'SK':'ACCOUNT_DELETION','status':state})


def attest(w, acknowledged=True):
    return w.age.lambda_handler({'body':json.dumps({'sub':'different-synthetic-owner','over18Acknowledged':acknowledged}),
        'requestContext':{'authorizer':{'jwt':{'claims':{'sub':SUB}}}}}, None)


def test_signup_creates_pending_profile_and_age_uses_authenticated_owner(world):
    original = event()
    assert world.post.lambda_handler(original, None) is original
    assert profile(world)['status'] == 'PENDING_AGE_GATE'
    assert profile(world)['ageVerified'] is False
    assert attest(world)['statusCode'] == 200
    assert profile(world)['status'] == 'ACTIVE'
    assert profile(world)['ageVerified'] is True
    assert world.ddb.Table('users').scan()['Count'] == 1


def test_duplicate_signup_preserves_profile_and_is_not_claimed_success(world):
    world.post.lambda_handler(event(), None)
    assert attest(world)['statusCode'] == 200
    before = profile(world)
    with pytest.raises(RuntimeError, match='^POST_CONFIRMATION_FAILED$'):
        world.post.lambda_handler(event(), None)
    assert profile(world) == before


@pytest.mark.parametrize('state', ['REQUESTED','COMPLETE','UNKNOWN'])
@pytest.mark.parametrize('writer', ['signup','age'])
def test_any_fixed_fence_prevents_profile_creation_or_update(world, state, writer):
    if writer == 'age':
        world.post.lambda_handler(event(), None)
    before = profile(world)
    fence(world, state)
    if writer == 'signup':
        with pytest.raises(RuntimeError, match='^POST_CONFIRMATION_FAILED$'):
            world.post.lambda_handler(event(), None)
    else:
        assert attest(world)['statusCode'] == 404
    assert profile(world) == before


@pytest.mark.parametrize('writer', ['signup','age'])
def test_fence_arriving_at_commit_cancels_profile_mutation(world, monkeypatch, writer):
    if writer == 'age':
        world.post.lambda_handler(event(), None)
    before = profile(world)
    app = world.post if writer == 'signup' else world.age
    original = app.dynamodb_client.transact_write_items
    def raced(**kwargs):
        fence(world)
        return original(**kwargs)
    monkeypatch.setattr(app.dynamodb_client, 'transact_write_items', raced)
    if writer == 'signup':
        with pytest.raises(RuntimeError):
            app.lambda_handler(event(), None)
    else:
        assert attest(world)['statusCode'] == 404
    assert profile(world) == before


@pytest.mark.parametrize('shape', ['missing','wrong_owner','deleting','unknown'])
def test_age_refuses_missing_mismatched_or_closed_profile(world, shape):
    if shape != 'missing':
        row = {'PK':'USER#'+SUB,'SK':'PROFILE','sub':SUB,'status':'ACTIVE','ageVerified':False}
        if shape == 'wrong_owner':
            row['sub'] = 'other-synthetic-owner'
        else:
            row['status'] = 'DELETING' if shape == 'deleting' else 'UNKNOWN'
        world.ddb.Table('users').put_item(Item=row)
    before = profile(world)
    assert attest(world)['statusCode'] == 404
    assert profile(world) == before


def test_repeated_age_decision_changes_no_identity_or_created_state(world):
    world.post.lambda_handler(event(), None)
    before = profile(world)
    for _ in range(2):
        assert attest(world)['statusCode'] == 200
        current = profile(world)
        for key in ('PK','SK','sub','email','createdAt'):
            assert current[key] == before[key]
        assert current['status'] == 'ACTIVE' and current['ageVerified'] is True
    assert attest(world, False)['statusCode'] == 200
    assert profile(world)['status'] == 'PENDING_AGE_GATE'
    assert profile(world)['ageVerifiedAt'] is None


@pytest.mark.parametrize('writer', ['signup','age_api','age_trigger'])
@pytest.mark.parametrize('failure', ['denied','missing_ledger'])
def test_sdk_failure_has_fixed_diagnostic_and_no_sensitive_trace(world, monkeypatch, caplog, writer, failure):
    if writer != 'signup':
        world.post.lambda_handler(event(), None)
    before = profile(world)
    app = world.post if writer == 'signup' else world.age
    if failure == 'missing_ledger':
        world.ddb.Table('ledger').delete()
    else:
        def denied(**kwargs):
            raise ClientError({'Error':{'Code':'AccessDeniedException','Message':SENSITIVE}}, 'TransactWriteItems')
        monkeypatch.setattr(app.dynamodb_client, 'transact_write_items', denied)
    caplog.clear()
    if writer == 'age_api':
        response = attest(world)
        assert response['statusCode'] == 500
        assert json.loads(response['body'])['errorCode'] == 'AGE_ATTESTATION_INTERNAL_ERROR'
        assert SENSITIVE not in json.dumps(response)
    else:
        request = event()
        if writer == 'age_trigger':
            request['triggerSource'] = 'synthetic-trigger'
        with pytest.raises(RuntimeError) as caught:
            app.lambda_handler(request, None)
        assert str(caught.value) == ('POST_CONFIRMATION_FAILED' if writer == 'signup' else 'AGE_ATTESTATION_TRIGGER_FAILED')
        formatted = ''.join(traceback.format_exception(caught.value))
        assert SENSITIVE not in formatted and 'AccessDeniedException' not in formatted
        assert caught.value.__suppress_context__ is True
    assert SENSITIVE not in caplog.text
    assert not any(record.exc_info for record in caplog.records)
    assert profile(world) == before


@pytest.mark.parametrize('payload_event', [{}, {'request':{'userAttributes':None}}, {'request':{'userAttributes':{'sub':SENSITIVE.replace(' ','')}}}])
def test_malformed_event_or_sdk_failure_does_not_echo_payload(world, monkeypatch, caplog, payload_event):
    # Force the third malformed synthetic identifier to fail without allowing
    # its raw value into either the fixed log or runtime-facing exception chain.
    if payload_event.get('request',{}).get('userAttributes'):
        def bad(**kwargs):
            raise ValueError(SENSITIVE)
        monkeypatch.setattr(world.post.dynamodb_client, 'transact_write_items', bad)
    with pytest.raises(RuntimeError) as caught:
        world.post.lambda_handler(payload_event, None)
    assert SENSITIVE not in caplog.text
    assert SENSITIVE not in ''.join(traceback.format_exception(caught.value))
    assert world.ddb.Table('users').scan()['Count'] == 0


@pytest.mark.parametrize('reasons', [None, [], [{'Code':'ConditionalCheckFailed'}],
    [{'Code':'ConditionalCheckFailed'},{'Code':'TransactionConflict'}],
    [{'Code':'None'},{'Code':'ProvisionedThroughputExceeded'}],
    [None, {'Code':'ConditionalCheckFailed'}]])
def test_nonconditional_or_unknown_cancellation_is_storage_error(world, monkeypatch, reasons):
    world.post.lambda_handler(event(), None)
    before = profile(world)
    def canceled(**kwargs):
        response = {'Error':{'Code':'TransactionCanceledException','Message':SENSITIVE}}
        if reasons is not None:
            response['CancellationReasons'] = reasons
        raise ClientError(response, 'TransactWriteItems')
    monkeypatch.setattr(world.age.dynamodb_client, 'transact_write_items', canceled)
    response = attest(world)
    assert response['statusCode'] == 500
    assert SENSITIVE not in response['body']
    assert profile(world) == before
