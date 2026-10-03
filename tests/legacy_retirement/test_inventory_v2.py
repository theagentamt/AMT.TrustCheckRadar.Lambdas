"""Aggregate-only migration inventory: no identities, grants, or mutation path."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


planner = module('retirement_plan_v2', ROOT/'tools/legacy_access_inventory/plan.py')
collector = module('retirement_collect_v2', ROOT/'tools/legacy_access_inventory/collect_dev.py')
NOW = 1_800_000_000
OWNER = 'a' * 64
PAYLOAD = 'b' * 64
STAMP = '2026-01-01T00:00:00+00:00'
ZSTAMP = '2026-01-01T00:00:00Z'
EPOCH = '15c81ba4-2fa6-43c3-8895-889f08c931bf'
OPERATION = '47debb73-444b-4bb1-9889-fb56885b7922'


def request(status, suffix='one', **extra):
    row = {'PK': 'ANALYSIS#REQUEST#' + OWNER, 'SK': suffix, 'status': status,
           'payloadHash': PAYLOAD, 'createdAt': STAMP, 'updatedAt': STAMP,
           'expiresAt': NOW + 100, 'ttl': NOW + 100}
    if status == 'PROCESSING': row |= {'leaseToken': 'lease', 'leaseExpiresAt': NOW + 10}
    if status in ('RESULT_READY', 'COMPLETED'):
        row |= {'response': {'requestId': suffix}, 'resultReadyAt': STAMP}
    if status == 'COMPLETED': row['completedAt'] = STAMP
    return row | extra


def receipt(suffix='one', **extra):
    return {'PK': 'ANALYSIS#CONSUMPTION#' + OWNER, 'SK': suffix,
            'accountIdHash': OWNER, 'consumptionType': 'monthly', 'createdAt': STAMP,
            'expiresAt': NOW + 100, 'ttl': NOW + 100, **extra}


def entitlement():
    return {'PK':'USER#owner','SK':'ENTITLEMENT','accountId':'owner',
            'billingPeriodEndUtc':None,'billingPeriodStartUtc':None,'createdAt':STAMP,
            'entitlementTier':'FREE','isAccessGranted':False,'lastVerifiedAtUtc':None,
            'monthlyScanLimit':15,'platform':None,'productId':None,'remainingCredits':2,
            'remainingMonthlyScans':8,'subscriptionStatus':'expired','updatedAt':STAMP}


def consent(**changes):
    return {'PK':'USER#owner','SK':'CAMPAIGN_PARTICIPATION','schemaVersion':1,
            'recordVersion':1,'environment':'dev','state':'enrolled','stateVersion':1,
            'noticeVersion':'2026-09-07','policyVersion':'policy-1','consentEpochId':EPOCH,
            'effectiveFrom':ZSTAMP,'updatedAt':ZSTAMP,'lastOperationId':OPERATION} | changes


def consent_operation(**changes):
    return {'PK':'USER#owner','SK':'CAMPAIGN_OPERATION#'+OPERATION,'schemaVersion':1,
            'recordVersion':1,'operationId':OPERATION,'action':'join','consentEpochId':EPOCH,
            'resultingState':'enrolled','occurredAt':ZSTAMP,
            'expiresAt':1767225600+400*86400} | changes


def consent_audit(**changes):
    occurred = 1767225600
    return {'PK':'USER#owner','SK':f'CAMPAIGN_CONSENT#{EPOCH}#{occurred}#{OPERATION}',
            'schemaVersion':1,'recordVersion':1,'eventType':'campaign.participation.joined',
            'occurredAt':ZSTAMP,'noticeVersion':'2026-09-07','policyVersion':'policy-1',
            'consentEpochId':EPOCH,'operationId':OPERATION,'resultingState':'enrolled',
            'stateVersion':1,'effectiveMonthlyScanLimit':15,
            'expiresAt':occurred+400*86400} | changes


def deletion(**changes):
    return {'PK':'ACCOUNT#owner','SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,
            'environment':'dev','eventType':'account.deletion.completed','accountId':'owner',
            'operationId':OPERATION,'status':'COMPLETE','occurredAtEpoch':NOW-100,
            'deleteByEpoch':NOW-100+86400,'completedAtEpoch':NOW-10,
            'retainUntilEpoch':NOW-10+120*86400} | changes


def test_v2_settlement_requires_exact_identity_and_accounting_and_is_idempotent():
    settled = request('COMPLETED')
    value = {'schemaVersion': 2, 'observedAtEpoch': NOW, 'records': [
        {'family': 'request', 'item': settled}, {'family': 'consumption', 'item': receipt()},
        {'family': 'request', 'item': request('PROCESSING', suffix='two')},
        {'family': 'request', 'item': request('RESULT_READY', suffix='three', expiresAt=NOW-1, ttl=NOW-1)},
    ]}
    first = planner.plan(value); second = planner.plan(json.loads(json.dumps(value)))
    assert first == second
    assert first['requestLifecycle'] == {'neverDispatched': 0, 'inFlightAmbiguous': 2,
                                         'settled': 1, 'erased': 0, 'unknown': 0}
    assert first['expiredRecordCount'] == 1
    assert first['neverDispatchedProofAvailable'] is False
    assert not any(first[name] for name in ('applyAvailable', 'inventoryComplete',
                                             'replayQualified', 'migrationApproved'))
    assert OWNER not in json.dumps(first)


def test_unknown_settlement_and_legacy_bonus_are_preserved_without_privilege_conversion():
    value = {'schemaVersion': 2, 'observedAtEpoch': NOW, 'records': [
        {'family': 'request', 'item': request('COMPLETED')},
        {'family': 'consumption', 'item': receipt(createdAt='different')},
        {'family': 'entitlement', 'item': entitlement()},
        {'family': 'current_authority', 'item': {'PK': 'mystery', 'SK': 'mystery'}},
    ]}
    report = planner.plan(value)
    assert report['requestLifecycle']['settled'] == 0
    assert report['requestLifecycle']['unknown'] == 1
    assert report['legacyAccess'] == {'legacyBonus': 1, 'legacyFree': 0,
                                      'legacyPaid': 0, 'currentAccess': 0,
                                      'currentSupporting': 0,
                                      'unknown': 1}
    assert report['unknownRecovery'] == 'preserve_source_and_require_explicit_review'
    assert report['shapeClassifications']['unknown'] == 1
    assert not report['applyAvailable'] and OWNER not in json.dumps(report)


def test_duplicate_consumption_is_ambiguous_in_both_input_orders():
    base = [{'family':'request','item':request('COMPLETED')}]
    duplicates = [{'family':'consumption','item':receipt()},
                  {'family':'consumption','item':receipt()}]
    reports = [planner.plan({'schemaVersion':2,'observedAtEpoch':NOW,'records':base+order})
               for order in (duplicates, list(reversed(duplicates)))]
    for report in reports:
        assert report['requestLifecycle']['settled'] == 0
        assert report['requestLifecycle']['unknown'] == 1
        assert not report['migrationApproved'] and not report['applyAvailable']
    assert reports[0]['requestLifecycle'] == reports[1]['requestLifecycle']


def test_duplicate_conflicting_consumption_is_order_independent_unknown():
    first, second = receipt(), receipt(consumptionType='credit')
    reports = [planner.plan({'schemaVersion':2,'observedAtEpoch':NOW,'records':[
        {'family':'request','item':request('COMPLETED')},
        {'family':'consumption','item':left},{'family':'consumption','item':right}]})
        for left, right in ((first, second), (second, first))]
    assert [r['requestLifecycle'] for r in reports] == [
        {'neverDispatched':0,'inFlightAmbiguous':0,'settled':0,'erased':0,'unknown':1}
    ] * 2


def test_hostile_request_and_receipt_shapes_never_settle():
    hostile_requests = [request('COMPLETED', schemaVersion=999),
                        request('COMPLETED', recordType='UNRELATED', schemaVersion=1),
                        request('COMPLETED', unknownField='x')]
    hostile_receipts = [receipt(schemaVersion=999),
                        receipt(recordType='UNRELATED', schemaVersion=1),
                        receipt(unknownField='x')]
    for bad_request in hostile_requests:
        report = planner.plan({'schemaVersion':2,'observedAtEpoch':NOW,'records':[
            {'family':'request','item':bad_request},{'family':'consumption','item':receipt()}]})
        assert report['shapeClassifications']['unknown'] >= 1
        assert report['requestLifecycle']['unknown'] == 1 and report['requestLifecycle']['settled'] == 0
    for bad_receipt in hostile_receipts:
        report = planner.plan({'schemaVersion':2,'observedAtEpoch':NOW,'records':[
            {'family':'request','item':request('COMPLETED')},{'family':'consumption','item':bad_receipt}]})
        assert report['shapeClassifications']['unknown'] >= 1
        assert report['requestLifecycle']['unknown'] == 1 and report['requestLifecycle']['settled'] == 0


def test_exact_permitted_legacy_markers_can_be_classified_but_never_apply():
    marked_request = request('COMPLETED', recordType='LEGACY_ANALYSIS_REQUEST', schemaVersion=1)
    marked_receipt = receipt(recordType='LEGACY_ANALYSIS_CONSUMPTION', schemaVersion=1)
    report = planner.plan({'schemaVersion':2,'observedAtEpoch':NOW,'records':[
        {'family':'request','item':marked_request},{'family':'consumption','item':marked_receipt}]})
    assert report['shapeClassifications'] == {'known':2,'unknown':0}
    assert report['requestLifecycle']['settled'] == 1
    assert not report['applyAvailable'] and not report['migrationApproved']


def test_malformed_item_is_preserved_unknown_without_crash():
    report = planner.plan({'schemaVersion':2,'observedAtEpoch':NOW,'records':[
        {'family':'request','item':['not','a','map']} ]})
    assert report['shapeClassifications'] == {'known':0,'unknown':1}
    assert report['requestLifecycle']['unknown'] == 1


def test_exact_documented_consent_and_deletion_shapes_are_preservation_known_only():
    records = [('consent', consent()), ('consent_operation', consent_operation()),
               ('consent_audit', consent_audit()), ('deletion', deletion())]
    report = planner.plan({'schemaVersion':2,'observedAtEpoch':NOW,'records':[
        {'family':family,'item':item} for family,item in records]})
    assert report['shapeClassifications'] == {'known':4,'unknown':0}
    assert not any(report[name] for name in ('applyAvailable','inventoryComplete',
                                             'replayQualified','migrationApproved'))


def test_consent_and_deletion_hostile_versions_keys_environment_and_states_stay_unknown():
    fixtures = {
        'consent': consent,
        'consent_operation': consent_operation,
        'consent_audit': consent_audit,
        'deletion': deletion,
    }
    hostile = {
        'consent': [
            {'schemaVersion':999}, {'recordVersion':999}, {'PK':'ACCOUNT#other'},
            {'SK':'CAMPAIGN_PARTICIPATION#other'}, {'environment':'prod'}, {'state':'withdrawn'},
        ],
        'consent_operation': [
            {'schemaVersion':999}, {'recordVersion':999}, {'PK':'ACCOUNT#other'},
            {'SK':'CAMPAIGN_OPERATION#'+EPOCH}, {'environment':'dev'}, {'resultingState':'withdrawn'},
        ],
        'consent_audit': [
            {'schemaVersion':999}, {'recordVersion':999}, {'PK':'ACCOUNT#other'},
            {'SK':f'CAMPAIGN_CONSENT#{EPOCH}#{1767225601}#{OPERATION}'},
            {'environment':'dev'}, {'resultingState':'withdrawal_pending'},
        ],
        'deletion': [
            {'schemaVersion':999}, {'recordVersion':999}, {'PK':'ACCOUNT#other'},
            {'SK':'ACCOUNT_DELETION#other'}, {'environment':'prod'}, {'status':'REQUESTED'},
        ],
    }
    for family, factory in fixtures.items():
        missing_schema, missing_record = factory(), factory()
        missing_schema.pop('schemaVersion'); missing_record.pop('recordVersion')
        rows = [missing_schema, missing_record] + [factory(**change) for change in hostile[family]]
        for row in rows:
            report = planner.plan({'schemaVersion':2,'observedAtEpoch':NOW,
                                   'records':[{'family':family,'item':row}]})
            assert report['shapeClassifications'] == {'known':0,'unknown':1}
            assert report['preservationClassifications'] == {'unknown_shape':1}
            assert not report['migrationApproved'] and not report['applyAvailable']


def test_oversized_ascii_consent_audit_epoch_is_unknown_without_integer_conversion_crash():
    row = consent_audit(SK=f'CAMPAIGN_CONSENT#{EPOCH}#{"9" * 5000}#{OPERATION}')
    report = planner.plan({'schemaVersion':2,'observedAtEpoch':NOW,
                           'records':[{'family':'consent_audit','item':row}]})
    assert report['shapeClassifications'] == {'known':0,'unknown':1}
    assert report['preservationClassifications'] == {'unknown_shape':1}
    assert not report['migrationApproved'] and not report['applyAvailable']


class FakeClient:
    class Meta:
        def __init__(self, region): self.region_name = region
    def __init__(self, responses, region='us-east-1'):
        self.responses, self.calls, self.meta = list(responses), [], self.Meta(region)
    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)
    def __getattr__(self, name):
        raise AssertionError('unexpected SDK method: ' + name)


class FakeSts:
    def __init__(self, account='107827791950', region='us-east-1'):
        self.account, self.calls, self.meta = account, 0, FakeClient.Meta(region)
    def get_caller_identity(self):
        self.calls += 1
        return {'Account':self.account}


def av(item):
    result = {}
    for key, value in item.items():
        if isinstance(value, str): result[key] = {'S': value}
        elif isinstance(value, bool): result[key] = {'BOOL': value}
        elif isinstance(value, int): result[key] = {'N': str(value)}
        elif value is None: result[key] = {'NULL': True}
        else: raise AssertionError(value)
    return result


def test_collector_uses_scan_only_and_emits_aggregate_without_resources_or_identifiers():
    responses = [
        {'Items': [], 'Count': 0, 'ScannedCount': 0},
        {'Items': [av({'PK':'USER#owner','SK':'ENTITLEMENT','entitlementTier':'FREE',
                       'monthlyScanLimit':15,'remainingMonthlyScans':8,'remainingCredits':2,
                       'isAccessGranted':False,'accountId':'owner'})],
         'Count':1,'ScannedCount':1},
        {'Items': [av({'PK':'USER#private-owner','SK':'CAMPAIGN_PARTICIPATION','state':'enrolled'})],
         'Count':1,'ScannedCount':3},
        {'Items': [], 'Count':0,'ScannedCount':4},
    ]
    client, sts = FakeClient(responses), FakeSts()
    report = collector.collect(client, sts, dict(collector.EXPECTED_TABLES), NOW, region='us-east-1')
    encoded = json.dumps(report)
    assert len(client.calls) == 4 and all(call['ConsistentRead'] is False for call in client.calls)
    assert report['legacyAccess']['legacyBonus'] == 0
    assert report['projectedCandidates']['legacyBonus'] == 1
    assert report['writesPerformed'] is False and report['identifiersIncluded'] is False
    assert 'owner' not in encoded and 'trustcheckradar-dev-' not in encoded
    assert sts.calls == 1
    assert all(call['ProjectionExpression'] == collector.PROJECTION for call in client.calls)
    projection_fields = set(collector.PROJECTION.replace('#state','state').replace('#status','status').replace('#ttl','ttl').split(','))
    assert not projection_fields & collector.FORBIDDEN_CONTENT_FIELDS


def test_collector_rejects_arbitrary_and_swapped_tables_before_sdk_access():
    for tables in (
        {name:f'trustcheckradar-dev-{name}-private' for name in collector.EXPECTED_TABLES},
        dict(collector.EXPECTED_TABLES, requests=collector.EXPECTED_TABLES['authority'],
             authority=collector.EXPECTED_TABLES['requests']),
    ):
        client, sts = FakeClient([]), FakeSts()
        try: collector.collect(client, sts, tables, NOW, region='us-east-1'); assert False
        except ValueError as error: assert str(error) == 'INVENTORY_TABLES_INVALID'
        assert client.calls == [] and sts.calls == 0


def test_collector_rejects_wrong_region_and_account_before_scan():
    client, sts = FakeClient([]), FakeSts()
    try: collector.collect(client, sts, dict(collector.EXPECTED_TABLES), NOW, region='us-west-2'); assert False
    except ValueError as error: assert str(error) == 'DEV_REGION_REQUIRED'
    assert client.calls == [] and sts.calls == 0
    client, sts = FakeClient([]), FakeSts(account='000000000000')
    try: collector.collect(client, sts, dict(collector.EXPECTED_TABLES), NOW, region='us-east-1'); assert False
    except ValueError as error: assert str(error) == 'DEV_ACCOUNT_REQUIRED'
    assert client.calls == [] and sts.calls == 1
    for client, sts in ((FakeClient([], region='us-west-2'), FakeSts()),
                        (FakeClient([]), FakeSts(region='us-west-2'))):
        try: collector.collect(client, sts, dict(collector.EXPECTED_TABLES), NOW, region='us-east-1'); assert False
        except ValueError as error: assert str(error) == 'DEV_REGION_REQUIRED'
        assert client.calls == [] and sts.calls == 0


def test_collector_preserves_malformed_nonmap_item_unknown():
    responses=[{'Items':[[]],'Count':1,'ScannedCount':1}]+[
        {'Items':[],'Count':0,'ScannedCount':0} for _ in range(3)]
    report=collector.collect(FakeClient(responses),FakeSts(),dict(collector.EXPECTED_TABLES),NOW,region='us-east-1')
    assert report['shapeClassifications']['unknown']==1 and not report['applyAvailable']
