import json
import logging
import os
import time

import boto3

import config
from errors import AppError
from service import (
    AccountDeletionService,
    command_from_stream,
    delete_analysis_abuse_control,
    delete_device_bindings,
    delete_device_recovery_control,
    ensure_session_revoked,
    reconcile_session_revocations,
)
from shared_history import HistoryError
from shared_history.security import jwt_subject
from validation import deletion_request, validate_get


LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    if isinstance(event, dict) and isinstance(event.get("Records"), list):
        return _stream_handler(event)
    if event == {"schemaVersion": 1, "operation": "reconcile-session-revocation"}:
        return _reconciliation_handler()
    try:
        config.validate_config()
        route = _route_key(event)
        account_id = _subject(event)
        service = _service()
        if route == "POST /v1/users/account-deletion":
            _assert_recent_reauthentication(event)
            body = deletion_request(event)
            result = service.request(account_id, body["operationId"])
            _attempt_post_fence_cleanup(account_id)
            result = service.status(account_id)
            _metric("request", True)
            return _response(202, result)
        if route == "GET /v1/users/account-deletion":
            validate_get(event)
            result = service.status(account_id)
            _metric("status", True)
            return _response(200, result)
        raise AppError("NOT_FOUND", "The requested route was not found.")
    except AppError as err:
        _metric(_operation(event), False)
        return _response(err.status_code, _error(err))
    except HistoryError as err:
        _metric(_operation(event), False)
        app_error = AppError(err.code, err.message, retryable=err.retryable, details=err.details)
        return _response(app_error.status_code, _error(app_error))
    except Exception:
        LOGGER.exception("Account-data Lambda failed")
        _metric(_operation(event), False)
        return _response(500, _error(AppError("INTERNAL_ERROR", "An internal error occurred.")))


def _service():
    resource = boto3.resource("dynamodb")
    return AccountDeletionService(
        environment=config.APP_ENVIRONMENT,
        ledger_table=resource.Table(config.DELETION_LEDGER_TABLE_NAME),
        users_table_name=config.USERS_TABLE_NAME,
        ledger_table_name=config.DELETION_LEDGER_TABLE_NAME,
        dynamodb_client=boto3.client("dynamodb"),
        required_components=config.required_components(),
        erasure_sla_hours=config.ACCOUNT_DELETION_SLA_HOURS,
    )


def _attempt_post_fence_cleanup(account_id):
    ledger = boto3.resource("dynamodb").Table(config.DELETION_LEDGER_TABLE_NAME)
    command = ledger.get_item(
        Key={"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"},
        ConsistentRead=True,
    ).get("Item")
    try:
        ensure_session_revoked(
            command, user_pool_id=config.COGNITO_USER_POOL_ID,
            cognito=boto3.client("cognito-idp"), ledger_table=ledger,
        )
        delete_device_bindings(
            command,
            device_table=boto3.resource("dynamodb").Table(config.DEVICE_BINDINGS_TABLE_NAME),
            ledger_table=ledger,
            page_size=config.ACCOUNT_DELETION_DEVICE_DELETE_PAGE_SIZE,
        )
        delete_device_recovery_control(
            command,
            recovery_table=boto3.resource("dynamodb").Table(
                config.DEVICE_RECOVERY_CONTROL_TABLE_NAME
            ),
            ledger_table=ledger,
            page_size=config.ACCOUNT_DELETION_RECOVERY_DELETE_PAGE_SIZE,
            receipt_retention_days=config.DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS,
            audit_retention_days=config.DEVICE_RECOVERY_AUDIT_RETENTION_DAYS,
            rate_state_ttl_seconds=config.DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS,
            account_receipt_retention_days=(
                config.ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS
            ),
        )
        delete_analysis_abuse_control(
            command,
            abuse_table=boto3.resource("dynamodb").Table(
                config.ANALYSIS_ABUSE_TABLE_NAME
            ),
            ledger_table=ledger,
            page_size=config.ACCOUNT_DELETION_ANALYSIS_ABUSE_PAGE_SIZE,
            request_retention_seconds=config.ANALYSIS_REQUEST_ID_TTL_SECONDS,
            history_dedup_retention_days=config.HISTORY_DEDUP_RETENTION_DAYS,
            request_dedupe_policy_status=(
                config.ANALYSIS_REQUEST_DEDUPE_POLICY_STATUS
            ),
            legacy_request_retention_policy_status=(
                config.ANALYSIS_LEGACY_REQUEST_RETENTION_POLICY_STATUS
            ),
            consumption_deletion_policy_status=(
                config.ANALYSIS_CONSUMPTION_DELETION_POLICY_STATUS
            ),
            account_receipt_retention_days=(
                config.ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS
            ),
        )
    except Exception:
        # The durable stream consumer retries revocation. The deletion fence is
        # never rolled back because an external identity call failed.
        LOGGER.exception("Account deletion accepted; post-fence cleanup remains pending")


