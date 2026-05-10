#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta

import boto3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Support CLI for MVP device-binding recovery actions.",
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["RESET_ACTIVE_BINDING", "RECOVER_BINDING"],
        help="Recovery action to perform.",
    )
    parser.add_argument(
        "--account-id",
        required=True,
        help="Account id / Cognito sub for the affected user.",
    )
    parser.add_argument(
        "--binding-fingerprint",
        help="Existing binding fingerprint to recover. Required for RECOVER_BINDING.",
    )
    parser.add_argument(
        "--operator-id",
        required=True,
        help="Support/admin operator identifier for the audit output.",
    )
    parser.add_argument(
        "--table-name",
        default=os.environ.get("DEVICE_BINDINGS_TABLE_NAME") or os.environ.get("TABLE_NAME"),
        help="Device bindings DynamoDB table name. Defaults from DEVICE_BINDINGS_TABLE_NAME or TABLE_NAME.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
        help="AWS region for DynamoDB access.",
    )
    parser.add_argument(
        "--inactive-retention-days",
        type=int,
        default=int(os.environ.get("DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS", "180")),
        help="Days to retain inactive bindings before TTL cleanup.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the proposed action without modifying DynamoDB.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.table_name:
        print("Error: --table-name or DEVICE_BINDINGS_TABLE_NAME is required.", file=sys.stderr)
        return 2
    if args.action == "RECOVER_BINDING" and not args.binding_fingerprint:
        print("Error: --binding-fingerprint is required for RECOVER_BINDING.", file=sys.stderr)
        return 2

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(args.table_name)

    result = process_action(
        table=table,
        account_id=args.account_id,
        action=args.action,
        binding_fingerprint=args.binding_fingerprint,
        operator_id=args.operator_id,
        inactive_retention_days=args.inactive_retention_days,
        dry_run=args.dry_run,
    )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def process_action(
    *,
    table,
    account_id: str,
    action: str,
    binding_fingerprint: str | None,
    operator_id: str,
    inactive_retention_days: int,
    dry_run: bool,
) -> dict:
    now = datetime.now(UTC)
    active = get_active_binding(table, account_id)

    if action == "RESET_ACTIVE_BINDING":
        return reset_active_binding(
            table=table,
            account_id=account_id,
            active=active,
            operator_id=operator_id,
            now=now,
            inactive_retention_days=inactive_retention_days,
            dry_run=dry_run,
        )

    return recover_binding(
        table=table,
        account_id=account_id,
        binding_fingerprint=binding_fingerprint,
        active=active,
        operator_id=operator_id,
        now=now,
        inactive_retention_days=inactive_retention_days,
        dry_run=dry_run,
    )


def reset_active_binding(*, table, account_id: str, active: dict | None, operator_id: str, now: datetime, inactive_retention_days: int, dry_run: bool) -> dict:
    if not active:
        return build_result(
            result="NO_ACTIVE_BINDING",
            account_id=account_id,
            binding_fingerprint=None,
            status=None,
            operator_id=operator_id,
            recovered_at=now.isoformat(),
            dry_run=dry_run,
        )

    inactive = build_inactive_item(active, now, inactive_retention_days)
    if not dry_run:
        table.put_item(Item=inactive)

    return build_result(
        result="CLEARED",
        account_id=account_id,
        binding_fingerprint=inactive["bindingFingerprint"],
        status=inactive["status"],
        operator_id=operator_id,
        recovered_at=now.isoformat(),
        dry_run=dry_run,
    )


def recover_binding(
    *,
    table,
    account_id: str,
    binding_fingerprint: str | None,
    active: dict | None,
    operator_id: str,
    now: datetime,
    inactive_retention_days: int,
    dry_run: bool,
) -> dict:
    target = get_binding(table, account_id, binding_fingerprint)
    if not target:
        raise SystemExit("Error: no existing binding was found for the provided bindingFingerprint.")

    if active and active.get("bindingFingerprint") == binding_fingerprint:
        refreshed = build_active_item(target, now)
        if not dry_run:
            table.put_item(Item=refreshed)
        return build_result(
            result="ALREADY_ACTIVE",
            account_id=account_id,
            binding_fingerprint=refreshed["bindingFingerprint"],
            status=refreshed["status"],
            operator_id=operator_id,
            recovered_at=now.isoformat(),
            dry_run=dry_run,
        )

    if active:
        inactive = build_inactive_item(active, now, inactive_retention_days)
        if not dry_run:
            table.put_item(Item=inactive)

    activated = build_active_item(target, now)
    if not dry_run:
        table.put_item(Item=activated)

    return build_result(
        result="RECOVERED",
        account_id=account_id,
        binding_fingerprint=activated["bindingFingerprint"],
        status=activated["status"],
        operator_id=operator_id,
        recovered_at=now.isoformat(),
        dry_run=dry_run,
    )


def get_binding(table, account_id: str, binding_fingerprint: str | None):
    if not binding_fingerprint:
        return None
    response = table.get_item(Key={"PK": f"USER#{account_id}", "SK": f"DEVICE#{binding_fingerprint}"})
    return response.get("Item")


def get_active_binding(table, account_id: str):
    response = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :gsi1pk",
        ExpressionAttributeValues={":gsi1pk": f"USER#{account_id}#ACTIVE"},
        Limit=1,
        ScanIndexForward=False,
    )
    items = response.get("Items") or []
    return items[0] if items else None


def build_active_item(item: dict, now: datetime):
    now_iso = now.isoformat()
    return {
        "PK": item["PK"],
        "SK": item["SK"],
        "accountId": item["accountId"],
        "bindingFingerprint": item["bindingFingerprint"],
        "platform": item["platform"],
        "osVersion": item["osVersion"],
        "status": "ACTIVE",
        "firstSeenAt": item["firstSeenAt"],
        "lastSeenAt": now_iso,
        "deactivatedAt": None,
        "GSI1PK": f"USER#{item['accountId']}#ACTIVE",
        "GSI1SK": now_iso,
    }


def build_inactive_item(item: dict, now: datetime, inactive_retention_days: int):
    now_iso = now.isoformat()
    expires_at = int((now + timedelta(days=inactive_retention_days)).timestamp())
    updated = dict(item)
    updated["status"] = "INACTIVE"
    updated["deactivatedAt"] = now_iso
    updated["GSI1PK"] = f"USER#{item['accountId']}#INACTIVE"
    updated["GSI1SK"] = now_iso
    updated["expiresAt"] = expires_at
    return updated


def build_result(*, result: str, account_id: str, binding_fingerprint: str | None, status: str | None, operator_id: str, recovered_at: str, dry_run: bool) -> dict:
    return {
        "accountId": account_id,
        "bindingFingerprint": binding_fingerprint,
        "dryRun": dry_run,
        "operatorId": operator_id,
        "recoveredAt": recovered_at,
        "result": result,
        "status": status,
    }


if __name__ == "__main__":
    raise SystemExit(main())
