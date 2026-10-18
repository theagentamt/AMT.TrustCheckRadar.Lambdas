"""Technical contract consistency only, NOT a production mutation adapter."""
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "contracts/governed-history-mutation/2.0.0-candidate.1"
SCHEMAS = json.loads((PACKAGE / "schemas.json").read_text())
CONTRACT = json.loads((PACKAGE / "contract.json").read_text())
POLICY = json.loads((PACKAGE / "policy-decision.json").read_text())
FIXTURES = json.loads((PACKAGE / "fixtures.json").read_text())["cases"]


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: case["name"])
def test_closed_request_draft(case):
    schema = SCHEMAS[case["schema"]]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    if case["valid"]:
        validator.validate(case["value"])
    else:
        with pytest.raises(ValidationError):
            validator.validate(case["value"])


def test_no_new_production_authority_or_legacy_contract_inheritance():
    assert CONTRACT["executionAuthorized"] is False
    assert CONTRACT["productionImplementationAdded"] is False
    assert CONTRACT["existingContractsModified"] is False
    assert POLICY["executionAuthorized"] is False
    assert POLICY["status"] == "OWNER_DECISION_REQUIRED_NOT_APPROVAL"
    assert POLICY["minimalRecommendedBundle"]["approvalRequired"] is True
    assert CONTRACT["compatibility"]["newApiPaths"] == []
    assert CONTRACT["compatibility"]["newHeaders"] == []
    assert CONTRACT["compatibility"]["newQueryParameters"] == []
    legacy = json.loads((ROOT / "contracts/history/v1/contract-set.json").read_text())
    existing = {(r["method"], r["path"]) for r in legacy["routes"]}
    assert {(r["method"], r["path"]) for r in CONTRACT["routes"]} <= existing
    assert all(x["status"] == "HELD" for x in POLICY["recognitionLaterBundle"])


def test_numeric_candidate_does_not_confuse_active_backup_or_approved_lifetimes():
    bounds = POLICY["candidateBounds"]
    assert bounds["controlAdmissionHoldSeconds"] == (
        bounds["governedAssessmentSeconds"] + bounds["maxHistoryProducingLeaseSeconds"]
        + bounds["activePurgeSlaSeconds"])
    assert bounds["controlAdmissionHoldSeconds"] == 691680
    assert bounds["operationReceiptSeconds"] == bounds["backupPitrMaximumSeconds"] == 604800
    assert bounds["controlAdmissionHoldSeconds"] != bounds["backupPitrMaximumSeconds"]
    assert "doNotInfer" in POLICY and "notCoveredByExistingPolicy" in POLICY
    assert CONTRACT["operations"]["intentLease"]["retainedAtIssue"] is False
    assert CONTRACT["operations"]["intentLease"]["leaseSeconds"] == 600


def test_package_hashes_match_and_are_not_a_dispatch_manifest():
    entries = (PACKAGE / "SHA256SUMS").read_text().splitlines()
    expected = {line.split("  ")[1]: line.split("  ")[0] for line in entries}
    assert set(expected) == {"README.md", "contract.json", "schemas.json", "policy-decision.json", "fixtures.json"}
    for name, value in expected.items():
        assert hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest() == value
    assert not any(path.suffix == ".py" for path in PACKAGE.iterdir())


class ContractSpecification:
    """Small model of selected laws, not executable service/storage code."""

    def __init__(self):
        self.now, self.restore = 0, "external-current"
        self.state = ("incarnation-one", 0)
        self.receipts, self.projections, self.summaries, self.accounting = {}, {}, {}, set()
        self.incarnation_counter = 1
        self.control_expired = False

    def issue(self, operation, action="clear"):
        return {"operation": operation, "action": action, "expected": self.state,
                "restore": self.restore, "expires": self.now + 600}

    def mutate(self, lease, *, explicit_start=False):
        identity = lease["operation"]
        prior = self.receipts.get(identity)
        if prior and prior["until"] > self.now:
            if prior["intent"] != lease:
                raise ValueError("INTENT_CONFLICT")
            return prior["result"]
        if lease["expires"] <= self.now or lease["restore"] != self.restore:
            raise ValueError("EXPIRED_OR_RESTORE")
        if lease["expected"] != self.state:
            raise ValueError("STALE_STATE")
        if lease["action"] == "clear":
            if self.state is None:
                raise ValueError("NO_STATE")
            self.state = (self.state[0], self.state[1] + 1)
        elif lease["action"] == "start_fresh":
            if not explicit_start:
                raise ValueError("NOT_EXPLICIT")
            if self.state is not None and not self.control_expired:
                raise ValueError("LIVE_STATE")
            self.incarnation_counter += 1
            self.state = (f"incarnation-{self.incarnation_counter}", 0)
            self.control_expired = False
        else:
            raise ValueError("UNSUPPORTED")
        result = ("INVALIDATION_COMMITTED_NOT_PURGE_SUCCESS", self.state)
        self.receipts[identity] = {"intent": deepcopy(lease), "result": result,
                                   "until": self.now + 604800}
        return result

    def settle(self, check, admitted_state):
        self.accounting.add(check)
        if admitted_state == self.state and self.state is not None:
            self.projections.setdefault(check, admitted_state)
            self.summaries.setdefault(check, admitted_state)

    def visible(self, rows):
        return {k for k, state in rows.items() if state == self.state and self.state is not None}


