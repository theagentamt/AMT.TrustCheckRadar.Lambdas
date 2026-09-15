import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "age_attestation" / "app.py"
)


class _Serializer:
    def serialize(self, value):
        if value is None:
            return {"NULL": True}
        if isinstance(value, bool):
            return {"BOOL": value}
        return {"S": str(value)}


class _FakeClient:
    def __init__(self):
        self.calls = []

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs)
        return {}


class _ClientError(Exception):
    def __init__(self, response=None):
        self.response = response or {}


def _load_app(environment):
    client = _FakeClient()
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.client = lambda *_args, **_kwargs: client
    boto3_dynamodb_stub = types.ModuleType("boto3.dynamodb")
    boto3_types_stub = types.ModuleType("boto3.dynamodb.types")
    boto3_types_stub.TypeSerializer = _Serializer
    botocore_stub = types.ModuleType("botocore")
    botocore_exceptions_stub = types.ModuleType("botocore.exceptions")
    botocore_exceptions_stub.ClientError = _ClientError
    botocore_stub.exceptions = botocore_exceptions_stub
    module_name = "age_attestation_app_under_test"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with mock.patch.dict(
        sys.modules,
        {
            "boto3": boto3_stub,
            "boto3.dynamodb": boto3_dynamodb_stub,
            "boto3.dynamodb.types": boto3_types_stub,
            "botocore": botocore_stub,
            "botocore.exceptions": botocore_exceptions_stub,
        },
    ), mock.patch.dict(os.environ, environment, clear=True):
        spec.loader.exec_module(module)
    return module, client


class AgeAttestationDeletionFenceTests(unittest.TestCase):
    def test_profile_update_is_atomic_with_absent_deletion_fence(self):
        app, client = _load_app(
            {
                "USERS_TABLE_NAME": "users",
                "DELETION_LEDGER_TABLE_NAME": "ledger",
            }
        )

        app._update_user_attestation(
            sub="user-123",
            over_18_acknowledged=True,
            attested_at="2026-09-14T00:00:00+00:00",
            age_policy_version="v1.0",
        )

        actions = client.calls[0]["TransactItems"]
        self.assertEqual(actions[0]["ConditionCheck"]["TableName"], "ledger")
        self.assertEqual(
            actions[0]["ConditionCheck"]["Key"],
            {
                "PK": {"S": "ACCOUNT#user-123"},
                "SK": {"S": "ACCOUNT_DELETION"},
            },
        )
        update = actions[1]["Update"]
        self.assertEqual(update["TableName"], "users")
        self.assertIn("#status = :pending", update["ConditionExpression"])
        self.assertIn("#status = :active", update["ConditionExpression"])
        self.assertEqual(update["ExpressionAttributeValues"][":account"], {"S": "user-123"})

    def test_missing_deletion_ledger_fails_closed(self):
        app, _client = _load_app({"USERS_TABLE_NAME": "users"})

        with self.assertRaisesRegex(RuntimeError, "DELETION_LEDGER_TABLE_NAME"):
            app._update_user_attestation(
                sub="user-123",
                over_18_acknowledged=True,
                attested_at="2026-09-14T00:00:00+00:00",
                age_policy_version="v1.0",
            )


if __name__ == "__main__":
    unittest.main()
