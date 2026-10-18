from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("scope_preflight", ROOT / "scripts/check_governed_history_scope.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def inputs():
    requirements = [{"label": "selected_profile", "action": "dynamodb:GetItem",
                     "resource": "arn:aws:dynamodb:us-east-1:111111111111:table/users",
                     "leadingKey": "USER#selected-fixture", "attributes": ["PK", "SK", "sub", "status", "ageVerified"],
                     "select": None}]
    policy = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": ["dynamodb:GetItem"],
        "Resource": requirements[0]["resource"], "Condition": {
            "ForAllValues:StringEquals": {"dynamodb:Attributes": requirements[0]["attributes"],
                                           "dynamodb:LeadingKeys": ["USER#original-fixture"]},
            "Null": {"dynamodb:Attributes": "false", "dynamodb:LeadingKeys": "false"},
        },
    }]}
    return policy, requirements


def test_original_actor_only_policy_blocks_new_selected_actor():
    policy, requirements = inputs()
    assert MODULE.declared_scope_gaps(policy, requirements) == {
        "declaredNamespacesCovered": False, "missingRequirementLabels": ["selected_profile"],
        "effectiveIamDecisionEvaluated": False, "liveEndpointAcceptanceProven": False,
    }


def test_exact_selected_union_covers_both_without_mutating_inputs():
    policy, requirements = inputs()
    policy["Statement"][0]["Condition"]["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"].append("USER#selected-fixture")
    requirements.append(dict(requirements[0], label="original_profile", leadingKey="USER#original-fixture"))
    before = deepcopy((policy, requirements))
    assert MODULE.declared_scope_gaps(policy, requirements)["declaredNamespacesCovered"] is True
    assert (policy, requirements) == before


@pytest.mark.parametrize("mutate", [
    lambda p, r: p["Statement"][0].update(Resource="*"),
    lambda p, r: p["Statement"][0].update(Action="dynamodb:*"),
    lambda p, r: p["Statement"][0].update(Effect="Deny"),
    lambda p, r: p["Statement"][0]["Condition"]["Null"].pop("dynamodb:LeadingKeys"),
    lambda p, r: p["Statement"][0]["Condition"]["ForAllValues:StringEquals"].update({"dynamodb:LeadingKeys": ["USER#*"]}),
    lambda p, r: p["Statement"][0]["Condition"]["ForAllValues:StringEquals"].update({"dynamodb:Attributes": ["PK", "SK"]}),
])
def test_missing_exact_constraint_never_covers(mutate):
    policy, requirements = inputs()
    mutate(policy, requirements)
    assert MODULE.declared_scope_gaps(policy, requirements)["declaredNamespacesCovered"] is False


def test_query_requires_specific_attributes_and_selected_partition():
    policy, requirements = inputs()
    row = requirements[0]
    row.update(label="selected_history_index", action="dynamodb:Query", leadingKey="V1#k1#fixture", select="SPECIFIC_ATTRIBUTES")
    statement = policy["Statement"][0]
    statement["Action"] = ["dynamodb:Query"]
    condition = statement["Condition"]
    condition["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"] = [row["leadingKey"]]
    assert MODULE.declared_scope_gaps(policy, requirements)["declaredNamespacesCovered"] is False
    condition["StringEquals"] = {"dynamodb:Select": "ALL_ATTRIBUTES"}
    assert MODULE.declared_scope_gaps(policy, requirements)["declaredNamespacesCovered"] is False
    condition["StringEquals"]["dynamodb:Select"] = "SPECIFIC_ATTRIBUTES"
    result = MODULE.declared_scope_gaps(policy, requirements)
    assert result["declaredNamespacesCovered"] is True
    assert result["effectiveIamDecisionEvaluated"] is False
    assert result["liveEndpointAcceptanceProven"] is False


@pytest.mark.parametrize("change", [
    lambda p, r: r[0].update(action="dynamodb:PutItem"),
    lambda p, r: r[0].update(leadingKey="USER#*"),
    lambda p, r: r[0].update(resource="arn:aws:dynamodb:*"),
    lambda p, r: r.append(dict(r[0])),
    lambda p, r: r[0].update(select="ALL_ATTRIBUTES"),
    lambda p, r: r[0].update(attributes=[]),
    lambda p, r: p.update(Statement="unqualified"),
    lambda p, r: p["Statement"][0].update(Condition=["not-a-condition"]),
    lambda p, r: p["Statement"][0]["Condition"].update({"ForAllValues:StringEquals": ["bad"]}),
    lambda p, r: r[0].update(label="private_label_must_not_be_output"),
])
def test_unqualified_inputs_fail_closed(change):
    policy, requirements = inputs()
    change(policy, requirements)
    with pytest.raises(MODULE.ScopeInputError):
        MODULE.declared_scope_gaps(policy, requirements)
