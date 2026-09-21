import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "campaign_participation"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

for name in ["config", "errors", "service", "shared_entitlements", "shared_entitlements.config", "shared_entitlements.service"]:
    sys.modules.pop(name, None)

ENVIRONMENT = {
    "USERS_TABLE_NAME": "test-users",
    "ENTITLEMENTS_TABLE_NAME": "test-entitlements",
    "DELETION_LEDGER_TABLE_NAME": "test-deletion-ledger",
    "ENVIRONMENT": "dev",
    "CAMPAIGN_PARTICIPATION_NOTICE_VERSION": "research-consent-2026-09-21-v2",
    "CAMPAIGN_PARTICIPATION_POLICY_VERSION": "independent-research-v1",
    "CONSENT_INDEPENDENCE_ENABLED": "true",
    "PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT": "15",
}


class FakeClientError(Exception):
    def __init__(self, response=None, operation_name=None):
        super().__init__(operation_name or "client error")
        self.response = response or {}


botocore_ex = types.ModuleType("botocore.exceptions")
botocore_ex.ClientError = FakeClientError
sys.modules["botocore.exceptions"] = botocore_ex


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": dict(item)} if item else {}

users = FakeTable()
entitlements = FakeTable()
ledger = FakeTable()


class FakeResource:
    def Table(self, name):
        if name == "test-users":
            return users
        if name == "test-entitlements":
            return entitlements
        return ledger


class FakeClient:
    def __init__(self):
        self.transactions = []
        self.error = None

    def transact_write_items(self, TransactItems):
        if self.error:
            raise self.error
        self.transactions.append(TransactItems)
        return {}


client = FakeClient()
boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *_args, **_kwargs: FakeResource()
boto3_stub.client = lambda *_args, **_kwargs: client
sys.modules["boto3"] = boto3_stub


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


with mock.patch.dict(os.environ, ENVIRONMENT, clear=False):
    config = load("config", MODULE_DIR / "config.py")
    load("errors", MODULE_DIR / "errors.py")
    import shared_entitlements.service as shared_service  # noqa: E402
    service = load("service", MODULE_DIR / "service.py")

OPERATION_ID = "47debb73-444b-4bb1-9889-fb56885b7922"
EPOCH_ID = "15c81ba4-2fa6-43c3-8895-889f08c931bf"


def payload(action="join", operation_id=OPERATION_ID):
    return {"schemaVersion": 2, "expectedStateVersion": 0, "action": action, "noticeVersion": config.NOTICE_VERSION, "operationId": operation_id}


def enrolled_state(**updates):
    value = {
        "PK": "USER#user-123", "SK": "CAMPAIGN_PARTICIPATION", "schemaVersion": 1,
        "recordVersion": 1, "environment": "dev", "state": "enrolled", "stateVersion": 1,
        "noticeVersion": config.NOTICE_VERSION, "policyVersion": config.POLICY_VERSION,
        "consentEpochId": EPOCH_ID, "effectiveFrom": "2026-09-07T00:00:00Z",
        "updatedAt": "2026-09-07T00:00:00Z", "lastOperationId": OPERATION_ID,
    }
    value.update(updates)
    return value


