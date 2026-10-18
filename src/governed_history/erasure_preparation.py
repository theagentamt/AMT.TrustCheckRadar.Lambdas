"""Internal, non-dispatching preparation for selective governed assessment erasure.

This is not an HTTP contract, mutation result, dispatch capability or clear-epoch
implementation. The opaque plan cannot be serialized or executed. A future
executor must freshly prepare, authorize and fence its atomic commit under a
separately reviewed mutation/idempotency/lifecycle contract.
"""
from dataclasses import dataclass, field
import hmac
import re
from types import MappingProxyType, SimpleNamespace

from governed_history.security import authorize
from shared_check_authority.core import AuthorityError, OWNER_POLICY
from shared_governed_history.projection import RESULT, plain, validate_stored_receipt
from shared_history.security import jwt_subject


REMOVE_FIELDS = frozenset({"governedHistory", "governedHistoryDigest", "GSI2PK", "GSI2SK", "resultSummary"})
# Current receipt schema only. Unknown fields at preparation require a reviewed
# schema update; fields added later cannot be fenced by top-level item equality
# in DynamoDB and MUST be preserved, never treated as an all-copy success.
RECEIPT_FIELDS = frozenset({
    "PK", "SK", "recordType", "checkId", "payloadHmac", "clientCheckId", "projectionScope",
    "state", "chargedChecks", "receiptId", "processingOutcome", "basis", "grantRevision",
    "authorityRevision", "policyVersion", "periodSK", "executionToken", "settleByEpoch",
    "GSI1PK", "GSI1SK", "retentionDeadlineEpoch", "purchaseUsageKey", "accessUntilEpoch",
    "urlTransportVersion", "messageTransportVersion", "governedHistoryAcceptedAtEpoch",
    "governedHistorySourceType", "governedHistoryResultType", "expiresAt", "assessmentEpoch",
    "operationsReceipt", "operationsTerminalReasonCode", "operationsProjection",
}) | REMOVE_FIELDS
IDENTITY_ERRORS = frozenset({"AUTHENTICATION_REQUIRED", "ACCOUNT_UNAVAILABLE", "ACTIVE_DEVICE_REQUIRED",
                             "KEY_INVENTORY_UNAVAILABLE", "IDENTITY_READ_UNAVAILABLE"})


