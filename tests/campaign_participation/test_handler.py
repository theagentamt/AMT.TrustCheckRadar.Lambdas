import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "campaign_participation"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

for name in ["app", "config", "errors", "service", "validation"]:
    sys.modules.pop(name, None)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


load("errors", MODULE_DIR / "errors.py")
load("config", MODULE_DIR / "config.py")
service_stub = types.ModuleType("service")
service_stub.get_participation = lambda _identity: {}
service_stub.update_participation = lambda _identity, _payload: {}
sys.modules["service"] = service_stub
validation = load("validation", MODULE_DIR / "validation.py")
app = load("app", MODULE_DIR / "app.py")
validate_config = app.config.validate_config

OPERATION_ID = "47debb73-444b-4bb1-9889-fb56885b7922"


def event(method="GET", body=None, claims=None):
    value = {
        "routeKey": f"{method} /v1/users/campaign-participation",
        "requestContext": {"authorizer": {"jwt": {"claims": claims or {"sub": "user-123"}}}},
    }
    if body is not None:
        value["body"] = json.dumps(body)
    return value


class ParticipationHandlerTests(unittest.TestCase):
    def setUp(self):
        self.config_patch = mock.patch.object(app.config, "validate_config")
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def test_get_uses_only_jwt_subject_and_returns_no_identity(self):
        with mock.patch.object(app, "get_participation", return_value={"schemaVersion": 1, "state": "not_enrolled"}) as getter:
            response = app.lambda_handler(event(), None)
        self.assertEqual(response["statusCode"], 200)
        getter.assert_called_once_with("user-123")
        self.assertNotIn("user-123", response["body"])

    def test_legacy_or_principal_identity_is_not_trusted(self):
        value = {"routeKey": "GET /v1/users/campaign-participation", "requestContext": {"authorizer": {"claims": {"sub": "legacy"}, "principalId": "principal"}}}
        response = app.lambda_handler(value, None)
        self.assertEqual(response["statusCode"], 401)

    def test_put_validates_exact_contract(self):
        body = {"schemaVersion": 1, "action": "join", "noticeVersion": app.config.NOTICE_VERSION, "operationId": OPERATION_ID}
        with mock.patch.object(app, "update_participation", return_value={"schemaVersion": 1, "state": "enrolled"}) as updater:
            response = app.lambda_handler(event("PUT", body), None)
        self.assertEqual(response["statusCode"], 200)
        updater.assert_called_once_with("user-123", body)

    def test_unknown_field_including_account_id_is_rejected(self):
        body = {"schemaVersion": 1, "action": "join", "noticeVersion": app.config.NOTICE_VERSION, "operationId": OPERATION_ID, "accountId": "attacker"}
        response = app.lambda_handler(event("PUT", body), None)
        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "INVALID_REQUEST")

    def test_duplicate_json_field_is_rejected(self):
        value = event("PUT")
        value["body"] = (
            '{"schemaVersion":1,"action":"join","action":"withdraw",'
            f'"noticeVersion":"{app.config.NOTICE_VERSION}","operationId":"{OPERATION_ID}"}}'
        )
        response = app.lambda_handler(value, None)
        self.assertEqual(response["statusCode"], 400)

    def test_notice_version_and_uuid4_are_enforced(self):
        for changes in ({"noticeVersion": "old"}, {"operationId": "not-a-uuid"}, {"schemaVersion": True}):
            body = {"schemaVersion": 1, "action": "join", "noticeVersion": app.config.NOTICE_VERSION, "operationId": OPERATION_ID} | changes
            with self.subTest(changes=changes):
                response = app.lambda_handler(event("PUT", body), None)
                self.assertEqual(response["statusCode"], 400)

    def test_policy_version_is_the_semantic_string_policy_one(self):
        values = {
            "USERS_TABLE_NAME": "users", "ENTITLEMENTS_TABLE_NAME": "entitlements",
            "DELETION_LEDGER_TABLE_NAME": "ledger", "ENVIRONMENT": "dev",
            "NOTICE_VERSION": "notice-2026-09", "AUDIT_RETENTION_DAYS": 400,
            "DELETION_SLA_HOURS": 24, "FREE_MONTHLY_SCAN_LIMIT": 10,
            "PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT": 15, "PRO_MONTHLY_SCAN_LIMIT": 100,
        }
        with mock.patch.multiple(app.config, **values, POLICY_VERSION="policy-1"):
            validate_config()
        with mock.patch.multiple(app.config, **values, POLICY_VERSION=1):
            with self.assertRaises(RuntimeError):
                validate_config()

    def test_scan_quotas_are_configurable_with_safe_tier_ordering(self):
        values = {
            "USERS_TABLE_NAME": "users", "ENTITLEMENTS_TABLE_NAME": "entitlements",
            "DELETION_LEDGER_TABLE_NAME": "ledger", "ENVIRONMENT": "dev",
            "NOTICE_VERSION": "notice-2026-09", "POLICY_VERSION": "policy-1",
            "AUDIT_RETENTION_DAYS": 400, "DELETION_SLA_HOURS": 24,
        }
        with mock.patch.multiple(
            app.config,
            **values,
            FREE_MONTHLY_SCAN_LIMIT=15,
            PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT=20,
            PRO_MONTHLY_SCAN_LIMIT=1000,
        ):
            validate_config()

        invalid_quotas = ((0, 15, 1000), (15, 15, 1000), (15, 20, 20))
        for base_free, participating_free, pro in invalid_quotas:
            with self.subTest(
                base_free=base_free,
                participating_free=participating_free,
                pro=pro,
            ):
                with mock.patch.multiple(
                    app.config,
                    **values,
                    FREE_MONTHLY_SCAN_LIMIT=base_free,
                    PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT=participating_free,
                    PRO_MONTHLY_SCAN_LIMIT=pro,
                ):
                    with self.assertRaises(RuntimeError):
                        validate_config()


if __name__ == "__main__":
    unittest.main()
