"""Real SDK expressions for History sweep checkpoint progress; synthetic tables only."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

if os.environ.get("AMT_AUTHORITY_INTEGRATION") != "1":
    pytest.skip("Run isolated with AMT_AUTHORITY_INTEGRATION=1", allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import boto3
from botocore.exceptions import ClientError
from moto import mock_aws
from history_lifecycle.service import HistoryLifecycleService


@pytest.fixture
def world():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = resource.create_table(
            TableName="synthetic-history-control",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        service = HistoryLifecycleService(settings=SimpleNamespace(environment="dev"), content_table=None,
                                          control_table=table, abuse_table=None)
        yield service, table


@pytest.mark.parametrize("kind", ["EXPIRATION", "RECONCILIATION"])
@pytest.mark.parametrize("prefix", ["HISTORY", "CONTROL"])
def test_checkpoint_progress_rollover_and_stale_retry_preserves_committed_state(world, kind, prefix):
    service, table = world
    start = 1790431200
    previous = {"hourEpoch": start, "shard": 15, "stateVersion": 3}
    key = {"PK": "LIFECYCLE#dev", "SK": f"{kind}#{prefix}"}
    table.put_item(Item={**key, **previous, "recordType": "LIFECYCLE_CHECKPOINT", "unchanged": "sentinel"})
    other = {"PK": "LIFECYCLE#dev", "SK": "UNRELATED", "sentinel": "preserved"}
    table.put_item(Item=other)
    def advance():
        if kind == "EXPIRATION":
            return service._advance_checkpoint(kind, prefix, previous, start + 7200)
        return service._advance_reconciliation_checkpoint(prefix, previous, start - 3600, start, start + 7200)
    result = advance()
    expected_hour = start + 3600 if kind == "EXPIRATION" else start - 3600
    assert result == {"hourEpoch": expected_hour, "shard": 0, "stateVersion": 4}
    committed = table.get_item(Key=key, ConsistentRead=True)["Item"]
    assert committed == {**key, **result, "recordType": "LIFECYCLE_CHECKPOINT", "unchanged": "sentinel", "updatedAtEpoch": start + 7200}
    with pytest.raises(ClientError) as error:
        advance()  # A lost acknowledgement must not advance a second time.
    assert error.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
    assert table.get_item(Key=key, ConsistentRead=True)["Item"] == committed
    assert table.get_item(Key={"PK": other["PK"], "SK": other["SK"]})["Item"] == other


def test_reset_uses_same_guarded_sdk_expression(world):
    service, table = world
    previous = {"hourEpoch": 1790427600, "shard": 7, "stateVersion": 2}
    key = {"PK": "LIFECYCLE#dev", "SK": "RECONCILIATION#CONTROL"}
    table.put_item(Item={**key, **previous, "recordType": "LIFECYCLE_CHECKPOINT"})
    result = service._reset_checkpoint("RECONCILIATION", "CONTROL", previous, 1790431200, 1790434800)
    assert result == {"hourEpoch": 1790431200, "shard": 0, "stateVersion": 3}
    assert table.get_item(Key=key, ConsistentRead=True)["Item"]["shard"] == 0
