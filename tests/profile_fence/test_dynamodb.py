"""Real-SDK state transition tests with Moto; no live AWS calls."""
import importlib.util, json, os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest

if os.environ.get("AMT_AUTHORITY_INTEGRATION") != "1":
    pytest.skip("Run separately with the real SDK/Moto environment", allow_module_level=True)
import boto3
from moto import mock_aws

SUB = "synthetic-profile-owner"
ROOT = Path(__file__).resolve().parents[2]

class Cognito:
    def admin_get_user(self, **kwargs):
        return {"UserAttributes":[{"Name":"sub","Value":SUB},{"Name":"phone_number","Value":"+12025550123"},{"Name":"phone_number_verified","Value":"false"}]}
class PhoneMetadata:
    def classify(self, value): return "US", "FIXED_LINE_OR_MOBILE"

@pytest.fixture
def world(monkeypatch):
    for key, value in {"AWS_DEFAULT_REGION":"us-east-1","USERS_TABLE_NAME":"users","DELETION_LEDGER_TABLE_NAME":"ledger","AGE_ATTESTATION_USER_POOL_ID":"pool"}.items(): monkeypatch.setenv(key, value)
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        for name in ("users", "ledger"):
            ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST", KeySchema=[{"AttributeName":"PK","KeyType":"HASH"},{"AttributeName":"SK","KeyType":"RANGE"}], AttributeDefinitions=[{"AttributeName":"PK","AttributeType":"S"},{"AttributeName":"SK","AttributeType":"S"}])
        apps = {}
        for name in ("post_confirmation", "age_attestation"):
            spec = importlib.util.spec_from_file_location("qualified_"+name, ROOT/"src"/name/"app.py")
            app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app); apps[name] = app
        apps["age_attestation"].cognito_client = Cognito(); apps["age_attestation"].phone_metadata = PhoneMetadata()
        yield SimpleNamespace(ddb=ddb, post=apps["post_confirmation"], age=apps["age_attestation"])

def post_event(): return {"request":{"userAttributes":{"sub":SUB,"email":"synthetic@example.invalid"}},"response":{}}
def payload(operation_id=None): return {"schemaVersion":1,"operationId":operation_id or str(uuid4()),"over18Acknowledged":True,"agePolicyVersion":"v1.0"}
def event(body): return {"version":"2.0","routeKey":"POST /v1/users/age-attestation","requestContext":{"http":{"method":"POST"},"authorizer":{"jwt":{"claims":{"sub":SUB,"token_use":"access"}}}},"body":json.dumps(body),"isBase64Encoded":False}
def attest(w, body):
    response=w.age.lambda_handler(event(body),SimpleNamespace(aws_request_id="req")); return response["statusCode"],json.loads(response["body"])
def item(w, sk): return w.ddb.Table("users").get_item(Key={"PK":"USER#"+SUB,"SK":sk},ConsistentRead=True).get("Item")
def fence(w,state="REQUESTED"): w.ddb.Table("ledger").put_item(Item={"PK":"ACCOUNT#"+SUB,"SK":"ACCOUNT_DELETION","status":state})

def test_activation_receipt_and_exact_replay(world, monkeypatch):
    world.post.lambda_handler(post_event(),None); monkeypatch.setattr(world.age.time,"time",lambda:1_800_000_000); request=payload()
    status,first=attest(world,request); current=item(world,"PROFILE"); receipt=item(world,"AGE_ATTESTATION#"+request["operationId"])
    assert status==200 and first["replayed"] is False and current["status"]=="ACTIVE" and receipt["expiresAt"]==1_800_604_800
    status,replay=attest(world,request); assert status==200 and replay=={**first,"replayed":True} and item(world,"PROFILE")==current

def test_corrupt_same_operation_receipt_conflicts(world):
    world.post.lambda_handler(post_event(),None); request=payload(); assert attest(world,request)[0]==200
    receipt=item(world,"AGE_ATTESTATION#"+request["operationId"]); receipt["requestHash"]="0"*64; world.ddb.Table("users").put_item(Item=receipt)
    status,body=attest(world,request); assert status==409 and body["error"]["code"]=="IDEMPOTENCY_CONFLICT"

def test_new_operation_preserves_original_timestamp(world):
    world.post.lambda_handler(post_event(),None); assert attest(world,payload())[0]==200; before=item(world,"PROFILE")
    status,result=attest(world,payload()); assert status==200 and result["attestedAt"]==before["ageVerifiedAt"] and item(world,"PROFILE")==before

@pytest.mark.parametrize("state",["REQUESTED","COMPLETE","UNKNOWN"])
@pytest.mark.parametrize("replay",[False,True])
def test_fixed_fence_blocks_new_and_replay(world,state,replay):
    world.post.lambda_handler(post_event(),None); request=payload()
    if replay: assert attest(world,request)[0]==200
    fence(world,state); status,body=attest(world,request if replay else payload()); assert status==409 and body["error"]["code"]=="ACCOUNT_STATE_CONFLICT"

@pytest.mark.parametrize("shape,expected",[("missing","PROFILE_NOT_FOUND"),("wrong","ACCOUNT_STATE_CONFLICT"),("closed","ACCOUNT_STATE_CONFLICT")])
def test_missing_or_closed_profile_is_not_created(world,shape,expected):
    if shape!="missing": world.ddb.Table("users").put_item(Item={"PK":"USER#"+SUB,"SK":"PROFILE","sub":"other" if shape=="wrong" else SUB,"status":"DELETING" if shape=="closed" else "ACTIVE"})
    status,body=attest(world,payload()); assert status in (404,409) and body["error"]["code"]==expected
    if shape=="missing": assert item(world,"PROFILE") is None

def test_post_confirmation_duplicate_safe_but_fenced_duplicate_fails(world):
    request=post_event(); assert world.post.lambda_handler(request,None) is request; before=item(world,"PROFILE")
    assert world.post.lambda_handler(request,None) is request and item(world,"PROFILE")==before; fence(world)
    with pytest.raises(RuntimeError,match="^POST_CONFIRMATION_FAILED$"): world.post.lambda_handler(request,None)
