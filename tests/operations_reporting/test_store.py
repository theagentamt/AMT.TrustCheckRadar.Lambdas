from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import threading

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from moto import mock_aws
import pytest

from operations_reporting.reducer import aggregate_day, day_bounds
from operations_reporting.revisions import CORRECTION_SECONDS, RETENTION_SECONDS
from operations_reporting.store import AggregateStore, AggregateStoreFault


DAY = "2026-10-03"
START, END = day_bounds(DAY)
FIRST, NEXT = END + 60, END + 86400 + 60
FIXTURES = json.loads(Path(__file__).with_name("fixtures.json").read_text())
EVENTS = [x["event"] for x in FIXTURES["events"]]
REJECTIONS = [x["aggregate"] for x in FIXTURES["rejectionAggregates"]]
TABLE = "operations-unlinkable-test"


def summary(as_of=FIRST):
    return aggregate_day(deepcopy(EVENTS), deepcopy(REJECTIONS), local_day=DAY,
                         environment="dev", as_of_epoch=as_of)


def corrected():
    events = deepcopy(EVENTS)
    pending = next(e for e in events if e["eventType"] == "check_accepted"
                   and not any(t["eventType"] == "check_terminal" and t["logicalCheckRef"] == e["logicalCheckRef"] for t in events))
    terminal = next(deepcopy(e) for e in events if e["eventType"] == "check_terminal" and e["processingOutcome"] == "complete")
    for key in ("service", "inputKind", "logicalCheckRef", "accessClass"):
        terminal[key] = pending[key]
    terminal.update(eventId="op1_" + "f" * 32, eventTimeEpoch=FIRST + 3600,
                    accountingState="charged" if pending["accessClass"] != "complimentary" else "not_charged",
                    chargedChecks=0 if pending["accessClass"] == "complimentary" else 1)
    return aggregate_day([*events, terminal], deepcopy(REJECTIONS), local_day=DAY,
                         environment="dev", as_of_epoch=NEXT)


@pytest.fixture
def client():
    with mock_aws():
        c = boto3.client("dynamodb", region_name="us-east-1",
                         aws_access_key_id="synthetic", aws_secret_access_key="synthetic",
                         config=Config(retries={"total_max_attempts": 1}))
        c.create_table(TableName=TABLE, KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"},
                                                 {"AttributeName": "SK", "KeyType": "RANGE"}],
                       AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                             {"AttributeName": "SK", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST")
        yield c


def store(client, now=FIRST):
    return AggregateStore(client, TABLE, environment="dev", now=now if callable(now) else lambda: now)


def fault(code, fn):
    with pytest.raises(AggregateStoreFault) as caught:
        fn()
    assert str(caught.value) == code


def rows(client):
    return sorted(client.scan(TableName=TABLE)["Items"], key=lambda r: r["SK"]["S"])


def test_first_commit_is_actual_atomic_two_row_append_and_strong_read(client):
    assert store(client).read(DAY) == {"state": "MISSING", "record": None}
    result = store(client).commit(summary())
    assert result["decision"] == "COMMITTED" and len(rows(client)) == 2
    assert store(client).read(DAY) == {"state": "PRESENT", "record": result["record"]}
    assert all(r["expiresAtEpoch"] == {"N": str(START + RETENTION_SECONDS)} for r in rows(client))
    text = json.dumps(rows(client))
    assert "logicalCheckRef" not in text and "attemptRef" not in text and "eventId" not in text


def test_replay_does_not_write_or_renew_then_next_day_appends_immutable_revision(client):
    first = store(client).commit(summary())["record"]
    before = deepcopy(rows(client))
    assert store(client, NEXT).commit(summary(NEXT))["decision"] == "NO_CHANGE"
    assert rows(client) == before
    second = store(client, NEXT).commit(corrected())["record"]
    assert len(rows(client)) == 3 and second["revision"] == 2
    old = next(r for r in rows(client) if r["SK"]["S"] == "REVISION#0000000001")
    assert old == next(r for r in before if r["SK"] == old["SK"])
    assert second["previousRecordSha256"] == first["recordSha256"]
    assert second["expiresAtEpoch"] == first["expiresAtEpoch"]


def test_same_day_defer_is_explicit_and_has_no_hidden_pending_store(client):
    store(client).commit(summary())
    later = corrected(); later["asOfEpoch"] = FIRST + 7200
    before = rows(client)
    assert store(client, FIRST + 7200).commit(later)["decision"] == "DEFER_TO_NEXT_DAILY"
    assert rows(client) == before


@pytest.mark.parametrize("existing", [False, True])
def test_concurrent_writers_compare_actual_same_head_and_only_one_wins(client, existing):
    if existing:
        store(client).commit(summary())
    barrier = threading.Barrier(2)
    def submit(index):
        # Different valid candidates and request tokens share one prior hash.
        # Exact same DynamoDB ClientRequestToken may idempotently acknowledge
        # the same write again; that is still only one durable append.
        s = store(client, (NEXT if existing else FIRST) + index)
        original = s._read
        def synchronized(*args):
            result = original(*args); barrier.wait(timeout=10); return result
        s._read = synchronized
        try:
            return s.commit(corrected() if existing else summary())["decision"]
        except AggregateStoreFault as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))
    assert sorted(results) == ["COMMITTED", "CONDITIONAL_CONFLICT"]
    assert len(rows(client)) == (3 if existing else 2)


