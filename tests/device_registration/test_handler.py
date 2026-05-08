import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "device_registration"
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


class DeviceRegistrationHandlerTests(unittest.TestCase):
    def test_returns_success_response(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "body": json.dumps(
                {
                    "bindingFingerprint": "fp-1",
                    "platform": "ios",
                    "osVersion": "18.4",
                }
            ),
        }

        with mock.patch.object(
            app,
            "register_device",
            return_value={
                "decision": "NEW",
                "accountId": "user-123",
                "bindingFingerprint": "fp-1",
                "status": "ACTIVE",
                "firstSeenAt": "2026-05-07T00:00:00+00:00",
                "lastSeenAt": "2026-05-07T00:00:00+00:00",
                "deactivatedAt": None,
            },
        ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["decision"], "NEW")
        self.assertEqual(body["bindingFingerprint"], "fp-1")

    def test_returns_validation_error(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "body": json.dumps(
                {
                    "bindingFingerprint": "fp-1",
                    "platform": "windows",
                    "osVersion": "11",
                }
            ),
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(body["error"]["details"][0]["field"], "platform")

    def test_returns_unauthorized_without_trusted_identity(self):
        event = {
            "body": json.dumps(
                {
                    "bindingFingerprint": "fp-1",
                    "platform": "ios",
                    "osVersion": "18.4",
                }
            ),
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 401)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "UNAUTHORIZED")


if __name__ == "__main__":
    unittest.main()
