"""Delete identity only after exact, current account-erasure evidence.

The completed fixed fence is a durable suppression control, not an expiring
receipt. retainUntilEpoch records the existing informational 120-day policy; this
module never enables TTL or retires the fence. It makes no backup-erasure claim.
"""
from decimal import Decimal
import re
from uuid import UUID


REQUIRED_COMPONENTS = (
    "SESSION_REVOCATION", "DEVICE_BINDINGS", "DEVICE_RECOVERY", "ANALYSIS_ABUSE",
    "HISTORY", "CAMPAIGN", "CAMPAIGN_OUTBOX", "ENTITLEMENTS", "V1_AUTHORITY",
    "USER_PROFILE", "IDENTITY",
)
RETENTION_SECONDS = 120 * 86400
COMMAND_FIELDS = {"PK", "SK", "schemaVersion", "recordVersion", "environment", "eventType",
                  "accountId", "operationId", "status", "occurredAtEpoch", "deleteByEpoch"}
RECEIPT_FIELDS = {"PK", "SK", "schemaVersion", "recordVersion", "environment", "eventType",
                  "component", "status", "operationId", "occurredAtEpoch", "requestOccurredAtEpoch", "retainUntilEpoch"}
INVENTORY_FIELDS = {"PK", "SK", "recordType", "schemaVersion", "revision", "environment",
                    "coverage", "manifestSha256", "requiredComponents", "usernameIsSubVerified", "approvedAtEpoch"}
SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}$")


class FinalizationError(RuntimeError):
    """Fixed diagnostic only; never stores identity attributes or SDK messages."""


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    try:
        return int(value) if value == int(value) else None
    except (ValueError, OverflowError):
        return None


def require(condition, code):
    if not condition:
        raise FinalizationError(code)


