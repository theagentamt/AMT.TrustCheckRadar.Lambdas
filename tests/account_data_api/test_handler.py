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
            "sub": "01234567-89ab-4cde-8fab-0123456789ab", "iss": config.COGNITO_ISSUER,
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
            mock.patch.dict(os.environ, {"ACCOUNT_DELETION_HTTP_SUBJECTS_JSON": json.dumps(["01234567-89ab-4cde-8fab-0123456789ab"])}),
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
            [("01234567-89ab-4cde-8fab-0123456789ab", "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4")],
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
        for addition in [{"isBase64Encoded": True}, {"isBase64Encoded": 0},
                         {"queryStringParameters": []},
                         {"rawQueryString": "accountId=another-account"}]:
            with self.subTest(addition=addition):
                response = app.lambda_handler({**event(body=valid), **addition}, None)
                self.assertEqual(response["statusCode"], 400)
        self.assertEqual(self.subject.requests, [])

    def test_accepted_post_is_not_repolled_after_concurrent_identity_completion(self):
        snapshot = self.subject.status("01234567-89ab-4cde-8fab-0123456789ab")
        with mock.patch.object(self.subject,"request",return_value=snapshot), \
                mock.patch.object(self.subject,"status",side_effect=RuntimeError("identity already removed")):
            response = app.lambda_handler(event(body=json.dumps({"schemaVersion":1,"action":"DELETE_ACCOUNT","operationId":"3fefbf1a-caf4-4e72-ab61-4fb36bf925b4"})),None)
        self.assertEqual(response["statusCode"],202)
        self.assertEqual(json.loads(response["body"]),snapshot)

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

    def test_reconciliation_failure_retains_retry_without_private_exception_text(self):
        with mock.patch.object(app, "reconcile_session_revocations", side_effect=RuntimeError("private-account")):
            with self.assertRaisesRegex(RuntimeError, "^Account deletion reconciliation failed$") as captured:
                app.lambda_handler({"schemaVersion": 1, "operation": "reconcile-session-revocation"}, None)
        self.assertTrue(captured.exception.__suppress_context__)

    def test_stream_configuration_failure_propagates_for_batch_retry(self):
        with mock.patch.object(app.config, "validate_config", side_effect=RuntimeError("bad config")):
            with self.assertRaises(RuntimeError):
                app.lambda_handler({"Records": []}, None)


class DurableCampaignConfigurationTests(unittest.TestCase):
    """Exercise the real runtime validator before any account or SDK work."""

    def setUp(self):
        approved = {
            "ACCOUNT_DELETION_ENABLED": True,
            "ACCOUNT_IDENTITY_FINALIZER_ENABLED": True,
            "CAMPAIGN_RECOVERY_WRITES_ENABLED": True,
            "APP_ENVIRONMENT": "dev",
            "ACCOUNT_DATA_INVENTORY_MANIFEST_SHA256": "a" * 64,
            "ACCOUNT_DATA_INVENTORY_REVISION": 1,
            "COGNITO_USER_POOL_ID": "synthetic-pool",
            "COGNITO_USERNAME_IS_SUB": True,
            "ACCOUNT_DELETION_COMPLETION_STATUS": "complete",
        }
        for name in (
            "ENTITLEMENTS_TABLE_NAME", "USERS_TABLE_NAME", "DELETION_LEDGER_TABLE_NAME",
            "DEVICE_BINDINGS_TABLE_NAME", "DEVICE_RECOVERY_CONTROL_TABLE_NAME",
            "ANALYSIS_ABUSE_TABLE_NAME", "CAMPAIGN_OUTBOX_TABLE_NAME",
        ):
            approved[name] = "synthetic-table"
        for name in (
            "ACCOUNT_DELETION_POLICY_STATUS", "ACCOUNT_DATA_INVENTORY_STATUS",
            "ANALYSIS_REQUEST_DEDUPE_POLICY_STATUS",
            "ANALYSIS_LEGACY_REQUEST_RETENTION_POLICY_STATUS",
            "ANALYSIS_CONSUMPTION_DELETION_POLICY_STATUS",
            "CAMPAIGN_OUTBOX_LOCATOR_COVERAGE_STATUS", "USER_PROFILE_DELETION_POLICY_STATUS",
        ):
            approved[name] = "approved"
        components = [
            "SESSION_REVOCATION", "DEVICE_BINDINGS", "DEVICE_RECOVERY", "ANALYSIS_ABUSE",
            "HISTORY", "CAMPAIGN", "CAMPAIGN_OUTBOX", "ENTITLEMENTS", "V1_AUTHORITY",
            "PLAY_TOKENS", "USER_PROFILE", "IDENTITY",
        ]
        patches = [
            mock.patch.multiple(config, **approved),
            mock.patch.dict(os.environ, {"ACCOUNT_DELETION_REQUIRED_COMPONENTS_JSON": json.dumps(components)}),
            mock.patch.object(app.boto3, "resource", side_effect=AssertionError("Unexpected SDK resource")),
            mock.patch.object(app.boto3, "client", side_effect=AssertionError("Unexpected SDK client")),
            mock.patch.object(app, "_service", side_effect=AssertionError("Unexpected account service")),
        ]
        self.guards = [patch.start() for patch in patches]
        self.addCleanup(lambda: [patch.stop() for patch in reversed(patches)])

    def assert_no_account_work(self):
        for guard in self.guards[2:]:
            guard.assert_not_called()

    def test_fully_configured_runtime_accepts_recovery_writes_enabled(self):
        config.validate_config()
        self.assert_no_account_work()

    def test_enabled_api_rejects_missing_recovery_before_account_work(self):
        with mock.patch.object(config, "CAMPAIGN_RECOVERY_WRITES_ENABLED", False):
            for method in ("POST", "GET"):
                with self.subTest(method=method):
                    response = app.lambda_handler(event(method), None)
                    self.assertEqual(response["statusCode"], 503)
                    self.assertEqual(json.loads(response["body"])["error"]["code"], "SERVER_UNAVAILABLE")
        self.assert_no_account_work()

    def test_stream_rejects_missing_recovery_without_acknowledging_batch(self):
        with mock.patch.object(config, "CAMPAIGN_RECOVERY_WRITES_ENABLED", False):
            with self.assertRaises(app.AppError) as captured:
                app.lambda_handler({"Records": []}, None)
        self.assertEqual(captured.exception.code, "SERVER_UNAVAILABLE")
        self.assert_no_account_work()

    def test_schedule_rejects_missing_recovery_for_retry(self):
        with mock.patch.object(config, "CAMPAIGN_RECOVERY_WRITES_ENABLED", False):
            with self.assertRaisesRegex(RuntimeError, "^Account deletion reconciliation failed$"):
                app.lambda_handler({"schemaVersion": 1, "operation": "reconcile-session-revocation"}, None)
        self.assert_no_account_work()

    def test_disabled_runtime_keeps_feature_disabled_response(self):
        with mock.patch.multiple(config, ACCOUNT_DELETION_ENABLED=False, CAMPAIGN_RECOVERY_WRITES_ENABLED=False):
            response = app.lambda_handler(event(), None)
        self.assertEqual(response["statusCode"], 503)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "FEATURE_DISABLED")
        self.assert_no_account_work()


