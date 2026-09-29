"""Versioned synthetic fixtures validate through executable producer/transport contracts."""
import json,sys
from pathlib import Path
from copy import deepcopy
import pytest
from jsonschema import Draft202012Validator,FormatChecker,ValidationError
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'src'),str(Path(__file__).parent)]
from url_consumer.service import validate_envelope as url_validate,fresh_mapper
from message_consumer.service import validate_envelope as message_validate

@pytest.mark.parametrize('path,validate',[('url-consumer/1.0.0-candidate.2/fixtures.json',url_validate),('message-consumer/1.0.0-candidate.3/response-fixtures.json',message_validate)])
def test_new_fixtures_closed_shape_and_actual_semantics(path,validate):
    for fixture in json.loads((ROOT/'contracts'/path).read_text())['fixtures']:
        value=fixture['response'];assert validate(deepcopy(value))==value
        poison=deepcopy(value);poison['outcome']['evidence'][0]['validUntil']={}
        with pytest.raises((ValidationError,ValueError)):validate(poison)

def test_new_url_legacy_no_match_is_unverified_unavailable():
    m=fresh_mapper();b=json.loads((ROOT/'contracts/url-consumer/1.0.0-candidate.2/fixtures.json').read_text())['fixtures'][-1]['response']
    from test_consumer import result
    value={k:v for k,v in result().items() if k not in ('lookupObservedAt','lookupValidUntil')};value['schemaVersion']=1
    out=m.map_private(value,expected_check_id='client-1',request_scope='full_url',access=b['access'],accounting=b['accounting'],assessed_at='2027-01-15T08:00:00Z',same_check_replay_authorized=False,now=1800000000)
    assert out['verdict']=='unknown' and out['reasonCodes']==['PROVIDER_EVIDENCE_UNVERIFIED']
    assert out['evidence'][0]['outcome']=='unavailable' and out['evidence'][0]['observedAt'] is None