class Finalizer:
    def __init__(self, *, ledger_table, ledger_table_name, client, cognito,
                 user_pool_id, environment, now, enabled=False,
                 manifest_sha256=None, inventory_revision=None,
                 required_components=REQUIRED_COMPONENTS):
        # A copied function/package cannot activate identity deletion by default.
        require(type(enabled) is bool, "FINALIZER_CONFIGURATION_INVALID")
        self.enabled = enabled
        self.ledger, self.ledger_name, self.client = ledger_table, ledger_table_name, client
        self.cognito, self.pool, self.environment, self.now = cognito, user_pool_id, environment, now
        self.manifest, self.revision = manifest_sha256, inventory_revision
        self.components = tuple(required_components)

    def finalize(self, command):
        require(self.enabled, "FINALIZER_NOT_ENABLED")
        require(self.environment in {"dev", "uat", "prod"}
                and isinstance(self.ledger_name, str) and self.ledger_name
                and isinstance(self.pool, str) and re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+", self.pool)
                and isinstance(self.manifest, str) and re.fullmatch(r"[0-9a-f]{64}", self.manifest)
                and type(self.revision) is int and self.revision > 0
                and self.components == REQUIRED_COMPONENTS,
                "FINALIZER_CONFIGURATION_INVALID")
        now = self._now()
        self._validate_command(command, now, self.environment)
        current = self._get({"PK": command["PK"], "SK": command["SK"]})
        if self._completed(current, command, now):
            # The original atomic completion fence remains authoritative after
            # informational component receipts expire. Never call Cognito again.
            return {"complete": True, "alreadyComplete": True}
        require(current == command, "FINALIZER_COMMAND_UNVERIFIED")
        inventory = self._inventory(command, now)
        receipts = []
        for component in self.components:
            if component == "IDENTITY":
                continue
            receipt = self._get({"PK": command["PK"], "SK": "ACCOUNT_DELETION#" + component})
            self._validate_receipt(receipt, command, component, now)
            receipts.append(receipt)
        identity_key = {"PK": command["PK"], "SK": "ACCOUNT_DELETION#IDENTITY"}
        # No separate identity receipt may predate the atomic completed fence.
        require(self._get(identity_key) is None, "FINALIZER_IDENTITY_EVIDENCE_INVALID")
        proofs = [inventory, *receipts]
        # Check the entire immutable proof set before the irreversible provider
        # call, then check again atomically when recording completion.
        self._transact([self._condition(row) for row in [command, *proofs]])
        self._delete_identity(command["accountId"])
        completed_at = self._now()
        require(completed_at >= now, "FINALIZER_CLOCK_INVALID")
        for receipt in receipts:
            self._validate_receipt(receipt, command, receipt["component"], completed_at)
        identity = {**identity_key, "schemaVersion": 1, "recordVersion": 1,
                    "environment": self.environment, "eventType": "account.deletion.component.completed",
                    "component": "IDENTITY", "status": "COMPLETE", "operationId": command["operationId"],
                    "occurredAtEpoch": completed_at, "requestOccurredAtEpoch": command["occurredAtEpoch"],
                    "retainUntilEpoch": completed_at + RETENTION_SECONDS}
        completed = {**command, "eventType": "account.deletion.completed", "status": "COMPLETE",
                     "completedAtEpoch": completed_at, "retainUntilEpoch": completed_at + RETENTION_SECONDS}
        put = {"TableName": self.ledger_name, "Item": completed, **match_row(command)}
        operations = [self._condition(row) for row in proofs]
        operations.extend([
            {"Put": {"TableName": self.ledger_name, "Item": identity, "ConditionExpression": "attribute_not_exists(PK)"}},
            {"Put": put},
        ])
        try:
            self._transact(operations)
        except FinalizationError:
            # Another finalizer or a lost transaction acknowledgment may already
            # have committed the exact completed fence. Reading it is safe replay.
            if self._completed(self._get({"PK": command["PK"], "SK": command["SK"]}), command, self._now()):
                return {"complete": True, "alreadyComplete": True}
            raise
        return {"complete": True, "alreadyComplete": False}

    def _now(self):
        value = self.now()
        require(type(value) is int and value > 0, "FINALIZER_CLOCK_INVALID")
        return value

    def _get(self, key):
        try:
            return self.ledger.get_item(Key=key, ConsistentRead=True).get("Item")
        except Exception:
            raise FinalizationError("FINALIZER_STORE_UNAVAILABLE") from None

    def _inventory(self, command, now):
        key = {"PK": "INVENTORY#" + self.environment, "SK": "ACCOUNT_DATA_INVENTORY"}
        row = self._get(key)
        validate_inventory(row, self.environment, self.manifest, self.components,
                           expected_revision=self.revision, now_epoch=now)
        require(row["approvedAtEpoch"] < command["occurredAtEpoch"], "FINALIZER_INVENTORY_UNVERIFIED")
        return row

    @staticmethod
    def _validate_command(command, now, environment):
        try:
            operation = UUID(command["operationId"])
            account = command["accountId"]
            valid = (isinstance(command, dict) and set(command) == COMMAND_FIELDS
                     and isinstance(account, str) and SUBJECT.fullmatch(account)
                     and command["PK"] == "ACCOUNT#" + account and command["SK"] == "ACCOUNT_DELETION"
                     and integer(command["schemaVersion"]) == 1 and integer(command["recordVersion"]) == 1
                     and command["environment"] == environment and command["eventType"] == "account.deletion.requested"
                     and command["status"] == "REQUESTED" and str(operation) == command["operationId"] and operation.version == 4
                     and integer(command["occurredAtEpoch"]) is not None and 0 < command["occurredAtEpoch"] <= now
                     and integer(command["deleteByEpoch"]) == command["occurredAtEpoch"] + 86400)
        except (TypeError, ValueError, KeyError, AttributeError):
            valid = False
        require(valid, "FINALIZER_COMMAND_INVALID")

    def _validate_receipt(self, receipt, command, component, now):
        require(isinstance(receipt, dict) and set(receipt) == RECEIPT_FIELDS
                and receipt.get("PK") == command["PK"] and receipt.get("SK") == "ACCOUNT_DELETION#" + component
                and integer(receipt.get("schemaVersion")) == 1 and integer(receipt.get("recordVersion")) == 1
                and receipt.get("environment") == self.environment
                and receipt.get("eventType") == "account.deletion.component.completed"
                and receipt.get("component") == component and receipt.get("status") == "COMPLETE"
                and receipt.get("operationId") == command["operationId"]
                and integer(receipt.get("requestOccurredAtEpoch")) == command["occurredAtEpoch"]
                and integer(receipt.get("occurredAtEpoch")) is not None
                and command["occurredAtEpoch"] <= receipt["occurredAtEpoch"] <= now
                and integer(receipt.get("retainUntilEpoch")) == receipt["occurredAtEpoch"] + RETENTION_SECONDS
                and receipt["retainUntilEpoch"] > now,
                "FINALIZER_COMPONENT_UNVERIFIED")

    @staticmethod
    def _completed(current, command, now):
        if not isinstance(current, dict) or current.get("status") != "COMPLETE":
            return False
        return (set(current) == COMMAND_FIELDS | {"completedAtEpoch", "retainUntilEpoch"}
                and all(current.get(key) == value for key, value in command.items() if key not in {"status", "eventType"})
                and integer(current.get("schemaVersion")) == 1 and integer(current.get("recordVersion")) == 1
                and current.get("eventType") == "account.deletion.completed"
                and integer(current.get("completedAtEpoch")) is not None
                and command["occurredAtEpoch"] <= current["completedAtEpoch"] <= now
                and integer(current.get("retainUntilEpoch")) == current["completedAtEpoch"] + RETENTION_SECONDS)

    def _delete_identity(self, account):
        try:
            response = self.cognito.admin_get_user(UserPoolId=self.pool, Username=account)
        except Exception as error:
            if code(error) == "UserNotFoundException":
                return
            raise FinalizationError("FINALIZER_IDENTITY_UNAVAILABLE") from None
        require(isinstance(response, dict) and response.get("Username") == account,
                "FINALIZER_IDENTITY_MAPPING_INVALID")
        attributes = response.get("UserAttributes")
        require(isinstance(attributes, list) and len(attributes) <= 100,
                "FINALIZER_IDENTITY_MAPPING_INVALID")
        subs = [item.get("Value") for item in attributes if isinstance(item, dict) and item.get("Name") == "sub"]
        require(len(subs) == 1 and subs[0] == account, "FINALIZER_IDENTITY_MAPPING_INVALID")
        try:
            self.cognito.admin_delete_user(UserPoolId=self.pool, Username=account)
        except Exception as error:
            if code(error) != "UserNotFoundException":
                raise FinalizationError("FINALIZER_IDENTITY_DELETE_UNCONFIRMED") from None

    def _condition(self, row):
        return {"ConditionCheck": {"TableName": self.ledger_name,
                                   "Key": {"PK": row["PK"], "SK": row["SK"]}, **match_row(row)}}

    def _transact(self, operations):
        try:
            self.client.transact_write_items(TransactItems=[serialize_operation(op) for op in operations])
        except Exception:
            raise FinalizationError("FINALIZER_TRANSACTION_UNCONFIRMED") from None


