import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "qualify_dev_current_authority", ROOT / "scripts" / "qualify_dev_current_authority.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def credential_headers():
    return {
        "Authorization": "Bearer " + "x" * 64,
        "x-device-binding-fingerprint": "fingerprint",
    }


def snapshot():
    return {
        "schemaVersion": 1,
        "activeDevice": True,
        "access": {
            "basis": "complimentary",
            "externalChecksAllowed": True,
            "reason": "AVAILABLE",
        },
        "allowance": {
            "limit": None,
            "completedUsed": None,
            "reserved": None,
            "remaining": None,
            "periodEndsAtEpoch": None,
        },
    }


def settled(check_id, proof):
    return {
        "transportVersion": MODULE.VERSION,
        "checkId": check_id,
        "operationProof": proof,
        "state": "settled",
        "access": {
            "basis": "complimentary",
            "externalChecksAllowed": True,
            "unlimited": True,
        },
        "accounting": {
            "state": "not_charged",
            "chargedChecks": 0,
            "receiptId": "redacted-by-runner",
            "requiresReconciliation": False,
        },
        "outcome": {
            "processingOutcome": "complete",
            "verdict": "high_risk",
            "ruleIds": ["REQUEST_SECRET_DISCLOSURE"],
            "limitationCodes": [],
            "evidence": [],
        },
        "errorCode": None,
    }


def test_runs_one_complimentary_check_without_exposing_identifiers():
    calls = []
    proof = "v1_k1_ffffffff_1111111111111111_222222222222222222222222"
    state = {}

    def request(method, path, body=None):
        calls.append((method, path, body))
        if path == "/v1/access":
            return 200, snapshot()
        if path.endswith("/prepare"):
            state["check"] = body["checkId"]
            return 200, {
                "state": "prepared",
                "operationProof": proof,
                "accounting": {"chargedChecks": 0},
            }
        return 200, settled(state["check"], proof)

    result = MODULE.Harness(
        "https://api-dev.andmorethings.net", credential_headers(), request=request
    ).run()

    assert result == {
        "case": "SECUR4ALL-334_DEV_CURRENT_AUTHORITY",
        "passed": True,
        "syntheticOnly": True,
        "basis": "complimentary",
        "rulesOnly": True,
        "processingOutcome": "complete",
        "verdict": "high_risk",
        "rule": "REQUEST_SECRET_DISCLOSURE",
        "chargedChecks": 0,
        "exactReplayAccounting": True,
        "proofOnlyReconciliation": True,
        "allowanceUnchanged": True,
    }
    assert [path for _, path, _ in calls] == [
        "/v1/access",
        "/v1/message-checks/prepare",
        "/v1/message-checks",
        "/v1/message-checks",
        "/v1/message-checks/reconcile",
        "/v1/access",
    ]
    output = json.dumps(result)
    assert state["check"] not in output and proof not in output
    assert "Authorization" not in output and "fingerprint" not in output


def test_rejects_non_complimentary_access_before_message_mutation():
    calls = []

    def request(method, path, body=None):
        calls.append((method, path, body))
        value = snapshot()
        value["access"]["basis"] = "trial"
        value["allowance"]["limit"] = 10
        return 200, value

    with pytest.raises(MODULE.Refused):
        MODULE.Harness(
            "https://api-dev.andmorethings.net", credential_headers(), request=request
        ).run()
    assert calls == [("GET", "/v1/access", None)]


@pytest.mark.parametrize(
    "api_base",
    [
        "http://api-dev.andmorethings.net",
        "https://api.example.com",
        "https://user:pass@api-dev.andmorethings.net",
        "https://api-dev.andmorethings.net?token=secret",
    ],
)
def test_rejects_unreviewed_api_targets(api_base):
    with pytest.raises(MODULE.Refused):
        MODULE.Harness(api_base, credential_headers(), request=lambda *args: None)


def test_headers_require_mode_600_exact_fields_and_no_duplicates(tmp_path):
    path = tmp_path / "headers.json"
    path.write_text(json.dumps(credential_headers()))
    path.chmod(0o600)
    assert MODULE.headers(path) == credential_headers()

    path.chmod(0o644)
    with pytest.raises(MODULE.Refused):
        MODULE.headers(path)

    path.chmod(0o600)
    path.write_text('{"Authorization":"Bearer ' + "x" * 64 + '","Authorization":"duplicate",'
                    '"x-device-binding-fingerprint":"fingerprint"}')
    with pytest.raises(MODULE.Refused):
        MODULE.headers(path)

    target = tmp_path / "target.json"
    target.write_text(json.dumps(credential_headers()))
    target.chmod(0o600)
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(MODULE.Refused):
        MODULE.headers(path)


def test_snapshot_requires_the_exact_unlimited_allowance_shape():
    value = snapshot()
    del value["allowance"]["completedUsed"]
    with pytest.raises(MODULE.Refused):
        MODULE.Harness._complimentary_snapshot(value)


def test_execute_requires_explicit_synthetic_account_confirmation(tmp_path, monkeypatch):
    path = tmp_path / "headers.json"
    path.write_text(json.dumps(credential_headers()))
    path.chmod(0o600)
    monkeypatch.setattr(
        "sys.argv",
        [
            "qualify_dev_current_authority.py",
            "--api-base",
            "https://api-dev.andmorethings.net",
            "--headers-file",
            str(path),
            "--execute",
        ],
    )
    with pytest.raises(MODULE.Refused):
        MODULE.main()
