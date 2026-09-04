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


class _FakeResource:
    def __init__(self, table):
        self.table = table
        self.requested_table_name = None

    def Table(self, table_name):
        self.requested_table_name = table_name
        return self.table


class _FakeClientError(Exception):
    pass


def _load_app(environment):
    fake_table = _FakeTable()
    fake_resource = _FakeResource(fake_table)
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.resource = lambda *_args, **_kwargs: fake_resource
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
            "botocore": botocore_stub,
            "botocore.exceptions": botocore_exceptions_stub,
        },
    ), mock.patch.dict(os.environ, environment, clear=True):
        spec.loader.exec_module(module)
    return module, fake_resource, fake_table


class PostConfirmationHandlerTests(unittest.TestCase):
    def test_derives_table_name_from_infrastructure_arn(self):
        app, resource, _table = _load_app(
            {
                "USERS_TABLE_ARN": (
                    "arn:aws:dynamodb:us-east-1:123456789012:table/"
                    "trustcheckradar-dev-users"
                )
            }
        )

        self.assertEqual(app.TABLE_NAME, "trustcheckradar-dev-users")
        self.assertEqual(resource.requested_table_name, "trustcheckradar-dev-users")

    def test_creates_profile_without_overwriting_existing_item(self):
        app, _resource, table = _load_app({"USERS_TABLE_NAME": "users"})
        event = {
            "request": {
                "userAttributes": {
                    "sub": "user-123",
                    "email": "user@example.com",
                    "custom:over_18": "true",
                }
            },
            "response": {},
        }

        result = app.lambda_handler(event, None)

        self.assertIs(result, event)
        self.assertEqual(len(table.put_calls), 1)
        call = table.put_calls[0]
        self.assertEqual(call["Item"]["PK"], "USER#user-123")
        self.assertEqual(call["Item"]["SK"], "PROFILE")
        self.assertEqual(
            call["ConditionExpression"],
            "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )

    def test_requires_a_table_identifier(self):
        with self.assertRaisesRegex(RuntimeError, "USERS_TABLE_NAME"):
            _load_app({})
