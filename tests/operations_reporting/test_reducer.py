import copy
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from operations_reporting.reducer import (
    CORRECTION_SECONDS, MAX_BATCH_FACTS, ProjectionFault,
    aggregate_day, day_bounds, reconcile_rejection_snapshot,
)


FIXTURES = json.loads(Path(__file__).with_name("fixtures.json").read_text())
EVENTS = [x["event"] for x in FIXTURES["events"]]
REJECTIONS = [x["aggregate"] for x in FIXTURES["rejectionAggregates"]]
DAY = "2026-10-03"
AS_OF = day_bounds(DAY)[1] + 60


def run(events=None, rejections=None, **kwargs):
    return aggregate_day(copy.deepcopy(EVENTS if events is None else events),
                         copy.deepcopy(REJECTIONS if rejections is None else rejections),
                         local_day=kwargs.pop("local_day", DAY),
                         environment=kwargs.pop("environment", "dev"),
                         as_of_epoch=kwargs.pop("as_of_epoch", AS_OF), **kwargs)


def named(name):
    return copy.deepcopy(next(x["event"] for x in FIXTURES["events"] if x["name"] == name))


def fault(code, callback):
    with pytest.raises(ProjectionFault) as caught:
        callback()
    assert str(caught.value) == code
    assert caught.value.args == (code,)


def test_approved_fixture_counts_processing_accounting_and_coverage():
    report = run()
    expected = FIXTURES["expectedAggregate"]
    for field in ("accepted", "pending", "settled"):
        assert report[field] == expected[field]
    assert report["accepted"] == report["pending"] + sum(report["settled"].values())
    assert sum(a["deductedChecks"] for a in report["byAccessClass"].values()) == expected["chargedChecks"]
    assert report["byAccessClass"]["complimentary"]["complete"] == 1
    assert report["byAccessClass"]["complimentary"]["deductedChecks"] == 0
    assert sum(a["settlementUnknownChecks"] for a in report["byAccessClass"].values()) == 1
    totals = report["providerAttemptsByFamily"].values()
    assert sum(x["started"] for x in totals) == 3
    assert sum(x["awaitingFinish"] for x in totals) == 1
    assert sum(x["costKnownProviderAttempts"] for x in totals) == 1
    assert sum(x["costUnknownProviderAttempts"] for x in totals) == 2
    assert sum(x["knownProviderRequestUnits"] for x in totals) == 1
    assert sum(x["requestUnitsUnknownProviderAttempts"] for x in totals) == 1
    assert report["requestRejectionsByService"]["message_check"] == {"ALLOWANCE_EXHAUSTED": 1}
    assert report["localIntakeCoverage"] == "not_collected"
    assert report["producerCoverage"] == "not_qualified"
    assert report["deliveryPerformed"] is False


def test_duplicates_and_out_of_order_never_multiply_checks_or_attempts():
    assert run(EVENTS * 3, REJECTIONS * 3) == run()
    assert run(list(reversed(EVENTS))) == run()
    # Explicit settlement-before-admission and finish-before-start delivery.
    for offset in range(len(EVENTS)):
        assert run(EVENTS[offset:] + EVENTS[:offset]) == run()


def test_no_refs_event_ids_or_user_derived_dimensions_in_report():
    report = run()
    wire = json.dumps(report)
    for event in EVENTS:
        for key in ("eventId", "logicalCheckRef", "attemptRef"):
            if event[key] is not None:
                assert event[key] not in wire
    for key in ("eventId", "logicalCheckRef", "attemptRef", "accountId", "payload", "email"):
        assert key not in wire


@pytest.mark.parametrize("outcome", ["complete", "partial", "inconclusive", "failed", "blocked", "invalid_input", "unsupported", "unavailable"])
def test_all_outcomes_keep_accepted_equation(outcome):
    terminal = next(copy.deepcopy(e) for e in EVENTS if e["processingOutcome"] == outcome)
    accepted = next(copy.deepcopy(e) for e in EVENTS if e["logicalCheckRef"] == terminal["logicalCheckRef"] and e["eventType"] == "check_accepted")
    report = run([terminal, accepted], [])
    assert report["accepted"] == 1 and report["pending"] == 0
    assert report["settled"][outcome] == 1 and sum(report["settled"].values()) == 1