@pytest.mark.parametrize("when", [601, 604801, 1200000])
@pytest.mark.parametrize("missing_control", [False, True])
def test_expired_clear_or_startfresh_never_reexecutes_after_receipt_retirement(when, missing_control):
    model = ContractSpecification()
    old = model.issue("server-issued-one")
    original = model.mutate(old)
    model.now = when
    if missing_control:
        model.state = None
    if when < 604800:
        assert model.mutate(old) == original  # read original commit only
    else:
        with pytest.raises(ValueError, match="EXPIRED_OR_RESTORE"):
            model.mutate(old)
    # No old ABSENT StartFresh can become a new action after its evidence expires.
    model.state = None
    fresh = model.issue("another-server-issued", "start_fresh")
    model.mutate(fresh, explicit_start=True)
    model.now += 604801
    model.state = None
    with pytest.raises(ValueError, match="EXPIRED_OR_RESTORE"):
        model.mutate(fresh, explicit_start=True)


@pytest.mark.parametrize("order", list(itertools.permutations(["clear", "settle"])))
def test_clear_and_settlement_fence_both_public_copies_without_resetting_accounting(order):
    model = ContractSpecification()
    admitted = model.state
    lease = model.issue("clear-one")
    for action in order:
        if action == "clear": model.mutate(lease)
        else: model.settle("accepted-check", admitted)
    model.settle("accepted-check", admitted)
    assert model.visible(model.projections) == model.visible(model.summaries) == set()
    assert model.accounting == {"accepted-check"}


def test_same_operation_intent_conflict_and_lost_ack_readback_are_not_new_effects():
    model = ContractSpecification()
    lease = model.issue("one")
    original = model.mutate(lease)
    assert model.mutate(lease) == original
    assert model.state[1] == 1
    with pytest.raises(ValueError, match="INTENT_CONFLICT"):
        model.mutate(dict(lease, action="start_fresh"), explicit_start=True)
    assert model.state[1] == 1


def test_missing_control_does_not_prove_retirement_and_startfresh_is_explicit():
    model = ContractSpecification()
    old_state = model.state
    model.settle("old", old_state)
    model.state = None
    with pytest.raises(ValueError, match="NO_STATE"):
        model.mutate(model.issue("clear"))
    lease = model.issue("fresh", "start_fresh")
    with pytest.raises(ValueError, match="NOT_EXPLICIT"):
        model.mutate(lease)
    model.mutate(lease, explicit_start=True)
    assert model.state[0] != old_state[0]
    assert model.visible(model.projections) == model.visible(model.summaries) == set()


def test_restoring_controls_and_receipts_together_cannot_restore_external_authority():
    model = ContractSpecification()
    old_lease = model.issue("old")
    restored_state = model.state
    model.restore = "NEW-external-restore-epoch"
    model.state = restored_state  # rolled-back row alone is not current proof
    with pytest.raises(ValueError, match="EXPIRED_OR_RESTORE"):
        model.mutate(old_lease)
    # The model does not claim physical purge or opening traffic. Both are gates.


def test_startfresh_cannot_replace_live_control_but_explicit_known_expired_can():
    model = ContractSpecification()
    lease = model.issue("new", "start_fresh")
    with pytest.raises(ValueError, match="LIVE_STATE"):
        model.mutate(lease, explicit_start=True)
    old = model.state
    model.control_expired = True
    model.mutate(lease, explicit_start=True)
    assert model.state[0] != old[0]
