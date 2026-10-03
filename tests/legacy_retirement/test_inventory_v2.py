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


def request(status, suffix='one', **extra):
    return {'PK': 'ANALYSIS#REQUEST#private-owner', 'SK': suffix, 'status': status,
            'expiresAt': NOW + 100, 'ttl': NOW + 100, **extra}


def receipt(suffix='one', **extra):
    return {'PK': 'ANALYSIS#CONSUMPTION#private-owner', 'SK': suffix,
            'accountIdHash': 'private-owner', 'consumptionType': 'monthly',
            'createdAt': '2026-01-01T00:00:00+00:00',
            'expiresAt': NOW + 100, 'ttl': NOW + 100, **extra}


def test_v2_settlement_requires_exact_identity_and_accounting_and_is_idempotent():
    settled = request('COMPLETED', completedAt='2026-01-01T00:00:00+00:00')
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
    assert 'private-owner' not in json.dumps(first)


def test_unknown_settlement_and_legacy_bonus_are_preserved_without_privilege_conversion():
    value = {'schemaVersion': 2, 'observedAtEpoch': NOW, 'records': [
        {'family': 'request', 'item': request('COMPLETED', completedAt='same')},
        {'family': 'consumption', 'item': receipt(createdAt='different')},
        {'family': 'entitlement', 'item': {'PK': 'USER#private-owner', 'SK': 'ENTITLEMENT',
            'entitlementTier': 'FREE', 'monthlyScanLimit': 15,
            'remainingMonthlyScans': 8, 'remainingCredits': 2}},
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
    assert not report['applyAvailable'] and 'private-owner' not in json.dumps(report)


class FakeClient:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)
    def __getattr__(self, name):
        raise AssertionError('unexpected SDK method: ' + name)


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
        {'Items': [av({'PK':'USER#private-owner','SK':'ENTITLEMENT','entitlementTier':'FREE',
                       'monthlyScanLimit':15,'remainingMonthlyScans':8,'remainingCredits':2})],
         'Count':1,'ScannedCount':1},
        {'Items': [av({'PK':'USER#private-owner','SK':'CAMPAIGN_PARTICIPATION','state':'enrolled'})],
         'Count':1,'ScannedCount':3},
        {'Items': [], 'Count':0,'ScannedCount':4},
    ]
    client = FakeClient(responses)
    tables = {name:f'trustcheckradar-dev-{name}-private' for name in ('requests','authority','consent','deletion')}
    report = collector.collect(client, tables, NOW)
    encoded = json.dumps(report)
    assert len(client.calls) == 4 and all(call['ConsistentRead'] is False for call in client.calls)
    assert report['legacyAccess']['legacyBonus'] == 1
    assert report['writesPerformed'] is False and report['identifiersIncluded'] is False
    assert 'private-owner' not in encoded and 'trustcheckradar-dev-' not in encoded


def test_collector_rejects_non_dev_resources_before_sdk_access():
    client = FakeClient([])
    tables = {name:f'trustcheckradar-prod-{name}' for name in ('requests','authority','consent','deletion')}
    try:
        collector.collect(client, tables, NOW)
        assert False
    except ValueError as error:
        assert str(error) == 'DEV_TABLE_REQUIRED'
    assert client.calls == []
