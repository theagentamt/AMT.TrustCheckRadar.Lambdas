"""Existing receipt captures, explicit linkable Dev subset, zero persistence."""
from copy import deepcopy
import json

import pytest

from operations_reporting.reducer import (
    MAX_BATCH_FACTS, ProjectionFault, aggregate_selected_captures, day_bounds,
)
from operations_reporting.revisions import validate_summary
from shared_operations.projection import capture_projection
from shared_operations.receipts import admission_fields


DAY = "2026-10-03"
START, END = day_bounds(DAY)
SCOPE = {"schemaVersion": 1, "population": "selected_dev",
         "generation": "11111111-1111-4111-8111-111111111111",
         "sourceContract": "authority-receipt-capture-v2",
         "coverage": "selected_subset_not_environment_total"}


def capture(*, terminal=False, accepted=START + 10, expiry=END + 60, environment="dev"):
    ids = iter(["1" * 32, "2" * 32, "3" * 32])
    row = {"recordType": "V1_CHECK_RECEIPT", "state": "ADMITTED", "basis": "paid"}
    row.update(admission_fields({"entryPoint": "message", "target": {"sourceType": "ocr"}},
        accepted_at=accepted, receipt_deadline=expiry, access_class="paid",
        environment=environment, random_hex=lambda: next(ids)))
    if terminal:
        row.update(state="SETTLED", processingOutcome="complete", chargedChecks=1,
                   assessmentEpoch=accepted+1, receiptId="4"*32, operationsTerminalReasonCode="NONE")
    return capture_projection(row, now_epoch=accepted+2)


def preview(rows, *, now=END+1, scope=None):
    return aggregate_selected_captures(rows, local_day=DAY, as_of_epoch=now,
                                       source_scope=deepcopy(SCOPE) if scope is None else scope)


def test_original_capture_replay_and_settlement_merge_without_new_fields_or_aliases():
    admitted, settled = capture(), capture(terminal=True)
    before = deepcopy([admitted, settled])
    result = preview([settled, admitted, settled])
    assert result == validate_summary(result)
    assert result["accepted"] == result["settled"]["complete"] == 1
    assert result["byAccessClass"]["paid"]["deductedChecks"] == 1
    assert result["sourceScope"] == SCOPE
    assert result["producerCoverage"] == "not_qualified"
    assert result["deliveryPerformed"] is False
    assert result["requestRejectionsByService"] == {
        "message_check": {}, "url_check": {}, "recovery_clarification": {}}
    assert [admitted, settled] == before
    wire = json.dumps(result)
    for key in ("logicalCheckRef", "acceptedEventId", "terminalEventId"):
        assert admitted["reference"][key] not in wire
    result["sourceScope"]["generation"] = "changed"
    assert SCOPE["generation"] == "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("offset", [0, 1, 100000])
def test_shorter_original_deadline_refuses_even_inside_seven_day_cohort(offset):
    with pytest.raises(ProjectionFault, match="^LINKAGE_EXPIRED$"):
        preview([capture(expiry=END+1)], now=END+1+offset)


def test_no_remint_or_new_clock_and_exact_original_expiry_boundary():
    row = capture(expiry=START+7*86400)
    assert preview([row], now=START+7*86400-1)["pending"] == 1
    with pytest.raises(ProjectionFault, match="^LINKAGE_EXPIRED$"):
        preview([row], now=START+7*86400)
    assert row["reference"]["expiresAtEpoch"] == START+7*86400


@pytest.mark.parametrize("change", ["unknown-field", "invalid-capture", "longer-deadline", "bool-version"])
def test_unapproved_fields_or_capture_reconstruction_refused(change):
    row = capture()
    if change == "unknown-field": row["accountId"] = "PRIVATE_CANARY"
    elif change == "invalid-capture": row["events"][0]["logicalCheckRef"] = "forged"
    elif change == "longer-deadline": row["reference"]["expiresAtEpoch"] = START+8*86400
    else: row["schemaVersion"] = True
    with pytest.raises(ProjectionFault, match="^INVALID_EVENT$") as err:
        preview([row])
    assert str(err.value) == "INVALID_EVENT"


def test_conflicting_same_reference_deadline_and_foreign_environment_refused():
    row = capture(); changed = deepcopy(row); changed["reference"]["expiresAtEpoch"] += 1
    with pytest.raises(ProjectionFault, match="^FACT_CONFLICT$"): preview([row, changed])
    with pytest.raises(ProjectionFault, match="^ENVIRONMENT_MISMATCH$"):
        preview([capture(environment="uat")])


@pytest.mark.parametrize("scope", [None, {}, SCOPE | {"population": "environment_customer",
                        "coverage": "environment_completeness_unqualified"}])
def test_no_implicit_environment_scope(scope):
    with pytest.raises(ProjectionFault, match="^INVALID_ARGUMENT$"):
        aggregate_selected_captures([], local_day=DAY, as_of_epoch=END+1, source_scope=scope)


def test_empty_subset_explicitly_not_environment_total():
    result = preview([])
    assert result["accepted"] == 0 and result["sourceScope"]["coverage"] == "selected_subset_not_environment_total"
    assert all(v["fraction"] is None for v in result["outcomeRates"].values())


def test_bounds_future_capture_and_finite_hostile_error():
    with pytest.raises(ProjectionFault, match="^INVALID_ARGUMENT$"): preview([{}]*(MAX_BATCH_FACTS+1))
    with pytest.raises(ProjectionFault, match="^INVALID_EVENT_TIME$"): preview([capture()], now=START)
    class Hostile:
        def __deepcopy__(self, memo): pytest.fail("copy hook executed")
    row = capture(); row["events"] = Hostile()
    with pytest.raises(ProjectionFault, match="^INVALID_EVENT$"): preview([row])


def test_selected_result_still_cannot_enter_ninety_day_store():
    from operations_reporting.store import AggregateStore, AggregateStoreFault
    from unittest.mock import Mock
    client = Mock(); client.meta.config.retries = {"total_max_attempts": 1}
    store = AggregateStore(client, "existing-table", environment="dev", now=lambda: END+1, source_scope=SCOPE)
    with pytest.raises(AggregateStoreFault, match="^SCOPE_UNQUALIFIED$"): store.commit(preview([capture()]))
    with pytest.raises(AggregateStoreFault, match="^SCOPE_UNQUALIFIED$"): store.read(DAY)
    assert client.mock_calls == []
