"""Actual local Moto capture-to-preview path; no actor/service or new store."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_operations_ingestion import world, fresh, WORKER
from operations_reporting.reducer import aggregate_selected_captures, ProjectionFault


SCOPE = {"schemaVersion": 1, "population": "selected_dev",
         "generation": "11111111-1111-4111-8111-111111111111",
         "sourceContract": "authority-receipt-capture-v2",
         "coverage": "selected_subset_not_environment_total"}


def calculate(captured, now):
    day = datetime.fromtimestamp(captured["reference"]["acceptedAtEpoch"], ZoneInfo("America/Chicago")).date().isoformat()
    return aggregate_selected_captures([captured], local_day=day,
                                       as_of_epoch=now, source_scope=SCOPE)


def test_actual_guarded_capture_aggregates_without_any_additional_write_or_charge(world, monkeypatch):
    authority, _, _, row, _, clock = world
    ingest, proof, operation = fresh(world)
    ingest.start_attempt(WORKER, proof, operation["executionToken"])
    captured = ingest.capture(WORKER, proof)["projection"]
    pending = calculate(captured, clock[0])
    assert pending["accepted"] == pending["pending"] == 1
    assert pending["internalAttempts"]["awaitingFinish"] == 1
    authority.settle(WORKER, proof, operation["executionToken"], "complete")
    attempt = next(e["attemptRef"] for e in captured["events"] if e["eventType"] == "attempt_started")
    captured = ingest.finish_attempt(WORKER, proof, operation["executionToken"], attempt,
                                    outcome="succeeded", duration_ms=10)["projection"]
    before = deepcopy(authority.ddb.Table("authority").scan()["Items"])
    monkeypatch.setattr(authority.client, "transact_write_items", lambda **kw: pytest.fail("preview wrote"))
    report = calculate(captured, clock[0])
    assert report["accepted"] == report["settled"]["complete"] == 1
    assert report["internalAttempts"]["succeeded"] == 1
    assert sum(p["started"] for p in report["providerAttemptsByFamily"].values()) == 0
    assert authority.ddb.Table("authority").scan()["Items"] == before
    assert row("PERIOD#p1")["usedChecks"] == 1


def test_preview_deadline_is_existing_receipt_deadline_and_retirement_owns_erasure(world):
    _, _, _, row, _, clock = world
    ingest, proof, _ = fresh(world)
    captured = ingest.capture(WORKER, proof)["projection"]
    deadline = captured["reference"]["expiresAtEpoch"]
    clock[0] = deadline
    with pytest.raises(ProjectionFault, match="^LINKAGE_EXPIRED$"):
        calculate(captured, clock[0])
    assert ingest.capture(WORKER, proof)["projection"] is None
    remaining = row("CHECK#" + proof)
    assert "operationsProjection" not in remaining and "operationsReceipt" not in remaining