class ErasurePreparationError(Exception):
    """Finite source-only failure; no SDK, proof or row text is retained."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _freeze(value):
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError()
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, int, bool):
        return value
    raise ValueError()


def _thaw(value):
    if isinstance(value, MappingProxyType):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True, repr=False)
class _Snapshot:
    table: str = field(repr=False)
    key: object = field(repr=False)
    fields: object = field(repr=False)

    def __repr__(self):
        return "<PrivateErasureSnapshot>"


@dataclass(frozen=True, slots=True, repr=False)
class ErasurePreparation:
    _identity: tuple = field(repr=False)
    _receipt: _Snapshot = field(repr=False)
    _absent_known_fields: frozenset = field(repr=False)
    _prepared_at_epoch: int = field(repr=False)

    def __repr__(self):
        return "<ErasurePreparation PREPARED_NOT_AUTHORIZED>"

    def __reduce_ex__(self, protocol):
        raise TypeError("ERASURE_PLAN_NOT_SERIALIZABLE")

    def __getstate__(self):
        raise TypeError("ERASURE_PLAN_NOT_SERIALIZABLE")

    def public_summary(self):
        return {"status": "PREPARED_NOT_AUTHORIZED", "executionAuthorized": False,
                "mutationSucceeded": False, "removalFieldCount": len(REMOVE_FIELDS),
                "freshPreparationRequiredAtDispatch": True, "atomicIdentityAndReceiptFenceRequired": True,
                "idempotencyAndLifecycleContractStillRequired": True}


class _AcknowledgedReads:
    """Read-only facade also checks acknowledgments for minimized identity reads."""

    def __init__(self, authority):
        self.authority = authority
        self.snapshots = []

    def Table(self, name):
        reader = self

        class ReadOnlyTable:
            def get_item(self, **request):
                return reader.read(name, request)

        return ReadOnlyTable()

    def read(self, table, request):
        try:
            if request.get("ConsistentRead") is not True:
                raise ValueError()
            response = self.authority.ddb.Table(table).get_item(**request)
            if (type(response) is not dict or type(response.get("ResponseMetadata")) is not dict
                    or type(response["ResponseMetadata"].get("HTTPStatusCode")) is not int
                    or response["ResponseMetadata"]["HTTPStatusCode"] != 200):
                raise ValueError()
            item = response.get("Item")
            if item is not None and (type(item) is not dict
                                     or any(item.get(k) != v for k, v in request["Key"].items())):
                raise ValueError()
            snapshot = _Snapshot(table, _freeze(dict(request["Key"])), _freeze(plain(item)))
            self.snapshots.append(snapshot)
            # Validation and the plan must use the SAME detached capture, not
            # an SDK-owned response mutable across subsequent reads/callbacks.
            return {"Item": _thaw(snapshot.fields)}
        except Exception:
            raise ErasurePreparationError("ERASURE_READ_UNAVAILABLE") from None


def _clock(authority):
    try:
        value = authority.now()
        if type(value) is not int or value < 0:
            raise ValueError()
        return value
    except Exception:
        raise ErasurePreparationError("ERASURE_CLOCK_UNAVAILABLE") from None


def _validate_receipt(authority, account, key, expected_result_id, row, now):
    try:
        row = plain(row)
        if (type(row) is not dict or not set(row) <= RECEIPT_FIELDS
                or row.get("PK") != key["PK"] or row.get("SK") != key["SK"]
                or row.get("policyVersion") != OWNER_POLICY):
            raise ValueError()
        proof = key["SK"][len("CHECK#"):]
        digest = row.get("payloadHmac")
        if (row.get("checkId") != proof or type(digest) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError()
        key_id = authority._verify_token(account, proof, digest, allow_expired=True)
        if key["PK"] != authority._partition(account, key_id):
            raise ValueError()
        projection = validate_stored_receipt(row, now=now)
        accounting = projection["accounting"]
        summary = row.get("resultSummary")
        sorts = {f'{row["expiresAt"]:012d}#{key["PK"]}#{key["SK"]}'}
        if "operationsReceipt" in row:
            from shared_operations import retirement_metadata
            metadata = retirement_metadata(row)
            sorts.add(f'{min(row["expiresAt"], metadata["expiresAtEpoch"]):012d}#{key["PK"]}#{key["SK"]}')
        if (projection["resultId"] != expected_result_id
                or row.get("receiptId") != accounting["receiptId"]
                or row["receiptId"] != authority._mac(key_id, "receipt", proof)[:32]
                or type(row.get("chargedChecks")) is not int
                or row["chargedChecks"] != accounting["chargedChecks"]
                or row.get("processingOutcome") != projection["processingOutcome"]
                or type(row.get("assessmentEpoch")) is not int
                or row["assessmentEpoch"] != projection["settledAtEpoch"]
                or row.get("governedHistoryAcceptedAtEpoch") != projection["acceptedAtEpoch"]
                or row.get("governedHistorySourceType") != projection["sourceType"]
                or row.get("governedHistoryResultType") != projection["resultType"]
                or row.get("basis") not in {"paid", "trial", "complimentary"}
                or row["chargedChecks"] != int(projection["processingOutcome"] == "complete"
                                               and row["basis"] != "complimentary")
                or row.get("GSI1PK") != "V1_EXPIRING"
                or row.get("GSI1SK") not in sorts
                or type(summary) is not dict or summary.get("checkId") != row.get("clientCheckId")
                or {k: v for k, v in summary.items() if k != "checkId"} != projection["outcome"]):
            raise ValueError()
        family = "messageTransportVersion" if projection["resultType"] == "message" else "urlTransportVersion"
        other = "urlTransportVersion" if family == "messageTransportVersion" else "messageTransportVersion"
        if row.get(family) != projection["assessmentTransportVersion"] or other in row:
            raise ValueError()
        return row
    except Exception:
        raise ErasurePreparationError("ERASURE_RECORD_NOT_PREPARED") from None


def prepare_receipt_erasure(authority, event, server_lookup_key, expected_result_id):
    """Capture a private, authenticated snapshot; perform only strong reads.

    server_lookup_key must come from trusted server-side resolution, never from
    HTTP JSON. Even here its proof, owned PK/SK and canonical result are verified.
    Missing/expired/removed/invalid rows are NOT a prior-success acknowledgment.
    """
    started = _clock(authority)
    reader = _AcknowledgedReads(authority)
    identity = SimpleNamespace(s=authority.s, ddb=reader, now=lambda: started)
    try:
        account, _, _, _ = authorize(identity, event)
    except ErasurePreparationError:
        raise
    except AuthorityError as error:
        code = error.code if error.code in IDENTITY_ERRORS else "IDENTITY_READ_UNAVAILABLE"
        raise ErasurePreparationError(code) from None
    try:
        if (type(server_lookup_key) is not dict or set(server_lookup_key) != {"PK", "SK"}
                or type(server_lookup_key["SK"]) is not str
                or not server_lookup_key["SK"].startswith("CHECK#")
                or type(expected_result_id) is not str or not RESULT.fullmatch(expected_result_id)):
            raise ValueError()
        proof = server_lookup_key["SK"][len("CHECK#"):]
        key_id, _, _, _ = authority._token_parts(proof)
        key = {"PK": authority._partition(account, key_id), "SK": "CHECK#" + proof}
        if server_lookup_key != key:
            raise ValueError()
    except Exception:
        raise ErasurePreparationError("ERASURE_REFERENCE_INVALID") from None
    reader.read(authority.s.authority_table, {"Key": key, "ConsistentRead": True})
    finished = _clock(authority)
    if finished < started:
        raise ErasurePreparationError("ERASURE_CLOCK_UNAVAILABLE")
    try:
        final_account = jwt_subject(event, authority.s, now=lambda: finished)
        if not hmac.compare_digest(final_account, account):
            raise ValueError()
    except Exception:
        raise ErasurePreparationError("AUTHENTICATION_REQUIRED") from None
    row = _validate_receipt(authority, account, key, expected_result_id,
                            _thaw(reader.snapshots[-1].fields), finished)
    return ErasurePreparation(tuple(reader.snapshots[:-1]), reader.snapshots[-1],
                             RECEIPT_FIELDS - set(row), finished)
