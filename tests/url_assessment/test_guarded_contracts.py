from pathlib import Path
import hashlib
import json
import sys
from copy import deepcopy
from jsonschema import Draft202012Validator,FormatChecker
from referencing import Registry,Resource
from referencing.jsonschema import DRAFT202012
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from url_consumer.service import validate_envelope,fresh_mapper
from shared_governed_history import validate_public_projection


def validator(root,name):
    registry=Registry()
    for path in root.glob('*.schema.json'):
        value=json.loads(path.read_text())
        registry=registry.with_resource(path.name,Resource.from_contents(value,default_specification=DRAFT202012))
    return Draft202012Validator(json.loads((root/name).read_text()),registry=registry,format_checker=FormatChecker())


def test_new_contract_fixtures_validate_and_do_not_claim_activation():
    root=ROOT/'contracts/url-consumer/1.0.0-candidate.3'
    values=json.loads((root/'fixtures.json').read_text());assert values['activationEnabled'] is False
    for f in values['fixtures']:
        validator(root,'prepare.schema.json').validate(f['request'])
        validate_envelope(f['response'])
        assert f['response']['accounting']==f['response']['outcome']['accounting']
        assert f['response']['outcome']['contractVersion']=='0.4.0-candidate.1'
        if 'threat' in f['name']:assert f['response']['accounting']['chargedChecks']==0


def test_historical_and_guarded_history_items_share_only_new_read_envelope():
    old=ROOT/'contracts/governed-history/1.0.0-candidate.1'
    root=ROOT/'contracts/governed-history/1.0.0-candidate.2'
    value=json.loads((root/'fixtures.json').read_text())['fixtures'][0]['response']
    old_item=json.loads((old/'fixtures.json').read_text())['fixtures'][0]['response']['items'][0]
    value['items'].append(old_item)
    validator(root,'list.schema.json').validate(value)
    for item in value['items']:validate_public_projection(item)
    with pytest.raises(Exception):validator(old,'list.schema.json').validate(value)
    first=deepcopy(value['items'][0]);first['assessmentTransportVersion']='1.0.0-candidate.2'
    with pytest.raises(ValueError):validate_public_projection(first)


def test_new_mapper_rejects_old_private_dto_without_fallback():
    mapper=fresh_mapper(True)
    r=json.loads((ROOT/'contracts/url-consumer/1.0.0-candidate.3/fixtures.json').read_text())['fixtures'][2]['private']
    r.update(schemaVersion=2,scope='HTTP_REDIRECTS_AND_GOOGLE_LOOKUP')
    r.pop('matchScope');r.pop('targetContactStatus')
    assert not mapper.supported_private(r,'guarded-case','full_url')

@pytest.mark.parametrize('relative',['url-assessment/0.4.0-candidate.1',
    'url-consumer/1.0.0-candidate.3','governed-history/1.0.0-candidate.2'])
def test_new_contract_manifest_covers_exact_files(relative):
    root=ROOT/'contracts'/relative
    listed=set()
    for line in (root/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ');listed.add(name)
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
    assert listed=={p.name for p in root.iterdir() if p.is_file() and p.name!='SHA256SUMS'}


def test_existing_feedback_explicitly_rejects_new_assessment_version():
    from result_feedback.validation import request,FeedbackError
    value={'feedbackTransportVersion':'1.0.0-feedback-candidate.1','feedbackId':'feedback',
        'result':{'checkId':'guarded-case','operationProof':'v1_k1_00000000_0000000000000000_000000000000000000000000',
            'receiptId':'0'*32,'assessmentTransportVersion':'1.0.0-candidate.3'},'category':'unclear'}
    with pytest.raises(FeedbackError,match='CONTRACT_UNSUPPORTED'):request(value)
