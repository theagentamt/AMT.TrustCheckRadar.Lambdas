"""Offline explicit namespace-coverage preflight for governed History.

This is not an IAM policy simulator or an authorization decision. It detects a
specific deployment omission: Lambda subject admission without matching declared
read namespaces. No SDK, secret, endpoint or mutation adapter is present.
"""
import json
import sys
from pathlib import Path


class ScopeInputError(ValueError):
    """Malformed inputs must never be reported as covered."""


LABELS = frozenset({
    "original_profile", "selected_profile", "original_device", "selected_device",
    "original_deletion", "selected_deletion", "inventory",
    "original_authority", "selected_authority", "original_history_index", "selected_history_index",
})


def _mapping(value):
    if not isinstance(value, dict):
        raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
        result[key] = value
    return result


def _strings(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or any(not isinstance(x, str) for x in value):
        raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
    if len(set(value)) != len(value):
        raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
    return value


def declared_scope_gaps(document, requirements):
    """Return fixed requirement labels lacking an exact constrained Allow.

    Effective permissions still depend on explicit denies, boundaries, SCPs and
    live AWS behavior. Passing this check is only a prerequisite to that proof.
    """
    if not isinstance(document, dict) or not isinstance(document.get("Statement"), list):
        raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
    if not isinstance(requirements, list) or not requirements:
        raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
    labels, missing = set(), []
    for requirement in requirements:
        if not isinstance(requirement, dict) or set(requirement) != {
            "label", "action", "resource", "leadingKey", "attributes", "select",
        }:
            raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
        label = requirement["label"]
        if not isinstance(label, str) or label not in LABELS or label in labels:
            raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
        labels.add(label)
        if requirement["action"] not in ("dynamodb:GetItem", "dynamodb:Query"):
            raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
        for name in ("resource", "leadingKey"):
            value = requirement[name]
            if not isinstance(value, str) or not value or any(c in value for c in "*?${}"):
                raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
        attributes = set(_strings(requirement["attributes"]))
        expected_select = "SPECIFIC_ATTRIBUTES" if requirement["action"] == "dynamodb:Query" else None
        if requirement["select"] != expected_select:
            raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
        covered = False
        for statement in document["Statement"]:
            if not isinstance(statement, dict):
                raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
            if statement.get("Effect") != "Allow":
                continue
            actions = _strings(statement.get("Action"))
            resources = _strings(statement.get("Resource"))
            if requirement["action"] not in actions or requirement["resource"] not in resources:
                continue
            condition = _mapping(statement.get("Condition") or {})
            exact = _mapping(condition.get("ForAllValues:StringEquals") or {})
            null = _mapping(condition.get("Null") or {})
            if null.get("dynamodb:Attributes") != "false" or null.get("dynamodb:LeadingKeys") != "false":
                continue
            allowed_keys = _strings(exact.get("dynamodb:LeadingKeys"))
            allowed_attributes = set(_strings(exact.get("dynamodb:Attributes")))
            if any(any(c in key for c in "*?${}") for key in allowed_keys):
                continue
            if requirement["leadingKey"] not in allowed_keys or not attributes <= allowed_attributes:
                continue
            if expected_select and _mapping(condition.get("StringEquals") or {}).get("dynamodb:Select") != expected_select:
                continue
            covered = True
        if not covered:
            missing.append(label)
    return {
        "declaredNamespacesCovered": not missing,
        "missingRequirementLabels": missing,
        "effectiveIamDecisionEvaluated": False,
        "liveEndpointAcceptanceProven": False,
    }


def main():
    if len(sys.argv) != 2:
        raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
    path = Path(sys.argv[1])
    if path.stat().st_size > 262144:
        raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
    value = json.loads(path.read_text(), object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {"policy", "requirements"}:
        raise ScopeInputError("SCOPE_INPUT_UNQUALIFIED")
    result = declared_scope_gaps(value["policy"], value["requirements"])
    print(json.dumps(result, sort_keys=True))
    return 0 if result["declaredNamespacesCovered"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ScopeInputError, OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"declaredNamespacesCovered": False, "error": "SCOPE_INPUT_UNQUALIFIED"}))
        raise SystemExit(2)