def test_missing_authority_fact_never_becomes_an_accepted_check():
    terminal = named("message-complete-charged")
    report = run([terminal], [])
    assert report["accepted"] == 0
    assert report["reconciliationGaps"]["terminalWithoutAccepted"] == 1
    assert all(rate["fraction"] is None for rate in report["outcomeRates"].values())


def test_crash_after_start_is_unknown_cost_and_units_not_zero():
    accepted = named("qr-accepted-paid")
    started = named("qr-google-attempt-started")
    report = run([accepted, started], [])
    google = report["providerAttemptsByFamily"]["google_web_risk"]
    assert google["started"] == google["awaitingFinish"] == 1
    assert google["costUnknownProviderAttempts"] == google["requestUnitsUnknownProviderAttempts"] == 1
    assert google["costKnownProviderAttempts"] == google["requestUnitsKnownProviderAttempts"] == 0


def test_finish_without_start_never_fabricates_dispatch_or_known_cost():
    report = run([named("qr-accepted-paid"), named("qr-google-attempt-cost-unknown-units-known")], [])
    assert report["reconciliationGaps"]["finishWithoutStart"] == 1
    assert report["providerAttemptsByFamily"]["google_web_risk"]["started"] == 0


def test_attempt_without_admission_is_incomplete_not_provider_total():
    report = run([named("qr-google-attempt-started")], [])
    assert report["reconciliationGaps"]["attemptWithoutAccepted"] == 1
    assert report["providerAttemptsByFamily"]["google_web_risk"]["started"] == 0


@pytest.mark.parametrize("change", ["reasonCode", "eventTimeEpoch", "chargedChecks", "eventId"])
def test_conflicting_check_fact_never_last_write_wins(change):
    event = named("message-complete-charged")
    other = copy.deepcopy(event)
    if change == "eventId":
        other[change] = "op1_" + "f" * 32
        expected = "FACT_CONFLICT"
    elif change == "eventTimeEpoch":
        other[change] += 1
        expected = "EVENT_ID_CONFLICT"
    else:
        other[change] = "SERVICE_UNAVAILABLE" if change == "reasonCode" else 0
        expected = "INVALID_EVENT"
    fault(expected, lambda: run([event, other], []))


@pytest.mark.parametrize("field,value", [("accessClass", "paid"), ("inputKind", "ocr")])
def test_terminal_context_must_match_admission(field, value):
    accepted = named("message-accepted-trial")
    terminal = named("message-complete-charged")
    terminal[field] = value
    fault("CHECK_CONTEXT_CONFLICT", lambda: run([accepted, terminal], []))


def test_conflicting_attempt_cost_is_fault_not_double_spend():
    finished = named("qr-google-attempt-cost-unknown-units-known")
    other = copy.deepcopy(finished)
    other["estimatedCostMicrousd"] = 17
    fault("EVENT_ID_CONFLICT", lambda: run([finished, other], []))


def test_attempt_provider_and_owner_cannot_change_at_finish():
    started = named("qr-google-attempt-started")
    finished = named("qr-google-attempt-cost-unknown-units-known")
    finished["logicalCheckRef"] = "lc1_" + "f" * 32
    fault("ATTEMPT_CONTEXT_CONFLICT", lambda: run([started, finished], []))


def test_provider_start_after_authoritative_terminal_is_not_valid_work():
    accepted = named("qr-accepted-paid")
    terminal = named("qr-partial-not-charged")
    started = named("qr-google-attempt-started")
    started["eventTimeEpoch"] = terminal["eventTimeEpoch"] + 1
    fault("INVALID_EVENT_TIME", lambda: run([accepted, terminal, started], []))


