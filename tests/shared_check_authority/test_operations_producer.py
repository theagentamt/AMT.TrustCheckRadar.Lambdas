"""Actual local authority transactions with server-only operations production."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sys
import subprocess
import types

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_transactions import ACCOUNT, PAYLOAD, WORKER, Authority, AuthorityError, TRIAL_SECONDS, world, admit
from shared_operations import receipt_events
from shared_operations.receipts import OperationsReceiptFault, admission_fields, settlement_fields, RETENTION_SECONDS

SCHEMA = Draft202012Validator(json.loads((Path(__file__).resolve().parents[2] /
                                        "src/operations_reporting/event.schema.json").read_text()))


def enable(a, **kwargs):
    a.s = replace(a.s, operations_producer_enabled=True, **kwargs)
    a.s.validate()


def events(row, now):
    result = receipt_events(row, now_epoch=now)
    for event in result["events"]:
        SCHEMA.validate(event)
    return result


def test_disabled_is_default_has_no_metadata_and_does_not_publish(world):
    a, e, _, row, _, clock = world
    proof, op = admit(world)
    assert "operationsReceipt" not in row("CHECK#" + proof)
    assert events(row("CHECK#" + proof), clock[0]) == {"coverage": "NOT_RECORDED", "events": []}
    a.settle(WORKER, proof, op["executionToken"], "complete")
    assert "operationsTerminalReasonCode" not in row("CHECK#" + proof)


def test_atomic_admission_generates_random_internal_refs_once_and_never_public(world, monkeypatch):
    a, e, _, row, _, clock = world
    enable(a)
    import shared_operations.receipts as module
    generated = iter(["1" * 32, "2" * 32, "3" * 32])
    proof = a.prepare(e, PAYLOAD, "prep")
    # Pin only the projection RNG, leaving preparation/execution cryptography intact.
    calls = []
    original = module.admission_fields
    def recording(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs, random_hex=lambda: next(generated))
    import shared_operations
    monkeypatch.setattr(shared_operations, "admission_fields", recording)
    op = a.admit(e, PAYLOAD, proof)
    private = row("CHECK#" + proof)
    assert private["operationsReceipt"]["logicalCheckRef"] == "lc1_" + "1" * 32
    before = deepcopy(private["operationsReceipt"])
    assert len(calls) == 1
    again = a.admit(e, PAYLOAD, proof)
    assert again["admitted"] is False and len(calls) == 1
    assert row("CHECK#" + proof)["operationsReceipt"] == before
    assert "operations" not in json.dumps(op) and "lc1_" not in json.dumps(op)
    projected = events(private, clock[0])
    assert projected["coverage"] == "ACCEPTED_ONLY"
    assert projected["events"][0]["chargedChecks"] is None
    # No second per-subject/index mapping is created.
    assert not any(r["SK"].startswith("OPS") for r in a.ddb.Table("authority").scan()["Items"])


@pytest.mark.parametrize("outcome,reason,charge", [("complete", None, 1), ("inconclusive", None, 0),
    ("blocked", None, 0), ("invalid_input", None, 0), ("unsupported", None, 0),
    ("partial", "PROVIDER_RATE_LIMITED", 0), ("unavailable", "SERVICE_DISABLED", 0),
    ("failed", "SETTLED_FAILED", 0)])
def test_settled_facts_are_only_post_transaction_and_match_accounting(world, outcome, reason, charge):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    before = deepcopy(row("CHECK#" + proof)["operationsReceipt"])
    public = a.settle(WORKER, proof, op["executionToken"], outcome, operations_reason_code=reason)
    result = events(row("CHECK#" + proof), clock[0])
    assert result["coverage"] == "CHECK_LIFECYCLE_RECORDED"
    accepted, terminal = result["events"]
    assert accepted["logicalCheckRef"] == terminal["logicalCheckRef"]
    assert accepted["lifecycleRevision"] == 1 and terminal["lifecycleRevision"] == 2
    assert terminal["chargedChecks"] == public["chargedChecks"] == charge
    assert row("CHECK#" + proof)["operationsReceipt"] == before
    a.settle(WORKER, proof, op["executionToken"], "complete", operations_reason_code="NONE")
    assert events(row("CHECK#" + proof), clock[0]) == result
    wire = json.dumps(result)
    for forbidden in [ACCOUNT, proof, op["executionToken"], "payloadHmac", "https://example.com", "device-one"]:
        assert forbidden not in wire


@pytest.mark.parametrize("outcome", ["partial", "failed", "unavailable"])
def test_ambiguous_cause_is_explicit_missing_coverage_not_invented_provider_failure(world, outcome):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    a.settle(WORKER, proof, op["executionToken"], outcome)
    result = events(row("CHECK#" + proof), clock[0])
    assert result["coverage"] == "TERMINAL_REASON_NOT_RECORDED" and len(result["events"]) == 1
    assert row("CHECK#" + proof)["processingOutcome"] == outcome
    assert row("CHECK#" + proof)["chargedChecks"] == 0


@pytest.mark.parametrize("basis,charge", [("complimentary", 0), ("trial", 1)])
def test_source_access_class_and_actual_complete_deduction_remain_separate(world, basis, charge):
    a, e, _, row, change, clock = world
    enable(a)
    if basis == "trial":
        change('authority', 'ACCESS', basis=basis, activationKind='explicit', activatedAtEpoch=clock[0],
               validFromEpoch=clock[0], validUntilEpoch=clock[0] + TRIAL_SECONDS)
        change('authority', 'PERIOD#p1', limit=10, usedChecks=0)
    else:
        change('authority', 'ACCESS', basis=basis)
    proof, op = admit(world)
    a.settle(WORKER, proof, op["executionToken"], "complete")
    terminal = events(row("CHECK#" + proof), clock[0])["events"][1]
    assert terminal["accessClass"] == basis and terminal["chargedChecks"] == charge
    assert terminal["accountingState"] == ("charged" if charge else "not_charged")


def test_lost_admission_response_retains_one_committed_reference_and_one_reservation(world, monkeypatch):
    a, e, _, row, _, clock = world
    enable(a)
    proof = a.prepare(e, PAYLOAD, "p")
    original = a.client.transact_write_items
    fired = []
    def uncertain(**kwargs):
        result = original(**kwargs)
        if any("Put" in x and x["Put"]["Item"].get("recordType") == "V1_CHECK_RECEIPT" for x in kwargs['TransactItems']):
            fired.append(1)
            raise RuntimeError("PRIVATE_STORAGE_DETAIL")
        return result
    monkeypatch.setattr(a.client, "transact_write_items", uncertain)
    result = a.admit(e, PAYLOAD, proof)
    before = deepcopy(row("CHECK#" + proof)["operationsReceipt"])
    assert result["admitted"] is False and len(fired) == 1
    assert a.admit(e, PAYLOAD, proof)["admitted"] is False
    assert row("CHECK#" + proof)["operationsReceipt"] == before
    assert row("PERIOD#p1")["reservedChecks"] == 1
    assert events(row("CHECK#" + proof), clock[0])["coverage"] == "ACCEPTED_ONLY"


def test_invalid_reason_fails_before_any_settlement_accounting_or_metadata_change(world):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    before = deepcopy(row("CHECK#" + proof))
    with pytest.raises(AuthorityError, match="OPERATIONS_RECEIPT_INVALID"):
        a.settle(WORKER, proof, op["executionToken"], "complete", operations_reason_code="PRIVATE_EXCEPTION")
    assert row("CHECK#" + proof) == before and row("PERIOD#p1")["reservedChecks"] == 1


def test_expired_lease_source_proves_failed_zero_and_operation_expired(world):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    clock[0] += 121
    a._settle(WORKER, proof, None, "failed", expired_recovery=True)
    terminal = events(row("CHECK#" + proof), clock[0])["events"][1]
    assert terminal["reasonCode"] == "OPERATION_EXPIRED" and terminal["chargedChecks"] == 0


def test_actual_hmac_only_worker_stamps_same_finite_terminal_fact_once(world):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    original = deepcopy(row("CHECK#" + proof)["operationsReceipt"])
    clock[0] += 121
    from shared_check_authority.recovery import Recovery
    worker = Recovery(a.ddb, a.s.authority_table, now=lambda: clock[0])
    assert worker.expire(a._partition(ACCOUNT, 'k1'), proof) is True
    terminal = events(row("CHECK#" + proof), clock[0])["events"][1]
    assert terminal["reasonCode"] == "OPERATION_EXPIRED" and terminal["chargedChecks"] == 0
    assert terminal["eventTimeEpoch"] == clock[0]
    assert row("CHECK#" + proof)["receiptId"].startswith("expired_")
    assert row("CHECK#" + proof)["operationsReceipt"] == original
    assert worker.expire(a._partition(ACCOUNT, 'k1'), proof) is False
    assert row("PERIOD#p1")["reservedChecks"] == row("PERIOD#p1")["usedChecks"] == 0


def test_hmac_worker_metadata_change_cannot_commit_counter_or_terminal_fact(world, monkeypatch):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    clock[0] += 121
    from shared_check_authority.recovery import Recovery
    worker = Recovery(a.ddb, a.s.authority_table, now=lambda: clock[0])
    original = worker.client.transact_write_items
    def racing(**kwargs):
        stored = row("CHECK#" + proof)
        stored["operationsReceipt"]["logicalCheckRef"] = "lc1_" + "e" * 32
        a.ddb.Table("authority").put_item(Item=stored)
        return original(**kwargs)
    monkeypatch.setattr(worker.client, 'transact_write_items', racing)
    with pytest.raises(AuthorityError, match='RECOVERY_TRANSACTION_UNCERTAIN'):
        worker.expire(a._partition(ACCOUNT, 'k1'), proof)
    assert row("CHECK#" + proof)["state"] == 'ADMITTED'
    assert row("PERIOD#p1")["reservedChecks"] == 1 and row("PERIOD#p1")["usedChecks"] == 0


def test_hmac_worker_default_path_rows_and_transaction_match_pinned_release(world):
    a, e, _, row, _, clock = world
    proof, op = admit(world)
    clock[0] += 121
    seed = deepcopy(a.ddb.Table('authority').scan()['Items'])
    from shared_check_authority.recovery import Recovery
    baseline = types.ModuleType('shared_check_authority._ops_baseline_recovery')
    baseline.__package__ = 'shared_check_authority'
    source = subprocess.check_output(['git', '-C', str(Path(__file__).resolve().parents[2]),
        'show', 'cf3b9ad3849a42999f7493efc54ec8350f10b6dc:src/shared_check_authority/recovery.py'])
    exec(compile(source, '<pinned release recovery>', 'exec'), baseline.__dict__)
    def run(kind):
        table = a.ddb.Table('authority')
        for item in table.scan()['Items']:
            table.delete_item(Key={k: item[k] for k in ('PK', 'SK')})
        for item in seed:
            table.put_item(Item=deepcopy(item))
        calls, times = [], []
        def now():
            times.append(clock[0]); return clock[0]
        worker = kind(a.ddb, a.s.authority_table, now=now)
        original = worker.client.transact_write_items
        def capture(**kwargs):
            calls.append(deepcopy(kwargs)); return original(**kwargs)
        worker.client.transact_write_items = capture
        try:
            result = worker.expire(a._partition(ACCOUNT, 'k1'), proof)
        finally:
            worker.client.transact_write_items = original
        return result, calls, times, sorted(table.scan()['Items'], key=lambda x: (x['PK'], x['SK']))
    assert run(Recovery) == run(baseline.Recovery)


def test_metadata_swap_race_cannot_commit_settlement(world):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    original = a._transact
    def race(items):
        old = row("CHECK#" + proof)
        old["operationsReceipt"]["logicalCheckRef"] = "lc1_" + "f" * 32
        a.ddb.Table("authority").put_item(Item=old)
        return original(items)
    a._transact = race
    with pytest.raises(AuthorityError):
        a.settle(WORKER, proof, op["executionToken"], "complete")
    assert row("CHECK#" + proof)["state"] == "ADMITTED" and row("PERIOD#p1")["usedChecks"] == 0


def test_original_operations_expiry_never_follows_later_history_retention(world):
    a, e, _, row, _, clock = world
    from test_governed_history import url_payload, url_summary
    enable(a, governed_history_settlement_enabled=True,
           receipt_retention_seconds=RETENTION_SECONDS, counter_retention_seconds=RETENTION_SECONDS)
    payload = url_payload()
    proof = a.prepare(e, payload, "p", client_check_id="p")
    op = a.admit(e, payload, proof, client_check_id="p")
    accepted = clock[0]
    clock[0] += 5
    a.settle(WORKER, proof, op["executionToken"], "complete", result_summary=url_summary("p"))
    stored = row("CHECK#" + proof)
    assert stored["operationsReceipt"]["expiresAtEpoch"] == accepted + RETENTION_SECONDS
    assert stored["retentionDeadlineEpoch"] == accepted + 5 + RETENTION_SECONDS
    assert receipt_events(stored, now_epoch=accepted + RETENTION_SECONDS) == {"coverage": "EXPIRED", "events": []}
    # The source fence is not a claim of physical removal from the longer-lived row.
    assert "operationsReceipt" in stored


@pytest.mark.parametrize("field", ["subject", "email", "checkId", "payloadHmac", "freeText"])
def test_metadata_extension_cannot_be_projected(world, field):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    stored = row("CHECK#" + proof); stored["operationsReceipt"][field] = "PRIVATE_SENTINEL"
    with pytest.raises(OperationsReceiptFault, match="OPERATIONS_RECEIPT_INVALID"):
        receipt_events(stored, now_epoch=clock[0])


def test_producer_flag_type_is_strict_and_no_preparation_reference_is_minted(world):
    a, e, _, row, _, clock = world
    with pytest.raises(AuthorityError, match="POLICY_CONFIGURATION_UNAVAILABLE"):
        replace(a.s, operations_producer_enabled="false").validate()
    enable(a)
    a.prepare(e, PAYLOAD, "prepared-only", client_check_id="prepared-only")
    assert "operationsReceipt" not in row("PREPARE#prepared-only")


def test_random_failure_does_not_reserve_or_admit(world, monkeypatch):
    a, e, _, row, _, clock = world
    enable(a)
    proof = a.prepare(e, PAYLOAD, "p")
    import shared_operations
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_BODY")
    monkeypatch.setattr(shared_operations, "admission_fields", fail)
    with pytest.raises(AuthorityError, match="OPERATIONS_RECEIPT_INVALID"):
        a.admit(e, PAYLOAD, proof)
    assert row("CHECK#" + proof) is None and row("PERIOD#p1")["reservedChecks"] == 0