def _stream_handler(event):
    # Stream setup/configuration failures must propagate so Lambda retries the
    # batch. They must never be converted into an HTTP response envelope.
    config.validate_config()
    failures = []
    ledger = boto3.resource("dynamodb").Table(config.DELETION_LEDGER_TABLE_NAME)
    device_table = boto3.resource("dynamodb").Table(config.DEVICE_BINDINGS_TABLE_NAME)
    recovery_table = boto3.resource("dynamodb").Table(
        config.DEVICE_RECOVERY_CONTROL_TABLE_NAME
    )
    abuse_table = boto3.resource("dynamodb").Table(config.ANALYSIS_ABUSE_TABLE_NAME)
    cognito = boto3.client("cognito-idp")
    for record in event["Records"]:
        try:
            command = command_from_stream(record, environment=config.APP_ENVIRONMENT)
            if command:
                ensure_session_revoked(
                    command, user_pool_id=config.COGNITO_USER_POOL_ID,
                    cognito=cognito, ledger_table=ledger,
                )
                delete_device_bindings(
                    command, device_table=device_table, ledger_table=ledger,
                    page_size=config.ACCOUNT_DELETION_DEVICE_DELETE_PAGE_SIZE,
                )
                delete_device_recovery_control(
                    command, recovery_table=recovery_table, ledger_table=ledger,
                    page_size=config.ACCOUNT_DELETION_RECOVERY_DELETE_PAGE_SIZE,
                    receipt_retention_days=(
                        config.DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS
                    ),
                    audit_retention_days=config.DEVICE_RECOVERY_AUDIT_RETENTION_DAYS,
                    rate_state_ttl_seconds=(
                        config.DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS
                    ),
                    account_receipt_retention_days=(
                        config.ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS
                    ),
                )
                delete_analysis_abuse_control(
                    command, abuse_table=abuse_table, ledger_table=ledger,
                    page_size=config.ACCOUNT_DELETION_ANALYSIS_ABUSE_PAGE_SIZE,
                    request_retention_seconds=config.ANALYSIS_REQUEST_ID_TTL_SECONDS,
                    history_dedup_retention_days=(
                        config.HISTORY_DEDUP_RETENTION_DAYS
                    ),
                    request_dedupe_policy_status=(
                        config.ANALYSIS_REQUEST_DEDUPE_POLICY_STATUS
                    ),
                    legacy_request_retention_policy_status=(
                        config.ANALYSIS_LEGACY_REQUEST_RETENTION_POLICY_STATUS
                    ),
                    consumption_deletion_policy_status=(
                        config.ANALYSIS_CONSUMPTION_DELETION_POLICY_STATUS
                    ),
                    account_receipt_retention_days=(
                        config.ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS
                    ),
                )
        except Exception:
            LOGGER.exception("Account-deletion post-fence stream record failed")
            identifier = (record.get("dynamodb") or {}).get("SequenceNumber")
            if isinstance(identifier, str) and identifier:
                failures.append({"itemIdentifier": identifier})
            else:
                raise
    _metric("session-revocation", not failures)
    return {"batchItemFailures": failures}


