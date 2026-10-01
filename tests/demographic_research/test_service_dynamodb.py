import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid

import boto3
from moto import mock_aws
import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "demographic_research"
ACCOUNT = "47debb73-444b-4bb1-9889-fb56885b7922"
NOW = 1_800_000_000


def load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def world(monkeypatch):
    values = {
        "AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x",
        "DEMOGRAPHIC_RESEARCH_SERVICE_ENABLED": "true",
        "DEMOGRAPHIC_RESEARCH_ENROLLMENT_ENABLED": "true",
        "DEMOGRAPHIC_RESEARCH_ENVIRONMENT": "dev",
        "DEMOGRAPHIC_RESEARCH_USERS_TABLE_NAME": "users",
        "DEMOGRAPHIC_RESEARCH_DELETION_LEDGER_TABLE_NAME": "ledger",
        "DEMOGRAPHIC_RESEARCH_HTTP_SUBJECTS_JSON": f'["{ACCOUNT}"]',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    for name in ("service", "validation", "errors", "config"):
        sys.modules.pop(name, None)
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        for name in ("users", "ledger"):
            resource.create_table(TableName=name, KeySchema=[{"AttributeName":"PK","KeyType":"HASH"},{"AttributeName":"SK","KeyType":"RANGE"}],
                                  AttributeDefinitions=[{"AttributeName":"PK","AttributeType":"S"},{"AttributeName":"SK","AttributeType":"S"}],
                                  BillingMode="PAY_PER_REQUEST")
        resource.Table("users").put_item(Item={"PK":f"USER#{ACCOUNT}","SK":"PROFILE","sub":ACCOUNT,"status":"ACTIVE","ageVerified":True,
                                                 "agePolicyVersion":"v1.0","ageVerifiedAt":"2026-09-30T00:00:00Z"})
        load("errors"); config=load("config"); validation=load("validation"); service=load("service")
        yield resource, config, validation, service


def request(config, action, version, *, age="25_34", state="IL", operation=None):
    value = {"schemaVersion":1,"operationId":operation or str(uuid.uuid4()),"expectedStateVersion":version,
             "action":action,"noticeVersion":config.NOTICE_VERSION}
    if action in {"enroll","update"}:
        value |= {"ageBand":age,"stateCode":state}
    return value


def test_enroll_update_withdraw_are_isolated_idempotent_and_value_erasing(world):
    resource, config, _, service = world
    first = request(config, "enroll", 0)
    result = service.update_profile(ACCOUNT, first, now_epoch=NOW)
    Draft202012Validator(json.loads((ROOT/"contracts"/"demographic-research"/"1.0.0-candidate.1"/"response.schema.json").read_text())).validate(result)
    assert result["state"] == "enrolled" and result["validUntilEpoch"] == NOW + 400 * 86400
    assert result["capabilities"] == {"enrollmentEnabled":False,"updateEnabled":True,"withdrawalEnabled":True}
    assert service.update_profile(ACCOUNT, first, now_epoch=NOW) == result

    second = request(config, "update", 1, age="65_74", state="DC")
    changed = service.update_profile(ACCOUNT, second, now_epoch=NOW + 1)
    assert (changed["ageBand"], changed["stateCode"], changed["validUntilEpoch"]) == ("65_74", "DC", result["validUntilEpoch"])
    users = resource.Table("users")
    assert "Item" not in users.get_item(Key={"PK":f"USER#{ACCOUNT}","SK":"DEMOGRAPHIC_OPERATION#"+first["operationId"]})
    audit = users.get_item(Key={"PK":f"USER#{ACCOUNT}","SK":f"DEMOGRAPHIC_CONSENT#{users.get_item(Key={'PK':f'USER#{ACCOUNT}','SK':'DEMOGRAPHIC_RESEARCH'})['Item']['consentEpochId']}#{NOW+1}#{second['operationId']}"})["Item"]
    assert not {"ageBand","stateCode"} & set(audit)
    assert audit["valueCleanupCompletedAtEpoch"] == NOW + 1

    third = request(config, "withdraw", 2)
    withdrawn = service.update_profile(ACCOUNT, third, now_epoch=NOW + 2)
    assert withdrawn["state"] == "withdrawn" and withdrawn["ageBand"] is None and withdrawn["stateCode"] is None
    stored = users.get_item(Key={"PK":f"USER#{ACCOUNT}","SK":"DEMOGRAPHIC_RESEARCH"})["Item"]
    assert not {"ageBand","stateCode","validUntilEpoch"} & set(stored)
    all_rows = users.query(KeyConditionExpression="PK = :pk",ExpressionAttributeValues={":pk":f"USER#{ACCOUNT}"})["Items"]
    assert not any({"ageBand","stateCode"} & set(row) for row in all_rows if row["SK"].startswith("DEMOGRAPHIC_CONSENT#"))


def test_expiry_requires_reconsent_and_hides_values(world):
    _, config, _, service = world
    service.update_profile(ACCOUNT, request(config, "enroll", 0), now_epoch=NOW)
    expired = service.get_profile(ACCOUNT, now_epoch=NOW + 400 * 86400)
    assert expired["state"] == "review_required"
    assert expired["ageBand"] is None and expired["stateCode"] is None
    assert expired["capabilities"] == {"enrollmentEnabled":True,"updateEnabled":False,"withdrawalEnabled":True}
    with pytest.raises(service.AppError) as error:
        service.update_profile(ACCOUNT, request(config, "update", 1), now_epoch=NOW + 400 * 86400)
    assert error.value.code == "CONFLICT"
    reenrolled = service.update_profile(ACCOUNT, request(config, "enroll", 1), now_epoch=NOW + 400 * 86400)
    assert reenrolled["state"] == "enrolled" and reenrolled["stateVersion"] == 2


def test_expired_profile_can_still_be_withdrawn_when_enrollment_is_closed(world, monkeypatch):
    _, config, _, service = world
    service.update_profile(ACCOUNT, request(config,"enroll",0), now_epoch=NOW)
    monkeypatch.setattr(config,"ENROLLMENT_ENABLED",False)
    withdrawn=service.update_profile(ACCOUNT,request(config,"withdraw",1),now_epoch=NOW+400*86400)
    assert withdrawn["state"]=="withdrawn" and withdrawn["capabilities"]["withdrawalEnabled"] is False


def test_deletion_fence_and_restored_copy_authority_fail_closed(world):
    resource, config, _, service = world
    service.update_profile(ACCOUNT, request(config, "enroll", 0), now_epoch=NOW)
    current = resource.Table("users").get_item(Key={"PK":f"USER#{ACCOUNT}","SK":"DEMOGRAPHIC_RESEARCH"})["Item"]
    current["stateVersion"] = 99
    resource.Table("users").put_item(Item=current)
    with pytest.raises(service.AppError) as mismatch:
        service.get_profile(ACCOUNT, now_epoch=NOW + 1)
    assert mismatch.value.code == "SERVER_UNAVAILABLE"
    resource.Table("ledger").put_item(Item={"PK":f"ACCOUNT#{ACCOUNT}","SK":"ACCOUNT_DELETION"})
    with pytest.raises(service.AppError) as deletion:
        service.get_profile(ACCOUNT, now_epoch=NOW + 1)
    assert deletion.value.code == "ACCOUNT_DELETION_IN_PROGRESS"


def test_profile_and_deletion_checks_are_transaction_conditions(world):
    _, config, _, service = world
    captured = {}
    service.client.transact_write_items = lambda **kwargs: captured.update(kwargs)
    service.update_profile(ACCOUNT, request(config, "enroll", 0), now_epoch=NOW)
    checks = [x["ConditionCheck"] for x in captured["TransactItems"] if "ConditionCheck" in x]
    assert len(checks) == 2
    assert "ageVerified = :true" in checks[0]["ConditionExpression"] and "agePolicyVersion = :age_policy" in checks[0]["ConditionExpression"]
    assert checks[1]["ConditionExpression"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"


def test_current_age_policy_is_required(world):
    resource, _, _, service = world
    profile=resource.Table("users").get_item(Key={"PK":f"USER#{ACCOUNT}","SK":"PROFILE"})["Item"]
    profile["agePolicyVersion"]="obsolete"
    resource.Table("users").put_item(Item=profile)
    with pytest.raises(service.AppError) as error:
        service.get_profile(ACCOUNT, now_epoch=NOW)
    assert error.value.code=="AGE_VERIFICATION_REQUIRED"


def test_closed_enums_and_prefer_not_to_say(world):
    _, config, validation, _ = world
    base = request(config, "enroll", 0, age="PREFER_NOT_TO_SAY", state="PREFER_NOT_TO_SAY")
    event = {"body": __import__("json").dumps(base)}
    assert validation.parse(event, config.NOTICE_VERSION)["ageBand"] == "PREFER_NOT_TO_SAY"
    for field, value in (("ageBand","17_OR_YOUNGER"),("stateCode","US")):
        bad = dict(base); bad[field] = value
        with pytest.raises(Exception):
            validation.parse({"body":__import__("json").dumps(bad)}, config.NOTICE_VERSION)
