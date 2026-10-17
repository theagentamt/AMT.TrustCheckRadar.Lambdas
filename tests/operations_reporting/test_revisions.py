from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from operations_reporting.reducer import aggregate_day, day_bounds
from operations_reporting.revisions import (
    CORRECTION_SECONDS, RETENTION_SECONDS, RevisionFault,
    plan_revision, validate_summary,
)


FIXTURE = json.loads(Path(__file__).with_name("fixtures.json").read_text())
EVENTS = [x["event"] for x in FIXTURE["events"]]
REJECTIONS = [x["aggregate"] for x in FIXTURE["rejectionAggregates"]]
DAY = "2026-10-03"
START, END = day_bounds(DAY)
FIRST = END + 60
NEXT_DAY = END + 86400 + 60


def summary(events=None, as_of=FIRST):
    return aggregate_day(deepcopy(EVENTS if events is None else events), deepcopy(REJECTIONS),
                         local_day=DAY, environment="dev", as_of_epoch=as_of)


def settled_pending():
    pending = next(deepcopy(e["event"]) for e in FIXTURE["events"] if e["name"] == "pending-check-accepted")
    terminal = next(deepcopy(e) for e in EVENTS if e["eventType"] == "check_terminal" and e["processingOutcome"] == "complete")
    for field in ("service", "inputKind", "logicalCheckRef", "accessClass"):
        terminal[field] = pending[field]
    terminal.update(eventId="op1_" + "f" * 32, eventTimeEpoch=FIRST + 3600,
                    accountingState="charged" if pending["accessClass"] != "complimentary" else "not_charged",
                    chargedChecks=0 if pending["accessClass"] == "complimentary" else 1)
    return summary([*EVENTS, terminal], NEXT_DAY)


def revision():
    return plan_revision(None, summary(), generated_at_epoch=FIRST)["record"]


def fault(code, fn):
    with pytest.raises(RevisionFault) as caught:
        fn()
    assert str(caught.value) == code


def rehash(record):
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    record["reportSha256"] = digest(record["report"])
    record["recordSha256"] = digest({k: v for k, v in record.items() if k != "recordSha256"})


def test_kernel_summary_is_valid_and_inputs_are_not_mutated():
    report = summary()
    before = deepcopy(report)
    assert validate_summary(report) == report
    first = plan_revision(None, report, generated_at_epoch=FIRST)
    assert report == before
    record = first["record"]
    assert first["decision"] == "CREATE_REVISION"
    assert first["expectedPreviousSha256"] is None
    assert record["revision"] == 1 and record["previousRecordSha256"] is None
    assert record["expiresAtEpoch"] == START + RETENTION_SECONDS
    assert record["numericDelta"]["/accepted"] == 10
    assert record["report"]["producerCoverage"] == "not_qualified"
    assert record["report"]["deliveryPerformed"] is False


def test_replay_and_later_identical_counts_never_renew_expiry_or_emit_revision():
    old = revision()
    before = deepcopy(old)
    same = plan_revision(old, summary(as_of=NEXT_DAY), generated_at_epoch=NEXT_DAY)
    assert same["decision"] == "NO_CHANGE"
    assert same["record"] == old == before
    assert same["record"]["generatedAtEpoch"] == FIRST
    assert same["record"]["expiresAtEpoch"] == START + RETENTION_SECONDS


def test_late_settlement_changes_original_cohort_once_next_day_with_delta():
    old = revision()
    after = plan_revision(old, settled_pending(), generated_at_epoch=NEXT_DAY)
    record = after["record"]
    assert after["decision"] == "CREATE_REVISION"
    assert after["expectedPreviousSha256"] == old["recordSha256"]
    assert record["previousRecordSha256"] == old["recordSha256"] and record["revision"] == 2
    assert record["report"]["localDate"] == DAY
    assert record["numericDelta"]["/pending"] == -1
    assert record["numericDelta"]["/settled/complete"] == 1
    assert "/accepted" not in record["numericDelta"]
    assert record["expiresAtEpoch"] == old["expiresAtEpoch"]
    assert plan_revision(record, settled_pending(), generated_at_epoch=NEXT_DAY)["decision"] == "NO_CHANGE"


