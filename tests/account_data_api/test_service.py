import importlib.util
import sys
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[2] / "src" / "account_data_api"
sys.path.insert(0, str(MODULE))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("errors", MODULE / "errors.py")
service = _load("account_data_service_test", MODULE / "service.py")


class Table:
    def __init__(self, items=None):
        self.items = items or {}
        self.puts = []

    def get_item(self, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item, **_kwargs):
        key = (Item["PK"], Item["SK"])
        if (
            "attribute_not_exists" in (_kwargs.get("ConditionExpression") or "")
            and key in self.items
        ):
            error = RuntimeError("conditional failure")
            error.response = {
                "Error": {"Code": "ConditionalCheckFailedException"},
            }
            raise error
        self.puts.append(Item)
        self.items[key] = dict(Item)

    def delete_item(self, Key):
        self.items.pop((Key["PK"], Key["SK"]), None)


class ScanTable(Table):
    def __init__(self, pages, items=None):
        super().__init__(items)
        self.pages = list(pages)
        self.scans = []

    def scan(self, **kwargs):
        self.scans.append(kwargs)
        return self.pages.pop(0)


class DeviceTable:
    def __init__(self, pages):
        self.pages = list(pages)
        self.queries = []
        self.deletes = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return self.pages.pop(0)

    def delete_item(self, Key):
        self.deletes.append(Key)


class RecoveryTable(Table):
    def __init__(self, pages, items=None):
        super().__init__(items)
        self.pages = list(pages)
        self.queries = []
        self.deletes = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return self.pages.pop(0)

    def delete_item(self, Key):
        self.deletes.append(Key)
        super().delete_item(Key)


class OutboxTable(Table):
    def __init__(self, pages, items=None):
        super().__init__(items)
        self.pages = list(pages)
        self.queries = []
        self.deletes = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return self.pages.pop(0)

    def delete_item(self, Key, **kwargs):
        self.deletes.append((dict(Key), kwargs))
        super().delete_item(Key)


def _deserialize(item):
    result = {}
    for key, value in item.items():
        kind, raw = next(iter(value.items()))
        result[key] = raw if kind == "S" else int(raw) if kind == "N" else raw
    return result


class Client:
    def __init__(self, ledger):
        self.ledger = ledger
        self.transactions = []
        self.cancel = False

    def transact_write_items(self, TransactItems):
        self.transactions.append(TransactItems)
        if self.cancel:
            error = RuntimeError("cancelled")
            error.response = {"Error": {"Code": "TransactionCanceledException"}}
            raise error
        command = _deserialize(TransactItems[1]["Put"]["Item"])
        self.ledger.items[(command["PK"], command["SK"])] = command


def command():
    return {
        "PK": "ACCOUNT#account-1", "SK": "ACCOUNT_DELETION",
        "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
        "eventType": "account.deletion.requested", "accountId": "account-1",
        "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
        "status": "REQUESTED", "occurredAtEpoch": 100,
        "deleteByEpoch": 86_500,
    }


class AccountDeletionServiceTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Table()
        self.client = Client(self.ledger)
        self.subject = service.AccountDeletionService(
            environment="dev", ledger_table=self.ledger,
            users_table_name="users", ledger_table_name="ledger",
            dynamodb_client=self.client,
            required_components=("SESSION_REVOCATION", "HISTORY", "CAMPAIGN"),
            now=lambda: 100,
        )

    def test_request_atomically_fences_profile_and_writes_exact_command(self):
        result = self.subject.request(
            "account-1", "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4"
        )

        self.assertEqual(result["status"], "REQUESTED")
        self.assertFalse(result["completionEligible"])
        transaction = self.client.transactions[0]
        self.assertEqual(len(transaction), 2)
        profile = transaction[0]["Update"]
        self.assertEqual(profile["TableName"], "users")
        self.assertIn("#status = :active", profile["ConditionExpression"])
        self.assertIn("ageVerified = :true", profile["ConditionExpression"])
        stored = self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION")]
        self.assertEqual(set(stored), service.COMMAND_FIELDS)
        self.assertEqual(stored["deleteByEpoch"], 86_500)

    def test_same_operation_replays_and_different_operation_conflicts(self):
        self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION")] = command()

        replay = self.subject.request(
            "account-1", "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4"
        )
        self.assertEqual(replay["operationId"], command()["operationId"])
        self.assertEqual(self.client.transactions, [])

        with self.assertRaises(service.AppError) as raised:
            self.subject.request(
                "account-1", "47debb73-444b-4bb1-9889-fb56885b7922"
            )
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_status_accepts_only_receipts_bound_to_same_request(self):
        self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION")] = command()
        self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION#HISTORY")] = {
            "PK": "ACCOUNT#account-1", "SK": "ACCOUNT_DELETION#HISTORY",
            "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
            "eventType": "account.deletion.component.completed",
            "component": "HISTORY", "status": "COMPLETE",
            "operationId": command()["operationId"],
            "occurredAtEpoch": 110, "requestOccurredAtEpoch": 100,
            "retainUntilEpoch": 110 + 120 * 86400,
        }
        self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION#CAMPAIGN")] = {
            "PK": "ACCOUNT#account-1", "SK": "ACCOUNT_DELETION#CAMPAIGN",
            "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
            "eventType": "account.deletion.component.completed",
            "component": "CAMPAIGN", "status": "COMPLETE",
            "operationId": "47debb73-444b-4bb1-9889-fb56885b7922",
            "occurredAtEpoch": 111, "requestOccurredAtEpoch": 100,
            "retainUntilEpoch": 111 + 120 * 86400,
        }

        result = self.subject.status("account-1")

        self.assertEqual(
            result["components"],
            [
                {"component": "SESSION_REVOCATION", "status": "PENDING"},
                {"component": "HISTORY", "status": "COMPLETE"},
                {"component": "CAMPAIGN", "status": "PENDING"},
            ],
        )
        self.assertFalse(result["completionEligible"])

    def test_session_revocation_writes_idempotent_component_receipt(self):
        class Cognito:
            def __init__(self):
                self.calls = []
            def admin_user_global_sign_out(self, **kwargs):
                self.calls.append(kwargs)

        cognito = Cognito()

        self.assertTrue(service.ensure_session_revoked(
            command(), user_pool_id="pool", cognito=cognito, ledger_table=self.ledger
        ))

        self.assertEqual(cognito.calls, [{"UserPoolId": "pool", "Username": "account-1"}])
        receipt = self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION#SESSION_REVOCATION")]
        self.assertEqual(receipt["operationId"], command()["operationId"])
        self.assertTrue(service.ensure_session_revoked(
            command(), user_pool_id="pool", cognito=cognito, ledger_table=self.ledger
        ))
        self.assertEqual(len(cognito.calls), 1)

    def test_stream_parser_rejects_noncanonical_or_extra_commands(self):
        def av(value):
            return {"N": str(value)} if isinstance(value, int) else {"S": value}
        record = {
            "eventName": "INSERT",
            "dynamodb": {"NewImage": {key: av(value) for key, value in command().items()}},
        }
        self.assertEqual(
            service.command_from_stream(record, environment="dev"), command()
        )
        invalid = command() | {"unexpected": "value"}
        record["dynamodb"]["NewImage"] = {key: av(value) for key, value in invalid.items()}
        with self.assertRaises(ValueError):
            service.command_from_stream(record, environment="dev")

    def test_bounded_reconciliation_recovers_session_revocation_after_stream_window(self):
        ledger = ScanTable([{"Items": [command()], "ScannedCount": 1}])
        devices = DeviceTable([{"Items": []}])
        recovery = RecoveryTable([{"Items": []}])
        abuse = RecoveryTable([{"Items": []}])
        outbox = OutboxTable([{"Items": []}])

        class Cognito:
            def __init__(self):
                self.calls = []
            def admin_user_global_sign_out(self, **kwargs):
                self.calls.append(kwargs)

        cognito = Cognito()
        result = service.reconcile_session_revocations(
            environment="dev", ledger_table=ledger, device_table=devices,
            recovery_table=recovery, abuse_table=abuse, outbox_table=outbox,
            users_table=Table(),
            user_pool_id="pool",
            cognito=cognito, scan_limit=100, max_pages=1, now=lambda: 200,
        )

        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["revoked"], 1)
        self.assertEqual(result["deviceComponentsCompleted"], 1)
        self.assertTrue(result["completedFullPass"])
        self.assertEqual(result["fullPassAgeSeconds"], 0)
        self.assertEqual(cognito.calls[0]["Username"], "account-1")
        checkpoint = ledger.items[(
            "LIFECYCLE#dev",
            "ACCOUNT_DELETION_SESSION_REVOCATION_RECONCILIATION",
        )]
        self.assertEqual(checkpoint["completedPassAtEpoch"], 200)

    def test_campaign_outbox_cleanup_is_bounded_target_bound_and_receipted(self):
        account_hash = service.hashlib.sha256(b"account-1").hexdigest()
        partition = f"ACCOUNT#{account_hash}"
        event_id = "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc"
        event_expiry = 500
        locator = {
            "PK": partition, "SK": f"OUTBOX#{event_id}",
            "recordType": "CAMPAIGN_OUTBOX_LOCATOR", "schemaVersion": 1,
            "recordVersion": 1, "environment": "dev",
            "accountIdHash": account_hash, "statisticsEventId": event_id,
            "eventPK": f"EVENT#{event_id}", "eventSK": "OBSERVATION_READY",
            "eventExpiresAt": event_expiry,
            "expiresAt": event_expiry + 24 * 3600,
        }
        event = {
            "PK": f"EVENT#{event_id}", "SK": "OBSERVATION_READY",
            "statisticsEventId": event_id, "accountId": "account-1",
            "environment": "dev", "sanitizedText": "private",
        }
        continuation = {"PK": partition, "SK": locator["SK"]}
        outbox = OutboxTable(
            [{"Items": [locator], "LastEvaluatedKey": continuation},
             {"Items": []}],
            {
                (locator["PK"], locator["SK"]): locator,
                (event["PK"], event["SK"]): event,
            },
        )
        ledger = Table()

        first = service.delete_campaign_outbox(
            command(), outbox_table=outbox, ledger_table=ledger,
            locator_coverage_status="approved", now_epoch=200,
        )
        second = service.delete_campaign_outbox(
            command(), outbox_table=outbox, ledger_table=ledger,
            locator_coverage_status="approved", now_epoch=201,
        )

        self.assertEqual(first, {
            "deleted": 2, "complete": False, "alreadyComplete": False,
        })
        self.assertEqual(second, {
            "deleted": 0, "complete": True, "alreadyComplete": False,
        })
        self.assertEqual(outbox.queries[1]["ExclusiveStartKey"], continuation)
        self.assertNotIn((event["PK"], event["SK"]), outbox.items)
        self.assertNotIn((locator["PK"], locator["SK"]), outbox.items)
        event_delete = outbox.deletes[0]
        self.assertIn("accountId = :account_id", event_delete[1]["ConditionExpression"])
        self.assertEqual(
            event_delete[1]["ExpressionAttributeValues"][":account_id"],
            "account-1",
        )
        receipt = ledger.items[(
            "ACCOUNT#account-1", "ACCOUNT_DELETION#CAMPAIGN_OUTBOX",
        )]
        self.assertEqual(receipt["operationId"], command()["operationId"])
        self.assertEqual(receipt["retainUntilEpoch"], 201 + 120 * 86400)

    def test_campaign_outbox_cleanup_rejects_cross_account_target(self):
        account_hash = service.hashlib.sha256(b"account-1").hexdigest()
        event_id = "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc"
        locator = {
            "PK": f"ACCOUNT#{account_hash}", "SK": f"OUTBOX#{event_id}",
            "recordType": "CAMPAIGN_OUTBOX_LOCATOR", "schemaVersion": 1,
            "recordVersion": 1, "environment": "dev",
            "accountIdHash": account_hash, "statisticsEventId": event_id,
            "eventPK": f"EVENT#{event_id}", "eventSK": "OBSERVATION_READY",
            "eventExpiresAt": 500, "expiresAt": 500 + 24 * 3600,
        }
        event = {
            "PK": f"EVENT#{event_id}", "SK": "OBSERVATION_READY",
            "statisticsEventId": event_id, "accountId": "another-account",
            "environment": "dev",
        }
        outbox = OutboxTable(
            [{"Items": [locator]}],
            {(event["PK"], event["SK"]): event},
        )

        with self.assertRaisesRegex(ValueError, "target is invalid"):
            service.delete_campaign_outbox(
                command(), outbox_table=outbox, ledger_table=Table(),
                locator_coverage_status="approved", now_epoch=200,
            )

        self.assertEqual(outbox.deletes, [])

    def test_campaign_outbox_receipt_blocks_without_locator_coverage(self):
        result = service.delete_campaign_outbox(
            command(), outbox_table=OutboxTable([{"Items": []}]),
            ledger_table=Table(), now_epoch=200,
        )

        self.assertEqual(result["policyBlocked"], "CAMPAIGN_OUTBOX_LOCATOR_COVERAGE")
        self.assertFalse(result["complete"])

    def test_user_profile_cleanup_waits_for_prerequisites_then_preserves_consent(self):
        deletion = command()
        partition = "USER#account-1"
        operation_id = deletion["operationId"]
        consent_epoch = "15c81ba4-2fa6-43c3-8895-889f08c931bf"
        consent_operation = "47debb73-444b-4bb1-9889-fb56885b7922"
        profile = {
            "PK": partition, "SK": "PROFILE", "sub": "account-1",
            "status": "DELETION_REQUESTED", "ageVerified": True,
            "email": "private@example.com", "deletionOperationId": operation_id,
            "deletionRequestedAtEpoch": 100,
        }
        participation = {
            "PK": partition, "SK": "CAMPAIGN_PARTICIPATION",
            "state": "enrolled", "consentEpochId": consent_epoch,
        }
        operation = {
            "PK": partition, "SK": f"CAMPAIGN_OPERATION#{consent_operation}",
            "operationId": consent_operation,
        }
        consent = {
            "PK": partition,
            "SK": f"CAMPAIGN_CONSENT#{consent_epoch}#90#{consent_operation}",
            "schemaVersion": 1, "recordVersion": 1,
            "eventType": "campaign.participation.joined",
            "occurredAt": "2026-09-15T00:00:00Z",
            "noticeVersion": "notice-1", "policyVersion": "policy-1",
            "consentEpochId": consent_epoch, "operationId": consent_operation,
            "resultingState": "enrolled", "stateVersion": 1,
            "effectiveMonthlyScanLimit": 15,
            "expiresAt": 90 + 400 * 86400,
        }
        users = OutboxTable(
            [{"Items": [profile, participation, operation, consent]}],
            {(item["PK"], item["SK"]): item
             for item in (profile, participation, operation, consent)},
        )
        ledger = Table()

        blocked = service.delete_user_profile_state(
            deletion, users_table=users, ledger_table=ledger,
            policy_status="approved", now_epoch=200,
        )
        self.assertEqual(blocked["policyBlocked"], "USER_PROFILE_PREREQUISITES")
        self.assertEqual(users.queries, [])

        for component in service.USER_PROFILE_PREREQUISITES:
            receipt = service._component_receipt(deletion, component, 150)
            ledger.items[(receipt["PK"], receipt["SK"])] = receipt
        result = service.delete_user_profile_state(
            deletion, users_table=users, ledger_table=ledger,
            policy_status="approved", now_epoch=200,
        )

        self.assertTrue(result["complete"])
        self.assertEqual(result["deleted"], 3)
        self.assertNotIn((partition, "PROFILE"), users.items)
        self.assertNotIn((partition, "CAMPAIGN_PARTICIPATION"), users.items)
        self.assertNotIn((partition, operation["SK"]), users.items)
        self.assertEqual(users.items[(partition, consent["SK"])], consent)
        self.assertIn(
            ("ACCOUNT#account-1", "ACCOUNT_DELETION#USER_PROFILE"),
            ledger.items,
        )

    def test_user_profile_cleanup_is_policy_blocked_before_reads(self):
        users = OutboxTable([{"Items": []}])
        result = service.delete_user_profile_state(
            command(), users_table=users, ledger_table=Table(), now_epoch=200,
        )
        self.assertEqual(result["policyBlocked"], "USER_PROFILE_DELETION")
        self.assertEqual(users.queries, [])

    def test_device_cleanup_is_bounded_resumable_and_receipted(self):
        ledger = Table()
        continuation = {"PK": "USER#account-1", "SK": "DEVICE#one"}
        devices = DeviceTable([
            {
                "Items": [{"PK": "USER#account-1", "SK": "DEVICE#one"}],
                "LastEvaluatedKey": continuation,
            },
            {"Items": [{"PK": "USER#account-1", "SK": "ACTIVE_BINDING"}]},
        ])

        first = service.delete_device_bindings(
            command(), device_table=devices, ledger_table=ledger,
            page_size=100, now_epoch=200,
        )
        second = service.delete_device_bindings(
            command(), device_table=devices, ledger_table=ledger,
            page_size=100, now_epoch=201,
        )

        self.assertEqual(first, {"deleted": 1, "complete": False, "alreadyComplete": False})
        self.assertEqual(second, {"deleted": 1, "complete": True, "alreadyComplete": False})
        self.assertEqual(devices.queries[1]["ExclusiveStartKey"], continuation)
        self.assertEqual(
            devices.deletes,
            [
                {"PK": "USER#account-1", "SK": "DEVICE#one"},
                {"PK": "USER#account-1", "SK": "ACTIVE_BINDING"},
            ],
        )
        receipt = ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION#DEVICE_BINDINGS")]
        self.assertEqual(receipt["operationId"], command()["operationId"])
        self.assertEqual(receipt["retainUntilEpoch"], 201 + 120 * 86400)

    def test_recovery_cleanup_is_bounded_minimizes_security_evidence_and_receipts(self):
        operation_id = command()["operationId"]
        receipt_key = ("USER#account-1", f"RECOVERY#{operation_id}")
        audit_key = ("USER#account-1", f"AUDIT#0000000200#{operation_id}")
        rate_key = ("USER#account-1", "RATE#0")
        receipt = {
            "PK": receipt_key[0], "SK": receipt_key[1],
            "recordType": "DEVICE_RECOVERY_RECEIPT", "schemaVersion": 1,
            "operationId": operation_id, "operation": "REPLACE_ACTIVE_BINDING",
            "payloadHash": "a" * 64, "result": "RECOVERED", "status": "COMPLETE",
            "bindingFingerprint": "sensitive-fingerprint", "completedAtEpoch": 100,
            "expiresAt": 100 + 7 * 86400,
        }
        audit = {
            "PK": audit_key[0], "SK": audit_key[1],
            "recordType": "DEVICE_RECOVERY_AUDIT", "schemaVersion": 1,
            "operationId": operation_id, "actorType": "SELF",
            "action": "REPLACE_ACTIVE_BINDING", "result": "RECOVERED",
            "bindingFingerprint": "sensitive-fingerprint", "occurredAtEpoch": 200,
            "expiresAt": 200 + 90 * 86400,
        }
        rate = {
            "PK": rate_key[0], "SK": rate_key[1],
            "requestCount": 1, "expiresAt": 24 * 3600,
        }
        continuation = {"PK": receipt["PK"], "SK": receipt["SK"]}
        recovery = RecoveryTable(
            [
                {"Items": [receipt, rate], "LastEvaluatedKey": continuation},
                {"Items": [audit]},
            ],
            {receipt_key: receipt, audit_key: audit, rate_key: rate},
        )
        ledger = Table()

        first = service.delete_device_recovery_control(
            command(), recovery_table=recovery, ledger_table=ledger,
            page_size=100, now_epoch=300,
        )
        second = service.delete_device_recovery_control(
            command(), recovery_table=recovery, ledger_table=ledger,
            page_size=100, now_epoch=301,
        )

        self.assertEqual(
            first,
            {"deleted": 1, "minimized": 1, "complete": False,
             "alreadyComplete": False},
        )
        self.assertEqual(
            second,
            {"deleted": 0, "minimized": 1, "complete": True,
             "alreadyComplete": False},
        )
        self.assertEqual(recovery.queries[1]["ExclusiveStartKey"], continuation)
        self.assertNotIn(rate_key, recovery.items)
        self.assertNotIn("payloadHash", recovery.items[receipt_key])
        self.assertNotIn("bindingFingerprint", recovery.items[receipt_key])
        self.assertNotIn("bindingFingerprint", recovery.items[audit_key])
        component = ledger.items[(
            "ACCOUNT#account-1", "ACCOUNT_DELETION#DEVICE_RECOVERY"
        )]
        self.assertEqual(component["operationId"], operation_id)
        self.assertEqual(component["requestOccurredAtEpoch"], 100)
        self.assertEqual(component["retainUntilEpoch"], 301 + 120 * 86400)

        replay = service.delete_device_recovery_control(
            command(), recovery_table=recovery, ledger_table=ledger,
            page_size=100, now_epoch=302,
        )
        self.assertTrue(replay["alreadyComplete"])
        self.assertEqual(len(recovery.queries), 2)

    def test_recovery_cleanup_rejects_unknown_family_without_receipt(self):
        recovery = RecoveryTable([{"Items": [{
            "PK": "USER#account-1", "SK": "UNKNOWN#late-write",
        }]}])
        ledger = Table()

        with self.assertRaisesRegex(ValueError, "Unknown device-recovery"):
            service.delete_device_recovery_control(
                command(), recovery_table=recovery, ledger_table=ledger,
                page_size=100, now_epoch=300,
            )

        self.assertNotIn(
            ("ACCOUNT#account-1", "ACCOUNT_DELETION#DEVICE_RECOVERY"),
            ledger.items,
        )

    def test_analysis_abuse_cleanup_is_bounded_content_free_and_receipted(self):
        account_hash = service.hashlib.sha256(b"account-1").hexdigest()
        request_partition = f"ANALYSIS#REQUEST#{account_hash}"
        rate_partition = f"ANALYSIS#RATE#{account_hash}"
        scan_partition = f"ANALYSIS#SCAN_RATE#{account_hash}"
        consumption_partition = f"ANALYSIS#CONSUMPTION#{account_hash}"
        first_request = {
            "PK": request_partition, "SK": "request-1", "status": "RESULT_READY",
            "payloadHash": "a" * 64, "response": {"summary": "private"},
            "campaignAuthorization": {"consentEpochId": "private"},
            "historyAuthorization": {"acceptedSequence": 1},
            "statisticsEventId": "event-1", "leaseToken": "lease-1",
            "expiresAt": 500, "ttl": 500,
        }
        second_request = {
            "PK": request_partition, "SK": "request-2", "status": "COMPLETED",
            "payloadHash": "b" * 64, "response": {"summary": "expired"},
            "expiresAt": 199, "ttl": 199,
        }
        rate = {
            "PK": rate_partition, "SK": "0", "requestCount": 2,
            "expiresAt": 300, "ttl": 300,
        }
        scan_rate = {
            "PK": scan_partition, "SK": "0", "requestCount": 1,
            "expiresAt": 300, "ttl": 300,
        }
        consumption = {
            "PK": consumption_partition, "SK": "request-1",
            "accountIdHash": account_hash, "consumptionType": "monthly",
            "createdAt": "2026-09-14T00:00:00Z", "expiresAt": 500, "ttl": 500,
        }
        continuation = {"PK": request_partition, "SK": "request-1"}
        abuse = RecoveryTable([
            {"Items": [first_request], "LastEvaluatedKey": continuation},
            {"Items": [second_request]},
            {"Items": [rate]},
            {"Items": [scan_rate]},
            {"Items": [consumption]},
        ], {
            (first_request["PK"], first_request["SK"]): first_request,
            (second_request["PK"], second_request["SK"]): second_request,
            (rate["PK"], rate["SK"]): rate,
            (scan_rate["PK"], scan_rate["SK"]): scan_rate,
            (consumption["PK"], consumption["SK"]): consumption,
        })
        ledger = Table()

        results = [service.delete_analysis_abuse_control(
            command(), abuse_table=abuse, ledger_table=ledger,
            page_size=100, now_epoch=200 + index,
            request_dedupe_policy_status="approved",
            consumption_deletion_policy_status="approved",
        ) for index in range(5)]

        self.assertEqual(
            results[0],
            {"deleted": 0, "minimized": 1, "complete": False,
             "alreadyComplete": False, "family": "REQUEST"},
        )
        self.assertEqual(abuse.queries[1]["ExclusiveStartKey"], continuation)
        self.assertEqual(
            abuse.items[(request_partition, "request-1")],
            {
                "PK": request_partition, "SK": "request-1",
                "status": "COMPLETED_ERASED", "payloadHash": "a" * 64,
                "expiresAt": 500, "ttl": 500,
            },
        )
        self.assertNotIn((request_partition, "request-2"), abuse.items)
        self.assertNotIn((rate_partition, "0"), abuse.items)
        self.assertNotIn((scan_partition, "0"), abuse.items)
        self.assertNotIn((consumption_partition, "request-1"), abuse.items)
        self.assertTrue(results[-1]["complete"])
        receipt = ledger.items[(
            "ACCOUNT#account-1", "ACCOUNT_DELETION#ANALYSIS_ABUSE",
        )]
        self.assertEqual(receipt["operationId"], command()["operationId"])
        self.assertEqual(receipt["retainUntilEpoch"], 204 + 120 * 86400)
        self.assertNotIn(
            ("ACCOUNT#account-1", "ACCOUNT_DELETION#ANALYSIS_ABUSE_PROGRESS"),
            ledger.items,
        )

    def test_analysis_cleanup_rejects_nonstandard_request_retention(self):
        with self.assertRaisesRegex(
            ValueError, "Invalid analysis-abuse deletion policy"
        ):
            service.delete_analysis_abuse_control(
                command(), abuse_table=RecoveryTable([]), ledger_table=Table(),
                request_retention_seconds=86400,
            )

    def test_analysis_abuse_cleanup_erases_content_but_blocks_legacy_retention(self):
        account_hash = service.hashlib.sha256(b"account-1").hexdigest()
        partition = f"ANALYSIS#REQUEST#{account_hash}"
        abuse = RecoveryTable([{"Items": [{
            "PK": partition, "SK": "request-1",
            "status": "COMPLETED", "payloadHash": "a" * 64,
            "response": {"summary": "private"},
            "expiresAt": 200 + 901, "ttl": 200 + 901,
        }]}, {"Items": []}, {"Items": []}, {"Items": []}, {"Items": []}])
        ledger = Table()

        results = []
        for index in range(4):
            results.append(service.delete_analysis_abuse_control(
                command(), abuse_table=abuse, ledger_table=ledger,
                page_size=100, now_epoch=200,
                request_dedupe_policy_status="approved",
                consumption_deletion_policy_status="approved",
            ))

        self.assertEqual(
            results[-1]["policyBlocked"], "ANALYSIS_LEGACY_REQUEST_RETENTION"
        )
        self.assertEqual(abuse.items[(partition, "request-1")], {
            "PK": partition, "SK": "request-1", "status": "COMPLETED_ERASED",
            "payloadHash": "a" * 64, "expiresAt": 1000, "ttl": 1000,
        })
        self.assertNotIn(
            ("ACCOUNT#account-1", "ACCOUNT_DELETION#ANALYSIS_ABUSE"),
            ledger.items,
        )

        approved = service.delete_analysis_abuse_control(
            command(), abuse_table=abuse, ledger_table=ledger,
            page_size=100, now_epoch=200,
            request_dedupe_policy_status="approved",
            legacy_request_retention_policy_status="approved",
            consumption_deletion_policy_status="approved",
        )

        self.assertTrue(approved["complete"])
        self.assertEqual(abuse.items[(partition, "request-1")], {
            "PK": partition, "SK": "request-1", "status": "COMPLETED_ERASED",
            "payloadHash": "a" * 64, "expiresAt": 1000, "ttl": 1000,
        })

    def test_approved_legacy_request_is_shortened_without_extending_retention(self):
        account_hash = service.hashlib.sha256(b"account-1").hexdigest()
        partition = f"ANALYSIS#REQUEST#{account_hash}"
        long_lived = {
            "PK": partition, "SK": "request-1", "status": "RESULT_READY",
            "payloadHash": "a" * 64, "response": {"summary": "private"},
            "expiresAt": 2000, "ttl": 2000,
        }
        shorter = {
            "PK": partition, "SK": "request-2", "status": "COMPLETED",
            "payloadHash": "b" * 64, "response": {"summary": "private"},
            "expiresAt": 500, "ttl": 500,
        }
        abuse = RecoveryTable(
            [{"Items": [long_lived, shorter]}, {"Items": []},
             {"Items": []}, {"Items": []}, {"Items": []}],
            {
                (partition, "request-1"): long_lived,
                (partition, "request-2"): shorter,
            },
        )
        ledger = Table()

        results = [service.delete_analysis_abuse_control(
            command(), abuse_table=abuse, ledger_table=ledger, now_epoch=200,
            request_dedupe_policy_status="approved",
            legacy_request_retention_policy_status="approved",
            consumption_deletion_policy_status="approved",
        ) for _ in range(5)]

        self.assertTrue(results[-1]["complete"])
        self.assertEqual(abuse.items[(partition, "request-1")], {
            "PK": partition, "SK": "request-1", "status": "COMPLETED_ERASED",
            "payloadHash": "a" * 64, "expiresAt": 1000, "ttl": 1000,
        })
        self.assertEqual(abuse.items[(partition, "request-2")], {
            "PK": partition, "SK": "request-2", "status": "COMPLETED_ERASED",
            "payloadHash": "b" * 64, "expiresAt": 500, "ttl": 500,
        })

    def test_approved_legacy_request_past_normalized_boundary_is_deleted(self):
        account_hash = service.hashlib.sha256(b"account-1").hexdigest()
        partition = f"ANALYSIS#REQUEST#{account_hash}"
        request = {
            "PK": partition, "SK": "request-1", "status": "RESULT_READY",
            "payloadHash": "a" * 64, "response": {"summary": "private"},
            "expiresAt": 2000, "ttl": 2000,
        }
        abuse = RecoveryTable(
            [{"Items": [request]}], {(partition, "request-1"): request},
        )

        result = service.delete_analysis_abuse_control(
            command(), abuse_table=abuse, ledger_table=Table(), now_epoch=1001,
            legacy_request_retention_policy_status="approved",
        )

        self.assertEqual(result["deleted"], 1)
        self.assertNotIn((partition, "request-1"), abuse.items)

    def test_legacy_component_receipt_is_not_accepted_or_overwritten(self):
        receipt_key = (
            "ACCOUNT#account-1", "ACCOUNT_DELETION#ANALYSIS_ABUSE",
        )
        legacy = {
            "PK": receipt_key[0], "SK": receipt_key[1],
            "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
            "eventType": "account.deletion.component.completed",
            "component": "ANALYSIS_ABUSE", "status": "COMPLETE",
            "operationId": command()["operationId"],
            "occurredAtEpoch": 200, "requestOccurredAtEpoch": 100,
        }
        ledger = Table({receipt_key: legacy})
        abuse = RecoveryTable([{"Items": []} for _ in range(4)])

        for index in range(3):
            result = service.delete_analysis_abuse_control(
                command(), abuse_table=abuse, ledger_table=ledger,
                page_size=100, now_epoch=200 + index,
                request_dedupe_policy_status="approved",
                consumption_deletion_policy_status="approved",
            )
            self.assertFalse(result["complete"])
        with self.assertRaises(RuntimeError):
            service.delete_analysis_abuse_control(
                command(), abuse_table=abuse, ledger_table=ledger,
                page_size=100, now_epoch=203,
                request_dedupe_policy_status="approved",
                consumption_deletion_policy_status="approved",
            )

        self.assertEqual(ledger.items[receipt_key], legacy)

    def test_analysis_consumption_policy_blocks_deletion_and_receipt(self):
        account_hash = service.hashlib.sha256(b"account-1").hexdigest()
        consumption = {
            "PK": f"ANALYSIS#CONSUMPTION#{account_hash}", "SK": "request-1",
            "accountIdHash": account_hash, "consumptionType": "monthly",
            "expiresAt": 500, "ttl": 500,
        }
        abuse = RecoveryTable(
            [{"Items": []}, {"Items": []}, {"Items": []}],
            {(consumption["PK"], consumption["SK"]): consumption},
        )
        ledger = Table()

        for index in range(3):
            service.delete_analysis_abuse_control(
                command(), abuse_table=abuse, ledger_table=ledger,
                now_epoch=200 + index,
            )
        blocked = service.delete_analysis_abuse_control(
            command(), abuse_table=abuse, ledger_table=ledger, now_epoch=203,
        )

        self.assertEqual(
            blocked["policyBlocked"], "ANALYSIS_CONSUMPTION_DELETION"
        )
        self.assertIn((consumption["PK"], consumption["SK"]), abuse.items)
        self.assertEqual(len(abuse.queries), 3)
        self.assertNotIn(
            ("ACCOUNT#account-1", "ACCOUNT_DELETION#ANALYSIS_ABUSE"),
            ledger.items,
        )

    def test_analysis_cleanup_accepts_history_120_day_content_free_tombstone(self):
        account_hash = service.hashlib.sha256(b"account-1").hexdigest()
        partition = f"ANALYSIS#REQUEST#{account_hash}"
        expires_at = 100 + 120 * 86400
        tombstone = {
            "PK": partition, "SK": "request-1", "status": "COMPLETED_ERASED",
            "payloadHash": "a" * 64, "updatedAt": "private-metadata",
            "historyAuthorization": {"historyGeneration": 1},
            "statisticsEventId": "private-event", "expiresAt": expires_at,
            "ttl": expires_at,
        }
        abuse = RecoveryTable(
            [{"Items": [tombstone]}],
            {(partition, "request-1"): tombstone},
        )

        result = service.delete_analysis_abuse_control(
            command(), abuse_table=abuse, ledger_table=Table(), now_epoch=200,
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["minimized"], 1)
        self.assertEqual(abuse.items[(partition, "request-1")], {
            "PK": partition, "SK": "request-1", "status": "COMPLETED_ERASED",
            "payloadHash": "a" * 64, "expiresAt": expires_at, "ttl": expires_at,
        })


if __name__ == "__main__":
    unittest.main()
