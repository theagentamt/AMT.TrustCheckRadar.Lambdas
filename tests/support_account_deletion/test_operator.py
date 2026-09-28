"""Actual local RSA/admission/DynamoDB composition; no live IAM or mailbox proof."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from copy import deepcopy
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import support_deletion_operator as O
spec = importlib.util.spec_from_file_location('operator_verifier_fixture', Path(__file__).with_name('test_verifier.py'))
F = importlib.util.module_from_spec(spec); spec.loader.exec_module(F)
key = F.key

@pytest.fixture
def world(key, tmp_path, monkeypatch):
    with mock_aws():
        monkeypatch.setenv('SUPPORT_ACCOUNT_DELETION_ENABLED', 'true')
        s = F.Setup(key, tmp_path).prepare(); s.issue()
        body = F.V.private_read(s.output); calls = []
        class STS:
            def get_caller_identity(self):
                return {'Account': F.ACCOUNT, 'Arn': 'arn:aws:sts::' + F.ACCOUNT + ':assumed-role/VerifiedSupport/operator-session',
                    'UserId': s.f.c['operatorRoleId'] + ':operator-session'}
        sts = STS()
        def http(session, config, received):
            calls.append(received['record']['operationId'])
            s.f.record = received['record']; event = s.f.event(); event['body'] = O.C.canonical(received).decode()
            return 202, s.f.run(event)
        def run():
            return O.submit(s.f.c, body, s.case, s.directory, object(), sts, now=lambda: s.f.clock, http=http)
        yield SimpleNamespace(s=s, body=body, calls=calls, sts=sts, http=http, run=run)


def attempt(w):
    return F.V.private_read(w.s.directory / (w.body['record']['operationId'] + '.admission-intent.json'))


def test_actual_admission_and_no_second_post(world):
    w = world; result = w.run()
    assert result['admission'] == 'ACCEPTED' and not result['completionVerified'] and not result['repeatAdmission']
    assert w.s.f.write_count == 1 and len(w.calls) == 1
    assert attempt(w)['operationId'] == w.body['record']['operationId']
    with pytest.raises(FileExistsError): w.run()
    assert len(w.calls) == 1


def test_committed_lost_http_ack_status_reconciles_original(world):
    w = world
    def lost(*args):
        w.http(*args); raise TimeoutError('not emitted')
    with pytest.raises(TimeoutError):
        O.submit(w.s.f.c, w.body, w.s.case, w.s.directory, None, w.sts, now=lambda: w.s.f.clock, http=lost)
    out = O.status(w.s.f.c, w.s.p, attempt(w), w.s.v.clients, now=lambda: w.s.f.clock)
    assert out['status'] == 'INCOMPLETE' and out['originalOperationMatched'] and not out['repeatAdmission']
    assert len(w.calls) == 1 and w.s.f.write_count == 1


@pytest.mark.parametrize('mode', ['foreign-principal', 'expired', 'body-subject', 'body-extra', 'generation', 'signature'])
def test_refused_before_intent_or_post(world, mode):
    w = world
    if mode == 'foreign-principal': w.sts.get_caller_identity = lambda: {'Account': F.ACCOUNT, 'Arn': w.s.identity['Arn'], 'UserId': w.s.identity['UserId']}
    if mode == 'expired': w.s.f.clock += 120
    if mode == 'body-subject': w.body['record']['subject'] = F.fixture_module.OTHER
    if mode == 'body-extra': w.body['extra'] = True
    if mode == 'generation': w.body['record']['generation'] = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    if mode == 'signature': w.body['signature'] = 'short'
    with pytest.raises(Exception): w.run()
    assert not w.calls and not list(w.s.directory.glob('*.admission-intent.json'))


def test_directory_sync_failure_preserves_intent_never_posts(world, monkeypatch):
    w = world; real = O.V.os.fsync; count = 0
    def sync(fd):
        nonlocal count
        count += 1
        if count == 2: raise OSError('directory sync')
        return real(fd)
    monkeypatch.setattr(O.V.os, 'fsync', sync)
    with pytest.raises(OSError): w.run()
    assert not w.calls and attempt(w)['stage'] == 'POST_ATTEMPTED'
    with pytest.raises(FileExistsError): w.run()


def test_expiry_after_durable_intent_no_post(world, monkeypatch):
    w = world; real = O.V.exclusive
    def late(*args):
        real(*args); w.s.f.clock += 120
    monkeypatch.setattr(O.V, 'exclusive', late)
    with pytest.raises(O.C.Unavailable): w.run()
    assert not w.calls and attempt(w)['stage'] == 'POST_ATTEMPTED'


def test_wrong_202_body_never_saved_as_accepted(world):
    w = world
    with pytest.raises(O.C.Unavailable):
        O.submit(w.s.f.c, w.body, w.s.case, w.s.directory, None, w.sts,
            now=lambda: w.s.f.clock, http=lambda *args: (202, {'admission': 'ACCEPTED'}))
    assert not list(w.s.directory.glob('*.admission-accepted.json'))
    assert attempt(w)['stage'] == 'POST_ATTEMPTED'


def complete(w):
    w.run(); f = w.s.f; operation = w.body['record']['operationId']; f.clock += 1
    row = f.ledger.get_item(Key={'PK': 'ACCOUNT#' + F.SUBJECT, 'SK': 'ACCOUNT_DELETION'})['Item']
    row.update(eventType='account.deletion.completed', status='COMPLETE', completedAtEpoch=f.clock,
               retainUntilEpoch=f.clock + 120 * 86400)
    f.ledger.put_item(Item=row)
    for component in O.REQUIRED_COMPONENTS:
        f.ledger.put_item(Item={'PK': row['PK'], 'SK': 'ACCOUNT_DELETION#' + component,
            'schemaVersion': 1, 'recordVersion': 1, 'environment': 'dev',
            'eventType': 'account.deletion.component.completed', 'component': component,
            'status': 'COMPLETE', 'operationId': operation, 'requestOccurredAtEpoch': row['occurredAtEpoch'],
            'occurredAtEpoch': f.clock, 'retainUntilEpoch': f.clock + 120 * 86400})
    f.users.delete_item(Key={'PK': 'USER#' + F.SUBJECT, 'SK': 'PROFILE'})
    class Missing(Exception): pass
    class Cognito:
        exceptions = SimpleNamespace(UserNotFoundException=Missing)
        def admin_get_user(self, **kwargs): raise Missing()
    clients = dict(w.s.v.clients); clients['cognito-idp'] = Cognito()
    return clients


def test_final_status_real_twelve_validators_identity_absent(world):
    w = world; clients = complete(w); before = w.s.f.rows()
    # Expired capability is irrelevant for read-only observation, never renewed.
    w.s.f.clock += 300
    result = O.status(w.s.f.c, w.s.p, attempt(w), clients, now=lambda: w.s.f.clock)
    assert result['status'] == 'COMPLETE' and result['completionVerified'] and result['validComponentReceipts'] == 12
    assert result['profileAbsent'] and result['cognitoAbsent'] and not result['repeatAdmission']
    assert w.s.f.rows() == before


@pytest.mark.parametrize('mode', ['missing', 'foreign', 'profile', 'identity', 'wrong-operation', 'inventory', 'verifier-role', 'table-id'])
def test_incomplete_or_invalid_terminal_proof_never_complete(world, mode):
    w = world; clients = complete(w); f = w.s.f
    key = {'PK': 'ACCOUNT#' + F.SUBJECT, 'SK': 'ACCOUNT_DELETION#CAMPAIGN'}
    if mode == 'missing': f.ledger.delete_item(Key=key)
    if mode == 'foreign':
        row = f.ledger.get_item(Key=key)['Item']; row['operationId'] = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'; f.ledger.put_item(Item=row)
    if mode == 'profile': f.users.put_item(Item=f.profile)
    if mode == 'identity': clients['cognito-idp'] = SimpleNamespace(exceptions=clients['cognito-idp'].exceptions, admin_get_user=lambda **kw: {'exists': True})
    if mode == 'wrong-operation':
        key['SK'] = 'ACCOUNT_DELETION'; row = f.ledger.get_item(Key=key)['Item']; row['operationId'] = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'; f.ledger.put_item(Item=row)
    if mode == 'inventory': f.inventory['revision'] += 1; f.ledger.put_item(Item=f.inventory)
    if mode == 'verifier-role': w.s.identity['UserId'] = 'AROA' + 'C' * 17 + ':session'
    if mode == 'table-id': f.describe_table = lambda **kw: {'Table': {}}
    before = f.rows()
    if mode in ('missing', 'foreign'):
        out = O.status(f.c, w.s.p, attempt(w), clients, now=lambda: f.clock)
        assert out['status'] == 'INCOMPLETE' and out['validComponentReceipts'] == 11 and not out['completionVerified']
    else:
        with pytest.raises(Exception): O.status(f.c, w.s.p, attempt(w), clients, now=lambda: f.clock)
    assert f.rows() == before


def test_no_command_reports_unconfirmed_without_retry(world):
    w = world
    with pytest.raises(TimeoutError):
        O.submit(w.s.f.c, w.body, w.s.case, w.s.directory, None, w.sts, now=lambda: w.s.f.clock,
                 http=lambda *a: (_ for _ in ()).throw(TimeoutError()))
    out = O.status(w.s.f.c, w.s.p, attempt(w), w.s.v.clients, now=lambda: w.s.f.clock)
    assert out['status'] == 'ADMISSION_UNCONFIRMED' and not out['repeatAdmission']


def test_redirect_is_refused():
    with pytest.raises(O.C.Unavailable): O.NoRedirect().redirect_request(None, None, None, None, None, None)


@pytest.mark.parametrize('field,value', [('schemaVersion', True), ('completionVerified', 0)])
def test_typed_acknowledgment_refuses_json_bool_number_equivalence(world, field, value):
    w = world
    body = {'schemaVersion': 1, 'operationId': w.body['record']['operationId'], 'admission': 'ACCEPTED', 'completionVerified': False}
    body[field] = value
    with pytest.raises(O.C.Unavailable):
        O.submit(w.s.f.c, w.body, w.s.case, w.s.directory, None, w.sts,
            now=lambda: w.s.f.clock, http=lambda *a: (202, body))
    assert not list(w.s.directory.glob('*.admission-accepted.json'))


def test_accepted_output_failure_retains_intent_and_reconciles_without_post(world, monkeypatch):
    w = world; real = O.V.exclusive
    def fail_ack(path, value):
        if str(path).endswith('.admission-accepted.json'):
            raise OSError('synthetic output failure')
        return real(path, value)
    monkeypatch.setattr(O.V, 'exclusive', fail_ack)
    with pytest.raises(OSError): w.run()
    assert len(w.calls) == 1 and w.s.f.write_count == 1
    result = O.status(w.s.f.c, w.s.p, attempt(w), w.s.v.clients, now=lambda: w.s.f.clock)
    assert result['status'] == 'INCOMPLETE' and result['originalOperationMatched']
    with pytest.raises(FileExistsError): w.run()
    assert len(w.calls) == 1
