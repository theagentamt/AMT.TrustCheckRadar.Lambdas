from dataclasses import replace
import base64
from decimal import Decimal
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/shared_check_authority")]

from governed_history.service import GovernedHistoryError, Service, Settings
from message_evaluator.policy_v3 import evaluate
from shared_check_authority.core import TrustedWorkerContext
from shared_governed_history import RETENTION_SECONDS, VERSION
from shared_message_contract.validation_v3 import VERSION as MESSAGE_VERSION
from url_consumer.service import FRESH_VERSION as URL_VERSION
from test_transactions import ACCOUNT, world


def settings():
    return Settings("GSI2", 900, RETENTION_SECONDS, 20, 50, 262144)


def payload(text):
    return {"entryPoint": "message", "language": "en", "messageTransportVersion": MESSAGE_VERSION,
            "target": {"scope": "sanitized_message", "sourceType": "pasted_text", "sanitizedText": text,
                       "speakerRole": "other", "entities": [], "withheldLinks": False, "reviewedLinks": []}}


def add(authority, event, client, text):
    request = payload(text)
    proof = authority.prepare(event, request, client, client_check_id=client)
    admitted = authority.admit(event, request, proof, client_check_id=client)
    outcome = evaluate(client, {k: request[k] for k in ("entryPoint", "language", "target")}, rules_only=True)
    authority.settle(TrustedWorkerContext(ACCOUNT), proof, admitted["executionToken"],
                     outcome["processingOutcome"], result_summary=outcome)
    return proof


def add_url(authority, event, client):
    request = {"entryPoint": "standalone_url", "language": "en", "urlTransportVersion": URL_VERSION,
               "target": {"url": "https://example.com/", "scope": "full_url", "withheldComponents": []}}
    proof = authority.prepare(event, request, client, client_check_id=client)
    admitted = authority.admit(event, request, proof, client_check_id=client)
    outcome = {"schemaVersion": 2, "checkId": client, "verdict": "high_risk", "processingOutcome": "complete",
               "coverage": "supported_checks_complete", "reasonCodes": ["KNOWN_THREAT_MATCH"],
               "transportWarnings": [], "threatTypes": ["MALWARE"], "lookupCount": 1, "providerCallCount": 1,
               "observedHopCount": 1, "scope": "HTTP_REDIRECTS_AND_GOOGLE_LOOKUP", "consumerAccessEnabled": False,
               "lookupObservedAt": "2027-01-15T08:00:00Z", "lookupValidUntil": "2027-01-15T08:00:20Z"}
    authority.settle(TrustedWorkerContext(ACCOUNT), proof, admitted["executionToken"], "complete", result_summary=outcome)
    return proof


def service(world, *, fingerprint="device-one", device_version=1):
    authority, _, _, _, _, clock = world
    return Service(authority, settings(), account=ACCOUNT, fingerprint=fingerprint,
                   device_version=device_version, inventory_revision=1, now=lambda: clock[0])


def enable(world):
    authority = world[0]
    authority.s = replace(authority.s, receipt_retention_seconds=RETENTION_SECONDS,
                          counter_retention_seconds=RETENTION_SECONDS,
                          governed_history_settlement_enabled=True)


def test_list_detail_and_cursor_are_content_free_account_bound_and_stable(world):
    enable(world)
    authority, event, _, row, _, clock = world
    first_proof = add(authority, event, "first", "Send me your account password and the login code.")
    clock[0] += 1
    second_proof = add(authority, event, "second", "A request outside qualified coverage.")
    api = service(world)
    page = api.list(requested_limit="1")
    assert page["transportVersion"] == VERSION and len(page["items"]) == 1 and page["nextCursor"]
    assert page["items"][0]["processingOutcome"] == "inconclusive"
    assert "checkId" not in page["items"][0]["outcome"]
    next_page = api.list(cursor=page["nextCursor"], requested_limit="1")
    assert len(next_page["items"]) == 1
    detail = api.detail(next_page["items"][0]["resultId"])
    assert detail["result"] == next_page["items"][0]
    combined = str(page) + str(next_page)
    assert "account password" not in combined and first_proof not in combined and second_proof not in combined
    before = row("CHECK#" + first_proof)
    api.detail(detail["result"]["resultId"])
    assert row("CHECK#" + first_proof) == before

    with pytest.raises(GovernedHistoryError, match="INVALID_CURSOR"):
        service(world, fingerprint="other-device").list(cursor=page["nextCursor"])


