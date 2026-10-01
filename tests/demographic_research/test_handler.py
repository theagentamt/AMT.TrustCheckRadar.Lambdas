import importlib.util
import json
from pathlib import Path
import sys
import types
import uuid
from unittest import mock

SOURCE = Path(__file__).resolve().parents[2] / "src" / "demographic_research"
SUBJECT="47debb73-444b-4bb1-9889-fb56885b7922"


def load(name):
    spec=importlib.util.spec_from_file_location(name,SOURCE/f"{name}.py")
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module


def modules(monkeypatch, enabled):
    for name in ("app","validation","errors","config","service"):sys.modules.pop(name,None)
    monkeypatch.setenv("DEMOGRAPHIC_RESEARCH_SERVICE_ENABLED",str(enabled).lower())
    monkeypatch.setenv("DEMOGRAPHIC_RESEARCH_ENROLLMENT_ENABLED","false")
    monkeypatch.setenv("DEMOGRAPHIC_RESEARCH_ENVIRONMENT","dev")
    monkeypatch.setenv("DEMOGRAPHIC_RESEARCH_USERS_TABLE_NAME","users")
    monkeypatch.setenv("DEMOGRAPHIC_RESEARCH_DELETION_LEDGER_TABLE_NAME","ledger")
    monkeypatch.setenv("DEMOGRAPHIC_RESEARCH_HTTP_SUBJECTS_JSON",f'["{SUBJECT}"]')
    load("errors");load("config");load("validation")
    return load("app")


def event(method="GET", body=None, subject=SUBJECT):
    value={"version":"2.0","routeKey":f"{method} /v1/users/demographic-research-profile",
           "requestContext":{"authorizer":{"jwt":{"claims":{"sub":subject,"token_use":"access"}}}}}
    if body is not None:value["body"]=json.dumps(body)
    return value


def test_disabled_does_not_import_aws_service_or_parse_body(monkeypatch):
    app=modules(monkeypatch,False)
    value=event("PUT");value["body"]="not-json"
    response=app.lambda_handler(value,None)
    assert response["statusCode"]==503
    assert json.loads(response["body"])["error"]["code"]=="FEATURE_DISABLED"
    assert "service" not in sys.modules


def test_allowlist_is_default_closed(monkeypatch):
    app=modules(monkeypatch,True)
    response=app.lambda_handler(event(subject="d8af79ad-a4c7-4280-bfe6-f490be17dff3"),None)
    assert response["statusCode"]==503 and json.loads(response["body"])["error"]["code"]=="FEATURE_DISABLED"


def test_allowlist_requires_one_to_ten_unique_canonical_lowercase_uuids(monkeypatch):
    app=modules(monkeypatch,True)
    cases=["[]",json.dumps([str(uuid.uuid4()) for _ in range(11)]),
           '["not-a-uuid"]',f'["{SUBJECT.upper()}"]',f'["{SUBJECT}","{SUBJECT}"]']
    for raw in cases:
        monkeypatch.setenv("DEMOGRAPHIC_RESEARCH_HTTP_SUBJECTS_JSON",raw)
        with __import__('pytest').raises(app.AppError) as error:
            app.config.require_service(SUBJECT)
        assert error.value.code=="FEATURE_DISABLED"


def test_withdraw_remains_available_while_enrollment_closed(monkeypatch):
    app=modules(monkeypatch,True)
    stub=types.ModuleType("service")
    stub.get_profile=lambda *_:{"state":"enrolled"}
    stub.update_profile=lambda _,payload:{"state":"withdrawn","action":payload["action"]}
    sys.modules["service"]=stub
    body={"schemaVersion":1,"operationId":"47debb73-444b-4bb1-9889-fb56885b7922","expectedStateVersion":1,
          "action":"withdraw","noticeVersion":app.config.NOTICE_VERSION}
    response=app.lambda_handler(event("PUT",body),None)
    assert response["statusCode"]==200 and json.loads(response["body"])["state"]=="withdrawn"


def test_metrics_use_only_bounded_operation_dimensions(monkeypatch):
    app=modules(monkeypatch,True)
    stub=types.ModuleType("service")
    stub.get_profile=lambda *_:{"state":"not_enrolled"}
    stub.update_profile=lambda _,payload:{"state":payload["action"]}
    sys.modules["service"]=stub
    captured=[]
    monkeypatch.setattr(app,"metric",lambda operation,name:captured.append((operation,name)))
    assert app.lambda_handler(event(),None)["statusCode"]==200
    base={"schemaVersion":1,"operationId":"d8af79ad-a4c7-4280-bfe6-f490be17dff3","expectedStateVersion":1,
          "action":"withdraw","noticeVersion":app.config.NOTICE_VERSION}
    assert app.lambda_handler(event("PUT",base),None)["statusCode"]==200
    update=base|{"operationId":"50afe6ad-b3cf-49e9-a4f6-3014a441e06a","action":"update","ageBand":"35_44","stateCode":"DC"}
    assert app.lambda_handler(event("PUT",update),None)["statusCode"]==200
    bad=event("PUT");bad["body"]="bad"
    assert app.lambda_handler(bad,None)["statusCode"]==400
    assert captured==[("get","Success"),("withdraw","Success"),("update","Success"),("unknown","Rejected")]


def test_internal_error_keeps_parsed_action_dimension(monkeypatch):
    app=modules(monkeypatch,True)
    stub=types.ModuleType("service");stub.get_profile=lambda *_:{}
    stub.update_profile=lambda *_:(_ for _ in ()).throw(RuntimeError("private"))
    sys.modules["service"]=stub
    captured=[];monkeypatch.setattr(app,"metric",lambda operation,name:captured.append((operation,name)))
    body={"schemaVersion":1,"operationId":"d8af79ad-a4c7-4280-bfe6-f490be17dff3","expectedStateVersion":0,
          "action":"enroll","noticeVersion":app.config.NOTICE_VERSION,"ageBand":"25_34","stateCode":"IL"}
    response=app.lambda_handler(event("PUT",body),None)
    assert response["statusCode"]==503 and captured==[("enroll","InternalError")]


def test_emf_metric_sanitizes_unbounded_operation(monkeypatch,caplog):
    app=modules(monkeypatch,True)
    with caplog.at_level("INFO"):
        for operation in ("get","enroll","update","withdraw","private-subject"):
            app.metric(operation, "Rejected")
    values=[json.loads(record.message) for record in caplog.records[-5:]]
    assert [value["Operation"] for value in values]==["get","enroll","update","withdraw","unknown"]
    assert all(value["Environment"]=="dev" for value in values)
    assert all(value["_aws"]["CloudWatchMetrics"][0]["Namespace"]=="TrustCheckRadar/DemographicResearch" for value in values)
