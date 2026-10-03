"""Minimized read-only identity fences for governed History."""
import hashlib
import hmac

from shared_check_authority.core import AuthorityError, FINGERPRINT_PATTERN, SUBJECT_PATTERN, integral
from shared_check_authority.inventory import INVENTORY_KEY
from shared_history.security import jwt_subject

PROFILE_FIELDS = ("PK", "SK", "sub", "status", "ageVerified")
DEVICE_FIELDS = ("PK", "SK", "recordType", "bindingFingerprint", "stateVersion", "accountId", "status")
DELETION_FIELDS = ("PK", "SK")
INVENTORY_FIELDS = ("PK", "SK", "recordType", "schemaVersion", "revision", "coverage", "issuedKeys")


def _get(authority, table, key, fields):
    names = {f"#field{index}": field for index, field in enumerate(fields)}
    request = {"Key": key, "ConsistentRead": True,
               "ProjectionExpression": ", ".join(names), "ExpressionAttributeNames": names}
    return authority.ddb.Table(table).get_item(**request).get("Item")


def authorize(authority, event):
    try:
        account = jwt_subject(event, authority.s, now=authority.now)
        if not isinstance(account, str) or not SUBJECT_PATTERN.fullmatch(account):
            raise ValueError()
    except Exception:
        raise AuthorityError("AUTHENTICATION_REQUIRED") from None
    try:
        profile = _get(authority, authority.s.users_table,
                       {"PK": f"USER#{account}", "SK": "PROFILE"}, PROFILE_FIELDS)
        if (type(profile) is not dict
                or set(profile) != {"PK", "SK", "sub", "status", "ageVerified"}
                or profile.get("sub") != account or profile.get("status") != "ACTIVE"
                or profile.get("ageVerified") is not True):
            raise AuthorityError("ACCOUNT_UNAVAILABLE")
        deletion = _get(authority, authority.s.deletion_table,
                        {"PK": f"ACCOUNT#{account}", "SK": "ACCOUNT_DELETION"},
                        DELETION_FIELDS)
        if deletion is not None:
            raise AuthorityError("ACCOUNT_UNAVAILABLE")

        headers = event.get("headers")
        values = [value for key, value in headers.items()
                  if isinstance(key, str) and key.lower() == "x-device-binding-fingerprint"] if isinstance(headers, dict) else []
        if len(values) != 1 or not isinstance(values[0], str) or not FINGERPRINT_PATTERN.fullmatch(values[0]):
            raise AuthorityError("ACTIVE_DEVICE_REQUIRED")
        fingerprint = values[0]
        pointer = _get(authority, authority.s.devices_table,
                       {"PK": f"USER#{account}", "SK": "ACTIVE_BINDING"}, DEVICE_FIELDS)
        version = integral(pointer.get("stateVersion")) if pointer else None
        if (type(pointer) is not dict
                or set(pointer) != {"PK", "SK", "recordType", "bindingFingerprint", "stateVersion"}
                or pointer.get("recordType") != "ACTIVE_BINDING_POINTER"
                or version is None or version < 1
                or not isinstance(pointer.get("bindingFingerprint"), str)
                or not hmac.compare_digest(pointer["bindingFingerprint"], fingerprint)):
            raise AuthorityError("ACTIVE_DEVICE_REQUIRED")
        binding = _get(authority, authority.s.devices_table,
                       {"PK": f"USER#{account}", "SK": f"DEVICE#{fingerprint}"}, DEVICE_FIELDS)
        if (type(binding) is not dict
                or set(binding) != {"PK", "SK", "bindingFingerprint", "accountId", "status"}
                or binding.get("accountId") != account or binding.get("status") != "ACTIVE"
                or not isinstance(binding.get("bindingFingerprint"), str)
                or not hmac.compare_digest(binding["bindingFingerprint"], fingerprint)):
            raise AuthorityError("ACTIVE_DEVICE_REQUIRED")

        inventory = _get(authority, authority.s.authority_table, INVENTORY_KEY, INVENTORY_FIELDS)
        expected = {key: hashlib.sha256(value).hexdigest() for key, value in authority.s.hmac_keys.items()}
        revision = integral(inventory.get("revision")) if inventory else None
        if (type(inventory) is not dict
                or set(inventory) != {"PK", "SK", "recordType", "schemaVersion", "revision", "coverage", "issuedKeys"}
                or inventory.get("recordType") != "V1_HMAC_KEY_INVENTORY"
                or integral(inventory.get("schemaVersion")) != 1
                or revision is None or revision < 1
                or inventory.get("coverage") != "VERIFIED_COMPLETE"
                or inventory.get("issuedKeys") != expected):
            raise AuthorityError("KEY_INVENTORY_UNAVAILABLE")
        return account, fingerprint, version, revision
    except AuthorityError:
        raise
    except Exception:
        raise AuthorityError("IDENTITY_READ_UNAVAILABLE") from None
