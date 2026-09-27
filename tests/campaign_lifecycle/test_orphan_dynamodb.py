"""Bounded real DynamoDB SDK transactions against isolated Moto tables."""
import base64
import pytest
from tests.campaign_lifecycle.test_publication_dynamodb import world,ID,NOW,PERIOD
from shared_campaign_locators import core
from shared_campaign_contracts.metadata import empty_metadata
from orphan import plain,wire


def seed(world,count=12,checkpoint=True):
    p,d,put,get,summary,marker=world
    d.delete_item(TableName='pipeline',Key=core._key(summary['PK'],'SUMMARY'))
    for item in d.scan(TableName='pipeline')['Items']:
        row=plain(item)
        if row.get('SK','').startswith('CONTRIB#') or row.get('recordType')=='CAMPAIGN_CONTRIBUTOR_LOCATOR':
            d.delete_item(TableName='pipeline',Key=core._key(row['PK'],row['SK']))
    rows=[]
    for i in range(count):
        token=base64.urlsafe_b64encode(i.to_bytes(32,'big')).decode().rstrip('=')
        row={'PK':summary['PK'],'SK':'CONTRIB#'+token,'GSI1PK':f'CONTRIB#{PERIOD}#{token}',
             'GSI1SK':summary['PK'],'GSI3PK':'EXPIRY#dev','GSI3SK':NOW-1,'expiresAt':NOW-1,'periodId':PERIOD,
             'submissionCount':1,'vectorApplied':True,'vector':[1],'languageId':'en',
             'researchNoticeVersion':summary['researchNoticeVersion'],'researchPolicyVersion':summary['researchPolicyVersion'],
             'metadataSchemaVersion':1,**empty_metadata()}
        put('pipeline',row);put('pipeline',core.locator_for_target(row,'dev'));rows.append(row)
    saved={'PK':summary['PK'],'SK':'DELETION_RECOMPUTE','revision':1,'summaryVersion':1,'cursor':None,
           'contributors':0,'submissions':0,'vectors':0,'sums':[], 'cutoffEpoch':NOW-100,
           'expiresAt':NOW-1,'GSI3PK':'EXPIRY#dev','GSI3SK':NOW-1,'metadataSchemaVersion':1,
           'repairSchemaVersion':2,'periodId':PERIOD,**empty_metadata()}
    if checkpoint:d.put_item(TableName='pipeline',Item=wire(saved))
    return rows,saved


def snapshot(d):return sorted((repr(plain(v)) for v in d.scan(TableName='pipeline')['Items']))
def read(d,pk,sk):
    raw=d.get_item(TableName='pipeline',Key=core._key(pk,sk),ConsistentRead=True).get('Item')
    return plain(raw) if raw else None


def test_bounded_pairs_then_checkpoint_preserves_other_evidence(world):
    p,d,put,get,summary,_=world;rows,saved=seed(world)
    tomb={'PK':rows[0]['GSI1PK'],'SK':'TOMBSTONE','expiresAt':NOW+1};put('pipeline',tomb)
    aggregate={'PK':'CAMPAIGN#'+ID,'SK':'AGGREGATE','expiresAt':NOW+100};put('intelligence',aggregate)
    for expected in (10,2):
        result=p.recover_expired_orphan(ID,PERIOD)
        assert result['expiredPairs']==expected and not result['candidateEmptyObserved'] and not result['scopeComplete']
        assert read(d,summary['PK'],'DELETION_RECOMPUTE')==saved
    result=p.recover_expired_orphan(ID,PERIOD)
    assert result['checkpointDeleted'] and result['candidateEmptyObserved'] and not result['retirementEligible']
    assert read(d,summary['PK'],'DELETION_RECOMPUTE') is None
    assert read(d,tomb['PK'],'TOMBSTONE')==tomb and get('intelligence',aggregate['PK'],'AGGREGATE')==aggregate


@pytest.mark.parametrize('kind',['summary','live','mixed','unknown','legacy','partial','bad-vector','extra'])
def test_unqualified_full_partition_preserved_before_first_mutation(world,kind):
    p,d,put,get,summary,_=world;rows,saved=seed(world)
    if kind=='summary':put('pipeline',summary)
    elif kind=='live':put('pipeline',rows[-1]|{'expiresAt':NOW+1,'GSI3SK':NOW+1})
    elif kind=='mixed':put('pipeline',rows[-1]|{'periodId':PERIOD+1})
    elif kind=='unknown':put('pipeline',{'PK':summary['PK'],'SK':'UNKNOWN'})
    elif kind in ('legacy','partial'):
        saved.pop('repairSchemaVersion')
        if kind=='legacy':saved.pop('periodId')
        d.put_item(TableName='pipeline',Item=wire(saved))
    elif kind=='bad-vector':put('pipeline',rows[-1]|{'vector':[True]})
    else:put('pipeline',rows[-1]|{'unknown':'preserve'})
    before=snapshot(d)
    with pytest.raises(Exception):p.recover_expired_orphan(ID,PERIOD)
    assert snapshot(d)==before


