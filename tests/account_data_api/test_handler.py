import importlib.util
import json
import os
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE = Path(__file__).resolve().parents[2] / "src" / "account_data_api"
SRC = MODULE.parent
for path in (SRC, MODULE):
    sys.path.insert(0, str(path))
os.environ.update({
    "APP_ENVIRONMENT": "dev",
    "COGNITO_ISSUER": "https://cognito-idp.us-east-1.amazonaws.com/pool",
    "COGNITO_APP_CLIENT_ID": "client",
})


class Resource:
    def Table(self, _name):
        return object()


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *_args, **_kwargs: Resource()
boto3_stub.client = lambda *_args, **_kwargs: object()
sys.modules.setdefault("boto3", boto3_stub)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("errors", MODULE / "errors.py")
config = _load("config", MODULE / "config.py")
_load("validation", MODULE / "validation.py")
_load("service", MODULE / "service.py")
app = _load("account_data_app_test", MODULE / "app.py")


def event(method="POST", *, auth_age=30, body=None):
    current = int(time.time())
    return {
        "routeKey": f"{method} /v1/users/account-deletion",
        "requestContext": {"authorizer": {"jwt": {"claims": {
            "sub": "account-1", "iss": config.COGNITO_ISSUER,
            "client_id": config.COGNITO_APP_CLIENT_ID, "token_use": "access",
            "exp": str(current + 600), "scope": config.COGNITO_REQUIRED_SCOPE,
            "auth_time": str(current - auth_age), "iat": str(current - auth_age),
        }}}},
        "body": body,
    }


class Subject:
    def __init__(self):
        self.requests = []
    def request(self, account_id, operation_id):
        self.requests.append((account_id, operation_id))
        return self.status(account_id)
    def status(self, _account_id):
        return {
            "schemaVersion": 1, "operation": "ACCOUNT_DELETION",
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "status": "REQUESTED", "requestedAtEpoch": 100,
            "deleteByEpoch": 86_500, "completionEligible": False,
            "components": [{"component": "HISTORY", "status": "PENDING"}],
        }


class AccountDataHandlerTests(unittest.TestCase):
    def setUp(self):
        self.subject = Subject()
        self.patches = [
            mock.patch.object(app.config, "validate_config"),
            mock.patch.object(app, "_service", return_value=self.subject),
            mock.patch.object(app, "_attempt_post_fence_cleanup"),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(self.patches)])

    def test_post_targets_only_token_subject_and_returns_accepted(self):
        request = event(body=json.dumps({
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "DELETE_ACCOUNT",
        }))

        response = app.lambda_handler(request, None)

        self.assertEqual(response["statusCode"], 202)
        self.assertEqual(
            self.subject.requests,
            [("account-1", "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4")],
        )
        self.assertEqual(response["headers"]["Cache-Control"], "private, no-store")

    def test_post_rejects_stale_reauthentication(self):
        request = event(auth_age=301, body=json.dumps({
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "DELETE_ACCOUNT",
        }))

        response = app.lambda_handler(request, None)

        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "REAUTHENTICATION_REQUIRED")

    def test_post_rejects_body_targeting_and_get_rejects_body(self):
        request = event(body=json.dumps({
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "DELETE_ACCOUNT", "accountId": "victim",
        }))
        self.assertEqual(app.lambda_handler(request, None)["statusCode"], 400)

        get_request = event(method="GET", body="{}")
        self.assertEqual(app.lambda_handler(get_request, None)["statusCode"], 400)

    def test_get_status_does_not_require_active_profile(self):
        response = app.lambda_handler(event(method="GET", body=None), None)

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["status"], "REQUESTED")

    def test_malformed_wire_requests_never_begin_deletion(self):
        valid = json.dumps({"schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "DELETE_ACCOUNT"})
        bad_bodies = [
            json.loads(valid), valid.replace('"schemaVersion": 1', '"schemaVersion": true'),
            valid.replace('"schemaVersion": 1', '"schemaVersion": 1.0'),
            valid.replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1'),
            valid + " " * 1024, "[" * 1024, "NaN", "\\ud800",
        ]
        for body in bad_bodies:
            with self.subTest(body_type=type(body).__name__):
                response = app.lambda_handler(event(body=body), None)
                self.assertEqual(response["statusCode"], 400)
        for addition in [{"isBase64Encoded": True},
                         {"queryStringParameters": []},
                         {"rawQueryString": "accountId=another-account"}]:
            with self.subTest(addition=addition):
                response = app.lambda_handler({**event(body=valid), **addition}, None)
                self.assertEqual(response["statusCode"], 400)
        self.assertEqual(self.subject.requests, [])

    def test_service_exception_is_not_copied_into_logs_or_response(self):
        private = "private-account-and-provider-content"
        with mock.patch.object(self.subject, "status", side_effect=RuntimeError(private)), \
                self.assertLogs(app.LOGGER, level="ERROR") as captured:
            response = app.lambda_handler(event(method="GET"), None)
        self.assertEqual(response["statusCode"], 500)
        self.assertNotIn(private, response["body"])
        self.assertNotIn(private, " ".join(captured.output))

    def test_stream_failure_uses_dynamodb_sequence_number(self):
        record = {
            "eventID": "not-the-batch-identifier",
            "eventName": "INSERT",
            "dynamodb": {"SequenceNumber": "12345", "NewImage": {}},
        }
        with mock.patch.object(app, "command_from_stream", return_value={"command": True}), \
                mock.patch.object(app, "ensure_session_revoked", side_effect=RuntimeError("failed")), \
                mock.patch.object(app, "delete_device_bindings"), \
                mock.patch.object(app.boto3, "resource", return_value=Resource()), \
                mock.patch.object(app.boto3, "client", return_value=object()):
            result = app.lambda_handler({"Records": [record]}, None)

        self.assertEqual(result, {"batchItemFailures": [{"itemIdentifier": "12345"}]})

    def test_stream_configuration_failure_propagates_for_batch_retry(self):
        with mock.patch.object(app.config, "validate_config", side_effect=RuntimeError("bad config")):
            with self.assertRaises(RuntimeError):
                app.lambda_handler({"Records": []}, None)


if __name__ == "__main__":
    unittest.main()
