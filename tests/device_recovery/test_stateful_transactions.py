import copy
import importlib.util
import sys
import types
import unittest
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "device_recovery"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


def _deserialize(value):
    result = {}
    for key, encoded in value.items():
        kind, raw = next(iter(encoded.items()))
        if kind == "S":
            result[key] = raw
        elif kind == "N":
            result[key] = int(raw)
        elif kind == "BOOL":
            result[key] = raw
        elif kind == "NULL":
            result[key] = None
        else:
            raise AssertionError(kind)
    return result


class ConditionalFailure(RuntimeError):
    response = {"Error": {"Code": "TransactionCanceledException"}}


class StatefulStore:
    def __init__(self):
        self.items = {}

    def get(self, table, key):
        return self.items.get((table, key["PK"], key["SK"]))

    def put(self, table, item):
        self.items[(table, item["PK"], item["SK"])] = copy.deepcopy(item)


class StatefulTable:
    def __init__(self, store, name):
        self.store = store
        self.name = name

    def get_item(self, Key, **_kwargs):
        item = self.store.get(self.name, Key)
        return {"Item": copy.deepcopy(item)} if item else {}

    def query(self, **kwargs):
        gsi_key = kwargs["ExpressionAttributeValues"][":gsi1pk"]
        limit = kwargs.get("Limit")
        items = [
            copy.deepcopy(item)
            for (table, _pk, _sk), item in self.store.items.items()
            if table == self.name and item.get("GSI1PK") == gsi_key
        ]
        items.sort(key=lambda item: item.get("GSI1SK", ""), reverse=True)
        return {"Items": items[:limit] if limit else items}


class StatefulClient:
    def __init__(self, store):
        self.store = store
        self.before_commit = None

    def transact_write_items(self, TransactItems):
        if self.before_commit:
            callback, self.before_commit = self.before_commit, None
            callback()
        staged = copy.deepcopy(self.store.items)
        try:
            for operation in TransactItems:
                if "ConditionCheck" in operation:
                    self._condition_check(staged, operation["ConditionCheck"])
                elif "Update" in operation:
                    self._update(staged, operation["Update"])
                elif "Put" in operation:
                    self._put(staged, operation["Put"])
                else:
                    raise AssertionError(operation)
        except ConditionalFailure:
            raise
        self.store.items = staged

    @staticmethod
    def _key(table, encoded_key):
        key = _deserialize(encoded_key)
        return table, key["PK"], key["SK"]

    def _condition_check(self, staged, operation):
        key = self._key(operation["TableName"], operation["Key"])
        item = staged.get(key)
        expression = operation["ConditionExpression"]
        values = _deserialize(operation.get("ExpressionAttributeValues", {}))
        if expression == "attribute_not_exists(PK)":
            valid = item is None
        elif "ageVerified" in expression:
            valid = bool(
                item
                and item.get("status") == values[":active"]
                and item.get("ageVerified") is values[":true"]
                and item.get("sub") == values[":account"]
            )
        elif "bindingFingerprint = :current" in expression:
            valid = bool(
                item
                and item.get("bindingFingerprint") == values[":current"]
                and item.get("stateVersion") == values[":version"]
            )
        else:
            raise AssertionError(expression)
        if not valid:
            raise ConditionalFailure("condition failed")

    def _update(self, staged, operation):
        key = self._key(operation["TableName"], operation["Key"])
        item = staged.get(key)
        values = _deserialize(operation["ExpressionAttributeValues"])
        expression = operation["ConditionExpression"]
        if "requestCount" in expression:
            count = int((item or {}).get("requestCount", 0))
            if count >= values[":maximum"]:
                raise ConditionalFailure("rate limited")
            staged[key] = dict(item or _deserialize(operation["Key"])) | {
                "requestCount": count + values[":one"],
                "expiresAt": values[":expires_at"],
            }
            return
        if not item or item.get("bindingFingerprint") != values[":current"] or item.get("stateVersion") != values[":version"]:
            raise ConditionalFailure("pointer changed")
        staged[key] = dict(item) | {
            "bindingFingerprint": values.get(":target", values.get(":none")),
            "stateVersion": values[":next"],
            "updatedAt": values[":now"],
        }

    def _put(self, staged, operation):
        item = _deserialize(operation["Item"])
        key = (operation["TableName"], item["PK"], item["SK"])
        previous = staged.get(key)
        expression = operation.get("ConditionExpression")
        values = _deserialize(operation.get("ExpressionAttributeValues", {}))
        if expression == "attribute_not_exists(PK) AND attribute_not_exists(SK)":
            valid = previous is None
        elif expression == "#status = :active AND bindingFingerprint = :current":
            valid = bool(
                previous
                and previous.get("status") == values[":active"]
                and previous.get("bindingFingerprint") == values[":current"]
            )
        elif expression == "#status = :previous":
            valid = bool(previous and previous.get("status") == values[":previous"])
        elif expression is None:
            valid = True
        else:
            raise AssertionError(expression)
        if not valid:
            raise ConditionalFailure("put condition failed")
        staged[key] = item


