"""Synthetic finalization tests. Moto DynamoDB and an in-memory Cognito stub only."""
import os
from pathlib import Path
import sys

import pytest

if os.environ.get("AMT_AUTHORITY_INTEGRATION") != "1":
    pytest.skip("Run with AMT_AUTHORITY_INTEGRATION=1 in isolated Moto environment", allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import boto3
from moto import mock_aws
from shared_account_finalization.service import (
    Finalizer, FinalizationError, REQUIRED_COMPONENTS, RETENTION_SECONDS,
    validate_inventory, inventory_condition, serialize_operation,
)

ACCOUNT = "account-a"
NOW = 1800000000
MANIFEST = "a" * 64
OPERATION = "ee9a078b-ae70-4d6f-8229-e8a34a72c9ec"


class CognitoError(Exception):
    def __init__(self, code):
        super().__init__("private-contact@example.invalid")
        self.response = {"Error": {"Code": code}}


class Cognito:
    def __init__(self):
        self.present = True
        self.gets, self.deletes = [], []
        self.response = {"Username": ACCOUNT, "UserAttributes": [{"Name": "sub", "Value": ACCOUNT}, {"Name": "email", "Value": "private-contact@example.invalid"}]}

    def admin_get_user(self, **kwargs):
        self.gets.append(kwargs)
        if not self.present:
            raise CognitoError("UserNotFoundException")
        return self.response

    def admin_delete_user(self, **kwargs):
        self.deletes.append(kwargs)
        self.present = False


def receipt(command, component, occurred=NOW - 1):
    return {"PK": command["PK"], "SK": "ACCOUNT_DELETION#" + component,
            "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
            "eventType": "account.deletion.component.completed", "component": component,
            "status": "COMPLETE", "operationId": OPERATION, "occurredAtEpoch": occurred,
            "requestOccurredAtEpoch": command["occurredAtEpoch"], "retainUntilEpoch": occurred + RETENTION_SECONDS}


@pytest.fixture
def world():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        client = boto3.client("dynamodb", region_name="us-east-1")
        table = ddb.create_table(TableName="ledger", KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}], AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST")
        command = {"PK": "ACCOUNT#" + ACCOUNT, "SK": "ACCOUNT_DELETION", "schemaVersion": 1, "recordVersion": 1,
                   "environment": "dev", "eventType": "account.deletion.requested", "accountId": ACCOUNT,
                   "operationId": OPERATION, "status": "REQUESTED", "occurredAtEpoch": NOW - 10, "deleteByEpoch": NOW - 10 + 86400}
        inventory = {"PK": "INVENTORY#dev", "SK": "ACCOUNT_DATA_INVENTORY", "recordType": "ACCOUNT_DATA_INVENTORY",
                     "schemaVersion": 1, "revision": 1, "environment": "dev", "coverage": "VERIFIED_COMPLETE",
                     "manifestSha256": MANIFEST, "requiredComponents": list(REQUIRED_COMPONENTS),
                     "usernameIsSubVerified": True, "approvedAtEpoch": NOW - 20}
        table.put_item(Item=command)
        table.put_item(Item=inventory)
        table.put_item(Item={"PK":command["PK"],"SK":"CAMPAIGN_RECOVERY_CONTROL",
            "recordType":"CAMPAIGN_RECOVERY_CONTROL","schemaVersion":1,"environment":"dev",
            "revision":2,"pendingJobs":0,"state":"SEALED"})
        for component in REQUIRED_COMPONENTS[:-1]:
            table.put_item(Item=receipt(command, component))
        cognito = Cognito()
        finalizer = Finalizer(ledger_table=table, ledger_table_name="ledger", client=client, cognito=cognito,
                              user_pool_id="us-east-1_example", environment="dev", now=lambda: NOW,
                              enabled=True, manifest_sha256=MANIFEST, inventory_revision=1)
        yield finalizer, table, command, inventory, cognito


def row(table, key):
    return table.get_item(Key={"PK": key["PK"], "SK": key["SK"]}, ConsistentRead=True).get("Item")


def test_default_disabled_never_reads_or_calls_cognito(world):
    finalizer, _, command, _, cognito = world
    finalizer.enabled = False
    with pytest.raises(FinalizationError, match="NOT_ENABLED"):
        finalizer.finalize(command)
    assert cognito.gets == cognito.deletes == []


def test_complete_fence_and_identity_receipt_are_atomic_and_minimal(world):
    finalizer, table, command, _, cognito = world
    assert finalizer.finalize(command) == {"complete": True, "alreadyComplete": False}
    completed = row(table, command)
    assert completed == command | {"eventType": "account.deletion.completed", "status": "COMPLETE", "completedAtEpoch": NOW, "retainUntilEpoch": NOW + RETENTION_SECONDS}
    assert row(table, {"PK": command["PK"], "SK": "ACCOUNT_DELETION#IDENTITY"}) == receipt(command, "IDENTITY", NOW)
    assert cognito.deletes == [{"UserPoolId": "us-east-1_example", "Username": ACCOUNT}]
    assert "private-contact" not in repr(completed) and "expiresAt" not in completed
    assert finalizer.finalize(command) == {"complete": True, "alreadyComplete": True}
    assert len(cognito.gets) == len(cognito.deletes) == 1


