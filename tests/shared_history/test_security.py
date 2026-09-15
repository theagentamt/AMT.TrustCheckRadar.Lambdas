import sys
import unittest
from decimal import Decimal
from pathlib import Path


SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shared_history.errors import HistoryError
from shared_history.security import (
    assert_active_device_binding,
    assert_authoritative_account_active,
    jwt_subject,
)


class Settings:
    cognito_issuer = "https://cognito-idp.us-east-1.amazonaws.com/pool"
    cognito_app_client_id = "client-1"
    cognito_required_scope = "aws.cognito.signin.user.admin"


def event(**changes):
    claims = {
        "sub": "account-1", "iss": Settings.cognito_issuer,
        "client_id": Settings.cognito_app_client_id, "token_use": "access",
        "exp": "200", "scope": "openid aws.cognito.signin.user.admin",
    }
    claims.update(changes)
    return {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}}


class JwtSecurityTests(unittest.TestCase):
    def test_accepts_only_bound_unexpired_access_token(self):
        self.assertEqual(jwt_subject(event(), Settings(), now=lambda: 100), "account-1")

    def test_rejects_wrong_issuer_client_type_expiry_and_scope(self):
        variants = [
            {"iss": "https://cognito-idp.us-east-1.amazonaws.com/other"},
            {"client_id": "other"},
            {"token_use": "id"},
            {"exp": "100"},
            {"scope": "openid"},
        ]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(HistoryError) as raised:
                jwt_subject(event(**changes), Settings(), now=lambda: 100)
            self.assertEqual(raised.exception.code, "UNAUTHORIZED")

    def test_rejects_legacy_and_principal_fallbacks(self):
        for value in (
            {"requestContext": {"authorizer": {"claims": {"sub": "account-1"}}}},
            {"requestContext": {"authorizer": {"principalId": "account-1"}}},
        ):
            with self.assertRaises(HistoryError):
                jwt_subject(value, Settings(), now=lambda: 100)

    def test_preissued_token_is_blocked_by_authoritative_deletion_fence(self):
        class Table:
            def __init__(self, item):
                self.item = item
            def get_item(self, **_kwargs):
                return {"Item": self.item} if self.item else {}

        profile = {"sub": "account-1", "status": "ACTIVE", "ageVerified": True}
        assert_authoritative_account_active("account-1", Table(profile), Table(None))
        with self.assertRaises(HistoryError) as raised:
            assert_authoritative_account_active(
                "account-1", Table(profile), Table({"status": "REQUESTED"})
            )
        self.assertEqual(raised.exception.code, "FORBIDDEN")


class DeviceBindingSecurityTests(unittest.TestCase):
    class Table:
        def __init__(self, items):
            self.items = items

        def get_item(self, Key, **_kwargs):
            item = self.items.get((Key["PK"], Key["SK"]))
            return {"Item": item} if item else {}

    @staticmethod
    def _table(state_version):
        return DeviceBindingSecurityTests.Table({
            ("USER#account-1", "ACTIVE_BINDING"): {
                "recordType": "ACTIVE_BINDING_POINTER",
                "bindingFingerprint": "fingerprint-1",
                "stateVersion": state_version,
            },
            ("USER#account-1", "DEVICE#fingerprint-1"): {
                "accountId": "account-1", "status": "ACTIVE",
                "bindingFingerprint": "fingerprint-1",
            },
        })

    def test_history_read_and_mutation_binding_accept_sdk_decimal_version(self):
        # Both History handlers call this shared guard with boto3 resource Tables,
        # which deserialize DynamoDB Number values as Decimal.
        assert_active_device_binding(
            {"headers": {"x-device-binding-fingerprint": "fingerprint-1"}},
            "account-1", self._table(Decimal("1")),
        )

    def test_binding_rejects_bool_fraction_nonfinite_and_nonpositive_versions(self):
        invalid = (
            True, 0, Decimal("0"), Decimal("1.5"), Decimal("NaN"),
            Decimal("Infinity"), "1",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(HistoryError) as raised:
                assert_active_device_binding(
                    {"headers": {
                        "x-device-binding-fingerprint": "fingerprint-1",
                    }},
                    "account-1", self._table(value),
                )
            self.assertEqual(raised.exception.code, "SERVER_UNAVAILABLE")
