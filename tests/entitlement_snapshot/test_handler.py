import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "entitlement_snapshot"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


class _FakeTable:
    def get_item(self, **kwargs):
        return {}


class _FakeResource:
    def Table(self, _name):
        return _FakeTable()


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: _FakeResource()
boto3_stub.client = lambda *args, **kwargs: object()
sys.modules.setdefault("boto3", boto3_stub)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_load_module("errors", MODULE_DIR / "errors.py")
_load_module("config", MODULE_DIR / "config.py")
_load_module("service", MODULE_DIR / "service.py")
app = _load_module("app", MODULE_DIR / "app.py")


class EntitlementSnapshotHandlerTests(unittest.TestCase):
    def test_returns_success_response(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
        }

        with mock.patch.object(
            app,
            "get_entitlement_snapshot",
            return_value={
                "entitlement": {
                    "tier": "pro",
                    "status": "active",
                    "platform": "google_play",
                    "productId": "trustcheck_radar_pro_monthly",
                    "billingPeriodStart": "2026-07-01T00:00:00Z",
                    "billingPeriodEnd": "2026-08-01T00:00:00Z",
                    "isAccessGranted": True,
                },
                "usage": {
                    "periodMode": "billing_cycle",
                    "periodKey": "2026-07",
                    "limit": 100,
                    "usedCount": 9,
                    "remaining": 91,
                },
                "guidance": {
                    "restoreRecommended": False,
                },
            },
        ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["entitlement"]["tier"], "pro")
        self.assertEqual(body["usage"]["remaining"], 91)
        self.assertFalse(body["guidance"]["restoreRecommended"])

    def test_returns_unauthorized_without_trusted_identity(self):
        response = app.lambda_handler({}, None)

        self.assertEqual(response["statusCode"], 401)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "UNAUTHORIZED")

    def test_returns_server_unavailable_error(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
        }

        with mock.patch.object(
            app,
            "get_entitlement_snapshot",
            side_effect=app.AppError("SERVER_UNAVAILABLE", "The entitlements table is not configured.", retryable=False),
        ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 500)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "SERVER_UNAVAILABLE")

    def test_returns_internal_error_on_unexpected_failure(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
        }

        with mock.patch.object(app, "get_entitlement_snapshot", side_effect=RuntimeError("boom")):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 500)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")


if __name__ == "__main__":
    unittest.main()
