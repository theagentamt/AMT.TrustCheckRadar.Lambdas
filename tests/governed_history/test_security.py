from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/shared_check_authority")]

from governed_history import app
from governed_history.security import (DELETION_FIELDS, DEVICE_FIELDS,
                                        INVENTORY_FIELDS, PROFILE_FIELDS, authorize)
from shared_check_authority.core import AuthorityError
from shared_check_authority.inventory import INVENTORY_KEY, INVENTORY_FIELDS as STARTUP_INVENTORY_FIELDS
from test_transactions import world


def test_reader_identity_fences_use_only_minimized_projected_gets(world, monkeypatch):
    authority, event, _, _, _, _ = world
    requests = []
    factory = authority.ddb.Table

    class Table:
        def __init__(self, name):
            self.name = name
            self.delegate = factory(name)

        def get_item(self, **kwargs):
            requests.append((self.name, kwargs))
            return self.delegate.get_item(**kwargs)

    monkeypatch.setattr(authority.ddb, "Table", Table)
    assert authorize(authority, event)[1:] == ("device-one", 1, 1)
    assert [(name, tuple(request["ExpressionAttributeNames"].values())) for name, request in requests] == [
        ("users", PROFILE_FIELDS),
        ("deletion", DELETION_FIELDS),
        ("devices", DEVICE_FIELDS),
        ("devices", DEVICE_FIELDS),
        ("authority", INVENTORY_FIELDS),
    ]
    assert all(request["ConsistentRead"] is True for _, request in requests)
    requested = " ".join(value for _, request in requests
                         for value in request["ExpressionAttributeNames"].values())
    for forbidden in ("email", "phone", "given_name", "family_name", "demographic", "purchaseToken"):
        assert forbidden not in requested


def test_actual_reader_runtime_startup_and_authorization_project_every_get(world, monkeypatch):
    """The handler loader must not perform a broader inventory read before authorize()."""
    import boto3
    from shared_check_authority import inventory, runtime

    original, event, _, _, _, _ = world
    requests = []
    factory = original.ddb.Table

    class Table:
        def __init__(self, name):
            self.name = name
            self.delegate = factory(name)

        def get_item(self, **kwargs):
            requests.append((self.name, kwargs))
            return self.delegate.get_item(**kwargs)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

    class Resource:
        meta = original.ddb.meta

        @staticmethod
        def Table(name):
            return Table(name)

    settings = original.s
    values = {
        "STAGE": "dev", "AUTHORITY_ENABLED": "true",
        "USERS_TABLE_NAME": settings.users_table,
        "DEVICE_BINDINGS_TABLE_NAME": settings.devices_table,
        "DELETION_LEDGER_TABLE_NAME": settings.deletion_table,
        "AUTHORITY_TABLE_NAME": settings.authority_table,
        "COGNITO_ISSUER": settings.cognito_issuer,
        "COGNITO_APP_CLIENT_ID": settings.cognito_app_client_id,
        "COGNITO_REQUIRED_SCOPE": settings.cognito_required_scope,
        "AUTHORITY_POLICY_VERSION": settings.policy_version,
        "OPERATION_VALIDITY_SECONDS": str(settings.operation_validity_seconds),
        "WORKER_SETTLEMENT_SECONDS": str(settings.worker_settlement_seconds),
        "RECONCILIATION_SECONDS": str(settings.reconciliation_seconds),
        "RECEIPT_RETENTION_SECONDS": "604800",
        "COUNTER_RETENTION_SECONDS": "604800",
        "ATTEMPT_WINDOW_SECONDS": str(settings.attempt_window_seconds),
        "ATTEMPTS_PER_WINDOW": str(settings.attempts_per_window),
        "MAX_INFLIGHT": str(settings.max_inflight),
        "GOVERNED_HISTORY_SETTLEMENT_ENABLED": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(inventory, "load_keyring",
                        lambda: (settings.active_key_id, settings.hmac_keys))
    monkeypatch.setattr(boto3, "resource", lambda *args, **kwargs: Resource())

    from shared_check_authority import engineering

    class Reader:
        def __init__(self, *_args, **_kwargs):
            pass

        def list(self, **_kwargs):
            return {"transportVersion": "1.0.0-governed-history-candidate.1",
                    "serverTimeEpoch": 1, "items": [], "nextCursor": None}

    monkeypatch.setenv("GOVERNED_HISTORY_LIST_ENABLED", "true")
    monkeypatch.setattr(engineering, "require_engineering_subject", lambda _event: "synthetic-account")
    monkeypatch.setattr(app.Settings, "from_env", classmethod(lambda _cls: object()))
    monkeypatch.setattr(app, "Service", Reader)
    event.update({"version": "2.0", "routeKey": "GET /v1/users/analysis-history",
                  "requestContext": event["requestContext"] | {"http": {"method": "GET"}},
                  "queryStringParameters": None, "body": None, "isBase64Encoded": False})
    result = app.lambda_handler(event, None)
    assert result["statusCode"] == 200
    assert requests[0][0] == "authority"
    assert tuple(requests[0][1]["ExpressionAttributeNames"].values()) == STARTUP_INVENTORY_FIELDS
    assert all(request.get("ProjectionExpression") for _, request in requests)
    assert all(request.get("ExpressionAttributeNames") for _, request in requests)
    assert all(request.get("ConsistentRead") is True for _, request in requests)


def test_mutation_inventory_verifier_keeps_full_row_default(world, monkeypatch):
    from shared_check_authority.inventory import verified_inventory

    authority, _, _, _, _, _ = world
    requests = []
    delegate = authority.ddb.Table("authority")

    class Table:
        def get_item(self, **kwargs):
            requests.append(kwargs)
            return delegate.get_item(**kwargs)

    class Resource:
        @staticmethod
        def Table(_name):
            return Table()

    verified_inventory(Resource(), "authority", authority.s.hmac_keys)
    assert requests == [{"Key": INVENTORY_KEY, "ConsistentRead": True}]


def test_minimized_inventory_validation_fails_closed(world):
    authority, event, _, _, _, _ = world
    current = authority.ddb.Table("authority").get_item(Key=INVENTORY_KEY, ConsistentRead=True)["Item"]
    current["issuedKeys"] = {"k1": "0" * 64}
    authority.ddb.Table("authority").put_item(Item=current)
    with pytest.raises(AuthorityError, match="KEY_INVENTORY_UNAVAILABLE"):
        authorize(authority, event)


def test_projected_read_failure_is_not_misreported_as_authentication(world, monkeypatch):
    authority, event, _, _, _, _ = world
    monkeypatch.setattr(authority.ddb, "Table", lambda _name: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(AuthorityError, match="IDENTITY_READ_UNAVAILABLE"):
        authorize(authority, event)
