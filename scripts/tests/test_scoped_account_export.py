import base64
import copy
import importlib.util
import json
from pathlib import Path
import pytest
pytest.importorskip("jsonschema",reason="Operator contract checks require jsonschema; production has no new dependency")

spec=importlib.util.spec_from_file_location('scoped_export',Path(__file__).resolve().parents[1]/'check_scoped_account_export.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
NOW=1800000000
PLAN={'schemaVersion':1,'endpoint':'https://abcdefghij.execute-api.us-east-1.amazonaws.com/v1/users/account-export',
      'subject':'12345678-1234-7234-8234-123456789abc','issuer':'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_fixture',
      'clientId':'a'*26,'sourceCommit':'b'*40}

def credentials():
    claims={'sub':PLAN['subject'],'iss':PLAN['issuer'],'client_id':PLAN['clientId'],'token_use':'access',
            'scope':'aws.cognito.signin.user.admin','auth_time':NOW,'iat':NOW,'exp':NOW+3600}
    middle=base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return {'accessToken':'eyJhbGciOiJSUzI1NiJ9.'+middle+'.signed','deviceFingerprint':'d'*64}

def pages():
    scope=json.loads((M.CONTRACT/'manifest.json').read_text());result=[]
    for i,family in enumerate(scope['included']):
        result.append({'schemaVersion':1,'exportTransportVersion':'1.0.0-account-export-candidate.3',
            'operation':'ACCOUNT_EXPORT','operationId':'12345678-1234-4234-8234-123456789abc',
            'status':'COMPLETE' if i==19 else 'IN_PROGRESS','startedAtEpoch':NOW,'expiresAtEpoch':NOW+900,
            'observedAtEpoch':NOW,'pageNumber':i,'family':family,'items':[],
            'nextCursor':None if i==19 else 'synthetic-opaque-cursor'*3,'scope':scope})
    return result

def execute(rows,*,clock=lambda:NOW,change=None):
    calls=[]
    def send(endpoint,auth,body):
        i=len(calls);calls.append(copy.deepcopy(body));row=rows[i]
        if change:change(i,row)
        return 200,{'cache-control':'private, no-store'},json.dumps(row).encode()
    report=M.run(PLAN,credentials(),transport=send,now=clock,monotonic=lambda:0)
    return report,calls

def test_full_page_chain_keeps_credentials_content_cursors_out_of_report():
    report,calls=execute(pages())
    assert report['outcome']=='EXPORT_COMPLETE' and report['pages']==20
    assert len(report['itemsByFamily'])==20
    assert calls[0]=={'schemaVersion':1,'action':'START_EXPORT'}
    assert all(c['action']=='CONTINUE_EXPORT' for c in calls[1:])
    rendered=json.dumps(report)
    for secret in (PLAN['subject'],credentials()['accessToken'],'synthetic-opaque-cursor','operationId','deviceFingerprint'):
        assert secret not in rendered

@pytest.mark.parametrize('mutation',[
    lambda row:row.update(operationId='98765432-1234-4234-8234-123456789abc'),
    lambda row:row.update(expiresAtEpoch=NOW+901),
    lambda row:row.update(pageNumber=99),
    lambda row:row.update(items=[{'email':'safe','accessToken':'secret'}]),
    lambda row:row.update(items=[{'email':{'purchaseToken':'secret'}}]),
    lambda row:row.update(status='COMPLETE',nextCursor=None),
])
def test_invalid_page_stops_without_retry_or_secret_report(mutation):
    rows=pages();mutation(rows[1]);report,calls=execute(rows)
    assert report['outcome']=='OBSERVATION_UNAVAILABLE' and len(calls)==2
    assert 'secret' not in json.dumps(report)


def test_network_failure_preserves_progress_and_never_retries_start():
    calls=[]
    def send(*args):calls.append(1);raise TimeoutError('secret')
    report=M.run(PLAN,credentials(),transport=send,now=lambda:NOW,monotonic=lambda:0)
    assert len(calls)==1 and report['outcome']=='OBSERVATION_UNAVAILABLE' and 'secret' not in json.dumps(report)


def test_authentication_expiring_during_call_cannot_qualify():
    clock=[NOW]
    report,calls=execute(pages(),clock=lambda:clock[0],change=lambda i,row:clock.__setitem__(0,NOW+301))
    assert len(calls)==1 and report['pages']==0 and report['outcome']=='OBSERVATION_UNAVAILABLE'


def test_fixed_error_only_no_private_payload():
    report=M.run(PLAN,credentials(),transport=lambda *args:(403,{},b'{"error":{"code":"ACCOUNT_UNAVAILABLE","secret":"private"}}'),now=lambda:NOW,monotonic=lambda:0)
    assert report['outcome']=='REJECTED' and report['errorCode']=='ACCOUNT_UNAVAILABLE'
    assert 'private' not in json.dumps(report)

@pytest.mark.parametrize('field,value',[('endpoint','https://example.invalid/export'),('subject','not-a-subject'),('sourceCommit','main'),('issuer','https://example.invalid')])
def test_reject_unbound_plan(field,value):
    with pytest.raises((M.Unverified,ValueError)):M.plan_check(PLAN|{field:value})


def test_dry_run_never_reads_credentials_or_calls_network(monkeypatch,tmp_path,capsys):
    path=tmp_path/'plan.json';path.write_text(json.dumps(PLAN))
    monkeypatch.setattr(M,'source_check',lambda plan:None)
    monkeypatch.setattr(M.sys,'argv',['checker','--plan',str(path)])
    monkeypatch.setattr(M.sys.stdin,'read',lambda *_:pytest.fail('dryrun must not read credentials'))
    assert M.main()==0 and json.loads(capsys.readouterr().out)['networkCalls']==0

@pytest.mark.parametrize('item',[
    {'kind':'google_play_verification'},
    {'kind':'google_play_verification','platform':'google_play','verifiedAccessUntilEpoch':NOW,'expiresAtEpoch':NOW+604801,'acknowledgment':'pending'},
    {'kind':'google_play_preparation','platform':'google_play','preparedAtEpoch':True},
    {'kind':'google_play_preparation','platform':'google_play','preparedAtEpoch':NOW+1},
])
def test_incomplete_or_malformed_play_projection_never_qualifies(item):
    rows=pages();rows[-1]['items']=[item];report,calls=execute(rows)
    assert report['outcome']=='OBSERVATION_UNAVAILABLE' and report['pages']==19


def test_unknown_nested_object_in_public_scalar_is_rejected():
    rows=pages();rows[0]['items']=[{'email':{'unknownNestedCredential':'secret'}}]
    report,calls=execute(rows)
    assert report['outcome']=='OBSERVATION_UNAVAILABLE' and len(calls)==1 and report['pages']==0


def test_exact_play_variants_pass():
    rows=pages();rows[-1]['items']=[{'kind':'google_play_preparation','platform':'google_play','preparedAtEpoch':NOW},
        {'kind':'google_play_verification','platform':'google_play','verifiedAccessUntilEpoch':NOW+1,'expiresAtEpoch':NOW+604801,'acknowledgment':'pending'}]
    report,_=execute(rows)
    assert report['outcome']=='EXPORT_COMPLETE' and report['itemsByFamily']['play_verification']==2