@pytest.mark.parametrize("component", REQUIRED_COMPONENTS[:-1])
def test_every_upstream_component_including_campaign_is_required(world, component):
    finalizer, table, command, _, cognito = world
    table.delete_item(Key={"PK": command["PK"], "SK": "ACCOUNT_DELETION#" + component})
    with pytest.raises(FinalizationError, match="COMPONENT_UNVERIFIED"):
        finalizer.finalize(command)
    assert cognito.gets == [] and row(table, command)["status"] == "REQUESTED"


@pytest.mark.parametrize("change", [
    {"schemaVersion": True}, {"recordVersion": True}, {"operationId": "different"},
    {"requestOccurredAtEpoch": NOW - 11}, {"occurredAtEpoch": NOW + 1},
    {"retainUntilEpoch": NOW}, {"extra": "private-contact@example.invalid"},
])
def test_wrong_stale_future_or_extra_component_evidence_blocks_identity(world, change):
    finalizer, table, command, _, cognito = world
    table.put_item(Item=receipt(command, "CAMPAIGN") | change)
    with pytest.raises(FinalizationError, match="COMPONENT_UNVERIFIED"):
        finalizer.finalize(command)
    assert cognito.gets == []


@pytest.mark.parametrize("change", [
    {"revision": 2}, {"manifestSha256": "b" * 64}, {"coverage": "PENDING"},
    {"usernameIsSubVerified": False}, {"approvedAtEpoch": NOW - 10},
    {"approvedAtEpoch": NOW - 9}, {"requiredComponents": ["IDENTITY"]},
    {"schemaVersion": True}, {"extra": "not approved"},
])
def test_unverified_or_newer_inventory_cannot_bless_old_command(world, change):
    finalizer, table, command, inventory, cognito = world
    table.put_item(Item=inventory | change)
    with pytest.raises(FinalizationError, match="INVENTORY_UNVERIFIED"):
        finalizer.finalize(command)
    assert cognito.gets == []


@pytest.mark.parametrize("response", [
    {"Username": "other-account", "UserAttributes": [{"Name": "sub", "Value": ACCOUNT}]},
    {"Username": ACCOUNT, "UserAttributes": [{"Name": "sub", "Value": "other-account"}]},
    {"Username": ACCOUNT, "UserAttributes": [{"Name": "sub", "Value": ACCOUNT}] * 2},
    {"Username": ACCOUNT, "UserAttributes": []},
])
def test_identity_mapping_must_match_exactly_once(world, response):
    finalizer, table, command, _, cognito = world
    cognito.response = response
    with pytest.raises(FinalizationError, match="MAPPING_INVALID"):
        finalizer.finalize(command)
    assert cognito.deletes == [] and row(table, command)["status"] == "REQUESTED"


def test_already_absent_identity_requires_every_upstream_proof(world):
    finalizer, table, command, _, cognito = world
    cognito.present = False
    assert finalizer.finalize(command)["complete"]
    assert cognito.deletes == [] and row(table, command)["status"] == "COMPLETE"


def test_access_denied_is_not_identity_absence_and_leaks_no_sdk_detail(world, monkeypatch):
    finalizer, table, command, _, cognito = world
    def denied(**_):
        raise CognitoError("AccessDeniedException")
    monkeypatch.setattr(cognito, "admin_get_user", denied)
    with pytest.raises(FinalizationError, match="IDENTITY_UNAVAILABLE") as error:
        finalizer.finalize(command)
    assert "private-contact" not in str(error.value)
    assert cognito.deletes == [] and row(table, command)["status"] == "REQUESTED"


def test_lost_identity_delete_response_keeps_pending_then_absence_reconciles(world, monkeypatch):
    finalizer, table, command, _, cognito = world
    original = cognito.admin_delete_user
    def lost_response(**kwargs):
        original(**kwargs)
        raise CognitoError("ServiceUnavailableException")
    monkeypatch.setattr(cognito, "admin_delete_user", lost_response)
    with pytest.raises(FinalizationError, match="DELETE_UNCONFIRMED"):
        finalizer.finalize(command)
    assert row(table, command)["status"] == "REQUESTED"
    assert row(table, {"PK": command["PK"], "SK": "ACCOUNT_DELETION#IDENTITY"}) is None
    assert finalizer.finalize(command)["complete"]
    assert len(cognito.deletes) == 1


def test_proof_race_before_provider_call_prevents_identity_deletion(world, monkeypatch):
    finalizer, table, command, inventory, cognito = world
    original = finalizer._transact
    def change_before_proof(operations):
        table.put_item(Item=inventory | {"revision": 2})
        original(operations)
    monkeypatch.setattr(finalizer, "_transact", change_before_proof)
    with pytest.raises(FinalizationError, match="TRANSACTION_UNCONFIRMED"):
        finalizer.finalize(command)
    assert cognito.gets == [] and row(table, command)["status"] == "REQUESTED"


