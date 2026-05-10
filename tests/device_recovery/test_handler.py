import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "device_recovery"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


class _FakeTable:
    def get_item(self, **kwargs):
        return {}

    def put_item(self, **kwargs):
        return {}

    def query(self, **kwargs):
        return {"Items": []}


class _FakeResource:
    def Table(self, _name):
        return _FakeTable()


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: _FakeResource()
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
_load_module("validation", MODULE_DIR / "validation.py")
_load_module("service", MODULE_DIR / "service.py")
app = _load_module("app", MODULE_DIR / "app.py")


class DeviceRecoveryHandlerTests(unittest.TestCase):
    def test_returns_success_response(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "support-1"}}}},
            "body": json.dumps(
                {
                    "action": "RESET_ACTIVE_BINDING",
                    "accountId": "user-123",
                }
            ),
        }

        with mock.patch.object(
            app,
            "process_recovery",
            return_value={
                "result": "CLEARED",
                "accountId": "user-123",
                "bindingFingerprint": "fp-1",
                "status": "INACTIVE",
                "operatorId": "support-1",
                "recoveredAt": "2026-05-10T00:00:00+00:00",
            },
        ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["result"], "CLEARED")

    def test_returns_validation_error(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "support-1"}}}},
            "body": json.dumps(
                {
                    "action": "RECOVER_BINDING",
                    "accountId": "user-123",
                }
            ),
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(body["error"]["details"][0]["field"], "bindingFingerprint")

    def test_returns_unauthorized_without_trusted_identity(self):
        event = {
            "body": json.dumps(
                {
                    "action": "RESET_ACTIVE_BINDING",
                    "accountId": "user-123",
                }
            ),
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 401)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "UNAUTHORIZED")


if __name__ == "__main__":
    unittest.main()
