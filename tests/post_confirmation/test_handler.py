import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[2] / "src" / "post_confirmation" / "app.py"


class _FakeTable:
    def __init__(self):
        self.put_calls = []

    def put_item(self, **kwargs):
        self.put_calls.append(kwargs)
        return {}


class _FakeClient:
    def __init__(self):
        self.transact_calls = []
        self.effects = []

    def transact_write_items(self, **kwargs):
        self.transact_calls.append(kwargs)
        if self.effects:
            effect = self.effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
        return {}


class _FakeResource:
    def __init__(self, table):
        self.table = table
        self.requested_table_name = None

    def Table(self, table_name):
        self.requested_table_name = table_name
        return self.table


class _FakeClientError(Exception):
    def __init__(self, response=None):
        self.response = response or {}


class _Serializer:
    def serialize(self, value):
        if value is None:
            return {"NULL": True}
        if isinstance(value, bool):
            return {"BOOL": value}
        if isinstance(value, str):
            return {"S": value}
        raise TypeError(type(value).__name__)


def _load_app(environment):
    fake_table = _FakeTable()
    fake_client = _FakeClient()
    fake_resource = _FakeResource(fake_table)
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.resource = lambda *_args, **_kwargs: fake_resource
    boto3_stub.client = lambda *_args, **_kwargs: fake_client
    boto3_dynamodb_stub = types.ModuleType("boto3.dynamodb")
    boto3_types_stub = types.ModuleType("boto3.dynamodb.types")
    boto3_types_stub.TypeSerializer = _Serializer
    botocore_stub = types.ModuleType("botocore")
    botocore_exceptions_stub = types.ModuleType("botocore.exceptions")
    botocore_exceptions_stub.ClientError = _FakeClientError
    botocore_stub.exceptions = botocore_exceptions_stub

    module_name = "post_confirmation_app_under_test"
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
    return module, fake_resource, fake_table, fake_client


class PostConfirmationHandlerTests(unittest.TestCase):
    @staticmethod
    def _event():
        return {
            "request": {
                "userAttributes": {
                    "sub": "user-123",
                    "email": "user@example.com",
                    "custom:over_18": "true",
                }
            },
            "response": {},
        }

    def test_derives_table_name_from_infrastructure_arn(self):
        app, resource, _table, _client = _load_app(
            {
                "USERS_TABLE_ARN": (
                    "arn:aws:dynamodb:us-east-1:123456789012:table/"
                    "trustcheckradar-dev-users"
                ),
                "DELETION_LEDGER_TABLE_NAME": "ledger",
            }
        )

        self.assertEqual(app.TABLE_NAME, "trustcheckradar-dev-users")
        self.assertEqual(resource.requested_table_name, "trustcheckradar-dev-users")

    def test_creates_profile_without_overwriting_existing_item(self):
        app, _resource, table, client = _load_app(
            {
                "USERS_TABLE_NAME": "users",
                "DELETION_LEDGER_TABLE_NAME": "ledger",
            }
        )
        event = self._event()

        result = app.lambda_handler(event, None)

        self.assertIs(result, event)
        self.assertEqual(table.put_calls, [])
        self.assertEqual(len(client.transact_calls), 1)
        actions = client.transact_calls[0]["TransactItems"]
        fence = actions[0]["ConditionCheck"]
        call = actions[1]["Put"]
        self.assertEqual(fence["TableName"], "ledger")
        self.assertEqual(fence["Key"]["PK"], {"S": "ACCOUNT#user-123"})
        self.assertEqual(fence["Key"]["SK"], {"S": "ACCOUNT_DELETION"})
        self.assertEqual(call["Item"]["PK"], {"S": "USER#user-123"})
        self.assertEqual(call["Item"]["SK"], {"S": "PROFILE"})
        self.assertEqual(
            call["ConditionExpression"],
            "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )

    def test_duplicate_profile_is_a_safe_noop_after_atomic_authority_check(self):
        app, _resource, _table, client = _load_app(
            {
                "USERS_TABLE_NAME": "users",
                "DELETION_LEDGER_TABLE_NAME": "ledger",
            }
        )
        client.effects = [
            _FakeClientError(
                {
                    "Error": {"Code": "TransactionCanceledException"},
                    "CancellationReasons": [
                        {"Code": "None"},
                        {"Code": "ConditionalCheckFailed"},
                    ],
                }
            ),
            None,
        ]
        event = self._event()

        self.assertIs(app.lambda_handler(event, None), event)
        self.assertEqual(len(client.transact_calls), 2)
        duplicate_checks = client.transact_calls[1]["TransactItems"]
        self.assertEqual(len(duplicate_checks), 2)
        self.assertEqual(duplicate_checks[0]["ConditionCheck"]["TableName"], "ledger")
        profile = duplicate_checks[1]["ConditionCheck"]
        self.assertEqual(profile["TableName"], "users")
        self.assertIn("#sub = :account", profile["ConditionExpression"])
        self.assertIn("#status = :pending", profile["ConditionExpression"])
        self.assertIn("#status = :active", profile["ConditionExpression"])

    def test_duplicate_fence_or_mismatch_still_fails_closed(self):
        app, _resource, _table, client = _load_app(
            {
                "USERS_TABLE_NAME": "users",
                "DELETION_LEDGER_TABLE_NAME": "ledger",
            }
        )
        conditional = _FakeClientError(
            {
                "Error": {"Code": "TransactionCanceledException"},
                "CancellationReasons": [
                    {"Code": "None"},
                    {"Code": "ConditionalCheckFailed"},
                ],
            }
        )
        client.effects = [conditional, conditional]

        with self.assertRaisesRegex(RuntimeError, "^POST_CONFIRMATION_FAILED$"):
            app.lambda_handler(self._event(), None)

    def test_requires_a_table_identifier(self):
        with self.assertRaisesRegex(RuntimeError, "USERS_TABLE_NAME"):
            _load_app({})

    def test_requires_deletion_ledger_for_resurrection_fence(self):
        with self.assertRaisesRegex(RuntimeError, "DELETION_LEDGER_TABLE_NAME"):
            _load_app({"USERS_TABLE_NAME": "users"})
