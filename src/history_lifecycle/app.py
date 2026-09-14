import json
import logging
import os

import boto3

from shared_history import HistoryError, HistorySettings
from service import HistoryLifecycleService

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    try:
        settings = HistorySettings.from_env()
        settings.validate_lifecycle()
        if not isinstance(event, dict) or set(event) != {"schemaVersion", "operation"}:
            raise HistoryError("INVALID_REQUEST", "The lifecycle event is invalid.")
        if event != {"schemaVersion": 1, "operation": "sweep"}:
            raise HistoryError("INVALID_REQUEST", "The lifecycle event is unsupported.")
        resource = boto3.resource("dynamodb")
        result = HistoryLifecycleService(
            settings=settings,
            content_table=resource.Table(settings.content_table_name),
            control_table=resource.Table(settings.control_table_name),
        ).sweep()
        _success_metric(result)
        return result
    except HistoryError as err:
        LOGGER.warning("History lifecycle rejected | errorCode=%s", err.code)
        _failure_metric()
        raise
    except Exception:
        LOGGER.exception("History lifecycle failed")
        _failure_metric()
        raise


def _success_metric(result):
    metrics = [
        {"Name": "ExpiredContentRecords", "Unit": "Count"},
        {"Name": "ExpiredControlRecords", "Unit": "Count"},
        {"Name": "CompletedErasureJobs", "Unit": "Count"},
        {"Name": "OverdueErasureJobs", "Unit": "Count"},
        {"Name": "ObservedPendingCompletions", "Unit": "Count"},
        {"Name": "StuckPendingCompletions", "Unit": "Count"},
        {"Name": "LifecycleWorksetTruncated", "Unit": "Count"},
        {"Name": "LifecycleSweepSuccess", "Unit": "Count"},
    ]
    values = {
        "ExpiredContentRecords": result["expiredContentRecords"],
        "ExpiredControlRecords": result["expiredControlRecords"],
        "CompletedErasureJobs": result["completedErasureJobs"],
        "OverdueErasureJobs": result["overdueErasureJobs"],
        "ObservedPendingCompletions": result["observedPendingCompletions"],
        "StuckPendingCompletions": result["stuckPendingCompletions"],
        "LifecycleWorksetTruncated": 1 if result["worksetTruncated"] else 0,
        "LifecycleSweepSuccess": 1,
    }
    for name, key in (
        ("OldestPendingCompletionAgeSeconds", "oldestPendingCompletionAgeSeconds"),
        ("OldestPendingErasureAgeSeconds", "oldestPendingErasureAgeSeconds"),
    ):
        if result[key] is not None:
            metrics.append({"Name": name, "Unit": "Seconds"})
            values[name] = result[key]
    _emit(result["completedAtEpoch"], metrics, values)


def _failure_metric():
    import time
    _emit(int(time.time()), [{"Name": "LifecycleSweepFailure", "Unit": "Count"}], {"LifecycleSweepFailure": 1})


def _emit(epoch, metrics, values):
    LOGGER.info(json.dumps({
        "_aws": {
            "Timestamp": epoch * 1000,
            "CloudWatchMetrics": [{
                "Namespace": "AMT/TrustCheckRadar/History",
                "Dimensions": [["Environment"]],
                "Metrics": metrics,
            }],
        },
        "Environment": os.environ.get("APP_ENVIRONMENT", "unknown"),
        **values,
    }, separators=(",", ":")))