def test_orphan_immutable_revision_conflict_cannot_advance_head_halfway(client):
    result = store(client).commit(summary())
    client.delete_item(TableName=TABLE, Key={"PK": rows(client)[0]["PK"], "SK": {"S": "HEAD"}})
    before = rows(client)
    fault("CONDITIONAL_CONFLICT", lambda: store(client).commit(summary()))
    assert rows(client) == before and len(before) == 1
    assert result["record"]["revision"] == 1


class Proxy:
    def __init__(self, client):
        self.client, self.meta = client, client.meta
        self.gets = self.writes = 0
    def get_item(self, **kwargs):
        self.gets += 1
        assert kwargs["ConsistentRead"] is True
        return self.client.get_item(**kwargs)
    def transact_write_items(self, **kwargs):
        self.writes += 1
        return self.client.transact_write_items(**kwargs)


def test_lost_write_response_is_unknown_once_and_explicit_read_reconciles(client, caplog, capsys):
    p = Proxy(client)
    def uncertain(**kwargs):
        p.writes += 1; client.transact_write_items(**kwargs)
        raise RuntimeError("PRIVATE_SERVICE_RESPONSE")
    p.transact_write_items = uncertain
    fault("WRITE_OUTCOME_UNKNOWN", lambda: store(p).commit(summary()))
    assert p.writes == 1
    assert store(client).read(DAY)["state"] == "PRESENT"
    assert store(client).commit(summary())["decision"] == "NO_CHANGE"
    assert len(rows(client)) == 2
    assert caplog.text == "" and capsys.readouterr().out == ""


@pytest.mark.parametrize("reasons", [[], [{"Code": "ConditionalCheckFailed"}, {"Code": "ThrottlingError"}],
                                     None, "PRIVATE_BODY", [None]])
def test_mixed_or_malformed_write_failure_never_becomes_known_cas_loss(client, reasons):
    p = Proxy(client)
    def failed(**kwargs):
        p.writes += 1
        raise ClientError({"Error": {"Code": "TransactionCanceledException", "Message": "PRIVATE_RESPONSE"},
                           "CancellationReasons": reasons}, "TransactWriteItems")
    p.transact_write_items = failed
    fault("WRITE_OUTCOME_UNKNOWN", lambda: store(p).commit(summary()))
    assert p.writes == 1 and not rows(client)


def test_read_failure_is_unavailable_not_missing_or_zero(client):
    p = Proxy(client)
    def failed(**kwargs):
        raise RuntimeError("PRIVATE_STORAGE_BODY")
    p.get_item = failed
    fault("READ_UNAVAILABLE", lambda: store(p).read(DAY))
    fault("READ_UNAVAILABLE", lambda: store(p).commit(summary()))
    assert p.writes == 0


@pytest.mark.parametrize("response", [None, {}, {"ResponseMetadata": {"HTTPStatusCode": 503}}])
def test_malformed_sdk_read_response_is_not_an_observed_missing_row(client, response):
    p = Proxy(client)
    p.get_item = lambda **kwargs: response
    fault("READ_UNAVAILABLE", lambda: store(p).read(DAY))
    assert p.writes == 0


