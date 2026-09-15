import logging
import os
import json
import time

import boto3

import config
from service import (
    parse_account_deletion_record,
    reconcile_account_deletions,
    start_history_deletion,
)


LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    reconciliation = event == {"schemaVersion": 1, "operation": "reconcile"}
    try:
        config.validate_config()
        resource = boto3.resource("dynamodb")
        control = resource.Table(config.HISTORY_CONTROL_TABLE_NAME)
        ledger = resource.Table(config.DELETION_LEDGER_TABLE_NAME)
        client = boto3.client("dynamodb")
        if reconciliation:
            result = reconcile_account_deletions(
                environment=config.APP_ENVIRONMENT,
                schema_version=config.HISTORY_SCHEMA_VERSION,
                control_table=control,
                control_table_name=config.HISTORY_CONTROL_TABLE_NAME,
                deletion_ledger_table=ledger,
                deletion_ledger_table_name=config.DELETION_LEDGER_TABLE_NAME,
                dynamodb_client=client,
                erasure_sla_hours=config.HISTORY_ERASURE_SLA_HOURS,
                scan_limit=config.RECONCILIATION_SCAN_LIMIT,
                max_pages=config.RECONCILIATION_MAX_PAGES,
            )
            LOGGER.info(
                "History account-deletion reconciliation completed | scanned=%s matched=%s started=%s pending=%s completed=%s truncated=%s",
                result["scanned"], result["matched"], result["started"],
                result["alreadyPending"], result["completed"], result["worksetTruncated"],
            )
            _reconciliation_metric(result)
            return {"schemaVersion": config.HISTORY_SCHEMA_VERSION, "operation": "reconcile", **result}
        if not isinstance(event, dict) or not isinstance(event.get("Records"), list):
            raise ValueError("Unsupported History account-deletion bridge event")
        totals = {"started": 0, "alreadyPending": 0, "completed": 0}
        for record in event.get("Records", []):
            command = parse_account_deletion_record(
                record,
                environment=config.APP_ENVIRONMENT,
                schema_version=config.HISTORY_SCHEMA_VERSION,
            )
            if command is None:
                continue
            result = start_history_deletion(
                command,
                control_table=control,
                control_table_name=config.HISTORY_CONTROL_TABLE_NAME,
                deletion_ledger_table=ledger,
                deletion_ledger_table_name=config.DELETION_LEDGER_TABLE_NAME,
                dynamodb_client=client,
                schema_version=config.HISTORY_SCHEMA_VERSION,
                erasure_sla_hours=config.HISTORY_ERASURE_SLA_HOURS,
            )
            for key in totals:
                totals[key] += 1 if result.get(key) else 0
        LOGGER.info(
            "History account-deletion bridge completed | started=%s pending=%s completed=%s",
            totals["started"], totals["alreadyPending"], totals["completed"],
        )
        return {"schemaVersion": config.HISTORY_SCHEMA_VERSION, **totals}
    except Exception:
        if reconciliation:
            _reconciliation_failure_metric()
        raise


def _reconciliation_metric(result):
    values = {
        "AccountDeletionReconciliationSuccess": 1,
        "AccountDeletionReconciliationScanned": result["scanned"],
        "AccountDeletionReconciliationMatched": result["matched"],
        "AccountDeletionReconciliationStarted": result["started"],
        "AccountDeletionReconciliationAlreadyPending": result["alreadyPending"],
        "AccountDeletionReconciliationCompleted": result["completed"],
        "AccountDeletionReconciliationWorksetTruncated": 1 if result["worksetTruncated"] else 0,
        "AccountDeletionReconciliationFullPassCompleted": 1 if result["completedFullPass"] else 0,
    }
    metrics = [{"Name": name, "Unit": "Count"} for name in values]
    if result["fullPassAgeSeconds"] is not None:
        values["AccountDeletionReconciliationFullPassAgeSeconds"] = result["fullPassAgeSeconds"]
        metrics.append({"Name": "AccountDeletionReconciliationFullPassAgeSeconds", "Unit": "Seconds"})
    _emit(result["completedAtEpoch"], metrics, values)


def _reconciliation_failure_metric():
    _emit(
        int(time.time()),
        [{"Name": "AccountDeletionReconciliationFailure", "Unit": "Count"}],
        {"AccountDeletionReconciliationFailure": 1},
    )


def _emit(epoch, metrics, values):
    LOGGER.info(json.dumps({
        "_aws": {"Timestamp": epoch * 1000, "CloudWatchMetrics": [{
            "Namespace": "AMT/TrustCheckRadar/History",
            "Dimensions": [["Environment"]], "Metrics": metrics,
        }]},
        "Environment": config.APP_ENVIRONMENT or "unknown",
        **values,
    }, separators=(",", ":")))
