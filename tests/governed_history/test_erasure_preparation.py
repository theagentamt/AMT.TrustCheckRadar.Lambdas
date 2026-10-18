"""Synthetic source preparation and hypothetical CAS tests, never service writes."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from dataclasses import replace
from pathlib import Path
import json
import pickle
import sys
from types import MappingProxyType

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/governed_history"),
               str(ROOT / "tests/shared_check_authority")]
from test_transactions import ACCOUNT, world
from test_service import add, add_url, enable, service
from governed_history.erasure_preparation import (
    ErasurePreparationError, RECEIPT_FIELDS, REMOVE_FIELDS, prepare_receipt_erasure,
)
from shared_check_authority.core import TrustedWorkerContext


@pytest.fixture
def settled(world):
    enable(world)
    authority, event, _, row, _, _ = world
    proof = add(authority, event, "erase-message", "Send me your password and the login code.")
    original = row("CHECK#" + proof)
    return world, proof, original


def prepare(settled, **kwargs):
    world, _, original = settled
    return prepare_receipt_erasure(world[0], kwargs.get("event", world[1]),
                                   kwargs.get("key", {k: original[k] for k in ("PK", "SK")}),
                                   kwargs.get("result", original["governedHistory"]["resultId"]))


def thaw(value):
    if isinstance(value, MappingProxyType):
        return {key: thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [thaw(item) for item in value]
    return value


def hypothetical_actions(plan, *, now):
    """TEST-ONLY supported-schema CAS model. Not a source executor or API.

    It cannot establish the future commit/idempotency/erased-replay contract.
    Source deliberately exposes no equivalent method or dispatch wiring.
    """
    actions = []
    for index, snapshot in enumerate((*plan._identity, plan._receipt)):
        names, values, conditions = {}, {}, []
        fields = thaw(snapshot.fields)
        if fields is None:
            conditions.append("attribute_not_exists(PK)")
        else:
            for number, (name, value) in enumerate(sorted(fields.items())):
                names[f"#f{number}"] = name
                values[f":v{number}"] = value
                conditions.append(f"#f{number} = :v{number}")
        operation = {"TableName": snapshot.table, "Key": thaw(snapshot.key),
                     "ConditionExpression": " AND ".join(conditions)}
        if index == len(plan._identity):
            for number, name in enumerate(sorted(plan._absent_known_fields), start=len(names)):
                names[f"#f{number}"] = name
                conditions.append(f"attribute_not_exists(#f{number})")
            conditions.append("expiresAt > :now")
            values[":now"] = now
            operation["ConditionExpression"] = " AND ".join(conditions)
            operation["UpdateExpression"] = "REMOVE " + ", ".join(sorted(REMOVE_FIELDS))
        if names:
            operation["ExpressionAttributeNames"] = names
        if values:
            operation["ExpressionAttributeValues"] = values
        actions.append({"Update" if index == len(plan._identity) else "ConditionCheck": operation})
    return actions


def apply_synthetic(world, plan):
    world[0].client.transact_write_items(TransactItems=hypothetical_actions(plan, now=world[5][0]))


def assert_error(code, fn):
    with pytest.raises(ErasurePreparationError) as error:
        fn()
    assert error.value.code == code
    assert str(error.value) == code


def test_preparation_has_no_writes_and_private_immutable_nonserializable_snapshot(settled, monkeypatch):
    world, proof, original = settled
    authority = world[0]
    monkeypatch.setattr(authority.client, "transact_write_items", lambda **kw: pytest.fail("WRITE"))
    plan = prepare(settled)
    assert len(plan._identity) == 5
    assert set(plan._receipt.fields) | plan._absent_known_fields == RECEIPT_FIELDS
    assert repr(plan) == "<ErasurePreparation PREPARED_NOT_AUTHORIZED>"
    assert proof not in repr(plan) and ACCOUNT not in repr(plan._receipt)
    assert plan.public_summary()["executionAuthorized"] is False
    assert plan.public_summary()["mutationSucceeded"] is False
    assert not any(hasattr(plan, name) for name in ("execute", "transact_items", "to_json", "dispatch"))
    with pytest.raises(FrozenInstanceError):
        plan._prepared_at_epoch = 0
    with pytest.raises(TypeError):
        plan._receipt.fields["state"] = "ADMITTED"
    with pytest.raises(TypeError):
        plan._receipt.fields["governedHistory"]["accounting"]["chargedChecks"] = 99
    with pytest.raises(TypeError, match="ERASURE_PLAN_NOT_SERIALIZABLE"):
        pickle.dumps(plan)
    with pytest.raises(TypeError):
        json.dumps(plan)
    assert world[3]("CHECK#" + proof) == original


@pytest.mark.parametrize("family", ["message", "url"])
def test_synthetic_remove_preserves_every_other_field_and_no_resurrection(settled, family, monkeypatch):
    world, proof, original = settled
    authority, event, _, row, _, _ = world
    if family == "url":
        proof = add_url(authority, event, "erase-url")
        original = row("CHECK#" + proof)
        settled = world, proof, original
    plan = prepare(settled)
    stale = deepcopy(original)
    accounting_before = row("PERIOD#p1")
    apply_synthetic(world, plan)
    erased = row("CHECK#" + proof)
    assert erased == {k: v for k, v in original.items() if k not in REMOVE_FIELDS}
    assert row("PERIOD#p1") == accounting_before
    api = service(world)
    monkeypatch.setattr(api, "_query", lambda *args, **kw: {"Items": [stale]})
    assert api.list()["items"] == []
    assert api._canonical(stale) is None
    replay = authority.settle(TrustedWorkerContext(ACCOUNT), proof, original["executionToken"],
                              original["processingOutcome"], result_summary=original["resultSummary"])
    assert replay["state"] == "SETTLED" and replay["resultSummary"] is None
    assert authority.reconcile(event, proof)["resultSummary"] is None
    assert row("CHECK#" + proof) == erased
    assert_error("ERASURE_RECORD_NOT_PREPARED", lambda: prepare(settled))
    with pytest.raises(Exception):
        apply_synthetic(world, plan)  # no inferred duplicate-success protocol


def test_no_paid_grant_required_and_unrelated_operations_preserved(settled):
    world, proof, original = settled
    authority, _, _, row, change, _ = world
    change("authority", "ACCESS", state="INACTIVE")
    change("authority", "CHECK#" + proof, operationsProjection={"synthetic": "preserve-exactly"})
    original = row("CHECK#" + proof)
    plan = prepare((world, proof, original))
    apply_synthetic(world, plan)
    assert row("CHECK#" + proof) == {k: v for k, v in original.items() if k not in REMOVE_FIELDS}


def test_actual_operations_obligation_and_earlier_cleanup_schedule_preserved(world):
    enable(world)
    authority, event, _, row, _, _ = world
    authority.s = replace(authority.s, operations_producer_enabled=True)
    proof = add(authority, event, "with-operations", "An unqualified message.")
    original = row("CHECK#" + proof)
    assert "operationsReceipt" in original
    plan = prepare((world, proof, original))
    apply_synthetic(world, plan)
    assert row("CHECK#" + proof) == {k: v for k, v in original.items() if k not in REMOVE_FIELDS}


def test_forged_proof_with_valid_shape_and_owned_partition_is_rejected(settled):
    world, proof, original = settled
    forged = proof[:-24] + "0" * 24
    changed = dict(original, SK="CHECK#" + forged, checkId=forged)
    world[0].ddb.Table("authority").put_item(Item=changed)
    assert_error("ERASURE_RECORD_NOT_PREPARED", lambda: prepare((world, forged, changed)))


def test_final_validation_uses_captured_row_not_later_mutated_sdk_response(settled, monkeypatch):
    world, _, original = settled
    authority = world[0]
    table_factory = authority.ddb.Table
    captured = []

    class Table:
        def __init__(self, name): self.table = table_factory(name)
        def get_item(self, **kwargs):
            response = self.table.get_item(**kwargs)
            captured.append(response)
            return response

    monkeypatch.setattr(authority.ddb, "Table", Table)
    clock = world[5][0]

    def now():
        if captured:
            # Six reads are already frozen before final clock/JWT validation.
            for response in captured:
                response.get("Item", {}).update(receiptId="later-private-mutation")
        return clock

    monkeypatch.setattr(authority, "now", now)
    plan = prepare(settled)
    assert thaw(plan._receipt.fields) == original


@pytest.mark.parametrize("field,value", [
    ("receiptId", "0" * 32), ("chargedChecks", 1), ("chargedChecks", True),
    ("processingOutcome", "failed"), ("assessmentEpoch", 1), ("assessmentEpoch", True),
    ("checkId", "foreign"), ("payloadHmac", "0" * 64), ("policyVersion", "other"),
    ("resultSummary", {}), ("governedHistoryAcceptedAtEpoch", 1),
    ("governedHistorySourceType", "ocr"), ("governedHistoryResultType", "url"),
    ("governedHistoryDigest", "f" * 64), ("expiresAt", 1),
    ("retentionDeadlineEpoch", 1), ("GSI2SK", "bad"), ("GSI1SK", "bad"),
    ("newUnknownField", "unknown"), ("state", "ADMITTED"),
    ("urlTransportVersion", "1.0.0-candidate.2"), ("basis", "unknown"),
])
def test_mixed_corrupt_or_unknown_receipt_is_not_prepared(settled, field, value):
    world, proof, _ = settled
    world[4]("authority", "CHECK#" + proof, **{field: value})
    assert_error("ERASURE_RECORD_NOT_PREPARED", lambda: prepare(settled))


@pytest.mark.parametrize("kind", ["missing", "expired", "already_removed"])
def test_absence_expiry_and_removed_summary_are_not_a_success_receipt(settled, kind):
    world, proof, original = settled
    if kind == "missing":
        world[0].ddb.Table("authority").delete_item(Key={k: original[k] for k in ("PK", "SK")})
    elif kind == "expired":
        world[5][0] = int(original["expiresAt"])
        world[1]["requestContext"]["authorizer"]["jwt"]["claims"]["exp"] = str(world[5][0] + 90)
    else:
        apply_synthetic(world, prepare(settled))
    assert_error("ERASURE_RECORD_NOT_PREPARED", lambda: prepare(settled))


@pytest.mark.parametrize("kind", ["foreign_partition", "foreign_proof", "extra_key", "invalid_result"])
def test_server_reference_is_never_owner_authority(settled, kind):
    original = settled[2]
    key = {k: original[k] for k in ("PK", "SK")}
    result = original["governedHistory"]["resultId"]
    if kind == "foreign_partition":
        key["PK"] = settled[0][0]._partition("other-account", "k1")
    elif kind == "foreign_proof":
        key["SK"] = "CHECK#foreign"
    elif kind == "extra_key":
        key["owner"] = ACCOUNT
    else:
        result = "legacy-id"
    assert_error("ERASURE_REFERENCE_INVALID", lambda: prepare(settled, key=key, result=result))


def test_other_valid_result_id_does_not_select_or_authorize_receipt(settled):
    assert_error("ERASURE_RECORD_NOT_PREPARED",
                 lambda: prepare(settled, result="gh1_1800000000_" + "0" * 32))


@pytest.mark.parametrize("table,sk,values,code", [
    ("users", "PROFILE", {"status": "DELETED"}, "ACCOUNT_UNAVAILABLE"),
    ("users", "PROFILE", {"ageVerified": False}, "ACCOUNT_UNAVAILABLE"),
    ("deletion", "ACCOUNT_DELETION", {"state": "PENDING"}, "ACCOUNT_UNAVAILABLE"),
    ("devices", "ACTIVE_BINDING", {"bindingFingerprint": "device-other"}, "ACTIVE_DEVICE_REQUIRED"),
    ("devices", "DEVICE#device-one", {"accountId": "other"}, "ACTIVE_DEVICE_REQUIRED"),
])
def test_identity_rejections_before_preparation(settled, table, sk, values, code):
    settled[0][4](table, sk, **values)
    assert_error(code, lambda: prepare(settled))


@pytest.mark.parametrize("kind", ["profile", "deletion", "pointer", "binding", "inventory",
                                 "summary", "known_optional_added", "missing", "expired"])
def test_hypothetical_atomic_cas_rejects_concurrent_drift_without_partial_erasure(settled, kind):
    world, proof, original = settled
    authority, _, put, row, change, clock = world
    plan = prepare(settled)
    if kind == "profile": change("users", "PROFILE", status="DISABLED")
    elif kind == "deletion": put("deletion", "ACCOUNT_DELETION", state="PENDING")
    elif kind == "pointer": change("devices", "ACTIVE_BINDING", stateVersion=2)
    elif kind == "binding": change("devices", "DEVICE#device-one", status="INACTIVE")
    elif kind == "inventory":
        table = authority.ddb.Table("authority")
        key = {"PK": "V1#CONTROL", "SK": "HMAC_KEY_INVENTORY"}
        item = table.get_item(Key=key)["Item"]
        table.put_item(Item=item | {"revision": 2})
    elif kind == "summary": change("authority", "CHECK#" + proof, resultSummary={"changed": True})
    elif kind == "known_optional_added": change("authority", "CHECK#" + proof, operationsTerminalReasonCode="LATE")
    elif kind == "missing": authority.ddb.Table("authority").delete_item(Key={k: original[k] for k in ("PK", "SK")})
    else: clock[0] = int(original["expiresAt"])
    before = row("CHECK#" + proof)
    with pytest.raises(Exception):
        apply_synthetic(world, plan)
    assert row("CHECK#" + proof) == before


def test_later_unknown_attribute_is_preserved_not_claimed_fenced_or_all_copy_erased(settled):
    world, proof, _ = settled
    plan = prepare(settled)
    world[4]("authority", "CHECK#" + proof, futureUnreviewedPurpose={"doNotErase": True})
    apply_synthetic(world, plan)
    assert world[3]("CHECK#" + proof)["futureUnreviewedPurpose"] == {"doNotErase": True}
    assert plan.public_summary()["mutationSucceeded"] is False


@pytest.mark.parametrize("which", range(6))
@pytest.mark.parametrize("malformed", ["no_ack", "bad_ack", "exception", "wrong_key"])
def test_every_read_must_be_acknowledged_and_match_key(settled, monkeypatch, which, malformed):
    authority = settled[0][0]
    factory = authority.ddb.Table
    calls = [0]

    class Table:
        def __init__(self, name): self.table = factory(name)
        def get_item(self, **kwargs):
            response = self.table.get_item(**kwargs)
            index = calls[0]
            calls[0] += 1
            if index == which:
                if malformed == "no_ack": response.pop("ResponseMetadata", None)
                elif malformed == "bad_ack": response["ResponseMetadata"]["HTTPStatusCode"] = 503
                elif malformed == "exception": raise RuntimeError("DO_NOT_OUTPUT_PRIVATE_SDK_TEXT")
                else: response["Item"] = {"PK": "wrong", "SK": "wrong"}
            assert kwargs["ConsistentRead"] is True
            return response

    monkeypatch.setattr(authority.ddb, "Table", Table)
    with pytest.raises(ErasurePreparationError) as error:
        prepare(settled)
    assert error.value.code in {"IDENTITY_READ_UNAVAILABLE", "ERASURE_READ_UNAVAILABLE"}
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("kind", ["backwards", "invalid", "expired_auth"])
def test_clock_and_auth_rechecked_at_end_of_snapshot(settled, kind, monkeypatch):
    world = settled[0]
    initial = world[5][0]
    ending = initial - 1 if kind == "backwards" else True if kind == "invalid" else initial + 91
    values = iter([initial, ending])
    monkeypatch.setattr(world[0], "now", lambda: next(values))
    assert_error("AUTHENTICATION_REQUIRED" if kind == "expired_auth" else "ERASURE_CLOCK_UNAVAILABLE",
                 lambda: prepare(settled))


def test_preparation_module_is_not_handler_wiring_or_dispatch():
    import ast
    path = ROOT / "src/governed_history/erasure_preparation.py"
    module = ast.parse(path.read_text())
    forbidden = {"transact_write_items", "put_item", "update_item", "delete_item", "query", "scan"}
    assert not any(isinstance(node, ast.Attribute) and node.attr in forbidden for node in ast.walk(module))
    assert "erasure_preparation" not in (ROOT / "src/governed_history/app.py").read_text()
    assert "erasure_preparation" not in (ROOT / "src/shared_check_authority/core.py").read_text()