def test_proof_race_during_provider_call_never_writes_false_completion(world, monkeypatch):
    finalizer, table, command, _, cognito = world
    original = cognito.admin_delete_user
    def change_during_delete(**kwargs):
        original(**kwargs)
        table.put_item(Item=receipt(command, "CAMPAIGN", NOW))
    monkeypatch.setattr(cognito, "admin_delete_user", change_during_delete)
    with pytest.raises(FinalizationError, match="TRANSACTION_UNCONFIRMED"):
        finalizer.finalize(command)
    assert not cognito.present
    assert row(table, command)["status"] == "REQUESTED"
    assert row(table, {"PK": command["PK"], "SK": "ACCOUNT_DELETION#IDENTITY"}) is None
    # New exact upstream proof can safely be re-read; absence of the mapped
    # identity permits reconciliation without another destructive provider call.
    assert finalizer.finalize(command)["complete"]


def test_lost_final_transaction_acknowledgment_reads_completed_fence(world, monkeypatch):
    finalizer, table, command, _, cognito = world
    original = finalizer._transact
    def lost_ack(operations):
        original(operations)
        if any("Put" in op for op in operations):
            raise FinalizationError("FINALIZER_TRANSACTION_UNCONFIRMED")
    monkeypatch.setattr(finalizer, "_transact", lost_ack)
    assert finalizer.finalize(command) == {"complete": True, "alreadyComplete": True}
    assert row(table, command)["status"] == "COMPLETE" and len(cognito.deletes) == 1


def test_component_expiry_during_identity_call_cannot_become_completion(world, monkeypatch):
    finalizer, table, command, _, cognito = world
    original = cognito.admin_delete_user
    def expiry_during_delete(**kwargs):
        original(**kwargs)
        finalizer.now = lambda: NOW + RETENTION_SECONDS
    monkeypatch.setattr(cognito, "admin_delete_user", expiry_during_delete)
    with pytest.raises(FinalizationError, match="COMPONENT_UNVERIFIED"):
        finalizer.finalize(command)
    assert row(table, command)["status"] == "REQUESTED"
    assert row(table, {"PK": command["PK"], "SK": "ACCOUNT_DELETION#IDENTITY"}) is None


def test_component_list_cannot_omit_campaign_in_configuration(world):
    finalizer, table, command, _, cognito = world
    finalizer.components = tuple(c for c in REQUIRED_COMPONENTS if c != "CAMPAIGN")
    with pytest.raises(FinalizationError, match="CONFIGURATION_INVALID"):
        finalizer.finalize(command)
    assert cognito.gets == []


def test_changed_requested_command_is_not_replayed_as_completed(world):
    finalizer, table, command, _, cognito = world
    finalizer.finalize(command)
    with pytest.raises(FinalizationError, match="COMMAND_UNVERIFIED"):
        finalizer.finalize(command | {"operationId": "ee9a078b-ae70-4d6f-8229-e8a34a72c9ed"})
    assert len(cognito.deletes) == 1


def test_completed_suppression_fence_survives_informational_receipt_expiry(world):
    finalizer, table, command, inventory, cognito = world
    finalizer.finalize(command)
    for component in REQUIRED_COMPONENTS:
        table.delete_item(Key={"PK": command["PK"], "SK": "ACCOUNT_DELETION#" + component})
    finalizer.now = lambda: NOW + RETENTION_SECONDS + 1
    assert finalizer.finalize(command) == {"complete": True, "alreadyComplete": True}
    assert row(table, command)["status"] == "COMPLETE" and len(cognito.deletes) == 1


def test_shared_admission_inventory_helper_and_atomic_guard(world):
    finalizer, table, _, inventory, _ = world
    assert validate_inventory(inventory, "dev", MANIFEST, now_epoch=NOW) == inventory
    finalizer.client.transact_write_items(TransactItems=[serialize_operation(inventory_condition("ledger", inventory))])
    table.put_item(Item=inventory | {"revision": 2})
    with pytest.raises(Exception):
        finalizer.client.transact_write_items(TransactItems=[serialize_operation(inventory_condition("ledger", inventory))])


@pytest.mark.parametrize("kind", ["missing", "open", "unknown"])
def test_recovery_jobs_must_be_authoritatively_sealed_before_identity(world, kind):
    finalizer, table, command, _, cognito = world
    key={"PK":command["PK"],"SK":"CAMPAIGN_RECOVERY_CONTROL"}
    if kind == "missing":table.delete_item(Key=key)
    else:
        value=table.get_item(Key=key)["Item"]
        value.update({"state":"OPEN","pendingJobs":1} if kind=="open" else {"extra":True})
        table.put_item(Item=value)
    with pytest.raises(FinalizationError, match="CAMPAIGN_RECOVERY_UNVERIFIED"):
        finalizer.finalize(command)
    assert not cognito.gets and not cognito.deletes


def test_finalizer_removes_sealed_control_in_atomic_terminal_transaction(world):
    finalizer, table, command, _, _=world
    finalizer.finalize(command)
    assert row(table,{"PK":command["PK"],"SK":"CAMPAIGN_RECOVERY_CONTROL"}) is None