def test_one_attempt_ordinal_cannot_hide_another_dispatch():
    accepted = named("qr-accepted-paid")
    started = named("qr-google-attempt-started")
    duplicate = copy.deepcopy(started)
    duplicate["eventId"] = "op1_" + "e" * 32
    duplicate["attemptRef"] = "at1_" + "e" * 32
    fault("ATTEMPT_ORDINAL_CONFLICT", lambda: run([accepted, started, duplicate], []))


@pytest.mark.parametrize("day,hours", [("2026-03-08", 23), ("2026-11-01", 25), ("2026-10-03", 24)])
def test_chicago_dst_half_open_bounds(day, hours):
    start, end = day_bounds(day)
    assert end - start == hours * 3600
    assert datetime.fromtimestamp(start, ZoneInfo("America/Chicago")).isoformat().startswith(day + "T00:00")


def test_late_terminal_corrects_original_cohort_never_finish_day():
    accepted = named("message-accepted-trial")
    terminal = named("message-complete-charged")
    terminal["eventTimeEpoch"] = day_bounds(DAY)[1] + 120
    before = run([accepted], [], as_of_epoch=terminal["eventTimeEpoch"] - 1)
    after = run([terminal, accepted], [], as_of_epoch=terminal["eventTimeEpoch"] + 1)
    assert before["accepted"] == after["accepted"] == 1
    assert before["pending"] == 1 and after["settled"]["complete"] == 1
    next_day = run([terminal, accepted], [], local_day="2026-10-04", as_of_epoch=terminal["eventTimeEpoch"] + 1)
    assert next_day["accepted"] == 0 and sum(next_day["settled"].values()) == 0


def test_late_attempt_finish_stays_in_start_day():
    accepted = named("qr-accepted-paid")
    started = named("qr-google-attempt-started")
    finished = named("qr-google-attempt-cost-unknown-units-known")
    finished["eventTimeEpoch"] = day_bounds(DAY)[1] + 120
    report = run([finished, started, accepted], [], as_of_epoch=finished["eventTimeEpoch"] + 1)
    assert report["providerAttemptsByFamily"]["google_web_risk"]["failed"] == 1
    next_day = run([finished, started, accepted], [], local_day="2026-10-04", as_of_epoch=finished["eventTimeEpoch"] + 1)
    assert next_day["providerAttemptsByFamily"]["google_web_risk"]["started"] == 0


def test_expired_cohort_refuses_recalculation_not_reversing_prior_totals():
    fault("CORRECTION_WINDOW_CLOSED", lambda: run(as_of_epoch=day_bounds(DAY)[0] + CORRECTION_SECONDS))


def test_expired_other_day_linkage_is_rejected():
    accepted = named("message-accepted-trial")
    accepted["eventTimeEpoch"] = day_bounds(DAY)[0] - CORRECTION_SECONDS
    fault("LINKAGE_EXPIRED", lambda: run([accepted], []))


def test_rejection_replay_larger_replacement_and_stale_fault():
    old = REJECTIONS[0]
    assert reconcile_rejection_snapshot(old, old) == old
    new = {**old, "rejectedCount": 5}
    assert reconcile_rejection_snapshot(old, new) == new
    assert old["rejectedCount"] == 1
    fault("STALE_REJECTION", lambda: reconcile_rejection_snapshot(new, old))
    report = run([], [new, new])
    assert report["accepted"] == 0
    assert report["requestRejectionsByService"]["message_check"]["ALLOWANCE_EXHAUSTED"] == 5


def test_rejection_batch_requires_reconciled_head_never_sums_old_and_new():
    old = REJECTIONS[0]
    new = {**old, "rejectedCount": 5}
    for rows in ([old, new], [new, old]):
        fault("REJECTION_CONTEXT_CONFLICT", lambda: run([], rows))
    changed = {**old, "service": "url_check"}
    fault("REJECTION_CONTEXT_CONFLICT", lambda: reconcile_rejection_snapshot(old, changed))


