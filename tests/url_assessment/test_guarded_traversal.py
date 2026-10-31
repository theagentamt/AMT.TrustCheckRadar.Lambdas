"""Isolated functional tests: no DNS, AWS, provider or target network."""
from copy import deepcopy
from pathlib import Path
import importlib.util
import sys
import pytest
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'src/url_assessment'), str(ROOT/'src/url_redirect_resolver')]
from guarded_service import assess
from assessment_service import Budget, Unavailable
from url_redirect_resolver.resolver import ResolutionStop, resolve_step, Deadline
from shared_url_guarded import supported, supported_step

NOW = 1800000000
OBS = '2027-01-15T08:00:00Z'
EXP = '2027-01-15T08:01:00Z'

def event(url='https://example.com/', scope='full_url'):
    return dict(schemaVersion=2,checkId='guarded-case',url=url,scope=scope,executionBudgetMs=18000)

class Dependencies:
    def __init__(self, pages=None, threats=None):
        self.pages = pages or [(200,None)]
        self.threats = threats or {}
        self.actions = []
        self.provider_call_count = 0
    def validate_destination(self, target, budget):
        self.actions.append(('dns',target.url))
    def lookup(self,url,budget):
        self.actions.append(('lookup',url));self.provider_call_count+=1
        threats=self.threats.get(url,[])
        if isinstance(threats, Exception):raise threats
        return dict(threatTypes=threats,observedAt=OBS,validUntil=EXP if threats else None)
    def step(self,check,url,budget):
        self.actions.append(('fetch',url))
        page=self.pages.pop(0)
        if isinstance(page,Exception):raise page
        status,location=page
        return dict(schemaVersion=2,checkId=check,resolutionStatus='http_response',
                    hops=[dict(url=url,httpStatus=status)],location=location,
                    reasonCodes=[],requestCount=1,scope='HTTP_SINGLE_HOP_ONLY')

def run(deps, request=None):
    request=request or event()
    r=assess(request,deps,Budget(18),now=lambda:NOW)
    assert supported(r,request['checkId'],request['scope']),r
    assert 'example.com' not in str(r)
    return r


def test_initial_threat_never_fetches():
    d=Dependencies(threats={'https://example.com/':['MALWARE']});r=run(d)
    assert d.actions==[('dns','https://example.com/'),('lookup','https://example.com/')]
    assert r['observedHopCount']==0 and r['lookupCount']==1
    assert r['matchScope']=='full_submitted_url' and r['targetContactStatus']=='not_contacted'


def test_nested_shorteners_are_generic_and_checked_before_each_visit():
    d=Dependencies([(302,'https://second.example/path'),(307,'https://third.example/'),(200,None)])
    r=run(d)
    urls=['https://example.com/','https://second.example/path','https://third.example/']
    assert d.actions==[(kind,url) for url in urls for kind in ('dns','lookup','fetch')]
    assert r['verdict']=='no_known_threat_detected' and r['observedHopCount']==3


def test_redirect_threat_is_not_fetched_or_invented_as_an_observed_hop():
    d=Dependencies([(302,'https://second.example/')],{'https://second.example/':['SOCIAL_ENGINEERING']})
    r=run(d)
    assert [x[1] for x in d.actions if x[0]=='fetch']==['https://example.com/']
    assert (r['lookupCount'],r['observedHopCount'],r['matchScope'])==(2,1,'redirect_destination')


def test_origin_only_does_not_visit_or_recover_withheld_path():
    d=Dependencies();r=run(d,event(scope='origin_only'))
    assert r['reasonCodes']==['ORIGIN_ONLY_NOT_FULL_LINK'] and r['lookupCount']==1
    assert not any(x[0]=='fetch' for x in d.actions)
    d=Dependencies();r=run(d,event('https://example.com/withheld',scope='origin_only'))
    assert r['reasonCodes']==['INVALID_REQUEST'] and not d.actions

@pytest.mark.parametrize('target',['file:///etc/passwd','intent://x/#Intent;scheme=https;end',
    'javascript:alert(1)','data:text/plain,hi','https://127.0.0.1/',
    'http://169.254.169.254/','https://example.com/?token=abc', 'https://example.com/a%250a'])
def test_invalid_redirect_has_no_lookup_or_fetch_for_destination(target):
    d=Dependencies([(302,target)]);r=run(d)
    assert r['verdict']=='unknown'
    assert len([x for x in d.actions if x[0]=='lookup'])==1
    assert len([x for x in d.actions if x[0]=='fetch'])==1