def test_same_local_day_corrections_defer_and_coalesce_without_mutating_previous():
    old = revision()
    current = settled_pending()
    current["asOfEpoch"] = FIRST + 7200
    after = plan_revision(old, current, generated_at_epoch=FIRST + 7200)
    assert after["decision"] == "DEFER_TO_NEXT_DAILY" and after["record"] == old
    next_run = plan_revision(old, settled_pending(), generated_at_epoch=NEXT_DAY)
    assert next_run["record"]["revision"] == 2


def test_concurrent_plans_expose_same_cas_precondition_not_a_durable_guarantee():
    old = revision()
    left = plan_revision(old, settled_pending(), generated_at_epoch=NEXT_DAY)
    right = plan_revision(old, settled_pending(), generated_at_epoch=NEXT_DAY + 1)
    assert left["expectedPreviousSha256"] == right["expectedPreviousSha256"] == old["recordSha256"]
    assert left["record"]["revision"] == right["record"]["revision"] == 2
    assert left["record"]["recordSha256"] != right["record"]["recordSha256"]


def test_losing_projection_facts_cannot_reverse_already_counted_aggregate():
    old = revision()
    current = summary([], as_of=NEXT_DAY)
    fault("AGGREGATE_REGRESSION", lambda: plan_revision(old, current, generated_at_epoch=NEXT_DAY))


def test_losing_known_cost_or_provider_start_is_not_unknown_or_zero_correction():
    old = revision()
    removed = [e for e in EVENTS if not e["eventType"].startswith("attempt_")]
    fault("AGGREGATE_REGRESSION", lambda: plan_revision(old, summary(removed, NEXT_DAY), generated_at_epoch=NEXT_DAY))


def test_waiting_attempt_enrichment_can_reduce_unknown_coverage():
    accepted = next(deepcopy(e) for e in EVENTS if e["eventType"] == "check_accepted" and e["service"] == "url_check")
    started = next(deepcopy(e) for e in EVENTS if e["eventType"] == "attempt_started" and e["logicalCheckRef"] == accepted["logicalCheckRef"])
    finish = next(deepcopy(e) for e in EVENTS if e["eventType"] == "attempt_finished" and e["attemptRef"] == started["attemptRef"])
    finish.update(providerRequestUnits=1, estimatedCostMicrousd=17)
    before = summary([accepted, started])
    old = plan_revision(None, before, generated_at_epoch=FIRST)["record"]
    after = plan_revision(old, summary([accepted, started, finish], NEXT_DAY), generated_at_epoch=NEXT_DAY)
    assert after["decision"] == "CREATE_REVISION"
    assert after["record"]["numericDelta"]["/providerAttemptsByFamily/google_web_risk/costUnknownProviderAttempts"] == -1
    assert after["record"]["numericDelta"]["/providerAttemptsByFamily/google_web_risk/knownEstimatedCostMicrousd"] == 17


@pytest.mark.parametrize("field", ["subject", "accountId", "email", "token", "providerResponse", "tags"])
def test_no_linkable_or_arbitrary_field_can_enter_summary_or_revision(field, caplog, capsys):
    report = {**summary(), field: "PRIVATE_SENTINEL"}
    fault("INVALID_SUMMARY", lambda: validate_summary(report))
    old = {**revision(), field: "PRIVATE_SENTINEL"}
    fault("INVALID_PREVIOUS_REVISION", lambda: plan_revision(old, summary(), generated_at_epoch=FIRST))
    assert caplog.text == "" and capsys.readouterr().out == ""


@pytest.mark.parametrize("field,value", [("accepted", 11), ("pending", 0), ("windowEndEpoch", END + 1), ("timezone", "UTC"), ("producerCoverage", "complete"), ("deliveryPerformed", True)])
def test_changed_denominator_or_fabricated_coverage_rejected(field, value):
    fault("INVALID_SUMMARY", lambda: validate_summary({**summary(), field: value}))


@pytest.mark.parametrize("field", ["reportSha256", "recordSha256", "expiresAtEpoch", "revision", "generatedAtEpoch", "plannedRunLocalDate"])
def test_previous_integrity_and_fixed_original_expiry(field):
    old = revision()
    if isinstance(old[field], int):
        old[field] += 1
    else:
        old[field] = "f" * 64
    fault("INVALID_PREVIOUS_REVISION", lambda: plan_revision(old, settled_pending(), generated_at_epoch=NEXT_DAY))


