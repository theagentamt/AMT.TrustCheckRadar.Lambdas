import json
import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock


SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *_args, **_kwargs: None
boto3_stub.client = lambda *_args, **_kwargs: None
sys.modules.setdefault("boto3", boto3_stub)

from history_mutation_api import app as mutation_app
from history_read_api import app as read_app


class Table:
    def __init__(self, items=None):
        self.items = items or {}

    def get_item(self, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}


class Resource:
    def __init__(self):
        self.device = Table({
            ("USER#account-1", "ACTIVE_BINDING"): {
                "recordType": "ACTIVE_BINDING_POINTER",
                "bindingFingerprint": "fingerprint-1",
                "stateVersion": Decimal("1"),
            },
            ("USER#account-1", "DEVICE#fingerprint-1"): {
                "accountId": "account-1", "status": "ACTIVE",
                "bindingFingerprint": "fingerprint-1",
            },
        })
        self.other = Table()

    def Table(self, name):
        return self.device if name == "device-bindings" else self.other


class Settings:
    users_table_name = "users"
    deletion_ledger_table_name = "ledger"
    device_bindings_table_name = "device-bindings"
    content_table_name = "history-content"
    control_table_name = "history-control"

    def validate_reads(self):
        return None

    def validate_mutations(self):
        return None


def event(route, *, body=None):
    return {
        "routeKey": route,
        "headers": {"X-Device-Binding-Fingerprint": "fingerprint-1"},
        "queryStringParameters": None,
        "body": body,
    }


class HistoryApiBindingTests(unittest.TestCase):
    def setUp(self):
        self.resource = Resource()

    def test_read_handler_accepts_sdk_decimal_pointer_version(self):
        service = mock.Mock()
        service.list_history.return_value = {
            "schemaVersion": 1, "items": [], "serverTimeEpoch": 100,
        }
        with mock.patch.object(
            read_app.HistorySettings, "from_env", return_value=Settings()
        ), mock.patch.object(
            read_app, "jwt_subject", return_value="account-1"
        ), mock.patch.object(
            read_app, "assert_authoritative_account_active"
        ), mock.patch.object(
            read_app.boto3, "resource", return_value=self.resource
        ), mock.patch.object(
            read_app.CursorStore, "from_aws", return_value=object()
        ), mock.patch.object(
            read_app, "HistoryReadService", return_value=service
        ):
            response = read_app.lambda_handler(
                event("GET /v1/users/history"), None
            )

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["items"], [])

    def test_mutation_handler_accepts_sdk_decimal_pointer_version(self):
        service = mock.Mock()
        service.bootstrap.return_value = {
            "schemaVersion": 1, "operation": "BOOTSTRAP",
            "status": "COMPLETE",
        }
        with mock.patch.object(
            mutation_app.HistorySettings, "from_env", return_value=Settings()
        ), mock.patch.object(
            mutation_app, "jwt_subject", return_value="account-1"
        ), mock.patch.object(
            mutation_app, "assert_authoritative_account_active"
        ), mock.patch.object(
            mutation_app.boto3, "resource", return_value=self.resource
        ), mock.patch.object(
            mutation_app.boto3, "client", return_value=object()
        ), mock.patch.object(
            mutation_app, "HistoryMutationService", return_value=service
        ):
            response = mutation_app.lambda_handler(
                event(
                    "POST /v1/users/history/bootstrap",
                    body=json.dumps({"schemaVersion": 1}),
                ),
                None,
            )

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
