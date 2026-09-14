from dataclasses import dataclass
import json
import os

from .errors import HistoryError


DEFAULT_BADGE_CATALOG = (
    {
        "id": "checks_1",
        "threshold": 1,
        "titleKey": "badges.checks_1.title",
        "descriptionKey": "badges.checks_1.description",
    },
    {
        "id": "checks_5",
        "threshold": 5,
        "titleKey": "badges.checks_5.title",
        "descriptionKey": "badges.checks_5.description",
    },
    {
        "id": "checks_20",
        "threshold": 20,
        "titleKey": "badges.checks_20.title",
        "descriptionKey": "badges.checks_20.description",
    },
)


def _enabled(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() == "true"


def _optional_positive_int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name, "" if default is None else str(default))
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as err:
        raise HistoryError("SERVER_UNAVAILABLE", f"{name} must be a positive integer.") from err
    if value < 1:
        raise HistoryError("SERVER_UNAVAILABLE", f"{name} must be a positive integer.")
    return value


def _optional_nonnegative_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as err:
        raise HistoryError("SERVER_UNAVAILABLE", f"{name} must be a nonnegative integer.") from err
    if value < 0:
        raise HistoryError("SERVER_UNAVAILABLE", f"{name} must be a nonnegative integer.")
    return value


@dataclass(frozen=True)
class HistorySettings:
    environment: str
    cognito_issuer: str
    cognito_app_client_id: str
    cognito_required_scope: str
    content_table_name: str
    control_table_name: str
    device_bindings_table_name: str
    users_table_name: str
    deletion_ledger_table_name: str
    analysis_abuse_table_name: str
    expiration_index_name: str
    lifecycle_index_name: str
    schema_version: int
    retention_days: int
    erasure_sla_hours: int
    reads_enabled: bool
    writes_enabled: bool
    mutations_enabled: bool
    recognition_enabled: bool
    lifecycle_enabled: bool
    durable_replay_enabled: bool
    api_contract_status: str
    recognition_contract_status: str
    pitr_policy_approved: bool
    control_retention_policy_approved: bool
    cursor_secret_name: str
    cursor_ttl_seconds: int | None
    default_page_size: int | None
    max_page_size: int | None
    max_response_bytes: int | None
    dedup_retention_days: int | None
    mutation_retention_days: int | None
    max_summary_bytes: int | None
    max_list_items: int | None
    max_text_field_bytes: int | None
    badge_catalog: tuple[dict, ...]
    new_id_recognition_policy: str
    lifecycle_start_epoch_hour: int | None
    badge_qualification_policy: str
    lifecycle_max_items_per_sweep: int | None
    erasure_batch_size: int | None
    completion_stuck_seconds: int | None
    completion_recheck_seconds: int | None
    lifecycle_max_bucket_queries_per_sweep: int | None
    expiration_reconciliation_hours: int | None

    @classmethod
    def from_env(cls):
        try:
            schema_version = int(os.environ.get("HISTORY_SCHEMA_VERSION", "1"))
            retention_days = int(os.environ.get("HISTORY_RETENTION_DAYS", "90"))
            erasure_sla_hours = int(os.environ.get("HISTORY_ERASURE_SLA_HOURS", "24"))
        except ValueError as err:
            raise HistoryError("SERVER_UNAVAILABLE", "History policy configuration is invalid.") from err
        raw_catalog = os.environ.get("HISTORY_BADGE_CATALOG_JSON", "")
        try:
            catalog = tuple(json.loads(raw_catalog)) if raw_catalog else DEFAULT_BADGE_CATALOG
        except (ValueError, TypeError) as err:
            raise HistoryError("SERVER_UNAVAILABLE", "The badge catalog is invalid.") from err
        return cls(
            environment=os.environ.get("APP_ENVIRONMENT", ""),
            cognito_issuer=os.environ.get("COGNITO_ISSUER", ""),
            cognito_app_client_id=os.environ.get("COGNITO_APP_CLIENT_ID", ""),
            cognito_required_scope=os.environ.get(
                "COGNITO_REQUIRED_SCOPE", "aws.cognito.signin.user.admin"
            ),
            content_table_name=os.environ.get("HISTORY_CONTENT_TABLE_NAME", ""),
            control_table_name=os.environ.get("HISTORY_CONTROL_TABLE_NAME", ""),
            device_bindings_table_name=os.environ.get("DEVICE_BINDINGS_TABLE_NAME", ""),
            users_table_name=os.environ.get("USERS_TABLE_NAME", ""),
            deletion_ledger_table_name=os.environ.get("DELETION_LEDGER_TABLE_NAME", ""),
            analysis_abuse_table_name=os.environ.get("ANALYSIS_ABUSE_TABLE_NAME", ""),
            expiration_index_name=os.environ.get("HISTORY_EXPIRATION_INDEX_NAME", "ExpirationIndex"),
            lifecycle_index_name=os.environ.get("HISTORY_LIFECYCLE_INDEX_NAME", "PendingLifecycleIndex"),
            schema_version=schema_version,
            retention_days=retention_days,
            erasure_sla_hours=erasure_sla_hours,
            reads_enabled=_enabled("HISTORY_READS_ENABLED"),
            writes_enabled=_enabled("HISTORY_WRITES_ENABLED"),
            mutations_enabled=_enabled("HISTORY_MUTATIONS_ENABLED"),
            recognition_enabled=_enabled("RECOGNITION_ENABLED"),
            lifecycle_enabled=_enabled("HISTORY_LIFECYCLE_ENABLED"),
            durable_replay_enabled=_enabled("HISTORY_DURABLE_REPLAY_ENABLED"),
            api_contract_status=os.environ.get("HISTORY_API_CONTRACT_STATUS", "pending"),
            recognition_contract_status=os.environ.get("HISTORY_RECOGNITION_CONTRACT_STATUS", "pending"),
            pitr_policy_approved=_enabled("HISTORY_PITR_POLICY_APPROVED"),
            control_retention_policy_approved=_enabled("HISTORY_CONTROL_RETENTION_POLICY_APPROVED"),
            cursor_secret_name=os.environ.get("HISTORY_CURSOR_SECRET_NAME", ""),
            cursor_ttl_seconds=_optional_positive_int("HISTORY_CURSOR_TTL_SECONDS", 900),
            default_page_size=_optional_positive_int("HISTORY_DEFAULT_PAGE_SIZE", 20),
            max_page_size=_optional_positive_int("HISTORY_MAX_PAGE_SIZE", 50),
            max_response_bytes=_optional_positive_int("HISTORY_MAX_RESPONSE_BYTES", 262144),
            dedup_retention_days=_optional_positive_int("HISTORY_DEDUP_RETENTION_DAYS", 120),
            mutation_retention_days=_optional_positive_int("HISTORY_MUTATION_RETENTION_DAYS", 7),
            max_summary_bytes=_optional_positive_int("HISTORY_MAX_SUMMARY_BYTES", 4096),
            max_list_items=_optional_positive_int("HISTORY_MAX_LIST_ITEMS", 20),
            max_text_field_bytes=_optional_positive_int("HISTORY_MAX_TEXT_FIELD_BYTES", 1024),
            badge_catalog=catalog,
            new_id_recognition_policy=os.environ.get("HISTORY_NEW_ID_RECOGNITION_POLICY", "count"),
            lifecycle_start_epoch_hour=_optional_nonnegative_int("HISTORY_LIFECYCLE_START_EPOCH_HOUR"),
            badge_qualification_policy=os.environ.get(
                "HISTORY_BADGE_QUALIFICATION_POLICY",
                "all_server_accepted_completed_assessments",
            ),
            lifecycle_max_items_per_sweep=_optional_positive_int("HISTORY_LIFECYCLE_MAX_ITEMS_PER_SWEEP"),
            erasure_batch_size=_optional_positive_int("HISTORY_ERASURE_BATCH_SIZE"),
            completion_stuck_seconds=_optional_positive_int("HISTORY_COMPLETION_STUCK_SECONDS"),
            completion_recheck_seconds=_optional_positive_int("HISTORY_COMPLETION_RECHECK_SECONDS"),
            lifecycle_max_bucket_queries_per_sweep=_optional_positive_int("HISTORY_LIFECYCLE_MAX_BUCKET_QUERIES_PER_SWEEP"),
            expiration_reconciliation_hours=_optional_positive_int("HISTORY_EXPIRATION_RECONCILIATION_HOURS"),
        )

    def validate_common(self) -> None:
        if self.environment not in {"dev", "uat", "prod"}:
            self._unavailable("APP_ENVIRONMENT must be dev, uat, or prod.")
        if not self.content_table_name or not self.control_table_name:
            self._unavailable("History tables must be configured.")
        if self.schema_version != 1 or self.retention_days != 90 or self.erasure_sla_hours != 24:
            self._unavailable("The approved History schema, retention, or erasure policy is not configured.")
        if not self.pitr_policy_approved or not self.control_retention_policy_approved:
            self._unavailable("PITR and durable control-retention decisions must be approved before activation.")

    def validate_api_auth(self) -> None:
        if (
            not self.cognito_issuer.startswith("https://cognito-idp.")
            or not self.cognito_app_client_id
            or self.cognito_required_scope != "aws.cognito.signin.user.admin"
        ):
            self._unavailable("The Cognito access-token contract is not configured.")

    def validate_reads(self) -> None:
        if not self.reads_enabled:
            raise HistoryError("FEATURE_DISABLED", "History reads are not enabled.", retryable=True)
        self.validate_common()
        self.validate_api_auth()
        if not self.device_bindings_table_name:
            self._unavailable("The device-binding table must be configured.")
        if not self.users_table_name or not self.deletion_ledger_table_name:
            self._unavailable("The account authority must be configured.")
        if self.api_contract_status != "approved":
            self._unavailable("The History API contract is not approved.")
        required = (
            self.cursor_ttl_seconds,
            self.default_page_size,
            self.max_page_size,
            self.max_response_bytes,
        )
        if any(value is None for value in required) or not self.cursor_secret_name:
            self._unavailable("History cursor and pagination policy must be configured.")
        if (
            self.default_page_size != 20
            or self.max_page_size != 50
            or self.max_response_bytes != 262144
            or self.cursor_ttl_seconds != 900
        ):
            self._unavailable("The approved History paging and response contract is not configured.")

    def validate_writes(self) -> None:
        if not self.writes_enabled:
            raise HistoryError("FEATURE_DISABLED", "History writes are not enabled.", retryable=True)
        self.validate_write_contract()
        if not self.users_table_name or not self.deletion_ledger_table_name:
            self._unavailable("The account authority must be configured for History writes.")
        if not self.durable_replay_enabled:
            self._unavailable("Durable replay protection must be enabled with History writes.")

    def validate_write_contract(self) -> None:
        self.validate_common()
        required = (
            self.dedup_retention_days,
            self.max_summary_bytes,
            self.max_list_items,
            self.max_text_field_bytes,
        )
        if any(value is None for value in required):
            self._unavailable("History content and durable deduplication bounds must be configured.")
        if (
            self.max_summary_bytes != 4096
            or self.max_list_items != 20
            or self.max_text_field_bytes != 1024
        ):
            self._unavailable("The approved History content bounds are not configured.")
        if not self._dedup_retention_covers_cleanup():
            self._unavailable(
                "Durable deduplication retention must cover History retention plus the erasure cleanup allowance."
            )

    def validate_durable_replay(self) -> None:
        if not self.durable_replay_enabled:
            raise HistoryError("FEATURE_DISABLED", "Durable replay protection is not enabled.", retryable=True)
        self.validate_write_contract()

    def validate_mutations(self) -> None:
        if not self.mutations_enabled:
            raise HistoryError("FEATURE_DISABLED", "History mutations are not enabled.", retryable=True)
        self.validate_common()
        self.validate_api_auth()
        if not self.device_bindings_table_name or not self.users_table_name or not self.deletion_ledger_table_name:
            self._unavailable("Mutation identity, user, and deletion-ledger tables must be configured.")
        if (
            self.api_contract_status != "approved"
            or self.mutation_retention_days is None
            or self.mutation_retention_days != 7
            or not self._dedup_retention_covers_cleanup()
            or not self.analysis_abuse_table_name
        ):
            self._unavailable("The History mutation and receipt-retention contract is not approved.")

    def validate_recognition(self) -> None:
        if not self.recognition_enabled:
            raise HistoryError("FEATURE_DISABLED", "Recognition is not enabled.", retryable=True)
        self.validate_common()
        if self.recognition_contract_status != "approved":
            self._unavailable("The recognition contract is not approved.")
        if self.new_id_recognition_policy != "count":
            self._unavailable("The new-request-ID recognition policy is not approved.")
        if self.badge_qualification_policy != "all_server_accepted_completed_assessments":
            self._unavailable("The recognition qualification policy is not approved.")
        thresholds = [item.get("threshold") for item in self.badge_catalog if isinstance(item, dict)]
        ids = [item.get("id") for item in self.badge_catalog if isinstance(item, dict)]
        expected_ids = ["checks_1", "checks_5", "checks_20"]
        expected_keys = [
            (f"badges.{badge_id}.title", f"badges.{badge_id}.description")
            for badge_id in expected_ids
        ]
        keys = [
            (item.get("titleKey"), item.get("descriptionKey"))
            for item in self.badge_catalog if isinstance(item, dict)
        ]
        if (
            thresholds != [1, 5, 20]
            or any(isinstance(value, bool) or not isinstance(value, int) for value in thresholds)
            or ids != expected_ids
            or keys != expected_keys
        ):
            self._unavailable("The stable badge catalog is not configured.")

    def validate_lifecycle(self) -> None:
        if not self.lifecycle_enabled:
            raise HistoryError("FEATURE_DISABLED", "History lifecycle processing is not enabled.", retryable=True)
        self.validate_common()
        if not self._dedup_retention_covers_cleanup():
            self._unavailable(
                "Durable deduplication retention must cover History retention plus the erasure cleanup allowance."
            )
        if (
            self.lifecycle_start_epoch_hour is None
            or self.lifecycle_start_epoch_hour % 3600 != 0
            or self.lifecycle_max_items_per_sweep is None
            or self.lifecycle_max_items_per_sweep < 3
            or self.lifecycle_max_items_per_sweep > 1000
            or self.lifecycle_max_bucket_queries_per_sweep is None
            or self.lifecycle_max_bucket_queries_per_sweep < 40
            or self.lifecycle_max_bucket_queries_per_sweep > 1000
            or self.expiration_reconciliation_hours is None
            or self.expiration_reconciliation_hours > 168
            or self.erasure_batch_size is None
            or self.erasure_batch_size > 25
            or self.completion_stuck_seconds is None
            or self.completion_recheck_seconds is None
            or self.mutation_retention_days is None
            or not self.analysis_abuse_table_name
            or not self.deletion_ledger_table_name
        ):
            self._unavailable("The bounded History lifecycle checkpoint policy is incomplete.")

    def _dedup_retention_covers_cleanup(self) -> bool:
        if self.dedup_retention_days is None:
            return False
        dedup_seconds = self.dedup_retention_days * 86400
        required_seconds = self.retention_days * 86400 + self.erasure_sla_hours * 3600
        return dedup_seconds >= required_seconds

    @staticmethod
    def _unavailable(message: str):
        raise HistoryError("SERVER_UNAVAILABLE", message, retryable=False)