if __name__ == "__main__":
    unittest.main()


class HttpSubjectScopeTests(unittest.TestCase):
    V4 = "01234567-89ab-4cde-8fab-0123456789ab"
    V7 = "01993d31-cafe-7abc-8abc-0123456789ab"

    def call(self, method, subject, raw, *, environment="dev"):
        request = event(method=method, body=None if method == "GET" else json.dumps({
            "schemaVersion": 1, "action": "DELETE_ACCOUNT",
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4"}))
        request["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = subject
        with mock.patch.dict(os.environ, {"ACCOUNT_DELETION_HTTP_SUBJECTS_JSON": raw}), \
                mock.patch.object(config, "APP_ENVIRONMENT", environment), \
                mock.patch.object(config, "validate_config"), \
                mock.patch.object(app, "_service", return_value=Subject()) as service, \
                mock.patch.object(app, "_attempt_post_fence_cleanup") as cleanup:
            response = app.lambda_handler(request, None)
            if response["statusCode"] == 503:
                service.assert_not_called()
                cleanup.assert_not_called()
            return response

    def test_selected_canonical_subject_versions_get_and_post(self):
        for subject in (self.V4, self.V7):
            for method, status in (("GET", 200), ("POST", 202)):
                with self.subTest(subject=subject, method=method):
                    self.assertEqual(self.call(method, subject, json.dumps([subject]))["statusCode"], status)

    def test_empty_outside_malformed_configuration_is_fixed_unavailable(self):
        values = ["[]", json.dumps([self.V7]), "null", "{}", "true", "[", "[1]",
                  json.dumps([self.V4, self.V4]), json.dumps([self.V4.upper()]),
                  json.dumps([self.V4.replace("-", "")]), json.dumps([" " + self.V4]),
                  json.dumps(["{" + self.V4 + "}"]), json.dumps([None]),
                  json.dumps([f"{n:08x}-89ab-4cde-8fab-0123456789ab" for n in range(11)]),
                  " " * 513, json.dumps(["\ud800"])]
        for raw in values:
            for method in ("GET", "POST"):
                with self.subTest(raw_index=values.index(raw), method=method):
                    response = self.call(method, self.V4, raw)
                    self.assertEqual(response["statusCode"], 503)
                    error = json.loads(response["body"])["error"]
                    self.assertEqual(error["code"], "SERVER_UNAVAILABLE")
                    self.assertEqual(error["message"], "Account deletion is not available.")
                    self.assertTrue(error["retryable"])
                    self.assertNotIn(self.V4, response["body"])

    def test_non_dev_is_closed_even_with_selected_subject(self):
        for environment in ("", "uat", "prod"):
            self.assertEqual(self.call("GET", self.V4, json.dumps([self.V4]), environment=environment)["statusCode"], 503)

    def test_default_absent_list_and_workers_do_not_consult_http_scope(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(config, "APP_ENVIRONMENT", "dev"):
            with self.assertRaises(config.AppError) as error:
                config.require_http_subject(self.V4)
            self.assertEqual(error.exception.code, "SERVER_UNAVAILABLE")
            with mock.patch.object(config, "require_http_subject", side_effect=AssertionError("HTTP only")), \
                    mock.patch.object(app, "_stream_handler", return_value={"batchItemFailures": []}) as stream, \
                    mock.patch.object(app, "_reconciliation_handler", return_value={"processed": 0}) as scheduled:
                self.assertEqual(app.lambda_handler({"Records": []}, None), {"batchItemFailures": []})
                self.assertEqual(app.lambda_handler({"schemaVersion": 1, "operation": "reconcile-session-revocation"}, None), {"processed": 0})
                stream.assert_called_once()
                scheduled.assert_called_once()

    def test_disabled_feature_precedes_subject_gate(self):
        with mock.patch.object(config, "ACCOUNT_DELETION_ENABLED", False), \
                mock.patch.object(config, "require_http_subject", side_effect=AssertionError("unreachable")), \
                mock.patch.object(app, "_service", side_effect=AssertionError("unreachable")):
            response = app.lambda_handler(event(method="GET"), None)
            self.assertEqual(json.loads(response["body"])["error"]["code"], "FEATURE_DISABLED")
