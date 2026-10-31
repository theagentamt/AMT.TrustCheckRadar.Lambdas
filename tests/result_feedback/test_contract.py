import json,sys
from pathlib import Path
from copy import deepcopy
import pytest
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from result_feedback.validation import request,response,FeedbackError
P=ROOT/'contracts/result-feedback/1.0.0-candidate.1'

def test_fixtures_match_closed_contract():
    f=json.loads((P/'fixtures.json').read_text())
    for kind in ('request','response'):
        schema=json.loads((P/(kind+'.schema.json')).read_text());Draft202012Validator.check_schema(schema)
        for case in f[kind+'s']:
            Draft202012Validator(schema).validate(case[kind])
            if kind=='request':request(case[kind])

@pytest.mark.parametrize('extra',['accountId','message','sanitizedText','url','explanation','campaignId','verdict'])
def test_no_content_or_claimed_owner_fields(extra):
    r=deepcopy(json.loads((P/'fixtures.json').read_text())['requests'][0]['request']);r[extra]='private'
    with pytest.raises(FeedbackError):request(r)


def test_ack_state_coherence_rejects_conflicting_values():
    schema=Draft202012Validator(json.loads((P/'response.schema.json').read_text()))
    good=json.loads((P/'fixtures.json').read_text())['responses'][0]['response']
    for change in ({'receivedAt':None},{'retryable':True},{'reasonCode':'SERVICE_UNAVAILABLE'},{'status':'unknown'}):
        with pytest.raises(Exception):schema.validate(good|change)
    with pytest.raises(FeedbackError):response('accepted',feedback_id='x',check_id='x',received=5,expires=5)


def test_guarded_feedback_versioned_request_and_response_are_closed():
    from result_feedback.validation import GUARDED_VERSION,request,response,response_version
    root=P.parent/'1.0.0-candidate.2'
    body=deepcopy(json.loads((P/'fixtures.json').read_text())['requests'][0]['request'])
    body.update(feedbackTransportVersion=GUARDED_VERSION)
    body['result']['assessmentTransportVersion']='1.0.0-candidate.3'
    request(body)
    Draft202012Validator(json.loads((root/'request.schema.json').read_text())).validate(body)
    reply=response('accepted',version=GUARDED_VERSION,feedback_id=body['feedbackId'],
        check_id=body['result']['checkId'],received=1800000000,expires=1800000600)
    Draft202012Validator(json.loads((root/'response.schema.json').read_text())).validate(reply)
    assert response_version({'body':json.dumps(body)})==GUARDED_VERSION
    assert response_version({'body':'{"feedbackTransportVersion":"1.0.0-feedback-candidate.2","feedbackTransportVersion":"invalid"}'})!=GUARDED_VERSION


def test_guarded_feedback_fixtures_and_manifest():
    import hashlib
    root=P.parent/'1.0.0-candidate.2'
    for line in (root/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ');assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
    f=json.loads((root/'fixtures.json').read_text());assert f['activationEnabled'] is False
    for kind in ('request','response'):
        validator=Draft202012Validator(json.loads((root/(kind+'.schema.json')).read_text()))
        for item in f[kind+'s']:validator.validate(item[kind])