def test_cursor_duplicate_keys_and_type_tampering_fail_closed(world):
    enable(world)
    authority, _, _, _, _, _ = world
    api = service(world)
    raw = (b'{"schemaVersion":1,"schemaVersion":1,"transportVersion":"' + VERSION.encode()
           + b'","keyId":"k1","accountBinding":"x","deviceBinding":"x",'
             b'"deviceVersion":1,"inventoryRevision":1,"before":"GOVERNED#000000000001#'
             + b'0' * 32 + b'","expiresAtEpoch":999999999999}')
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    token = "ghc1." + encoded + "." + authority._mac("k1", "governed-history-cursor", encoded)
    with pytest.raises(GovernedHistoryError, match="INVALID_CURSOR"):
        api.list(cursor=token)


def test_index_query_requests_only_sparse_projection_before_canonical_read(world, monkeypatch):
    enable(world)
    authority, event, _, _, _, _ = world
    add(authority, event, "projected", "A request outside qualified coverage.")
    table = authority.ddb.Table("authority")
    table_factory = authority.ddb.Table
    original = table.query
    projections, selections = [], []

    def query(**kwargs):
        projections.append(kwargs["ProjectionExpression"])
        selections.append(kwargs["Select"])
        return original(**kwargs)

    monkeypatch.setattr(table, "query", query)
    monkeypatch.setattr(authority.ddb, "Table", lambda name: table if name == "authority" else table_factory(name))
    assert service(world).list()["items"]
    assert projections == [Service.INDEX_PROJECTION] * len(authority.deletion_partitions(ACCOUNT))
    assert selections == ["SPECIFIC_ATTRIBUTES"] * len(projections)
    assert "governedHistoryDigest" not in Service.INDEX_PROJECTION
    assert "retentionDeadlineEpoch" not in Service.INDEX_PROJECTION


def test_dense_stale_multi_partition_pages_bound_canonical_reads_and_continue_without_skips(world, monkeypatch):
    enable(world)
    api = service(world)
    api.partitions = tuple(f"partition-{index}" for index in range(4))
    candidates = {}
    for partition_index, partition in enumerate(api.partitions):
        rows = []
        for row_index in range(6):
            order = 1000 - (row_index * 4 + partition_index)
            rows.append({"PK": partition, "SK": f"CHECK#{order}", "GSI2PK": partition,
                         "GSI2SK": f"GOVERNED#{order:012d}#{order:032x}"})
        candidates[partition] = rows

    def query(partition, *, before=None, exact=None, limit=51):
        rows = [row for row in candidates[partition]
                if before is None or row["GSI2SK"] < before]
        return {"Items": rows[:limit], "LastEvaluatedKey": ({"synthetic": True} if len(rows) > limit else None)}

    reads = []

    def canonical(candidate):
        reads.append(candidate["GSI2SK"])
        if int(candidate["SK"].split("#")[1]) > 980:
            return None
        return {"resultId": candidate["GSI2SK"], "processingOutcome": "inconclusive"}

    monkeypatch.setattr(api, "_query", query)
    monkeypatch.setattr(api, "_canonical", canonical)
    first = api.list()
    assert first["items"] == [] and first["nextCursor"]
    assert len(reads) == Service.MAX_CANONICAL_READS
    second = api.list(cursor=first["nextCursor"])
    assert len(reads) == 24
    assert len(second["items"]) == 4 and second["nextCursor"] is None
    assert len({item["resultId"] for item in second["items"]}) == 4


