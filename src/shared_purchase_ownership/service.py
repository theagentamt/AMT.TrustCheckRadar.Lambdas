"""Atomic purchase locks and account locators for approved account erasure.

No binding is transferred in place. All aliases must be absent or already owned
by the claimant. A deletion-fenced owner's exact bindings can be erased, after
which a different active account must obtain fresh store verification to claim.
Coverage is separately inventoried: adding locators does not discover old rows.
"""
from datetime import datetime
from decimal import Decimal
import hashlib
import re
from uuid import UUID


MAX_LINEAGE = 8
TOKEN = re.compile(r"^[A-Za-z0-9\-._~+/=]{10,4096}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}$")
INVENTORY_KEY = {"PK": "PURCHASE#CONTROL", "SK": "OWNERSHIP_INVENTORY"}
OWNER_FIELDS = {"PK", "SK", "recordType", "schemaVersion", "revision", "accountId",
                "purchaseTokenHash", "platform", "productId", "verifiedAtEpoch"}
LOCATOR_FIELDS = {"PK", "SK", "recordType", "schemaVersion", "accountId", "purchaseTokenHash"}


class OwnershipError(RuntimeError):
    """Fixed code only: never carries a token, account, SDK error or stored content."""


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    try:
        return int(value) if value == int(value) else None
    except (ValueError, OverflowError):
        return None