@pytest.mark.parametrize('kind',['mixed','unknown'])
def test_no_checkpoint_does_not_bypass_partition_validation(world,kind):
    p,d,put,get,summary,_=world;rows,_=seed(world,checkpoint=False)
    put('pipeline',rows[-1]|({'periodId':PERIOD+1} if kind=='mixed' else {'SK':'UNKNOWN'}))
    before=snapshot(d)
    with pytest.raises(Exception):p.recover_expired_orphan(ID,PERIOD)
    assert snapshot(d)==before


def test_full_pagination_is_bounded_before_mutation(world):
    p,d,*_=world;seed(world,100);before=snapshot(d)
    with pytest.raises(Exception):p.recover_expired_orphan(ID,PERIOD)
    assert snapshot(d)==before


@pytest.mark.parametrize('kind',['wrong-pk','repeated'])
def test_corrupt_or_repeated_pagination_preserves_partition(world,kind):
    p,d,*_=world;seed(world,30);before=snapshot(d);original=d.query;first=[None]
    def query(**kwargs):
        value=original(**kwargs)
        if kind=='wrong-pk':value['LastEvaluatedKey']=core._key('CANDIDATE#wrong','x')
        elif first[0] is None:first[0]=value
        else:value=first[0]
        return value
    p.d=type('Proxy',(),{'__getattr__':lambda self,n:query if n=='query' else getattr(d,n)})()
    with pytest.raises(Exception):p.recover_expired_orphan(ID,PERIOD)
    assert snapshot(d)==before


@pytest.mark.parametrize('kind',['summary','checkpoint','period','inventory','replacement'])
def test_transaction_race_preserves_pair(world,kind):
    p,d,put,get,summary,marker=world;rows,saved=seed(world,1)
    def transact(**kwargs):
        if kind=='summary':put('pipeline',summary)
        elif kind=='checkpoint':d.put_item(TableName='pipeline',Item=wire(saved|{'revision':2}))
        elif kind=='period':
            row=read(d,f'PERIOD#{PERIOD}','HMAC_KEY');put('pipeline',row|{'admissionRevision':row['admissionRevision']+1})
        elif kind=='inventory':put('pipeline',marker|{'revision':2})
        else:put('pipeline',rows[0]|{'submissionCount':2})
        return d.transact_write_items(**kwargs)
    p.d=type('Proxy',(),{'__getattr__':lambda self,n:transact if n=='transact_write_items' else getattr(d,n)})()
    with pytest.raises(Exception):p.recover_expired_orphan(ID,PERIOD)
    assert read(d,rows[0]['PK'],rows[0]['SK']) is not None
    locator=core.locator_for_target(rows[0],'dev');assert read(d,locator['PK'],locator['SK'])==locator


@pytest.mark.parametrize('count',[0,1])
def test_committed_lost_ack_retry_uses_fresh_state(world,count):
    p,d,*_=world;seed(world,count)
    def transact(**kwargs):
        d.transact_write_items(**kwargs);raise RuntimeError('synthetic lost ack')
    p.d=type('Proxy',(),{'__getattr__':lambda self,n:transact if n=='transact_write_items' else getattr(d,n)})()
    with pytest.raises(RuntimeError):p.recover_expired_orphan(ID,PERIOD)
    p.d=d;result=p.recover_expired_orphan(ID,PERIOD)
    assert result['candidateEmptyObserved'] and not result['scopeComplete'] and not result['retirementEligible']


@pytest.mark.parametrize('after',['get','query','locator'])
def test_time_budget_stops_before_next_sdk_call(world,after):
    p,d,*_=world;seed(world,1);before=snapshot(d);left=[10000];calls=[]
    def invoke(name,**kwargs):
        assert left[0]>=6000;calls.append(name);result=getattr(d,name)(**kwargs)
        if (after=='get' and len(calls)==1 or after=='query' and name=='query'
            or after=='locator' and name=='get_item' and len(calls)>5):left[0]=5999
        return result
    p.remaining=lambda:left[0]
    p.d=type('Proxy',(),{'__getattr__':lambda self,n:lambda **kw:invoke(n,**kw)})()
    with pytest.raises(Exception):p.recover_expired_orphan(ID,PERIOD)
    assert 'transact_write_items' not in calls and snapshot(d)==before


@pytest.mark.parametrize('ident,period',[(ID.upper(),PERIOD),(ID,True),('bad',PERIOD)])
def test_invalid_inputs_do_not_reach_storage(world,ident,period):
    p,*_=world
    p.d=type('NoStorage',(),{'__getattr__':lambda *a:pytest.fail('unexpected SDK access')})()
    with pytest.raises(Exception):p.recover_expired_orphan(ident,period)
