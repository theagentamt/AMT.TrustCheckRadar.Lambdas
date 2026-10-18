"""Independent synthetic snapshot, privacy and preservation counterexamples."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests/governed_history'),
               str(ROOT / 'tests/shared_check_authority')]
from test_transactions import world
from test_erasure_preparation import settled, prepare, apply_synthetic
from test_service import add, service
from governed_history.erasure_preparation import ErasurePreparationError, REMOVE_FIELDS


@pytest.mark.parametrize('initially_valid', [False, True])
def test_receipt_validation_uses_exact_frozen_capture_not_mutated_sdk_response(settled, monkeypatch, initially_valid):
    authority = settled[0][0]
    original = settled[2]
    factory = authority.ddb.Table
    retained = []

    class Table:
        def __init__(self, name): self.table = factory(name)
        def get_item(self, **kwargs):
            response = self.table.get_item(**kwargs)
            if kwargs['Key']['SK'].startswith('CHECK#'):
                if not initially_valid: response['Item']['chargedChecks'] = 9
                retained.append(response)
            return response

    monkeypatch.setattr(authority.ddb, 'Table', Table)
    now = settled[0][5][0]

    def clock():
        if retained:
            # This runs after immutable capture but before final validation.
            retained[0]['Item'].clear()
            retained[0]['Item'].update(deepcopy(original))
            if initially_valid: retained[0]['Item']['chargedChecks'] = 9
        return now

    monkeypatch.setattr(authority, 'now', clock)
    if initially_valid:
        plan = prepare(settled)
        assert plan._receipt.fields['chargedChecks'] == original['chargedChecks']
        assert plan._receipt.fields['receiptId'] == original['receiptId']
    else:
        with pytest.raises(ErasurePreparationError, match='ERASURE_RECORD_NOT_PREPARED'):
            prepare(settled)


def test_actual_both_producers_earlier_operations_queue_is_preserved(settled, monkeypatch):
    world = settled[0]
    authority, event, _, row, _, clock = world
    authority.s = replace(authority.s, operations_producer_enabled=True)
    original_settle = authority.settle

    def delayed_settle(*args, **kwargs):
        clock[0] += 3
        return original_settle(*args, **kwargs)

    monkeypatch.setattr(authority, 'settle', delayed_settle)
    proof = add(authority, event, 'independent-ops-earlier', 'A request outside qualified coverage.')
    before = row('CHECK#' + proof)
    assert before['operationsReceipt']['expiresAtEpoch'] < before['expiresAt']
    assert before['GSI1SK'].startswith(f'{int(before["operationsReceipt"]["expiresAtEpoch"]):012d}#')
    assert service(world).detail(before['governedHistory']['resultId'])['result']['resultId']
    plan = prepare((world, proof, before))
    apply_synthetic(world, plan)
    after = row('CHECK#' + proof)
    assert after == {k: v for k, v in before.items() if k not in REMOVE_FIELDS}
    assert after['operationsReceipt'] == before['operationsReceipt']
    assert after['GSI1SK'] == before['GSI1SK']


@pytest.mark.parametrize('status', [True, 200.0, '200', None])
def test_canonical_read_ack_is_exact_integer_not_coerced(settled, monkeypatch, status):
    authority = settled[0][0]
    factory = authority.ddb.Table

    class Table:
        def __init__(self, name): self.table = factory(name)
        def get_item(self, **kwargs):
            response = self.table.get_item(**kwargs)
            if kwargs['Key']['SK'].startswith('CHECK#'):
                response['ResponseMetadata']['HTTPStatusCode'] = status
            return response

    monkeypatch.setattr(authority.ddb, 'Table', Table)
    with pytest.raises(ErasurePreparationError, match='ERASURE_READ_UNAVAILABLE'):
        prepare(settled)


def test_preparation_does_not_mistake_missing_item_for_previously_erased_success(settled, monkeypatch):
    authority = settled[0][0]
    factory = authority.ddb.Table

    class Table:
        def __init__(self, name): self.table = factory(name)
        def get_item(self, **kwargs):
            response = self.table.get_item(**kwargs)
            if kwargs['Key']['SK'].startswith('CHECK#'): response.pop('Item')
            return response

    monkeypatch.setattr(authority.ddb, 'Table', Table)
    with pytest.raises(ErasurePreparationError, match='ERASURE_RECORD_NOT_PREPARED'):
        prepare(settled)


def test_public_views_are_finite_and_new_summary_is_not_shared_mutable_state(settled):
    plan = prepare(settled)
    text = json.dumps(plan.public_summary()) + repr(plan) + repr(plan._identity) + repr(plan._receipt)
    original = settled[2]
    for value in (original['PK'], original['checkId'], original['executionToken'],
                  original['receiptId'], original['payloadHmac']):
        assert value not in text
    one = plan.public_summary()
    one['executionAuthorized'] = True
    one['mutationSucceeded'] = True
    assert plan.public_summary()['executionAuthorized'] is False
    assert plan.public_summary()['mutationSucceeded'] is False


def test_recreated_canonical_row_cannot_be_removed_by_a_captured_plan(settled):
    world, proof, before = settled
    plan = prepare(settled)
    table = world[0].ddb.Table('authority')
    key = {k: before[k] for k in ('PK', 'SK')}
    table.delete_item(Key=key)
    recreated = deepcopy(before)
    recreated['executionToken'] = '0' * 32
    recreated['clientCheckId'] = 'different-owner-operation'
    table.put_item(Item=recreated)
    with pytest.raises(Exception): apply_synthetic(world, plan)
    assert world[3]('CHECK#' + proof) == recreated