@pytest.mark.parametrize('error',['PROVIDER_RATE_LIMITED','PROVIDER_RESPONSE_INVALID','SECRET_UNAVAILABLE'])
def test_no_provider_result_means_no_target_visit(error):
    d=Dependencies(threats={'https://example.com/':Unavailable(error)})
    r=run(d);assert r['reasonCodes']==[error]
    assert r['targetContactStatus']=='not_contacted' and not any(x[0]=='fetch' for x in d.actions)


def test_dns_private_or_mixed_results_never_reach_google():
    d=Dependencies()
    def denied(t,b):raise ResolutionStop('NON_PUBLIC_DESTINATION','blocked')
    d.validate_destination=denied
    r=run(d);assert r['processingOutcome']=='blocked' and not d.actions


def test_uncertain_fetch_does_not_claim_no_contact_or_retry():
    d=Dependencies([Unavailable('RESOLVER_UNAVAILABLE')]);r=run(d)
    assert r['targetContactStatus']=='unknown' and r['observedHopCount']==0
    assert len([x for x in d.actions if x[0]=='fetch'])==1


def test_loop_and_six_request_limit_never_claim_complete():
    d=Dependencies([(302,'https://example.com/')]);assert run(d)['verdict']=='unknown'
    d=Dependencies([(302,f'https://hop{i}.example/') for i in range(6)])
    r=run(d);assert r['observedHopCount']==r['lookupCount']==6
    assert r['processingOutcome']=='partial' and len(d.actions)==18


def test_aggregate_target_budget_not_reset_at_redirect():
    from guarded_service import TargetBudget
    clock=[0.0];b=TargetBudget(Budget(18,lambda:clock[0]),lambda:clock[0])
    def first(x):clock[0]+=7;return x
    b.call(first)
    def second(x):assert x.remaining()==3;clock[0]+=3.01
    b.call(second)
    with pytest.raises(Unavailable):b.call(lambda x:None)


def test_step_revalidates_all_dns_and_never_follows_location():
    class Transport:
        def addresses(self,*_):return ['93.184.216.34']
        def fetch(self,*_):return 302,'https://second.example/'
    r=resolve_step('https://example.com/',Transport(),Deadline())|{'checkId':'guarded-case'}
    assert supported_step(r,'guarded-case','https://example.com/') and len(r['hops'])==1
    t=Transport();t.addresses=lambda *_:['93.184.216.34','10.0.0.1']
    t.fetch=lambda *_:pytest.fail('must not fetch mixed public/private')
    r=resolve_step('https://example.com/',t,Deadline())|{'checkId':'guarded-case'}
    assert supported_step(r,'guarded-case','https://example.com/') and r['requestCount']==0

@pytest.mark.parametrize('field,value',[('observedHopCount',1),('lookupCount',0),('providerCallCount',0),
    ('matchScope','observed_http_chain'),('targetContactStatus','contacted'),('location','https://raw.example/')])
def test_no_ambiguous_or_url_bearing_summary(field,value):
    r=run(Dependencies(threats={'https://example.com/':['MALWARE']}));r[field]=value
    assert not supported(r,'guarded-case','full_url')


def test_rebinding_between_precheck_and_worker_is_not_fetched():
    d=Dependencies()
    def step(check,url,budget):
        class ChangedDns:
            def addresses(self,*_):return ['127.0.0.1']
            def fetch(self,*_):pytest.fail('rebound address must not be contacted')
        return resolve_step(url,ChangedDns(),Deadline())|{'checkId':check}
    d.step=step
    r=run(d)
    assert r['verdict']=='unknown' and r['targetContactStatus']=='not_contacted'
    assert r['lookupCount']==1 and r['observedHopCount']==0


@pytest.mark.parametrize('status',[301,302,303,307,308])
def test_every_supported_redirect_status_is_gated(status):
    d=Dependencies([(status,'/destination'),(200,None)])
    r=run(d)
    assert r['processingOutcome']=='complete'
    assert d.actions[3:]==[('dns','https://example.com/destination'),
        ('lookup','https://example.com/destination'),('fetch','https://example.com/destination')]


def test_unsupported_refresh_response_cannot_claim_complete():
    d=Dependencies([Unavailable('HTTP_CHAIN_INCOMPLETE')]);r=run(d)
    assert r['processingOutcome']!='complete' and r['verdict']=='unknown'