def token_hash(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise OwnershipError("PURCHASE_TOKEN_INVALID")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verified_lineage(purchase_token, *, fetch, product_id, now_epoch):
    """Fetch head and every predecessor now; raw tokens stay in call-local memory.

    `fetch(token)` must call the trusted store for the fixed configured package.
    An unreadable predecessor, cycle, malformed response or chain longer than
    eight fails closed. Head alone must currently grant access; predecessors may
    be expired but must contain the configured product and a valid chain link.
    """
    current = purchase_token
    hashes = []
    head = None
    for _ in range(MAX_LINEAGE):
        digest = token_hash(current)
        if digest in hashes:
            raise OwnershipError("PURCHASE_LINEAGE_INVALID")
        try:
            response = fetch(current)
        except Exception:
            raise OwnershipError("PURCHASE_VERIFICATION_UNAVAILABLE") from None
        if not isinstance(response, dict):
            raise OwnershipError("PURCHASE_VERIFICATION_INVALID")
        items = response.get("lineItems")
        if not isinstance(items, list) or not 1 <= len(items) <= 32 or any(not isinstance(i, dict) for i in items):
            raise OwnershipError("PURCHASE_VERIFICATION_INVALID")
        matching = [i for i in items if i.get("productId") == product_id]
        if len(matching) != 1:
            raise OwnershipError("PURCHASE_PRODUCT_MISMATCH")
        if head is None:
            status = response.get("subscriptionState")
            expiry = epoch(matching[0].get("expiryTime"))
            if status not in {"SUBSCRIPTION_STATE_ACTIVE", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD", "SUBSCRIPTION_STATE_CANCELED"} or expiry <= now_epoch:
                raise OwnershipError("PURCHASE_NOT_ACTIVE")
            start = epoch(response.get("startTime"))
            if start > now_epoch or start >= expiry:
                raise OwnershipError("PURCHASE_VERIFICATION_INVALID")
            head = {"normalizedStatus": {"SUBSCRIPTION_STATE_ACTIVE": "active", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD": "grace", "SUBSCRIPTION_STATE_CANCELED": "canceled"}[status],
                    "billingPeriodStartUtc": response["startTime"],
                    "billingPeriodEndUtc": matching[0]["expiryTime"],
                    "isAccessGranted": True, "status": "accepted"}
        hashes.append(digest)
        linked = response.get("linkedPurchaseToken")
        if linked is None:
            return tuple(hashes), head
        token_hash(linked)
        current = linked
    raise OwnershipError("PURCHASE_LINEAGE_LIMIT")


def epoch(value):
    try:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError()
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        result = int(parsed.timestamp())
        if result <= 0:
            raise ValueError()
        return result
    except (ValueError, TypeError, OverflowError):
        raise OwnershipError("PURCHASE_VERIFICATION_INVALID") from None


class OwnershipStore:
    def __init__(self, *, table, ledger, users_table_name, table_name, ledger_table_name,
                 client, environment, now):
        if environment not in {"dev", "uat", "prod"} or not all([users_table_name, table_name, ledger_table_name]):
            raise OwnershipError("PURCHASE_CONFIGURATION_UNAVAILABLE")
        self.table, self.ledger, self.users_name = table, ledger, users_table_name
        self.table_name, self.ledger_name, self.client = table_name, ledger_table_name, client
        self.environment, self.now = environment, now

    def _get(self, key):
        return self.table.get_item(Key=key, ConsistentRead=True).get("Item")

    def _transact(self, operations):
        # This uses a low-level DynamoDB client, like purchase_handoff's existing
        # writer. Every mutation is conditional; SDK retry cannot grant twice.
        try:
            self.client.transact_write_items(TransactItems=[serialize_operation(op) for op in operations])
        except Exception:
            raise OwnershipError("PURCHASE_TRANSACTION_UNCONFIRMED") from None

    def claim(self, account_id, hashes, *, product_id, entitlement, expected_entitlement):
        """Called only after fresh successful `verified_lineage`, never cache replay."""
        subject(account_id)
        if not isinstance(hashes, tuple) or not 1 <= len(hashes) <= MAX_LINEAGE or len(set(hashes)) != len(hashes) or any(not isinstance(h, str) or not HASH.fullmatch(h) for h in hashes):
            raise OwnershipError("PURCHASE_LINEAGE_INVALID")
        if not isinstance(product_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", product_id):
            raise OwnershipError("PURCHASE_PRODUCT_MISMATCH")
        if not isinstance(entitlement, dict) or entitlement.get("PK") != "USER#" + account_id or entitlement.get("SK") != "ENTITLEMENT#google_play#" + product_id or entitlement.get("accountId") != account_id or entitlement.get("productId") != product_id or entitlement.get("platform") != "google_play":
            raise OwnershipError("PURCHASE_ENTITLEMENT_INVALID")
        now = self.now()
        if type(now) is not int or now <= 0:
            raise OwnershipError("PURCHASE_CONFIGURATION_UNAVAILABLE")
        operations = self._active_guards(account_id)
        put_entitlement = {"TableName": self.table_name, "Item": entitlement,
                           "ConditionExpression": "attribute_not_exists(PK)"}
        if expected_entitlement is not None:
            if not isinstance(expected_entitlement, dict) or expected_entitlement.get("PK") != entitlement["PK"] or expected_entitlement.get("SK") != entitlement["SK"] or not 2 <= len(expected_entitlement) <= 50:
                raise OwnershipError("PURCHASE_ENTITLEMENT_INVALID")
            # Fence the exact observed source values, including usage counters.
            # Otherwise a slow provider response could overwrite a later debit.
            fields = sorted(expected_entitlement)
            put_entitlement.update(
                ConditionExpression=" AND ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
                ExpressionAttributeNames={f"#f{i}": field for i, field in enumerate(fields)},
                ExpressionAttributeValues={f":v{i}": expected_entitlement[field] for i, field in enumerate(fields)},
            )
        operations.append({"Put": put_entitlement})
        for digest in hashes:
            key = owner_key(digest)
            existing = self._get(key)
            if existing is not None:
                validate_owner(existing, digest, account_id, product_id)
            revision = 1 if existing is None else integer(existing["revision"]) + 1
            owner = {**key, "recordType": "PURCHASE_OWNERSHIP", "schemaVersion": 1,
                     "revision": revision, "accountId": account_id, "purchaseTokenHash": digest,
                     "platform": "google_play", "productId": product_id, "verifiedAtEpoch": now}
            put = {"TableName": self.table_name, "Item": owner,
                   "ConditionExpression": "attribute_not_exists(PK)"}
            if existing is not None:
                put.update(ConditionExpression="accountId = :account AND revision = :revision AND recordType = :type AND schemaVersion = :schema",
                           ExpressionAttributeValues={":account": account_id, ":revision": existing["revision"], ":type": "PURCHASE_OWNERSHIP", ":schema": 1})
            locator = locator_item(account_id, digest)
            old_locator = self._get({"PK": locator["PK"], "SK": locator["SK"]})
            if old_locator is not None and old_locator != locator:
                raise OwnershipError("PURCHASE_LOCATOR_INVALID")
            operations.extend([
                {"Put": put},
                {"Put": {"TableName": self.table_name, "Item": locator,
                         "ConditionExpression": "attribute_not_exists(PK) OR (accountId = :account AND purchaseTokenHash = :hash AND recordType = :type AND schemaVersion = :schema)",
                         "ExpressionAttributeValues": {":account": account_id, ":hash": digest, ":type": "PURCHASE_OWNER_LOCATOR", ":schema": 1}}},
            ])
        self._transact(operations)

    def _active_guards(self, account_id):
        return [
            {"ConditionCheck": {"TableName": self.users_name, "Key": {"PK": "USER#" + account_id, "SK": "PROFILE"},
                                "ConditionExpression": "#state = :active AND ageVerified = :yes AND #sub = :account",
                                "ExpressionAttributeNames": {"#state": "status", "#sub": "sub"},
                                "ExpressionAttributeValues": {":active": "ACTIVE", ":yes": True, ":account": account_id}}},
            {"ConditionCheck": {"TableName": self.ledger_name, "Key": {"PK": "ACCOUNT#" + account_id, "SK": "ACCOUNT_DELETION"},
                                "ConditionExpression": "attribute_not_exists(PK)"}},
        ]

    def inventory(self):
        value = self._get(INVENTORY_KEY)
        fields = {"PK", "SK", "recordType", "schemaVersion", "revision", "coverage", "environment"}
        if not isinstance(value, dict) or set(value) != fields or value.get("recordType") != "PURCHASE_OWNERSHIP_INVENTORY" or integer(value.get("schemaVersion")) != 1 or integer(value.get("revision")) is None or value["revision"] < 1 or value.get("coverage") != "VERIFIED_COMPLETE" or value.get("environment") != self.environment:
            raise OwnershipError("PURCHASE_LEGACY_COVERAGE_UNVERIFIED")
        return value

    def owned_page(self, account_id, *, cursor=None, limit=20):
        """Bounded internal traversal; caller must authenticate/fence every page.

        Returned entries contain only public purchase verification metadata.
        Cursor remains internal and must be protected by the export envelope.
        """
        subject(account_id)
        self.inventory()
        kwargs = self._query(account_id, cursor, limit, prefix="PURCHASE_TOKEN#")
        response = self.table.query(**kwargs)
        public = []
        for locator in response.get("Items", []):
            digest = validate_locator(locator, account_id)
            item = self._get(owner_key(digest))
            validate_owner(item, digest, account_id)
            public.append({"platform": item["platform"], "productId": item["productId"], "verifiedAtEpoch": item["verifiedAtEpoch"]})
        continuation = response.get("LastEvaluatedKey")
        if continuation is not None:
            self._query(account_id, continuation, limit, prefix="PURCHASE_TOKEN#")
        return {"records": public, "cursor": continuation}

    def _query(self, account_id, cursor, limit, *, prefix=None):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise OwnershipError("PURCHASE_PAGE_INVALID")
        if cursor is not None and (not isinstance(cursor, dict) or set(cursor) != {"PK", "SK"} or cursor.get("PK") != "USER#" + account_id or not isinstance(cursor.get("SK"), str) or (prefix is not None and not cursor["SK"].startswith(prefix))):
            raise OwnershipError("PURCHASE_CURSOR_INVALID")
        kwargs = {"KeyConditionExpression": "PK = :pk", "ExpressionAttributeValues": {":pk": "USER#" + account_id}, "ConsistentRead": True, "Limit": limit}
        if prefix is not None:
            kwargs["KeyConditionExpression"] += " AND begins_with(SK, :prefix)"
            kwargs["ExpressionAttributeValues"][":prefix"] = prefix
        if cursor is not None:
            kwargs["ExclusiveStartKey"] = cursor
        return kwargs

    def delete_owned_batch(self, command, *, limit=20):
        """Erase at most one page; caller writes ENTITLEMENTS receipt only on complete.

        Query from the beginning after each successful batch: deleted rows cannot
        be re-created because every purchase writer checks the fixed fence. No
        intermediate account cursor or new retention record is needed.
        """
        account_id = self._command(command)
        inventory = self.inventory()
        response = self.table.query(**self._query(account_id, None, limit))
        items = response.get("Items", [])
        deleted = 0
        for item in items:
            if not isinstance(item, dict) or item.get("PK") != "USER#" + account_id or not isinstance(item.get("SK"), str):
                raise OwnershipError("PURCHASE_ACCOUNT_ROW_INVALID")
            operations = self._deletion_guards(command, inventory)
            if item["SK"].startswith("PURCHASE_TOKEN#"):
                digest = validate_locator(item, account_id)
                owner = self._get(owner_key(digest))
                validate_owner(owner, digest, account_id)
                operations.append({"Delete": {"TableName": self.table_name, "Key": owner_key(digest),
                    "ConditionExpression": "accountId = :account AND revision = :revision AND recordType = :type AND schemaVersion = :schema",
                    "ExpressionAttributeValues": {":account": account_id, ":revision": owner["revision"], ":type": "PURCHASE_OWNERSHIP", ":schema": 1}}})
            elif item["SK"] != "ENTITLEMENT" and not item["SK"].startswith(("ENTITLEMENT#", "USAGE#")):
                raise OwnershipError("PURCHASE_ACCOUNT_FAMILY_UNKNOWN")
            operations.append({"Delete": {"TableName": self.table_name, "Key": {"PK": item["PK"], "SK": item["SK"]}}})
            self._transact(operations)
            deleted += 1
        # A page that contained any rows requires another bounded pass to verify
        # emptiness. This avoids a stale scan page becoming completion evidence.
        return {"deleted": deleted, "complete": not items and not response.get("LastEvaluatedKey")}

    def _command(self, command):
        try:
            account = command["accountId"]
            subject(account)
            operation = UUID(command["operationId"])
            fields = {"PK", "SK", "schemaVersion", "recordVersion", "environment", "eventType", "accountId", "operationId", "status", "occurredAtEpoch", "deleteByEpoch"}
            valid = (set(command) == fields and command["PK"] == "ACCOUNT#" + account and command["SK"] == "ACCOUNT_DELETION"
                     and integer(command["schemaVersion"]) == 1 and integer(command["recordVersion"]) == 1
                     and command["environment"] == self.environment and command["eventType"] == "account.deletion.requested"
                     and command["status"] == "REQUESTED" and str(operation) == command["operationId"] and operation.version == 4
                     and integer(command["occurredAtEpoch"]) is not None and command["occurredAtEpoch"] > 0
                     and integer(command["deleteByEpoch"]) == command["occurredAtEpoch"] + 86400)
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            raise OwnershipError("PURCHASE_DELETION_COMMAND_INVALID")
        current = self.ledger.get_item(Key={"PK": command["PK"], "SK": command["SK"]}, ConsistentRead=True).get("Item")
        if current != command:
            raise OwnershipError("PURCHASE_DELETION_COMMAND_UNVERIFIED")
        return account

    def _deletion_guards(self, command, inventory):
        return [
            {"ConditionCheck": {"TableName": self.ledger_name, "Key": {"PK": command["PK"], "SK": command["SK"]},
                "ConditionExpression": "operationId = :operation AND occurredAtEpoch = :epoch AND #state = :requested AND accountId = :account AND environment = :environment",
                "ExpressionAttributeNames": {"#state": "status"},
                "ExpressionAttributeValues": {":operation": command["operationId"], ":epoch": command["occurredAtEpoch"], ":requested": "REQUESTED", ":account": command["accountId"], ":environment": self.environment}}},
            {"ConditionCheck": {"TableName": self.table_name, "Key": INVENTORY_KEY,
                "ConditionExpression": "revision = :revision AND coverage = :coverage AND environment = :environment AND recordType = :type AND schemaVersion = :schema",
                "ExpressionAttributeValues": {":revision": inventory["revision"], ":coverage": "VERIFIED_COMPLETE", ":environment": self.environment, ":type": "PURCHASE_OWNERSHIP_INVENTORY", ":schema": 1}}},
        ]


def subject(value):
    if not isinstance(value, str) or not SUBJECT.fullmatch(value):
        raise OwnershipError("PURCHASE_ACCOUNT_INVALID")


def owner_key(digest):
    return {"PK": "TOKEN#" + digest, "SK": "IDEMPOTENCY"}


def locator_item(account_id, digest):
    return {"PK": "USER#" + account_id, "SK": "PURCHASE_TOKEN#" + digest,
            "recordType": "PURCHASE_OWNER_LOCATOR", "schemaVersion": 1,
            "accountId": account_id, "purchaseTokenHash": digest}


def validate_locator(item, account_id):
    if not isinstance(item, dict):
        raise OwnershipError("PURCHASE_LOCATOR_INVALID")
    digest = item.get("purchaseTokenHash")
    if not isinstance(digest, str) or not HASH.fullmatch(digest) or set(item) != LOCATOR_FIELDS or integer(item.get("schemaVersion")) != 1 or item != locator_item(account_id, digest):
        raise OwnershipError("PURCHASE_LOCATOR_INVALID")
    return digest


def validate_owner(item, digest, account_id, product_id=None):
    if isinstance(item, dict) and item.get("accountId") != account_id:
        raise OwnershipError("PURCHASE_OWNERSHIP_CONFLICT")
    if not isinstance(item, dict) or set(item) != OWNER_FIELDS or item.get("PK") != "TOKEN#" + digest or item.get("SK") != "IDEMPOTENCY" or item.get("purchaseTokenHash") != digest or item.get("recordType") != "PURCHASE_OWNERSHIP" or integer(item.get("schemaVersion")) != 1 or integer(item.get("revision")) is None or item["revision"] < 1 or integer(item.get("verifiedAtEpoch")) is None or item["verifiedAtEpoch"] <= 0 or item.get("platform") != "google_play" or not isinstance(item.get("productId"), str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", item["productId"]) or (product_id is not None and item["productId"] != product_id):
        raise OwnershipError("PURCHASE_OWNERSHIP_UNVERIFIED")


def serialize(value):
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, (int, Decimal)):
        return {"N": str(value)}
    if isinstance(value, dict):
        return {"M": {key: serialize(item) for key, item in value.items()}}
    if isinstance(value, list):
        return {"L": [serialize(item) for item in value]}
    raise OwnershipError("PURCHASE_VALUE_INVALID")


def serialize_operation(operation):
    kind, specification = next(iter(operation.items()))
    return {kind: {key: ({name: serialize(value) for name, value in item.items()}
                        if key in {"Item", "Key", "ExpressionAttributeValues"} else item)
                   for key, item in specification.items()}}
