import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "purchase_handoff"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

for module_name in [
    "config",
    "errors",
    "google_play_client",
    "idempotency",
    "verification",
    "validation",
    "service",
    "app",
    "shared_entitlements",
    "shared_entitlements.service",
    "shared_entitlements.config",
]:
    sys.modules.pop(module_name, None)


class _FakeTable:
    def get_item(self, **kwargs):
        return {}

    def put_item(self, **kwargs):
        return {}


class _FakeResource:
    def Table(self, _name):
        return _FakeTable()


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: _FakeResource()
boto3_stub.client = lambda *args, **kwargs: object()
sys.modules["boto3"] = boto3_stub

botocore_ex = types.ModuleType("botocore.exceptions")
class _FakeClientError(Exception):
    def __init__(self, response=None):
        super().__init__("client error")
        self.response = response or {}
botocore_ex.ClientError = _FakeClientError
sys.modules["botocore.exceptions"] = botocore_ex


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_load_module("errors", MODULE_DIR / "errors.py")
_load_module("config", MODULE_DIR / "config.py")
_load_module("google_play_client", MODULE_DIR / "google_play_client.py")
_load_module("idempotency", MODULE_DIR / "idempotency.py")
_load_module("verification", MODULE_DIR / "verification.py")
import shared_entitlements.service  # noqa: E402,F401
_load_module("service", MODULE_DIR / "service.py")
_load_module("validation", MODULE_DIR / "validation.py")
app = _load_module("app", MODULE_DIR / "app.py")


class PurchaseHandoffHandlerTests(unittest.TestCase):
    def test_returns_success_response(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "body": json.dumps(
                {
                    "productId": "trustcheck_radar_pro_monthly",
                    "platform": "google_play",
                    "purchaseToken": "purchase-token-12345",
                    "purchaseState": "PURCHASED",
                    "packageName": "com.example.app",
                }
            ),
        }

        with mock.patch.object(
            app,
            "process_purchase_handoff",
            return_value={
                "verificationStatus": "accepted",
                "verificationReason": "Google Play verified the subscription purchase.",
                "idempotencyReplay": False,
                "entitlement": {
                    "tier": "pro",
                    "status": "active",
                    "platform": "google_play",
                    "productId": "trustcheck_radar_pro_monthly",
                    "billingPeriodStartUtc": "2026-06-27T19:15:00Z",
                    "billingPeriodEndUtc": "2026-07-27T19:15:00Z",
                    "lastVerifiedAtUtc": "2026-06-27T19:15:05Z",
                    "isAccessGranted": True,
                    "remainingMonthlyScans": 100,
                    "remainingCredits": 0,
                },
                "usage": {
                    "periodKey": "2026-06",
                    "monthlyLimit": 100,
                    "usedCount": 0,
                    "remainingCount": 100,
                },
            },
        ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["verificationStatus"], "accepted")
        self.assertEqual(body["entitlement"]["tier"], "pro")
        self.assertFalse(body["idempotencyReplay"])

    def test_returns_validation_error(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "body": json.dumps(
                {
                    "productId": "unknown_product",
                    "platform": "google_play",
                    "purchaseToken": "purchase-token-12345",
                    "packageName": "com.example.app",
                }
            ),
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(body["error"]["details"][0]["field"], "productId")

    def test_returns_unauthorized_without_trusted_identity(self):
        event = {
            "body": json.dumps(
                {
                    "productId": "trustcheck_radar_pro_monthly",
                    "platform": "google_play",
                    "purchaseToken": "purchase-token-12345",
                    "packageName": "com.example.app",
                }
            ),
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 401)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "UNAUTHORIZED")


if __name__ == "__main__":
    unittest.main()
