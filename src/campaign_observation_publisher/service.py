from __future__ import annotations

import base64
import json
import time

from botocore.exceptions import ClientError

from shared_research_consent import CURRENT_NOTICE, CURRENT_POLICY, authorized, condition_checks
from shared_campaign_locators import period as period_fence
from contracts import build_cluster_envelope
from shared_campaign_contracts import validate_app_features
from shared_campaign_locators import (load_inventory, inventory_condition, locator_for_target,
    locator_put, locator_condition, get_owned_locator, locator_pointer, deserialize as locator_deserialize)


TOKEN_DOMAIN = b"campaign-contributor:v1\0"
PERIOD_SECONDS = 14 * 24 * 60 * 60


def publish_observation(
    item: dict,
    *,
    pipeline_table_name: str,
    users_table_name: str,
    deletion_ledger_table_name: str,
    cluster_queue_url: str,
    hmac_key_id: str | None = None,
    hmac_key_resolver=None,
    transient_retention_days: int,
    dynamodb_client,
    kms_client,
    sqs_client,
    now_epoch: int | None = None,
    locator_manifest_sha256=None, locator_inventory_revision=0,
) -> str:
    period_fence.configuration()
    if not item["campaignConsentGranted"]:
        return "consent-suppressed"

    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    if item["expiresAt"] <= now_epoch:
        return "expired"

    app_features = validate_app_features(item["appFeatures"])
    if not _account_authorizes(
        item, users_table_name, deletion_ledger_table_name, dynamodb_client
    ):
        return "participation-suppressed"

    period_id = contributor_period_id(item["observedAtEpoch"])
    period_record=period_fence.read(dynamodb_client,pipeline_table_name,period_id,
        locator_manifest_sha256,locator_inventory_revision,now_epoch,states=('OPEN',))
    if hmac_key_id is None:
        if hmac_key_resolver is None:
            raise ValueError("A period HMAC key resolver is required")
        hmac_key_id = hmac_key_resolver(period_id)
    if hmac_key_id != period_record['keyArn']:
        raise RuntimeError('Campaign period key is unavailable')
    dynamodb_client=period_fence.GuardedClient(dynamodb_client,pipeline_table_name,period_record)
    event_id = item["statisticsEventId"]
    key = {"PK": {"S": f"EVENT#{event_id}"}, "SK": {"S": "DEDUPE"}}
    existing = dynamodb_client.get_item(
        TableName=pipeline_table_name,
        Key=key,
        ConsistentRead=True,
        ProjectionExpression="#status",
        ExpressionAttributeNames={"#status": "status"},
    ).get("Item")
    if existing and existing.get("status") == {"S": "PUBLISHED"}:
        return "duplicate"

    inventory = load_inventory(dynamodb_client,pipeline_table_name,item['environment'],
                               locator_manifest_sha256,locator_inventory_revision,now_epoch)
    if period_id < inventory['minimumPeriodId']:
        raise RuntimeError('Campaign locator period is not covered')
    contributor_token = derive_contributor_token(item["accountId"], key_id=hmac_key_id, kms_client=kms_client)
    if not existing:
        transient_expiry = min(
            item["expiresAt"] + 18 * 24 * 60 * 60,
            now_epoch + transient_retention_days * 24 * 60 * 60,
        )
        expiration_index = {
            "GSI3PK": f"EXPIRY#{item['environment']}",
            "GSI3SK": transient_expiry,
        }
        feature = {"PK":f"EVENT#{event_id}","SK":"FEATURE","GSI1PK":f"CONTRIB#{period_id}#{contributor_token}",
                   "periodId":period_id,"expiresAt":transient_expiry}
        feature_locator = locator_for_target(feature,item['environment'])
        try:
            dynamodb_client.transact_write_items(
                TransactItems=[
                    _tombstone_condition(pipeline_table_name, period_id, contributor_token),
                    *_authority_condition_checks(
                        item, users_table_name, deletion_ledger_table_name
                    ),
                    {
                        "Put": {
                            "TableName": pipeline_table_name,
                            "Item": _serialize_item(
                                {
                                    "PK": f"EVENT#{event_id}",
                                    "SK": "FEATURE",
                                    "schemaVersion": item["schemaVersion"],
                                    "recordVersion": item["recordVersion"],
                                    "environment": item["environment"],
                                    "statisticsEventId": event_id,
                                    "researchNoticeVersion": CURRENT_NOTICE,
                                    "researchPolicyVersion": CURRENT_POLICY,
                                    "periodId": period_id,
                                    "contributorToken": contributor_token,
                                    "GSI1PK": f"CONTRIB#{period_id}#{contributor_token}",
                                    "GSI1SK": f"EVENT#{event_id}#FEATURE",
                                    **expiration_index,
                                    **app_features,
                                    "expiresAt": transient_expiry,
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": pipeline_table_name,
                            "Item": _serialize_item(
                                {
                                    "PK": f"EVENT#{event_id}",
                                    "SK": "DEDUPE",
                                    "status": "PENDING",
                                    **locator_pointer(feature_locator),
                                    "periodId": period_id,
                                    **expiration_index,
                                    "expiresAt": transient_expiry,
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                        }
                    },
                    locator_put(pipeline_table_name,feature_locator),
                    inventory_condition(pipeline_table_name,inventory),
                ]
            )
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            if not _account_authorizes(
                item, users_table_name, deletion_ledger_table_name,
                dynamodb_client,
            ):
                return "participation-suppressed"
            concurrent = dynamodb_client.get_item(
                TableName=pipeline_table_name,
                Key=key,
                ConsistentRead=True,
                ProjectionExpression="#status",
                ExpressionAttributeNames={"#status": "status"},
            ).get("Item")
            if concurrent and concurrent.get("status") == {"S": "PUBLISHED"}:
                return "duplicate"
            if not concurrent:
                raise

    feature_raw = dynamodb_client.get_item(TableName=pipeline_table_name,
        Key={"PK":{"S":f"EVENT#{event_id}"},"SK":{"S":"FEATURE"}},ConsistentRead=True).get("Item")
    if not feature_raw:
        raise RuntimeError('Campaign feature locator is unavailable')
    feature = locator_deserialize(feature_raw)
    if feature.get('researchNoticeVersion') != CURRENT_NOTICE or feature.get('researchPolicyVersion') != CURRENT_POLICY:
        return 'legacy-feature-suppressed'
    feature_locator = get_owned_locator(dynamodb_client,pipeline_table_name,
                                       locator_for_target(feature,item['environment']))
    if feature_locator['targetExpiresAtEpoch'] <= now_epoch:
        return "expired"
    if not _account_authorizes(
        item, users_table_name, deletion_ledger_table_name, dynamodb_client
    ):
        return "participation-suppressed"
    envelope = build_cluster_envelope(item)
    latest=period_fence.read(dynamodb_client,pipeline_table_name,period_id,
        locator_manifest_sha256,locator_inventory_revision,now_epoch,states=('OPEN',))
    if latest != period_record:
        raise RuntimeError('Campaign period changed')
    sqs_client.send_message(
        QueueUrl=cluster_queue_url,
        MessageBody=json.dumps(envelope, separators=(",", ":"), sort_keys=True),
    )
    try:
        dynamodb_client.transact_write_items(TransactItems=[
            _tombstone_condition(pipeline_table_name, period_id, contributor_token),
            *_authority_condition_checks(
                item, users_table_name, deletion_ledger_table_name
            ),
            {
                "Update": {
                    "TableName": pipeline_table_name,
                    "Key": key,
                    "UpdateExpression": (
                        "SET #status = :published, "
                        "publishedAtEpoch = :published_at"
                    ),
                    "ConditionExpression": (
                        "attribute_exists(PK) AND attribute_exists(SK) "
                        "AND #status = :pending"
                    ),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":published": {"S": "PUBLISHED"},
                        ":pending": {"S": "PENDING"},
                        ":published_at": {"N": str(now_epoch)},
                    },
                }
            },
            locator_condition(pipeline_table_name,feature_locator),
            inventory_condition(pipeline_table_name,inventory),
        ])
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        if not _account_authorizes(
            item, users_table_name, deletion_ledger_table_name,
            dynamodb_client,
        ):
            return "participation-suppressed"
        concurrent = dynamodb_client.get_item(
            TableName=pipeline_table_name,
            Key=key,
            ConsistentRead=True,
            ProjectionExpression="#status",
            ExpressionAttributeNames={"#status": "status"},
        ).get("Item")
        if concurrent and concurrent.get("status") == {"S": "PUBLISHED"}:
            return "duplicate"
        raise
    return "published"



def _tombstone_condition(table, period, token):
    return {"ConditionCheck":{"TableName":table,
        "Key":{"PK":{"S":f"CONTRIB#{period}#{token}"},"SK":{"S":"TOMBSTONE"}},
        "ConditionExpression":"attribute_not_exists(PK) AND attribute_not_exists(SK)"}}

def _authority_condition_checks(item, users_table_name, deletion_ledger_table_name):
    return condition_checks(item, users_table_name, deletion_ledger_table_name)


def _account_authorizes(item, users_table_name, deletion_ledger_table_name, dynamodb_client):
    return authorized(item, users_table_name, deletion_ledger_table_name, dynamodb_client)


def contributor_period_id(epoch_seconds: int) -> int:
    return epoch_seconds // PERIOD_SECONDS


def derive_contributor_token(account_id: str, *, key_id: str, kms_client) -> str:
    response = kms_client.generate_mac(
        KeyId=key_id,
        Message=TOKEN_DOMAIN + account_id.encode("utf-8"),
        MacAlgorithm="HMAC_SHA_256",
    )
    return base64.urlsafe_b64encode(response["Mac"]).decode("ascii").rstrip("=")


def _serialize_item(value: dict) -> dict:
    return {key: _serialize_value(item) for key, item in value.items()}


def _serialize_value(value):
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float)):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [_serialize_value(item) for item in value]}
    raise TypeError(f"Unsupported DynamoDB value type: {type(value).__name__}")