@pytest.mark.parametrize("response", [None, {}, {"ResponseMetadata": {"HTTPStatusCode": 503}}])
def test_malformed_sdk_write_response_is_unknown_after_one_dispatch(client, response):
    p = Proxy(client)
    def uncertain(**kwargs):
        p.writes += 1
        client.transact_write_items(**kwargs)
        return response
    p.transact_write_items = uncertain
    fault("WRITE_OUTCOME_UNKNOWN", lambda: store(p).commit(summary()))
    assert p.writes == 1 and len(rows(client)) == 2


@pytest.mark.parametrize("mutation", ["extra", "hash", "payload", "expiry", "missing_revision"])
def test_corrupt_stored_rows_fail_closed_without_overwriting(client, mutation):
    store(client).commit(summary())
    head, row = rows(client)
    if mutation == "extra": head["customer"] = {"S": "PRIVATE_ID"}
    elif mutation == "hash": head["recordSha256"] = {"S": "0" * 64}
    elif mutation == "expiry": head["expiresAtEpoch"] = {"N": str(START + RETENTION_SECONDS + 1)}
    elif mutation == "payload": row["recordJson"] = {"S": '{"secret":"PRIVATE_ID"}'}
    if mutation == "missing_revision": client.delete_item(TableName=TABLE, Key={"PK": row["PK"], "SK": row["SK"]})
    else:
        client.put_item(TableName=TABLE, Item=head); client.put_item(TableName=TABLE, Item=row)
    before = rows(client)
    fault("INVALID_STORED_RECORD", lambda: store(client).read(DAY))
    fault("INVALID_STORED_RECORD", lambda: store(client, NEXT).commit(corrected()))
    assert rows(client) == before


def test_exact_logical_expiry_hides_even_undeleted_dynamodb_rows(client):
    store(client).commit(summary())
    expiry = START + RETENTION_SECONDS
    assert store(client, expiry - 1).read(DAY)["state"] == "PRESENT"
    assert store(client, expiry).read(DAY) == {"state": "EXPIRED", "record": None}
    fault("AGGREGATE_EXPIRED", lambda: store(client, expiry).commit(summary()))
    assert len(rows(client)) == 2


def test_delayed_read_cannot_return_present_after_expiry(client):
    store(client).commit(summary())
    expiry = START + RETENTION_SECONDS
    clock = iter([expiry - 1, expiry])
    assert store(client, lambda: next(clock)).read(DAY) == {"state": "EXPIRED", "record": None}


def test_correction_boundary_or_backwards_clock_between_read_and_write_blocks_dispatch(client):
    p = Proxy(client)
    for finish, code in [(START + CORRECTION_SECONDS, "CORRECTION_WINDOW_CLOSED"), (FIRST - 1, "CLOCK_UNAVAILABLE")]:
        clock = iter([FIRST, finish])
        fault(code, lambda: store(p, lambda: next(clock)).commit(summary()))
    assert p.writes == 0 and not rows(client)


@pytest.mark.parametrize("bad", [True, None, -1, float("nan")])
def test_invalid_clock_has_no_storage_operation(client, bad):
    p = Proxy(client)
    fault("CLOCK_UNAVAILABLE", lambda: store(p, bad).read(DAY))
    assert p.gets == p.writes == 0


@pytest.mark.parametrize("field", ["subject", "recipient", "eventId", "logicalCheckRef", "notes"])
def test_caller_sensitive_fields_rejected_before_storage(client, field):
    p = Proxy(client)
    report = summary(); report[field] = "PRIVATE_SENTINEL"
    fault("INVALID_INPUT", lambda: store(p).commit(report))
    assert p.gets == p.writes == 0


def test_fixed_environment_refuses_cross_environment_summary_before_storage(client):
    p = Proxy(client)
    report = summary(); report["environment"] = "prod"
    fault("INVALID_INPUT", lambda: store(p).commit(report))
    assert p.gets == p.writes == 0


def test_client_with_sdk_retries_is_refused_before_operations(client):
    class Bad:
        class meta:
            config = Config(retries={"total_max_attempts": 3})
    fault("INVALID_CONFIGURATION", lambda: store(Bad()))