def code(error):
    try:
        return error.response["Error"]["Code"]
    except (AttributeError, KeyError, TypeError):
        return None


def match_row(row):
    fields = sorted(row)
    return {"ConditionExpression": " AND ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
            "ExpressionAttributeNames": {f"#f{i}": field for i, field in enumerate(fields)},
            "ExpressionAttributeValues": {f":v{i}": row[field] for i, field in enumerate(fields)}}


def validate_inventory(value, environment, expected_manifest_sha,
                       required_components=REQUIRED_COMPONENTS, *,
                       expected_revision=None, now_epoch=None):
    """Shared admission/finalization gate; never creates or approves the marker.

    Admission must additionally require request time strictly greater than the
    returned approvedAtEpoch and include inventory_condition in its transaction.
    A material revision requires a new approval epoch and controlled migration;
    never reset an existing request's identity or original occurrence time.
    """
    require(environment in {"dev", "uat", "prod"}
            and isinstance(expected_manifest_sha, str) and re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha)
            and isinstance(required_components, (tuple, list)) and tuple(required_components) == REQUIRED_COMPONENTS
            and (expected_revision is None or type(expected_revision) is int and expected_revision > 0)
            and (now_epoch is None or type(now_epoch) is int and now_epoch > 0),
            "FINALIZER_CONFIGURATION_INVALID")
    require(isinstance(value, dict) and set(value) == INVENTORY_FIELDS
            and value.get("PK") == "INVENTORY#" + environment and value.get("SK") == "ACCOUNT_DATA_INVENTORY"
            and value.get("recordType") == "ACCOUNT_DATA_INVENTORY"
            and integer(value.get("schemaVersion")) == 1
            and integer(value.get("revision")) is not None and value["revision"] > 0
            and (expected_revision is None or value["revision"] == expected_revision)
            and value.get("environment") == environment and value.get("coverage") == "VERIFIED_COMPLETE"
            and value.get("manifestSha256") == expected_manifest_sha
            and value.get("requiredComponents") == list(REQUIRED_COMPONENTS)
            and value.get("usernameIsSubVerified") is True
            and integer(value.get("approvedAtEpoch")) is not None and value["approvedAtEpoch"] > 0
            and (now_epoch is None or value["approvedAtEpoch"] <= now_epoch),
            "FINALIZER_INVENTORY_UNVERIFIED")
    return dict(value)


def inventory_condition(table_name, inventory):
    """Native-value transaction guard; serialize for a low-level DynamoDB client."""
    return {"ConditionCheck": {"TableName": table_name,
                               "Key": {"PK": inventory["PK"], "SK": inventory["SK"]},
                               **match_row(inventory)}}


def serialize(value):
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, (int, Decimal)):
        return {"N": str(value)}
    if isinstance(value, list):
        return {"L": [serialize(item) for item in value]}
    raise FinalizationError("FINALIZER_VALUE_INVALID")


def serialize_operation(operation):
    kind, specification = next(iter(operation.items()))
    return {kind: {key: ({name: serialize(value) for name, value in item.items()}
                        if key in {"Item", "Key", "ExpressionAttributeValues"} else item)
                   for key, item in specification.items()}}


def completed_fence(value, environment, now, original_command=None):
    """Validate a durable terminal fence for harmless downstream replay skipping."""
    if not isinstance(value, dict):
        return False
    command = {key:value[key] for key in COMMAND_FIELDS if key in value}
    command.update(status="REQUESTED",eventType="account.deletion.requested")
    try:
        Finalizer._validate_command(command,now,environment)
    except FinalizationError:
        return False
    if original_command is not None and command != original_command:
        return False
    return Finalizer._completed(value,command,now)
