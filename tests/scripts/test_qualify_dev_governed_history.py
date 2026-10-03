import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))
from shared_governed_history import present_projection, validate_public_projection
from message_evaluator.policy_v3 import evaluate
SPEC = importlib.util.spec_from_file_location("qualify_dev_governed_history", ROOT / "scripts/qualify_dev_governed_history.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
FIXTURES = json.loads((ROOT / "contracts/message-consumer/1.0.0-candidate.3-rules-only.1/evaluation-fixtures.json").read_text())["fixtures"]


def access(used=0, *, basis="trial", available=False):
    activated = 1_800_000_000 if basis == "trial" else None
    remaining = 10 - used if basis == "trial" else None
    return {"schemaVersion": 1, "policyVersion": "owner-2026-09-20-v1", "activeDevice": True,
            "access": {"basis": basis, "externalChecksAllowed": basis == "trial" and remaining > 0,
                       "reason": ("AVAILABLE" if remaining else "ALLOWANCE_EXHAUSTED") if basis == "trial"
                                 else "EXTERNAL_ACCESS_UNAVAILABLE"},
            "allowance": {"limit": 10 if basis == "trial" else None,
                          "completedUsed": used if basis == "trial" else None,
                          "reserved": 0 if basis == "trial" else None,
                          "remaining": remaining,
                          "periodEndsAtEpoch": activated + module.TRIAL_SECONDS if activated else None},
            "trial": {"activationAvailable": available, "activatedAtEpoch": activated,
                      "expiresAtEpoch": activated + module.TRIAL_SECONDS if activated else None}}


def envelope(request, proof, outcome, charge, *, prepared=False):
    remaining = 9 if charge else 10
    return {"transportVersion": module.VERSION, "checkId": request["checkId"],
            "operationProof": proof, "state": "prepared" if prepared else "settled",
            "access": {"state": "allowed", "basis": "trial", "externalChecksAllowed": True,
                       "builtInChecksAllowed": True, "reconciliationAllowed": True,
                       "remainingChecks": remaining, "resetsAt": "2027-01-22T08:00:00Z",
                       "unlimited": False, "observedAt": "2027-01-15T08:00:00Z"},
            "accounting": {"state": "not_started" if prepared else ("charged" if charge else "not_charged"),
                           "chargedChecks": 0 if prepared else charge,
                           "receiptId": None if prepared else ("a" if charge else "b") * 32,
                           "requiresReconciliation": False},
            "outcome": None if prepared else outcome, "errorCode": None,
            "retryAfterSeconds": None, "expiresAt": 1_800_000_600}


def projection(outcome, charge, settled, *, receipt=None):
    receipt = receipt or (("a" if charge else "b") * 32)
    stored = {"schemaVersion": 1, "transportVersion": module.HISTORY_VERSION,
              "resultId": f"gh1_{settled}_{receipt}", "resultType": "message",
              "sourceType": "pasted_text", "acceptedAtEpoch": settled - 2,
              "settledAtEpoch": settled, "assessmentTransportVersion": module.VERSION,
              "processingOutcome": outcome["processingOutcome"],
              "accounting": {"state": "charged" if charge else "not_charged",
                             "chargedChecks": charge, "receiptId": receipt},
              "outcome": {key: value for key, value in outcome.items() if key != "checkId"},
              "expiresAtEpoch": settled + module.RETENTION_SECONDS}
    value = present_projection(stored, settled)
    validate_public_projection(value)
    return value


class Fake:
    def __init__(self, *, used=0, fresh=False, delay=True, different_receipt=False,
                 duplicate_on_replay=False, duplicate_same_id=False):
        self.used, self.fresh = used, fresh
        self.delay = delay
        self.different_receipt, self.duplicate_on_replay = different_receipt, duplicate_on_replay
        self.duplicate_same_id = duplicate_same_id
        self.activated = not fresh
        self.requests, self.receipts, self.items = [], {}, []
        self.hidden = 0

    @staticmethod
    def page(items):
        return {"transportVersion": module.HISTORY_VERSION, "serverTimeEpoch": 1_800_000_100,
                "items": list(reversed(items)), "nextCursor": None}

    def __call__(self, method, path, body):
        self.requests.append((method, path, body))
        if (method, path) == ("GET", "/v1/access"):
            return 200, access(self.used, basis="trial" if self.activated else "none", available=not self.activated)
        if (method, path) == ("POST", "/v1/access/trial"):
            assert body == {"schemaVersion": 1, "activate": True}
            self.activated = True
            return 200, access(self.used)
        if method == "GET" and path.startswith("/v1/users/analysis-history?"):
            if self.hidden:
                self.hidden -= 1
                return 200, self.page(self.items[:-1])
            return 200, self.page(self.items)
        if method == "GET" and path.startswith("/v1/users/analysis-history/"):
            result_id = path.rsplit("/", 1)[1]
            [item] = [item for item in self.items if item["resultId"] == result_id]
            return 200, {"transportVersion": module.HISTORY_VERSION, "serverTimeEpoch": 1_800_000_100,
                         "result": item}
        if method == "POST" and path == "/v1/message-checks/prepare":
            assert body["transportVersion"] == module.VERSION
            assert body["target"]["reviewedLinks"] == [] and body["target"]["withheldLinks"] is False
            proof = "v1_k1_6b49d23c_0123456789abcdef_2fe1925f225d8bb70b9ddbc3"
            self.receipts[body["checkId"]] = {"request": body, "proof": proof}
            return 200, envelope(body, proof, None, 0, prepared=True)
        if method == "POST" and path == "/v1/message-checks":
            receipt = self.receipts[body["checkId"]]
            assert body["operationProof"] == receipt["proof"]
            complete = ("password" in body["target"]["sanitizedText"]
                        or "código de acceso" in body["target"]["sanitizedText"])
            fixture = FIXTURES[1 if complete and body["language"] == "es" else (0 if complete else 3)]
            outcome = fixture["expectedOutcome"] | {"checkId": body["checkId"]}
            charge = fixture["expectedChargedChecks"]
            if "response" not in receipt:
                receipt["response"] = envelope(body, receipt["proof"], outcome, charge)
                different = "c" * 32 if self.different_receipt else None
                self.items.append(projection(outcome, charge, 1_800_000_010 + len(self.items), receipt=different))
                self.used += charge
                self.hidden = 1 if self.delay else 0
            elif self.duplicate_on_replay and not receipt.get("duplicated"):
                receipt["duplicated"] = True
                self.items.append(projection(outcome, charge, 1_800_000_010 + len(self.items), receipt="d" * 32))
            elif self.duplicate_same_id and not receipt.get("duplicated"):
                receipt["duplicated"] = True
                self.items.append(dict(self.items[-1]))
            return 200, receipt["response"]
        if method == "POST" and path == "/v1/message-checks/reconcile":
            assert set(body) == {"transportVersion", "checkId", "operationProof"}
            return 200, self.receipts[body["checkId"]]["response"]
        raise AssertionError((method, path))


def client(fake, budget=None):
    return module.Client("https://api-dev.andmorethings.net", {"Authorization": "Bearer " + "a" * 40,
                         "x-device-binding-fingerprint": "fixture-device"}, budget or module.Budget(), request=fake)


@pytest.mark.parametrize("language", ["en", "es"])
def test_exact_bounded_activation_two_results_replay_reconcile_and_reopen(language):
    fake, budget = Fake(fresh=True), module.Budget()
    value = module.Qualification(client(fake, budget), language=language, activate_trial=True,
                                 clock=lambda: 1_800_000_001,
                                 sleep=lambda _: None,
                                 fresh_client=lambda: client(fake, budget)).run()
    assert value["passed"] is True and value["settledResults"] == 2
    assert value["chargedChecks"] == 1 and value["zeroChargeResults"] == 1
    assert value["language"] == language
    assert value["requestCount"] == module.MAX_REQUESTS == 20
    assert value["requestCount"] == len(fake.requests)
    assert sum(1 for method, path, _ in fake.requests if (method, path) == ("POST", "/v1/access/trial")) == 1
    assert all("Authorization" not in json.dumps(item) and "operationProof" not in json.dumps(value)
               for item in [value])


def test_valid_partially_used_trial_is_not_reactivated_or_reset():
    fake, budget = Fake(used=3), module.Budget()
    value = module.Qualification(client(fake, budget), activate_trial=True, clock=lambda: 1_800_000_001,
                                 sleep=lambda _: None, fresh_client=lambda: client(fake, budget)).run()
    assert value["requestCount"] == 19 and fake.used == 4
    assert not any(path == "/v1/access/trial" for _, path, _ in fake.requests)


def test_dedicated_fixture_is_preserved_and_requires_fixed_ttl_cleanup():
    fake, budget = Fake(used=1), module.Budget()
    value = module.Qualification(client(fake, budget), fixture_mode="dedicated_reusable",
                                 clock=lambda: 1_800_000_001, sleep=lambda _: None,
                                 fresh_client=lambda: client(fake, budget)).run()
    assert value["fixtureMode"] == "dedicated_reusable"
    assert value["accountPreserved"] is True
    assert value["accountCleanupRequired"] is False
    assert value["requiresOperationalEvidence"] == [
        "provider_invocations_zero", "rollback_gates_inactive", "fixed_ttl_receipt_expiry"]
    assert not any(path == "/v1/access/trial" for _, path, _ in fake.requests)


def test_dedicated_fixture_rejects_trial_activation_or_reset_choice():
    with pytest.raises(module.Refused, match="QUALIFICATION_UNVERIFIED"):
        module.Qualification(client(Fake(used=1)), fixture_mode="dedicated_reusable",
                             activate_trial=True)


def test_disposable_fixture_retains_account_cleanup_obligation():
    fake, budget = Fake(used=1), module.Budget()
    value = module.Qualification(client(fake, budget), fixture_mode="disposable",
                                 clock=lambda: 1_800_000_001, sleep=lambda _: None,
                                 fresh_client=lambda: client(fake, budget)).run()
    assert value["fixtureMode"] == "disposable"
    assert value["accountPreserved"] is False
    assert value["accountCleanupRequired"] is True
    assert "disposable_account_cleanup" in value["requiresOperationalEvidence"]


def test_one_remaining_check_finishes_with_an_exhausted_valid_snapshot():
    fake, budget = Fake(used=9), module.Budget()
    value = module.Qualification(client(fake, budget), clock=lambda: 1_800_000_001,
                                 sleep=lambda _: None, fresh_client=lambda: client(fake, budget)).run()
    assert value["passed"] is True and fake.used == 10


@pytest.mark.parametrize("used,remaining", [(-1, 11), (11, -1), (3, 8)])
def test_trial_snapshot_rejects_out_of_range_or_inconsistent_allowance(used, remaining):
    value = access(max(0, min(used, 10)))
    value["allowance"]["completedUsed"] = used
    value["allowance"]["remaining"] = remaining
    with pytest.raises(module.Refused, match="QUALIFICATION_UNVERIFIED"):
        module.trial_snapshot(value, now=1_800_000_001)


def test_fresh_trial_requires_explicit_activation():
    fake = Fake(fresh=True)
    with pytest.raises(module.Refused, match="QUALIFICATION_UNVERIFIED"):
        module.Qualification(client(fake), activate_trial=False, clock=lambda: 1_800_000_001).run()
    assert len(fake.requests) == 1


def test_request_budget_fails_before_twenty_first_transport_call():
    calls = []
    budget = module.Budget()
    c = module.Client("https://api-dev.andmorethings.net", {"Authorization": "Bearer " + "a" * 40,
                      "x-device-binding-fingerprint": "fixture-device"}, budget,
                      request=lambda *args: (calls.append(args) or (200, {})))
    for _ in range(module.MAX_REQUESTS):
        c.call("GET", "/v1/access")
    with pytest.raises(module.Refused):
        c.call("GET", "/v1/access")
    assert len(calls) == module.MAX_REQUESTS


@pytest.mark.parametrize("mutation", ["different_receipt", "duplicate_on_replay", "duplicate_same_id"])
def test_accounting_or_duplicate_history_mismatch_fails_closed(mutation):
    fake = Fake(**{mutation: True})
    budget = module.Budget()
    with pytest.raises(module.Refused, match="QUALIFICATION_UNVERIFIED"):
        module.Qualification(client(fake, budget), clock=lambda: 1_800_000_001,
                             sleep=lambda _: None, fresh_client=lambda: client(fake, budget)).run()


@pytest.mark.parametrize("target", [
    "https://other.execute-api.us-east-1.amazonaws.com",
    "https://user@api-dev.andmorethings.net",
    "https://api-dev.andmorethings.net:444",
    "https://api-dev.andmorethings.net/dev",
    "https://icuak34th9.execute-api.us-east-1.amazonaws.com/dev",
    "https://api-dev.andmorethings.net?x=1",
    "https://api-dev.andmorethings.net#fragment",
])
def test_api_target_is_exactly_the_reviewed_dev_surface(target):
    with pytest.raises(module.Refused, match="QUALIFICATION_UNVERIFIED"):
        module.api_target(target)


def test_exact_execute_api_default_stage_is_accepted_without_a_base_path():
    assert module.api_target("https://icuak34th9.execute-api.us-east-1.amazonaws.com/") == (
        "icuak34th9.execute-api.us-east-1.amazonaws.com", "")


@pytest.mark.parametrize("language", ["en", "es"])
def test_each_live_language_case_matches_the_actual_rules_only_evaluator(language):
    for _, text, processing, charge in module.CASES[language]:
        intent = {"entryPoint": "message", "language": language,
                  "target": {"scope": "sanitized_message", "sourceType": "pasted_text",
                             "sanitizedText": text, "speakerRole": "other", "entities": [],
                             "reviewedLinks": [], "withheldLinks": False}}
        value = evaluate("fixture", intent, lookup=lambda *_: pytest.fail("provider called"),
                         ai=lambda *_: pytest.fail("AI called"), rules_only=True)
        assert value["processingOutcome"] == processing
        assert value["aiAssessmentStatus"] == "not_assessed"
        assert int(processing == "complete") == charge


def test_private_input_is_mode_600_and_failure_never_discloses_contents(tmp_path):
    secret = "SENTINEL_PRIVATE_VALUE"
    path = tmp_path / "headers.json"
    path.write_text(json.dumps({"Authorization": secret}))
    path.chmod(0o644)
    with pytest.raises(module.Refused) as caught:
        module.private_headers(path)
    assert secret not in str(caught.value)


def test_preflight_input_validation_has_no_network(tmp_path, monkeypatch, capsys):
    path = tmp_path / "headers.json"
    path.write_text(json.dumps({"Authorization": "Bearer " + "a" * 40,
                                "x-device-binding-fingerprint": "fixture-device"}))
    path.chmod(0o600)
    monkeypatch.setattr("sys.argv", ["qualify", "--api-base", "https://api-dev.andmorethings.net",
                                    "--headers-file", str(path)])
    monkeypatch.setattr(module.http.client, "HTTPSConnection", lambda *_args, **_kwargs: pytest.fail("network"))
    module.main()
    assert json.loads(capsys.readouterr().out) == {"validatedLocally": True, "networkCalls": 0}