def _reconciliation_handler():
    # Scheduler failures propagate so EventBridge/Lambda retry and alarm.
    try:
        config.validate_config()
        ledger = boto3.resource("dynamodb").Table(config.DELETION_LEDGER_TABLE_NAME)
        device_table = boto3.resource("dynamodb").Table(config.DEVICE_BINDINGS_TABLE_NAME)
        recovery_table = boto3.resource("dynamodb").Table(
            config.DEVICE_RECOVERY_CONTROL_TABLE_NAME
        )
        abuse_table = boto3.resource("dynamodb").Table(
            config.ANALYSIS_ABUSE_TABLE_NAME
        )
        result = reconcile_session_revocations(
            environment=config.APP_ENVIRONMENT,
            ledger_table=ledger,
            device_table=device_table,
            recovery_table=recovery_table,
            abuse_table=abuse_table,
            user_pool_id=config.COGNITO_USER_POOL_ID,
            cognito=boto3.client("cognito-idp"),
            scan_limit=config.ACCOUNT_DELETION_RECONCILIATION_SCAN_LIMIT,
            max_pages=config.ACCOUNT_DELETION_RECONCILIATION_MAX_PAGES,
            device_page_size=config.ACCOUNT_DELETION_DEVICE_DELETE_PAGE_SIZE,
            recovery_page_size=config.ACCOUNT_DELETION_RECOVERY_DELETE_PAGE_SIZE,
            analysis_abuse_page_size=(
                config.ACCOUNT_DELETION_ANALYSIS_ABUSE_PAGE_SIZE
            ),
            analysis_request_retention_seconds=(
                config.ANALYSIS_REQUEST_ID_TTL_SECONDS
            ),
            history_dedup_retention_days=config.HISTORY_DEDUP_RETENTION_DAYS,
            analysis_request_dedupe_policy_status=(
                config.ANALYSIS_REQUEST_DEDUPE_POLICY_STATUS
            ),
            analysis_legacy_request_retention_policy_status=(
                config.ANALYSIS_LEGACY_REQUEST_RETENTION_POLICY_STATUS
            ),
            analysis_consumption_deletion_policy_status=(
                config.ANALYSIS_CONSUMPTION_DELETION_POLICY_STATUS
            ),
        )
        _reconciliation_metric(result)
        return {"schemaVersion": 1, "operation": "reconcile-session-revocation", **result}
    except Exception:
        _reconciliation_failure_metric()
        raise


def _subject(event):
    settings = type("AccountDataAuthSettings", (), {
        "cognito_issuer": config.COGNITO_ISSUER,
        "cognito_app_client_id": config.COGNITO_APP_CLIENT_ID,
        "cognito_required_scope": config.COGNITO_REQUIRED_SCOPE,
    })()
    return jwt_subject(event, settings)


def _assert_recent_reauthentication(event, *, now=lambda: int(time.time())):
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    auth_time = _epoch(claims.get("auth_time"))
    issued_at = _epoch(claims.get("iat"))
    current = now()
    if (
        auth_time is None or issued_at is None
        or auth_time > current + 60 or issued_at > current + 60
        or issued_at < auth_time
        or current - auth_time > config.ACCOUNT_DELETION_MAX_REAUTH_AGE_SECONDS
    ):
        raise AppError(
            "REAUTHENTICATION_REQUIRED",
            "A recent server-verified sign-in is required for account deletion.",
        )


def _epoch(value):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _route_key(event):
    route = event.get("routeKey") if isinstance(event, dict) else None
    if isinstance(route, str):
        return route
    method = (((event.get("requestContext") or {}).get("http") or {}).get("method") or "").upper()
    return f"{method} {event.get('rawPath') or ''}"


