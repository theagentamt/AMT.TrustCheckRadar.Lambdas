"""Modern provenance preserves independent text findings across Google expiry."""
import sys
from pathlib import Path
from copy import deepcopy
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from message_evaluator.policy_v3 import evaluate
from shared_message_contract.validation_v3 import validate_summary,present
from shared_message_contract.validation import MessageError
NOW=1800000000

def request(text='Please review [URL_1].'):
    return {'entryPoint':'message','language':'en','target':{'scope':'sanitized_message','sourceType':'pasted_text','sanitizedText':text,'speakerRole':'other','entities':[{'token':'[URL_1]','type':'url'}],'reviewedLinks':[{'token':'[URL_1]','url':'https://example.com/','scope':'full_url','withheldComponents':[]}],'withheldLinks':False}}

def private(check='check',matched=True):
    return {'schemaVersion':2,'checkId':check,'verdict':'high_risk' if matched else 'no_known_threat_detected','processingOutcome':'complete','coverage':'supported_checks_complete','reasonCodes':['KNOWN_THREAT_MATCH'] if matched else ['NO_LIST_MATCH','BROWSER_NAVIGATION_NOT_EVALUATED'],'transportWarnings':[],'threatTypes':['MALWARE'] if matched else [],'lookupCount':1,'providerCallCount':1,'observedHopCount':1,'scope':'HTTP_REDIRECTS_AND_GOOGLE_LOOKUP','consumerAccessEnabled':False,'lookupObservedAt':'2027-01-15T08:00:00Z','lookupValidUntil':'2027-01-15T08:00:20Z' if matched else None}

def warning(*args):return {'assessment':'warning','context':'clear','reasons':[{'code':'AI_PRETEXT','spans':[{'start':0,'end':6}]}]}

@pytest.mark.parametrize('text,ai,expected,basis',[
 ('Please review [URL_1].',warning,'suspicious','ai_assessment'),

 ('Please review [URL_1].',lambda *a:{'assessment':'abstain','context':'insufficient','reasons':[]},'unknown',None)])
def test_expiry_removes_only_google_current_basis(text,ai,expected,basis):
    initial=evaluate('check',request(text),lookup=lambda _:private(),ai=ai,now=lambda:NOW)
    original=deepcopy(initial);expired=present(initial,NOW+20)
    assert initial==original and expired['verdict']==expected
    assert 'google_web_risk_lookup' not in expired['assessmentBasis']
    if basis:assert basis in expired['assessmentBasis']
    assert expired['evidence'][0]==initial['evidence'][0]|{'freshness':'expired'}
    assert expired['processingOutcome']!='complete'
    assert present(expired,NOW-10)==expired # rollback cannot rejuvenate explicit expiry

@pytest.mark.parametrize('mutation',[
 lambda e:e.update(validUntil=None),lambda e:e.update(observedAt=True),
 lambda e:e.update(threatTypes=[{}]),lambda e:e.update(freshness='future'),
 lambda e:e.update(validUntil='2027-01-15T08:00:00Z')])
def test_malformed_metadata_fails_closed(mutation):
    value=evaluate('check',request(),lookup=lambda _:private(),now=lambda:NOW)
    mutation(value['evidence'][0])
    with pytest.raises((ValueError,MessageError)):validate_summary(value)

def test_no_match_is_only_original_observation():
    value=evaluate('check',request(),lookup=lambda _:private(matched=False),ai=warning,now=lambda:NOW)
    item=value['evidence'][0]
    assert item['freshness']=='observation_only' and item['validUntil'] is None and item['threatTypes']==[]
    assert present(value,NOW+1000)['evidence']==value['evidence']


def test_independent_qualified_rule_survives_expiry():
    from message_evaluator.policy_v2 import result
    value=result('check',rules=['REQUEST_SECRET_DISCLOSURE'],limits=['WITHHELD_LINKS'],evidence=[{'source':'google_web_risk_lookup','outcome':'match','targetScope':'observed_http_chain'}])
    value['schemaVersion']=3
    value['evidence'][0].update(observedAt=private()['lookupObservedAt'],validUntil=private()['lookupValidUntil'],threatTypes=['MALWARE'],freshness='current')
    expired=present(value,NOW+20)
    assert expired['verdict']=='high_risk' and expired['assessmentBasis']==['qualified_rules']
    assert expired['ruleIds']==value['ruleIds']
