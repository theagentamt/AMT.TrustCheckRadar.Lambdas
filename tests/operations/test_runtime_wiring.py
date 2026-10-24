"""Normal handler wiring with isolated DynamoDB and an in-memory HTTP wire.

These source regressions neither qualify a model nor contact AWS/OpenAI.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests/shared_check_authority'), str(ROOT/'tests/operations')]
from test_publisher import opsworld, world, metadata, all_events, GEN
from test_transactions import ACCOUNT
from shared_check_authority.core import AuthorityError, OWNER_POLICY
from shared_check_authority.inventory import INVENTORY_KEY
from shared_operations.contract import OperationsError, RETENTION_SECONDS
from shared_operations.message_context import commitment
from shared_operations import runtime
from shared_message_contract import validation_v2 as v2, validation_v3 as v3
from message_consumer.service import Consumer
from message_evaluator import app, ai_provider, proposer


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT/relative)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


helpers = module('operations_message_helpers', 'tests/message_consumer/test_integration.py')
http = module('operations_http_helpers', 'tests/message_evaluator/test_proposer.py')


class Context:
    def get_remaining_time_in_millis(self): return 30000


@pytest.fixture
def configured(opsworld, monkeypatch):
    a = opsworld[0]
    values = {'STAGE':'dev', 'OPERATIONS_ENABLED':'true', 'OPERATIONS_TABLE_NAME':'operations',
        'OPERATIONS_STORE_GENERATION':GEN, 'AUTHORITY_TABLE_NAME':'authority', 'USERS_TABLE_NAME':'users',
        'DEVICE_BINDINGS_TABLE_NAME':'devices', 'DELETION_LEDGER_TABLE_NAME':'deletion',
        'AUTHORITY_POLICY_VERSION':OWNER_POLICY, 'MESSAGE_EVALUATOR_ENABLED':'true',
        'MESSAGE_AI_ENABLED':'true', 'MESSAGE_AI_QUALIFIED':'true', 'MESSAGE_AI_POLICY_VERSION':v2.POLICY,
        'MESSAGE_AI_POLICY_APPROVAL_SHA256':v2.APPROVAL_SHA}
    for key, value in values.items(): monkeypatch.setenv(key, value)
    # Keep the real context parser/publisher/transactions; inject only Moto resource/time.
    original = runtime.message_observer
    monkeypatch.setattr(runtime, 'message_observer', lambda event: original(event, resource=a.ddb, now=a.now))
    return opsworld


def intent_request():
    return helpers.request(text='An unusual request outside deterministic coverage.') | {'transportVersion':v3.VERSION}


def admission(world, *, mode='ai'):
    a, event, *_ = world
    request = intent_request()
    payload = {k: request[k] for k in ('entryPoint','language','target')} | {'messageTransportVersion':v3.VERSION}
    if mode == 'ai_primary_v1': payload['messageExecutionProfile'] = mode
    proof = a.prepare(event, payload, request['checkId'], client_check_id=request['checkId'], count_attempt=False)
    result = a.admit(event, payload, proof, client_check_id=request['checkId'], count_attempt=False, operations_mode=mode)
    wire = {'schemaVersion':3, 'checkId':request['checkId'], 'policyVersion':v2.POLICY,
        'intent':{k: request[k] for k in ('entryPoint','language','target')}, 'executionBudgetMs':10000,
        'providerCost':helpers.Budget().cost_policy.as_dict(), 'operationsContext':result['operationsContext']}
    if mode != 'ai': wire['runtimeMode'] = mode
    if mode == 'rules_only': wire.pop('providerCost')
    return payload, proof, result, wire


def install_http(monkeypatch, *, uncertain=False):
    cost = helpers.Budget().cost_policy
    monkeypatch.setattr(ai_provider, 'qualified_settings', lambda *_: proposer.Settings(cost.model,
        http.settings().secret_arn, 8000, cost.maxOutputTokens))
    monkeypatch.setattr(ai_provider.CostPolicy, 'from_env', lambda: cost)
    body = http.response({'result':{'assessment':'no_warning','context':'clear','reasons':[]}})
    body.update(model=cost.model, service_tier='default', usage={'input_tokens':100,'output_tokens':20,'total_tokens':120,
        'input_tokens_details':{'cached_tokens':0}, 'output_tokens_details':{'reasoning_tokens':0}})
    connections = []
    class Wire(http.Connection):
        def request(self, *args, **kwargs):
            super().request(*args, **kwargs)
            if uncertain: raise TimeoutError('synthetic post-send uncertainty')
    def factory(*args, **kwargs):
        connection = Wire(http.wire(body), *args, **kwargs); connections.append(connection); return connection
    original = proposer.propose
    monkeypatch.setattr(proposer, 'propose', lambda *args, **kwargs: original(*args, **kwargs,
        connection_factory=factory, secret_loader=lambda *_:'synthetic-key'))
    return connections


def test_consumer_to_handler_single_dispatch_and_context_never_persisted_or_sent(configured, monkeypatch, capsys):
    a, base, _, row, *_ = configured
    connections = install_http(monkeypatch); private = []
    def provider(request):
        private.append(deepcopy(request)); return app.lambda_handler(request, Context())
    consumer = Consumer(a, provider, lambda _:None, helpers.Budget(), allow_ai=True)
    request = intent_request()
    status, prepared = consumer.handle(helpers.event(base, 'POST /v1/message-checks/prepare', request))
    assert status == 200
    proof = prepared['operationProof']; submitted = request | {'operationProof':proof}
    status, result = consumer.handle(helpers.event(base, 'POST /v1/message-checks', submitted))
    assert status == 200 and result['outcome']['aiAssessmentStatus'] == 'no_warning', result
    assert result['outcome']['schemaVersion'] == 3 and len(connections) == 1
    context = private[0]['operationsContext']
    assert 'operationsContextDigest' in row('CHECK#'+proof)
    all_rows = [a.ddb.Table(table).scan()['Items'] for table in ('authority','operations','users','devices','deletion')]
    retained = str(all_rows) + str(result) + capsys.readouterr().out
    assert context['contextNonce'] not in retained and request['target']['sanitizedText'] not in retained
    assert 'operationsContext' not in json.dumps(result)
    provider_wire = b''.join(connections[0].test_socket.sent).decode()
    for value in (context['contextNonce'], ACCOUNT, context['partition'], proof, context['executionToken'], GEN):
        assert value not in provider_wire
    facts = [x['event'] for x in all_events(configured, proof)]
    assert len([x for x in facts if x['providerFamily'] == 'openai' and x['eventType'] == 'attempt_started']) == 1
    assert len([x for x in facts if x['providerFamily'] == 'openai' and x['eventType'] == 'attempt_finished']) == 1
    assert app.lambda_handler(private[0], Context()) == {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
    assert consumer.handle(helpers.event(base, 'POST /v1/message-checks', submitted))[0] == 200
    assert len(connections) == 1 and len(private) == 1


@pytest.mark.parametrize('field,value', [('account','other-account'), ('partition','V1#k1#'+'f'*64),
    ('proof','v1_k1_ffffffff_'+'a'*16+'_'+'b'*24), ('executionToken','c'*32), ('acceptedAtEpoch',1),
    ('generation','b'*32), ('keyFingerprint','d'*64), ('contextNonce','e'*64)])
def test_context_substitution_cannot_claim(configured, field, value):
    _, _, _, wire = admission(configured)
    wire['operationsContext'][field] = value
    with pytest.raises((OperationsError, AuthorityError)):
        runtime.message_observer(wire).start()
    assert not metadata(configured, admission_proof(wire, configured))['attempts']


def admission_proof(wire, world):
    # Resolve only synthetic test receipts; this is never a production source adapter.
    return next(x['checkId'] for x in world[0].ddb.Table('authority').scan()['Items'] if x.get('recordType')=='V1_CHECK_RECEIPT')


@pytest.mark.parametrize('mutation', ['text','client','version','mode','primary'])
def test_actual_private_request_commitment_and_mode_are_bound(configured, mutation):
    payload, proof, operation, wire = admission(configured)
    assert commitment(operation['operationsContext'], payload, wire['checkId']) == configured[3]('CHECK#'+proof)['operationsContextDigest']
    if mutation=='text': wire['intent']['target']['sanitizedText'] = 'A different reviewed request.'
    if mutation=='client': wire['checkId'] = 'another-check'
    if mutation=='version': wire['schemaVersion'] = 2
    if mutation=='mode': wire['runtimeMode'] = 'rules_only'
    if mutation=='primary': wire['runtimeMode'] = 'ai_primary_v1'
    with pytest.raises((OperationsError, AuthorityError)):
        runtime.message_observer(wire).start()
    assert metadata(configured, proof)['attempts'] == {}


def test_admission_lost_ack_returns_no_capability(configured, monkeypatch):
    a, event, *_ = configured
    req = intent_request(); payload = {k:req[k] for k in ('entryPoint','language','target')} | {'messageTransportVersion':v3.VERSION}
    proof = a.prepare(event, payload, req['checkId'], client_check_id=req['checkId'], count_attempt=False)
    original = a._transact
    def lost(items):
        original(items)
        if any(x.get('Put',{}).get('Item',{}).get('recordType')=='V1_CHECK_RECEIPT' for x in items):
            raise AuthorityError('TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(a, '_transact', lost)
    result = a.admit(event, payload, proof, client_check_id=req['checkId'], count_attempt=False, operations_mode='ai')
    assert result['admitted'] is False and 'operationsContext' not in result
    assert 'executionToken' not in result and metadata(configured, proof)['attempts'] == {}


@pytest.mark.parametrize('claim',['invocation','provider'])
def test_provider_claim_lost_ack_and_post_send_uncertainty_never_retry(configured, monkeypatch, claim):
    a = configured[0]; _, proof, _, wire = admission(configured)
    connections = install_http(monkeypatch)
    original = a.client.transact_write_items
    def lost(**kwargs):
        result = original(**kwargs)
        if any(x.get('Put',{}).get('Item',{}).get('event',{}).get('eventType')=='attempt_started'
               and x['Put']['Item']['event']['component']=='message_evaluator'
               and x['Put']['Item']['event']['providerFamily']==('openai' if claim=='provider' else None)
               for x in kwargs['TransactItems']):
            raise TimeoutError('synthetic lost acknowledgement')
        return result
    monkeypatch.setattr(a.client, 'transact_write_items', lost)
    first=app.lambda_handler(wire, Context())
    if claim=='provider':assert first['aiAssessmentStatus']=='unavailable'
    else:assert first=={'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
    assert app.lambda_handler(wire, Context()) == {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
    assert connections == []
    assert any(x['event']['component']=='message_evaluator' for x in all_events(configured, proof))


def test_http_uncertainty_retains_unknown_and_no_repeat(configured, monkeypatch):
    _, proof, _, wire = admission(configured); connections = install_http(monkeypatch, uncertain=True)
    assert app.lambda_handler(wire, Context())['aiAssessmentStatus'] == 'unavailable'
    assert app.lambda_handler(wire, Context()) == {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
    assert len(connections) == 1
    finish = next(x['event'] for x in all_events(configured, proof) if x['event']['providerFamily']=='openai' and x['event']['eventType']=='attempt_finished')
    assert finish['providerRequestUnits'] is None and finish['estimatedCostMicrousd'] is None


def test_inventory_rotation_is_transactional_without_key_read(configured, monkeypatch):
    a = configured[0]; _, proof, _, wire = admission(configured)
    observer = runtime.message_observer(wire)
    original = a.client.transact_write_items
    def rotate(**kwargs):
        row = a._get('authority', INVENTORY_KEY)
        a.ddb.Table('authority').put_item(Item=row | {'revision':2})
        return original(**kwargs)
    monkeypatch.setattr(a.client, 'transact_write_items', rotate)
    with pytest.raises(AuthorityError): observer.start()
    assert metadata(configured, proof)['attempts'] == {}


@pytest.mark.parametrize('fence',['inventory_missing','inventory_fingerprint','inventory_coverage','account_deleted','account_inactive','wrong_subject'])
def test_current_ownership_inventory_and_deletion_fences_stop_normal_handler(configured, monkeypatch, fence):
    a, _, _, _, change, _=configured
    _, proof, _, wire=admission(configured)
    if fence=='inventory_missing':a.ddb.Table('authority').delete_item(Key=INVENTORY_KEY)
    elif fence.startswith('inventory_'):
        row=a._get('authority',INVENTORY_KEY)
        if fence=='inventory_fingerprint':row['issuedKeys']={'k1':'f'*64}
        else:row['coverage']='UNQUALIFIED'
        a.ddb.Table('authority').put_item(Item=row)
    elif fence=='account_deleted':a.ddb.Table('deletion').put_item(Item={'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION'})
    elif fence=='account_inactive':change('users','PROFILE',status='DELETION_REQUESTED')
    else:change('users','PROFILE',sub='another-subject')
    connections=install_http(monkeypatch)
    assert app.lambda_handler(wire,Context())=={'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
    assert connections==[] and metadata(configured,proof)['attempts']=={}


def test_enabled_receiver_cannot_downgrade_to_context_free_request(configured, monkeypatch):
    _, _, _, wire = admission(configured)
    wire.pop('operationsContext')
    assert app.lambda_handler(wire, Context()) == {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
    monkeypatch.setenv('OPERATIONS_ENABLED', 'false')
    wire['operationsContext'] = {}
    assert app.lambda_handler(wire, Context()) == {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}


def test_maintenance_handler_constructs_matching_adapter_and_expires_operations(configured):
    from url_lease_recovery.app import run_passes
    from dataclasses import replace
    a, _, _, row, _, clock = configured
    # Exercise an original source deadline later than the operations deadline;
    # the cleanup must preserve that original receipt deadline.
    a.s=replace(a.s,receipt_retention_seconds=RETENTION_SECONDS+60,counter_retention_seconds=RETENTION_SECONDS+60)
    _, proof, operation, _ = admission(configured)
    from shared_check_authority.core import TrustedWorkerContext
    clock[0] += 30
    a.settle(TrustedWorkerContext(ACCOUNT), proof, operation['executionToken'], 'failed')
    clock[0] += RETENTION_SECONDS - 30
    counts = run_passes(a.ddb, 'authority', Context(), now=a.now)
    assert counts['failed'] == 0 and counts['operationsDeleted'] == 1, counts
    assert row('OPS#'+proof) is None and a.ddb.Table('operations').scan()['Items'] == [a.ddb.Table('operations').get_item(Key={'PK':'OPERATIONS_CONTROL','SK':'GENERATION'})['Item']]
    assert row('CHECK#'+proof)['operationsErasedGeneration'] == GEN
    assert row('CHECK#'+proof)['expiresAt'] == clock[0]+60
    assert a._get('authority', {'PK':'V1#CHECKPOINT','SK':'OPERATIONS_SWEEP_CURSOR'})['revision'] == 1


def test_deletion_constructor_reuses_one_keyring_and_matching_operations(configured, monkeypatch):
    import boto3
    from v1_authority_deletion import app as deletion
    from shared_check_authority import inventory, engineering
    from test_deletion import setup, receipt
    a, _, _, row, _, _ = configured
    _, proof, _, _ = admission(configured); _, cmd, _ = setup(configured)
    calls = []
    monkeypatch.setattr(inventory, 'load_keyring', lambda:(calls.append(1) or ('k1', a.s.hmac_keys)))
    monkeypatch.setattr(engineering, 'allowed_subjects', lambda:frozenset({ACCOUNT}))
    monkeypatch.setattr(boto3, 'resource', lambda *args, **kwargs:a.ddb)
    monkeypatch.setattr(deletion.time, 'time', a.now)
    monkeypatch.setenv('DELETION_RECEIPT_RETENTION_SECONDS', str(120*86400))
    monkeypatch.setenv('DELETION_LEDGER_STREAM_ARN', 'arn:aws:dynamodb:us-east-1:107827791950:table/deletion/stream/2026-09-20T00:00:00.000')
    monkeypatch.setenv('V1_AUTHORITY_DELETION_ENABLED', 'true')
    result = deletion.lambda_handler({'schemaVersion':1,'operation':'reconcile-v1-authority-deletion'}, Context())
    assert result['completed'] == 1 and result['failed'] == 0, result
    assert calls == [1] and receipt(a) is not None and row('OPS#'+proof) is None
    assert len(a.ddb.Table('operations').scan()['Items']) == 1


def test_bad_binding_stops_before_link_lookup_and_duplicate_invocation_stops_before_policy(configured, monkeypatch):
    _, proof, _, wire = admission(configured, mode='ai_primary_v1')
    monkeypatch.setenv('MESSAGE_AI_PRIMARY_ENABLED','true')
    # Bind a real private request with a reviewed link to a new admission.
    # A substituted link/context must stop before policy can issue a lookup.
    changed=deepcopy(wire)
    changed['intent']['target']['reviewedLinks']=[{'url':'https://example.com','scope':'full_url','withheldComponents':[]}]
    calls=[]
    monkeypatch.setattr(app,'lookup',lambda *_:calls.append('lookup'))
    assert app.lambda_handler(changed,Context()) == {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
    assert calls == [] and metadata(configured,proof)['attempts'] == {}
    connections=install_http(monkeypatch)
    assert app.lambda_handler(wire,Context())['aiAssessmentStatus']=='no_warning'
    assert app.lambda_handler(wire,Context()) == {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
    assert len(connections)==1 and calls==[]


def test_enabled_legacy_receiver_rejected_before_policy(configured, monkeypatch):
    from shared_message_contract import POLICY, APPROVAL_SHA
    monkeypatch.setenv('MESSAGE_POLICY_VERSION',POLICY)
    monkeypatch.setenv('MESSAGE_POLICY_APPROVAL_SHA256',APPROVAL_SHA)
    monkeypatch.setattr(app,'evaluate',lambda *_args,**_kwargs:pytest.fail('legacy fallback'))
    request={'schemaVersion':1,'checkId':'legacy','policyVersion':POLICY,'intent':{},'executionBudgetMs':1000,'operationsContext':{}}
    assert app.lambda_handler(request,Context())=={'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}


def test_busy_cleanup_queues_reserve_time_and_independent_operations_cursor(configured, monkeypatch):
    from url_lease_recovery import app as maintenance
    from shared_check_authority.recovery import Recovery
    from shared_check_authority.expiry import Expiry
    a=configured[0]; remaining=[30000]; calls=[]
    class Time:
        def get_remaining_time_in_millis(self):return remaining[0]
    def busy(name,kind):
        def sweep(**kwargs):
            assert kwargs['cursor'] is None
            while kwargs['can_continue']():remaining[0]-=100
            calls.append(name)
            return {'examined':1,'failed':0,'pages':1,kind:1,'cursor':{'PK':'synthetic','SK':name}}
        return sweep
    monkeypatch.setattr(Recovery,'sweep',lambda self,**kwargs:busy('lease','recovered')(**kwargs))
    monkeypatch.setattr(Expiry,'sweep',lambda self,**kwargs:busy('expiry','deleted')(**kwargs))
    monkeypatch.setattr(Expiry,'sweep_operations',lambda self,**kwargs:busy('operations','deleted')(**kwargs))
    result=maintenance.run_passes(a.ddb,'authority',Time(),now=a.now)
    assert calls==['lease','expiry','operations'] and result['failed']==0 and remaining[0]>=4400
    for sk,name in [('LEASE_SWEEP_CURSOR','lease'),('EXPIRY_SWEEP_CURSOR','expiry'),('OPERATIONS_SWEEP_CURSOR','operations')]:
        assert a._get('authority',{'PK':'V1#CHECKPOINT','SK':sk})['cursor']['SK']==name


@pytest.mark.parametrize('setting,value',[('OPERATIONS_ENABLED','yes'),('OPERATIONS_STORE_GENERATION','invalid'),('OPERATIONS_TABLE_NAME','authority')])
def test_malformed_cleanup_configuration_does_not_run(configured, monkeypatch, setting, value):
    from url_lease_recovery.app import run_passes
    monkeypatch.setenv(setting,value)
    with pytest.raises((OperationsError,KeyError)):run_passes(configured[0].ddb,'authority',Context(),now=configured[0].now)