@pytest.mark.parametrize("field", list(EVENTS[0]) + ["payload", "subject", "tags", "email"])
def test_hostile_value_in_every_event_field_is_fixed_failure_without_echo(field, caplog, capsys):
    event = copy.deepcopy(EVENTS[0])
    event[field] = "PRIVATE_SENTINEL raw-url token user@invalid.test"
    fault("INVALID_EVENT", lambda: run([event], []))
    assert caplog.text == ""
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("field", list(REJECTIONS[0]) + ["eventId", "logicalCheckRef", "accessClass", "timestamp", "token"])
def test_rejected_request_has_no_linkable_extension(field):
    event = {**REJECTIONS[0], field: "PRIVATE_SENTINEL"}
    fault("INVALID_REJECTION", lambda: run([], [event]))


@pytest.mark.parametrize("field,value", [("chargedChecks", True), ("lifecycleRevision", True), ("eventTimeEpoch", True), ("eventTimeEpoch", float("nan")), ("eventTimeEpoch", -1)])
def test_invalid_numbers_and_boolean_integers_rejected(field, value):
    event = {**EVENTS[0], field: value}
    fault("INVALID_EVENT", lambda: run([event], []))


def test_bounded_batch_and_large_injected_value():
    fault("BATCH_BOUND_EXCEEDED", lambda: run([EVENTS[0]] * (MAX_BATCH_FACTS + 1), []))
    fault("INVALID_EVENT", lambda: run([{**EVENTS[0], "payload": "x" * 10000}], []))


@pytest.mark.parametrize("kwargs", [{"environment": "unknown"}, {"as_of_epoch": True}, {"local_day": "20261003"}, {"local_day": "9999-12-31"}, {"as_of_epoch": 0}])
def test_invalid_report_arguments(kwargs):
    fault("INVALID_ARGUMENT", lambda: run(**kwargs))


def test_cross_environment_and_future_fact_fail_closed():
    fault("ENVIRONMENT_MISMATCH", lambda: run([{**EVENTS[0], "environment": "prod"}], []))
    fault("INVALID_EVENT_TIME", lambda: run([{**EVENTS[0], "eventTimeEpoch": AS_OF + 1}], []))
    terminal = named("message-complete-charged")
    terminal["eventTimeEpoch"] = EVENTS[0]["eventTimeEpoch"] - 1
    fault("INVALID_EVENT_TIME", lambda: run([EVENTS[0], terminal], []))


def test_rules_only_internal_component_not_provider_cost_coverage():
    accepted = named("message-accepted-trial")
    started = named("qr-google-attempt-started")
    started.update(logicalCheckRef=accepted["logicalCheckRef"], service="message_check", inputKind="pasted_text",
                   component="message_evaluator", attemptKind="internal_component", providerFamily=None)
    report = run([accepted, started], [])
    assert report["internalAttempts"]["awaitingFinish"] == 1
    assert sum(p["started"] for p in report["providerAttemptsByFamily"].values()) == 0


def test_unknown_outcome_cancellation_and_builtin_selector_not_check_facts():
    for outcome in ("cancelled", "not_an_analysis_check", "unknown"):
        event = {**named("message-complete-charged"), "processingOutcome": outcome}
        fault("INVALID_EVENT", lambda: run([event], []))


def test_no_service_client_handler_or_publisher_dependency():
    import ast
    import operations_reporting.reducer as reducer
    tree = ast.parse(Path(reducer.__file__).read_text())
    imports = [node.module if isinstance(node, ast.ImportFrom) else name.name
               for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
               for name in ([None] if isinstance(node, ast.ImportFrom) else node.names)]
    assert not {"boto3", "botocore", "requests", "urllib", "socket", "subprocess", "logging"}.intersection(imports)
    assert not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ("handler", "lambda_handler") for n in ast.walk(tree))


def test_existing_builder_refuses_new_library_as_a_runtime_function(tmp_path):
    import subprocess
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["bash", str(root / "scripts/build_lambda_zip.sh"),
                             "--function", "operations_reporting", "--skip-dependencies",
                             "--output-dir", str(tmp_path / "output")],
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "unknown function" in result.stderr
    assert not list(tmp_path.rglob("*.zip"))