store = StatefulStore()
client = StatefulClient(store)


class Resource:
    def Table(self, name):
        return StatefulTable(store, name)


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *_args, **_kwargs: Resource()
boto3_stub.client = lambda *_args, **_kwargs: client
sys.modules["boto3"] = boto3_stub


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, MODULE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_load("errors", "errors.py")
config = _load("config", "config.py")
service = _load("device_recovery_stateful_service", "service.py")


class DeviceRecoveryStatefulTransactionTests(unittest.TestCase):
    def setUp(self):
        store.items.clear()
        client.before_commit = None
        service.table = StatefulTable(store, "device-bindings")
        service.control_table = StatefulTable(store, "recovery-control")
        service.users_table = StatefulTable(store, "users")
        service.deletion_ledger_table = StatefulTable(store, "deletion-ledger")
        service.dynamodb_client = client
        config.DEVICE_BINDINGS_TABLE_NAME = "device-bindings"
        config.DEVICE_RECOVERY_CONTROL_TABLE_NAME = "recovery-control"
        config.USERS_TABLE_NAME = "users"
        config.DELETION_LEDGER_TABLE_NAME = "deletion-ledger"
        config.DEVICE_RECOVERY_AUDIT_RETENTION_DAYS = 90
        store.put("users", {
            "PK": "USER#user-123", "SK": "PROFILE", "sub": "user-123",
            "status": "ACTIVE", "ageVerified": True,
        })
        store.put("device-bindings", {
            "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
            "recordType": "ACTIVE_BINDING_POINTER", "schemaVersion": 1,
            "bindingFingerprint": "fp-old", "stateVersion": 1,
            "updatedAt": "2027-01-01T00:00:00+00:00",
        })
        store.put("device-bindings", {
            "PK": "USER#user-123", "SK": "DEVICE#fp-old",
            "accountId": "user-123", "bindingFingerprint": "fp-old",
            "platform": "ios", "osVersion": "18.4", "status": "ACTIVE",
            "firstSeenAt": "2027-01-01T00:00:00+00:00",
            "lastSeenAt": "2027-01-01T00:00:00+00:00", "deactivatedAt": None,
        })
        self.payload = {
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "REPLACE_ACTIVE_BINDING",
            "bindingFingerprint": "fp-new",
            "platform": "ios",
            "osVersion": "18.5",
        }

    def test_pointer_compare_and_set_prevents_partial_recovery_after_race(self):
        def concurrent_switch():
            pointer = store.get("device-bindings", {
                "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
            })
            pointer["bindingFingerprint"] = "fp-racer"
            pointer["stateVersion"] = 2
            store.put("device-bindings", {
                "PK": "USER#user-123", "SK": "DEVICE#fp-racer",
                "accountId": "user-123", "bindingFingerprint": "fp-racer",
                "platform": "android", "osVersion": "16", "status": "ACTIVE",
                "firstSeenAt": "2027-01-01T00:00:01+00:00",
                "lastSeenAt": "2027-01-01T00:00:01+00:00", "deactivatedAt": None,
            })

        client.before_commit = concurrent_switch
        with self.assertRaises(service.AppError) as context:
            service.process_self_recovery(
                account_id="user-123", payload=self.payload, now_epoch=1_800_000_000
            )

        self.assertEqual(context.exception.code, "CONFLICT")
        pointer = store.get("device-bindings", {
            "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
        })
        self.assertEqual(pointer["bindingFingerprint"], "fp-racer")
        self.assertEqual(pointer["stateVersion"], 2)
        self.assertIsNone(store.get("device-bindings", {
            "PK": "USER#user-123", "SK": "DEVICE#fp-new",
        }))
        self.assertIsNone(store.get("recovery-control", {
            "PK": "USER#user-123", "SK": f"RECOVERY#{self.payload['operationId']}",
        }))
        self.assertIsNone(store.get("recovery-control", {
            "PK": "USER#user-123", "SK": "RATE#1800000000",
        }))

    def test_deletion_fence_race_rolls_back_every_recovery_write(self):
        def concurrent_deletion():
            store.put("deletion-ledger", {
                "PK": "ACCOUNT#user-123", "SK": "ACCOUNT_DELETION",
                "operationId": "47debb73-444b-4bb1-9889-fb56885b7922",
            })

        client.before_commit = concurrent_deletion
        with self.assertRaises(service.AppError) as context:
            service.process_self_recovery(
                account_id="user-123", payload=self.payload, now_epoch=1_800_000_000
            )

        self.assertEqual(context.exception.code, "CONFLICT")
        pointer = store.get("device-bindings", {
            "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
        })
        self.assertEqual(pointer["bindingFingerprint"], "fp-old")
        self.assertEqual(pointer["stateVersion"], 1)
        self.assertIsNone(store.get("device-bindings", {
            "PK": "USER#user-123", "SK": "DEVICE#fp-new",
        }))
        self.assertFalse(any(
            table == "recovery-control"
            for table, _pk, _sk in store.items
        ))


if __name__ == "__main__":
    unittest.main()
