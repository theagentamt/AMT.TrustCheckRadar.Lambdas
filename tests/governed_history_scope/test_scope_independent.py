"""Independent adversarial coverage checks; all fixtures are synthetic/offline."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_governed_history_scope.py"
SPEC = importlib.util.spec_from_file_location("scope_independent", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture():
    requirement = {"label": "selected_profile", "action": "dynamodb:GetItem",
                   "resource": "arn:aws:dynamodb:us-east-1:111111111111:table/users",
                   "leadingKey": "USER#selected-synthetic", "attributes": ["PK", "SK", "sub"], "select": None}
    policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "dynamodb:GetItem",
              "Resource": requirement["resource"], "Condition": {"ForAllValues:StringEquals": {
                  "dynamodb:LeadingKeys": [requirement["leadingKey"]], "dynamodb:Attributes": requirement["attributes"]},
                  "Null": {"dynamodb:Attributes": "false", "dynamodb:LeadingKeys": "false"}}}]}
    return {"policy": policy, "requirements": [requirement]}


def invoke(tmp_path, raw):
    path = tmp_path / "scope.json"
    path.write_text(raw)
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(path)],
                            capture_output=True, text=True, check=False)
    return result, json.loads(result.stdout)


def test_coverage_does_not_mutate_input_or_claim_effective_permissions():
    value = fixture()
    before = deepcopy(value)
    result = MODULE.declared_scope_gaps(value["policy"], value["requirements"])
    assert value == before
    assert result["declaredNamespacesCovered"] is True
    assert result["effectiveIamDecisionEvaluated"] is False
    assert result["liveEndpointAcceptanceProven"] is False


@pytest.mark.parametrize("field", ["dynamodb:Attributes", "dynamodb:LeadingKeys"])
def test_boolean_false_does_not_replace_required_iam_null_string(field):
    value = fixture()
    value["policy"]["Statement"][0]["Condition"]["Null"][field] = False
    assert MODULE.declared_scope_gaps(value["policy"], value["requirements"])["declaredNamespacesCovered"] is False


def test_duplicate_requirement_attributes_are_unqualified():
    value = fixture()
    value["requirements"][0]["attributes"].append("PK")
    with pytest.raises(MODULE.ScopeInputError):
        MODULE.declared_scope_gaps(value["policy"], value["requirements"])


def test_nearby_resource_is_not_covered_by_exact_table_name():
    value = fixture()
    value["requirements"][0]["resource"] += "-different"
    assert MODULE.declared_scope_gaps(value["policy"], value["requirements"])["declaredNamespacesCovered"] is False


def test_cli_missing_scope_is_finite_and_never_echoes_identifier(tmp_path):
    value = fixture()
    value["policy"]["Statement"][0]["Condition"]["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"] = ["USER#original-synthetic"]
    process, result = invoke(tmp_path, json.dumps(value))
    assert process.returncode == 1
    assert result["missingRequirementLabels"] == ["selected_profile"]
    assert "selected-synthetic" not in process.stdout + process.stderr
    assert "original-synthetic" not in process.stdout + process.stderr


def test_cli_unknown_label_is_not_echoed(tmp_path):
    value = fixture()
    value["requirements"][0]["label"] = "private-identifier-not-for-output"
    process, result = invoke(tmp_path, json.dumps(value))
    assert process.returncode == 2
    assert result == {"declaredNamespacesCovered": False, "error": "SCOPE_INPUT_UNQUALIFIED"}
    assert "private-identifier-not-for-output" not in process.stdout + process.stderr


def test_cli_malformed_json_is_not_echoed(tmp_path):
    process, result = invoke(tmp_path, '{"private-identifier-not-for-output":')
    assert process.returncode == 2
    assert result["declaredNamespacesCovered"] is False
    assert "private-identifier-not-for-output" not in process.stdout + process.stderr


def test_cli_oversize_input_is_rejected_without_echo(tmp_path):
    process, result = invoke(tmp_path, "private-identifier-not-for-output" * 10000)
    assert process.returncode == 2
    assert result["declaredNamespacesCovered"] is False
    assert "private-identifier-not-for-output" not in process.stdout + process.stderr


def test_cli_duplicate_json_keys_cannot_replace_a_deny_with_allow(tmp_path):
    value = fixture()
    raw = json.dumps(value)
    raw = raw.replace('"Effect": "Allow"', '"Effect": "Deny", "Effect": "Allow"')
    process, result = invoke(tmp_path, raw)
    assert process.returncode == 2
    assert result == {"declaredNamespacesCovered": False, "error": "SCOPE_INPUT_UNQUALIFIED"}