def test_rehashed_retention_extension_still_rejected():
    old = revision()
    old["expiresAtEpoch"] += 86400
    rehash(old)
    fault("INVALID_PREVIOUS_REVISION", lambda: plan_revision(old, settled_pending(), generated_at_epoch=NEXT_DAY))


def test_delta_cannot_store_arbitrary_metric_dimension_or_text():
    old = revision()
    old["numericDelta"]["/account/PRIVATE_SENTINEL"] = 1
    rehash(old)
    fault("INVALID_PREVIOUS_REVISION", lambda: plan_revision(old, settled_pending(), generated_at_epoch=NEXT_DAY))


def test_summary_non_numeric_and_nested_identity_injections():
    for section in ("byAccessClass", "providerAttemptsByFamily", "terminalReasons", "reconciliationGaps"):
        report = summary()
        report[section]["PRIVATE_SENTINEL"] = "PRIVATE_SENTINEL"
        fault("INVALID_SUMMARY", lambda: validate_summary(report))
    report = summary()
    report["outcomeRates"]["complete"]["fraction"] = True
    fault("INVALID_SUMMARY", lambda: validate_summary(report))


def test_pending_unknown_accounting_cannot_be_relabelled_known_zero():
    report = summary()
    pending = next(a for a in report["byAccessClass"].values() if a["settlementUnknownChecks"])
    pending["settlementUnknownChecks"] -= 1
    pending["noDeductionChecks"] += 1
    fault("INVALID_SUMMARY", lambda: validate_summary(report))


def test_no_known_subtotal_without_known_cost_or_units_coverage():
    report = summary()
    report["providerAttemptsByFamily"]["openai"]["knownEstimatedCostMicrousd"] = 1
    fault("INVALID_SUMMARY", lambda: validate_summary(report))


def test_expiry_and_correction_boundaries_are_not_renewable():
    report = summary()
    fault("CORRECTION_WINDOW_CLOSED", lambda: plan_revision(None, report, generated_at_epoch=START + CORRECTION_SECONDS))
    fault("AGGREGATE_EXPIRED", lambda: plan_revision(None, report, generated_at_epoch=START + RETENTION_SECONDS))
    fault("INVALID_RUN", lambda: plan_revision(None, report, generated_at_epoch=True))
    fault("INVALID_RUN", lambda: plan_revision(None, report, generated_at_epoch=FIRST - 1))


def test_partial_day_is_not_a_daily_report():
    report = summary(as_of=END - 1)
    fault("INVALID_SUMMARY", lambda: validate_summary(report))


def test_stale_snapshot_or_mismatched_cohort_does_not_update_previous():
    old = revision()
    report = summary()
    report["asOfEpoch"] -= 1
    fault("STALE_SNAPSHOT", lambda: plan_revision(old, report, generated_at_epoch=FIRST))
    report = summary()
    report["environment"] = "prod"
    fault("INVALID_PREVIOUS_REVISION", lambda: plan_revision(old, report, generated_at_epoch=FIRST))


def test_output_contains_only_aggregate_numbers_not_event_or_attempt_refs():
    wire = json.dumps(plan_revision(None, summary(), generated_at_epoch=FIRST))
    for event in EVENTS:
        for key in ("eventId", "logicalCheckRef", "attemptRef"):
            if event[key] is not None:
                assert event[key] not in wire


def test_egress_validation_uses_one_serialized_snapshot(monkeypatch):
    import operations_reporting.revisions as r
    value = summary()
    original = r._canonical
    calls = []
    def snapshot_once(data):
        wire = original(data)
        calls.append(True)
        data["accountId"] = "PRIVATE_SENTINEL"
        return wire
    monkeypatch.setattr(r, "_canonical", snapshot_once)
    clean = r.validate_summary(value)
    assert len(calls) == 1 and "accountId" not in clean


def test_event_validation_checks_the_same_bytes_it_later_reduces(monkeypatch):
    import operations_reporting.reducer as r
    event = deepcopy(EVENTS[0])
    event["email"] = "PRIVATE_SENTINEL"
    original = r._canonical
    def capture_then_remove(data):
        wire = original(data)
        data.pop("email", None)
        return wire
    monkeypatch.setattr(r, "_canonical", capture_then_remove)
    with pytest.raises(r.ProjectionFault, match="^INVALID_EVENT$"):
        aggregate_day([event], [], local_day=DAY, environment="dev", as_of_epoch=FIRST)