def _operation(event):
    return {
        "POST /v1/users/account-deletion": "request",
        "GET /v1/users/account-deletion": "status",
    }.get(_route_key(event) if isinstance(event, dict) else "", "unknown")


def _metric(operation, success):
    name = "AccountDeletionSuccess" if success else "AccountDeletionFailure"
    LOGGER.info(json.dumps({
        "_aws": {"Timestamp": int(time.time()) * 1000, "CloudWatchMetrics": [{
            "Namespace": "AMT/TrustCheckRadar/AccountData",
            "Dimensions": [["Environment", "Operation"]],
            "Metrics": [{"Name": name, "Unit": "Count"}],
        }]},
        "Environment": config.APP_ENVIRONMENT or "unknown",
        "Operation": operation, name: 1,
    }, separators=(",", ":")))


def _reconciliation_metric(result):
    values = {
        "SessionRevocationReconciliationSuccess": 1,
        "SessionRevocationReconciliationScanned": result["scanned"],
        "SessionRevocationReconciliationMatched": result["matched"],
        "SessionRevocationReconciliationRevoked": result["revoked"],
        "SessionRevocationReconciliationAlreadyComplete": result["sessionAlreadyComplete"],
        "AccountDeletionDeviceRecordsDeleted": result["deviceRecordsDeleted"],
        "AccountDeletionDeviceComponentsCompleted": result["deviceComponentsCompleted"],
        "AccountDeletionRecoveryRecordsDeleted": result["recoveryRecordsDeleted"],
        "AccountDeletionRecoveryRecordsMinimized": result["recoveryRecordsMinimized"],
        "AccountDeletionRecoveryComponentsCompleted": result["recoveryComponentsCompleted"],
        "AccountDeletionAnalysisAbuseRecordsDeleted": result["analysisAbuseRecordsDeleted"],
        "AccountDeletionAnalysisAbuseRecordsMinimized": result["analysisAbuseRecordsMinimized"],
        "AccountDeletionAnalysisAbuseComponentsCompleted": result["analysisAbuseComponentsCompleted"],
        "AccountDeletionAnalysisAbusePolicyBlocked": result["analysisAbusePolicyBlocked"],
        "SessionRevocationReconciliationWorksetTruncated": 1 if result["worksetTruncated"] else 0,
        "SessionRevocationReconciliationFullPassCompleted": 1 if result["completedFullPass"] else 0,
    }
    metrics = [{"Name": name, "Unit": "Count"} for name in values]
    if result["fullPassAgeSeconds"] is not None:
        values["SessionRevocationReconciliationFullPassAgeSeconds"] = result["fullPassAgeSeconds"]
        metrics.append({"Name": "SessionRevocationReconciliationFullPassAgeSeconds", "Unit": "Seconds"})
    LOGGER.info(json.dumps({
        "_aws": {"Timestamp": result["completedAtEpoch"] * 1000, "CloudWatchMetrics": [{
            "Namespace": "AMT/TrustCheckRadar/AccountData",
            "Dimensions": [["Environment"]], "Metrics": metrics,
        }]},
        "Environment": config.APP_ENVIRONMENT or "unknown",
        **values,
    }, separators=(",", ":")))


def _reconciliation_failure_metric():
    LOGGER.info(json.dumps({
        "_aws": {"Timestamp": int(time.time()) * 1000, "CloudWatchMetrics": [{
            "Namespace": "AMT/TrustCheckRadar/AccountData",
            "Dimensions": [["Environment"]],
            "Metrics": [{"Name": "SessionRevocationReconciliationFailure", "Unit": "Count"}],
        }]},
        "Environment": config.APP_ENVIRONMENT or "unknown",
        "SessionRevocationReconciliationFailure": 1,
    }, separators=(",", ":")))


def _error(err):
    result = {"error": {"code": err.code, "message": err.message, "retryable": err.retryable}}
    if err.details:
        result["error"]["details"] = err.details
    return result


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
        },
        "body": json.dumps(body, separators=(",", ":")),
    }