def test_malformed_projected_nested_values_are_suppressed_before_canonical_read(world, monkeypatch):
    enable(world)
    api = service(world)
    malformed = {"PK": api.partitions[0], "SK": "CHECK#malformed", "GSI2PK": api.partitions[0],
                 "GSI2SK": "GOVERNED#000000000001#" + "0" * 32,
                 "governedHistory": {"settledAtEpoch": Decimal("NaN")}}
    monkeypatch.setattr(api, "_query", lambda *args, **kwargs: {"Items": [malformed]})
    monkeypatch.setattr(api, "_canonical", lambda candidate: None)
    assert api.list()["items"] == []
    with pytest.raises(GovernedHistoryError, match="NOT_FOUND"):
        api.detail("gh1_1_" + "0" * 32)


def test_response_size_cap_fails_closed(world, monkeypatch):
    enable(world)
    api = service(world)
    api.s = replace(api.s, max_response_bytes=1)
    monkeypatch.setattr(api, "_query", lambda *args, **kwargs: {"Items": []})
    with pytest.raises(GovernedHistoryError, match="SERVICE_UNAVAILABLE"):
        api.list()


def test_expired_deleted_and_corrupt_canonical_rows_are_suppressed(world, monkeypatch):
    enable(world)
    authority, event, _, row, _, clock = world
    proof = add(authority, event, "expiring", "A request outside qualified coverage.")
    stored = row("CHECK#" + proof)
    api = service(world)
    assert len(api.list()["items"]) == 1

    clock[0] = int(stored["expiresAt"])
    assert api.list()["items"] == []
    with pytest.raises(GovernedHistoryError, match="NOT_FOUND"):
        api.detail(stored["governedHistory"]["resultId"])

    clock[0] -= 1
    stale = {key: stored[key] for key in ("PK", "SK", "recordType", "state", "governedHistory",
                                          "governedHistoryDigest", "expiresAt", "retentionDeadlineEpoch", "GSI2PK", "GSI2SK")}
    authority.ddb.Table("authority").delete_item(Key={"PK": stored["PK"], "SK": stored["SK"]})
    monkeypatch.setattr(api, "_query", lambda *args, **kwargs: {"Items": [stale]})
    assert api.list()["items"] == []

    authority.ddb.Table("authority").put_item(Item=stored | {"governedHistoryDigest": "0" * 64})
    assert api._canonical(stale) is None


def test_detail_does_not_cross_account_partition_and_invalid_ids_fail_before_query(world, monkeypatch):
    enable(world)
    authority, event, _, row, _, _ = world
    proof = add(authority, event, "owned", "A request outside qualified coverage.")
    result_id = row("CHECK#" + proof)["governedHistory"]["resultId"]
    api = service(world)
    calls = []
    original = api._query
    monkeypatch.setattr(api, "_query", lambda partition, **kwargs: calls.append(partition) or original(partition, **kwargs))
    assert api.detail(result_id)["result"]["resultId"] == result_id
    assert set(calls) == set(authority.deletion_partitions(ACCOUNT))
    calls.clear()
    with pytest.raises(GovernedHistoryError, match="INVALID_REQUEST"):
        api.detail("v1_operation_proof")
    assert calls == []


def test_read_time_freshness_never_renews_deadline_or_original_accounting(world):
    enable(world)
    authority, event, _, row, _, clock = world
    proof = add_url(authority, event, "fresh-url")
    stored = row("CHECK#" + proof)
    original = stored["governedHistory"]
    api = service(world)
    current = api.detail(original["resultId"])["result"]
    assert current["outcome"]["verdict"] == "high_risk"
    assert current["presentation"]["verdict"] == "high_risk"
    assert current["presentation"]["freshnessStatus"] == "current"
    assert current["accounting"]["chargedChecks"] == 1
    deadline = current["expiresAtEpoch"]

    clock[0] += 20
    aged = api.detail(original["resultId"])["result"]
    assert aged["outcome"]["verdict"] == "high_risk"
    assert aged["outcome"]["lookupValidUntil"] == "2027-01-15T08:00:20Z"
    assert aged["presentation"]["verdict"] == "unknown"
    assert aged["presentation"]["reasonCodes"] == ["PROVIDER_EVIDENCE_EXPIRED"]
    assert aged["presentation"]["freshnessStatus"] == "historical"
    assert aged["accounting"] == current["accounting"]
    assert aged["expiresAtEpoch"] == deadline
    assert row("CHECK#" + proof)["governedHistory"] == original
