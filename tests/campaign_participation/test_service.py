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
    "CAMPAIGN_PARTICIPATION_NOTICE_VERSION": "notice-2026-09",
    "CAMPAIGN_PARTICIPATION_POLICY_VERSION": "policy-1",
    "FREE_MONTHLY_SCAN_LIMIT": "10",
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


class FakeResource:
    def Table(self, name):
        return users if name == "test-users" else entitlements


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
    return {"schemaVersion": 1, "action": action, "noticeVersion": config.NOTICE_VERSION, "operationId": operation_id}


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
        client.transactions.clear()
        client.error = None
        service.users_table = users
        service.entitlements_table = entitlements
        service.dynamodb = client
        shared_service.table = entitlements
        shared_service.participation_table = users

    def test_default_is_off_and_response_has_no_internal_identity(self):
        result = service.get_participation("user-123")

        self.assertEqual(result["state"], "not_enrolled")
        self.assertEqual(result["policyVersion"], "policy-1")
        self.assertEqual(result["baseFreeMonthlyScanLimit"], 10)
        self.assertEqual(result["participatingFreeMonthlyScanLimit"], 15)
        self.assertEqual(result["bonusMonthlyScans"], 5)
        self.assertEqual(result["effectiveMonthlyScanLimit"], 10)
        self.assertNotIn("PK", result)
        self.assertNotIn("accountId", result)

    def test_join_atomically_writes_state_receipt_and_adjusted_entitlement(self):
        with mock.patch.object(service.uuid, "uuid4", return_value=service.uuid.UUID(EPOCH_ID)):
            result = service.update_participation(
                "user-123", payload(), now_epoch=1_788_739_200, now_iso="2026-09-07T00:00:00Z"
            )

        self.assertEqual(result["state"], "enrolled")
        self.assertEqual(result["effectiveMonthlyScanLimit"], 15)
        transaction = client.transactions[0]
        self.assertEqual(len(transaction), 4)
        state = transaction[0]["Put"]["Item"]
        self.assertEqual(state["state"], {"S": "enrolled"})
        self.assertEqual(state["consentEpochId"], {"S": EPOCH_ID})
        receipt = transaction[1]["Put"]["Item"]
        self.assertTrue(receipt["SK"]["S"].startswith(f"CAMPAIGN_CONSENT#{EPOCH_ID}#"))
        self.assertEqual(receipt["expiresAt"], {"N": str(1_788_739_200 + 400 * 86400)})
        operation = transaction[2]["Put"]["Item"]
        self.assertEqual(operation["SK"], {"S": f"CAMPAIGN_OPERATION#{OPERATION_ID}"})
        entitlement = transaction[3]["Put"]["Item"]
        self.assertEqual(entitlement["monthlyScanLimit"], {"N": "15"})
        self.assertEqual(entitlement["remainingMonthlyScans"], {"N": "15"})

    def test_withdraw_is_one_transaction_with_exact_deletion_command(self):
        users.items[("USER#user-123", "CAMPAIGN_PARTICIPATION")] = enrolled_state()
        entitlements.items[("USER#user-123", "ENTITLEMENT#google_play#trustcheck_radar_pro_monthly")] = {
            "PK": "USER#user-123", "SK": "ENTITLEMENT#google_play#trustcheck_radar_pro_monthly",
            "accountId": "user-123", "entitlementTier": "FREE", "subscriptionStatus": "expired",
            "monthlyScanLimit": 15, "remainingMonthlyScans": 8, "remainingCredits": 0,
            "updatedAt": "2026-09-07T00:00:00Z", "createdAt": "2026-09-01T00:00:00Z",
        }

        result = service.update_participation(
            "user-123", payload("withdraw"), now_epoch=1_788_739_200, now_iso="2026-09-07T00:00:00Z"
        )

        self.assertEqual(result["state"], "withdrawal_pending")
        self.assertEqual(result["effectiveMonthlyScanLimit"], 10)
        transaction = client.transactions[0]
        self.assertEqual(len(transaction), 5)
        self.assertEqual(transaction[3]["Put"]["Item"]["remainingMonthlyScans"], {"N": "3"})
        command = transaction[4]["Put"]
        self.assertEqual(command["TableName"], "test-deletion-ledger")
        self.assertEqual(
            set(command["Item"]),
            {"PK", "SK", "schemaVersion", "recordVersion", "environment", "eventType", "accountId", "status", "occurredAtEpoch", "consentEpochId", "deleteByEpoch", "operationId"},
        )
        self.assertEqual(command["Item"]["status"], {"S": "PENDING"})
        self.assertEqual(command["Item"]["deleteByEpoch"], {"N": str(1_788_739_200 + 86400)})

    def test_quota_adjustment_preserves_used_count_and_pro_is_unchanged(self):
        free = {"entitlementTier": "FREE", "monthlyScanLimit": 10, "remainingMonthlyScans": 3}
        joined = service._adjust_entitlement(free, 15, "now")
        withdrawn = service._adjust_entitlement(joined, 10, "later")
        rejoined = service._adjust_entitlement(withdrawn, 15, "again")
        self.assertEqual((joined["remainingMonthlyScans"], withdrawn["remainingMonthlyScans"], rejoined["remainingMonthlyScans"]), (8, 3, 8))

        pro = {"entitlementTier": "PRO", "monthlyScanLimit": 100, "remainingMonthlyScans": 77}
        self.assertEqual(service._adjust_entitlement(pro, 15, "now"), pro)

    def test_operation_replay_does_not_write_again(self):
        users.items[("USER#user-123", f"CAMPAIGN_OPERATION#{OPERATION_ID}")] = {
            "PK": "USER#user-123", "SK": f"CAMPAIGN_OPERATION#{OPERATION_ID}",
            "schemaVersion": 1, "recordVersion": 1, "operationId": OPERATION_ID,
            "consentEpochId": EPOCH_ID, "action": "join",
        }
        users.items[("USER#user-123", "CAMPAIGN_PARTICIPATION")] = enrolled_state()

        result = service.update_participation("user-123", payload())

        self.assertEqual(result["state"], "enrolled")
        self.assertEqual(client.transactions, [])

    def test_reused_operation_id_for_another_action_is_rejected(self):
        users.items[("USER#user-123", f"CAMPAIGN_OPERATION#{OPERATION_ID}")] = {
            "PK": "USER#user-123", "SK": f"CAMPAIGN_OPERATION#{OPERATION_ID}",
            "schemaVersion": 1, "recordVersion": 1, "operationId": OPERATION_ID,
            "consentEpochId": EPOCH_ID, "action": "join",
        }
        with self.assertRaises(service.AppError) as error:
            service.update_participation("user-123", payload("withdraw"))
        self.assertEqual(error.exception.code, "CONFLICT")

    def test_reenrollment_never_reuses_withdrawn_epoch(self):
        users.items[("USER#user-123", "CAMPAIGN_PARTICIPATION")] = enrolled_state(
            state="withdrawn", stateVersion=3, effectiveUntil="2026-09-06T00:00:00Z"
        )
        new_epoch = "5187698a-d91e-429a-bb6a-9f7f5b97509f"
        with mock.patch.object(service.uuid, "uuid4", return_value=service.uuid.UUID(new_epoch)):
            service.update_participation("user-123", payload(operation_id="a57a0bee-c135-4ace-b61f-88b66d394f5d"))
        state = client.transactions[0][0]["Put"]["Item"]
        self.assertEqual(state["consentEpochId"], {"S": new_epoch})
        self.assertNotEqual(new_epoch, EPOCH_ID)


if __name__ == "__main__":
    unittest.main()