class ParticipationServiceTests(unittest.TestCase):
    def setUp(self):
        users.items.clear()
        entitlements.items.clear()
        ledger.items.clear()
        client.transactions.clear()
        client.error = None
        service.users_table = users
        service.entitlements_table = entitlements
        service.deletion_ledger_table = ledger
        service.dynamodb = client
        shared_service.table = entitlements
        shared_service.participation_table = users
        users.items[("USER#user-123", "PROFILE")] = {
            "PK": "USER#user-123", "SK": "PROFILE", "sub": "user-123",
            "status": "ACTIVE", "ageVerified": True,
        }

    def test_default_off_has_no_access_or_internal_identity(self):
        result = service.get_participation("user-123")
        self.assertEqual(result["state"], "not_enrolled")
        self.assertEqual(result["policyVersion"], "independent-research-v1")
        self.assertFalse(result["contributionEligible"])
        for forbidden in ("PK", "accountId", "monthlyScanLimit", "effectiveMonthlyScanLimit", "bonusMonthlyScans"):
            self.assertNotIn(forbidden, result)

    def test_join_writes_only_consent_never_entitlements(self):
        with mock.patch.object(service.uuid, "uuid4", return_value=service.uuid.UUID(EPOCH_ID)):
            result = service.update_participation("user-123", payload(), now_epoch=1788739200, now_iso="2026-09-07T00:00:00Z")
        self.assertEqual(result["state"], "enrolled")
        transaction = client.transactions[0]
        self.assertEqual(len(transaction), 5)
        self.assertTrue(all(next(iter(x.values()))["TableName"] != "test-entitlements" for x in transaction))
        self.assertEqual(transaction[1]["Put"]["Item"]["expiresAt"], {"N": str(1788739200 + 400*86400)})
        self.assertEqual(result["operation"]["requestSchemaVersion"], 2)
        self.assertEqual(result["operation"]["requestNoticeVersion"], config.CURRENT_NOTICE)

    def test_legacy_is_review_required_without_mutation(self):
        users.items[("USER#user-123", "CAMPAIGN_PARTICIPATION")] = enrolled_state(noticeVersion="2026-09-07", policyVersion="policy-1")
        result = service.get_participation("user-123")
        self.assertEqual(result["state"], "review_required")
        self.assertFalse(result["contributionEligible"])
        self.assertEqual(result["acceptedNoticeVersion"], "2026-09-07")
        self.assertEqual(client.transactions, [])

    def test_withdraw_legacy_while_join_gate_closed_preserves_notice_and_deadline(self):
        users.items[("USER#user-123", "CAMPAIGN_PARTICIPATION")] = enrolled_state(noticeVersion="2026-09-07", policyVersion="policy-1")
        with mock.patch.object(config, "CONSENT_INDEPENDENCE_ENABLED", False):
            result = service.update_participation("user-123", payload("withdraw") | {"schemaVersion":1,"noticeVersion":"2026-09-07"}, now_epoch=1788739200, now_iso="2026-09-07T00:00:00Z")
        self.assertEqual(result["state"], "withdrawal_pending")
        self.assertEqual(result["acceptedNoticeVersion"], "2026-09-07")
        self.assertEqual(result["withdrawalStatus"], "pending")
        transaction = client.transactions[0]
        self.assertEqual(len(transaction), 6)
        command = transaction[3]["Put"]["Item"]
        self.assertEqual(command["deleteByEpoch"], {"N": str(1788739200+86400)})
        self.assertEqual(command["status"], {"S":"PENDING"})
        self.assertTrue(all(next(iter(x.values()))["TableName"] != "test-entitlements" for x in transaction))

    def test_join_gate_and_old_join_never_mutate(self):
        for request, code in ((payload() | {"schemaVersion":1}, "POLICY_REVIEW_REQUIRED"), (payload(), "JOIN_UNAVAILABLE")):
            with mock.patch.object(config, "CONSENT_INDEPENDENCE_ENABLED", False):
                with self.assertRaises(service.AppError) as error:
                    service.update_participation("user-123", request)
                self.assertEqual(error.exception.code, code)
        self.assertEqual(client.transactions, [])

    def test_operation_evidence_independent_of_current_state_and_legacy_replay(self):
        old = {"PK":"USER#user-123","SK":f"CAMPAIGN_OPERATION#{OPERATION_ID}","schemaVersion":1,"recordVersion":1,"operationId":OPERATION_ID,"action":"join","consentEpochId":EPOCH_ID,"resultingState":"enrolled","occurredAt":"2026-09-07T00:00:00Z","expiresAt":1823299200}
        users.items[(old["PK"],old["SK"])] = old
        result = service.update_participation("user-123", payload() | {"schemaVersion":1})
        self.assertEqual(result["state"], "not_enrolled")
        self.assertEqual(result["operation"]["status"], "applied_legacy")
        self.assertIsNone(result["operation"]["requestNoticeVersion"])
        with self.assertRaises(service.AppError) as error:
            service.update_participation("user-123", payload())
        self.assertEqual(error.exception.code, "CONFLICT")
        self.assertEqual(client.transactions, [])

    def test_missing_operation_is_unresolved_not_synthesized(self):
        result = service.get_participation("user-123", OPERATION_ID)
        self.assertEqual(result["operation"]["status"], "not_found")
        self.assertIsNone(result["operation"]["action"])

    def test_unknown_state_fields_and_malformed_numbers_fail_closed(self):
        for change in ({"unexpected":"preserve"},{"stateVersion":True},{"stateVersion":1.5},{"lastOperationId":"bad"}):
            users.items[("USER#user-123","CAMPAIGN_PARTICIPATION")] = enrolled_state(**change)
            with self.assertRaises(service.AppError):
                service.update_participation("user-123", payload("withdraw"))
        self.assertEqual(client.transactions, [])

    def test_deletion_fence_blocks_replay_and_new_work(self):
        ledger.items[("ACCOUNT#user-123","ACCOUNT_DELETION")] = {"status":"COMPLETE"}
        with self.assertRaises(service.AppError) as error:
            service.get_participation("user-123", OPERATION_ID)
        self.assertEqual(error.exception.code,"FORBIDDEN")

    def test_explicit_new_notice_creates_new_epoch_preserves_old_audit(self):
        users.items[("USER#user-123","CAMPAIGN_PARTICIPATION")] = enrolled_state(noticeVersion="2026-09-07",policyVersion="policy-1")
        result = service.update_participation("user-123",payload() | {"expectedStateVersion":1})
        self.assertNotEqual(result["consentEpochId"],EPOCH_ID)
        self.assertEqual(result["stateVersion"],2)
        self.assertTrue(result["contributionEligible"])
        self.assertIn("noticeVersion = :notice",client.transactions[0][0]["Put"]["ConditionExpression"])

if __name__ == "__main__":
    unittest.main()
