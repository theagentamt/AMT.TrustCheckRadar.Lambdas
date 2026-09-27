"""SDK/Moto composition fixture; real AWS execution is separate evidence."""
import hashlib
import importlib.util
import os
from pathlib import Path
from copy import deepcopy
import time
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
import boto3
from moto import mock_aws
from shared_campaign_work import configuration as C
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('period_lifecycle',ROOT/'scripts/qualification/period_lifecycle.py')
Q=importlib.util.module_from_spec(spec);spec.loader.exec_module(Q)
RUN='012345abcdef'
PREFIX='amt-campaign-lifecycle-qual-'+RUN
KEY=f'arn:aws:kms:{Q.REGION}:{Q.ACCOUNT}:key/11111111-1111-4111-8111-111111111111'

@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.setenv('MOTO_ACCOUNT_ID',Q.ACCOUNT)
    with mock_aws():
        d=boto3.client('dynamodb',region_name=Q.REGION)
        tags={'Project':'trustcheckradar','Environment':'dev','Purpose':'campaign-lifecycle-qualification','QualificationRunId':RUN}
        for family in Q.FAMILIES:
            args={'TableName':PREFIX+'-'+family,'BillingMode':'PAY_PER_REQUEST',
                'KeySchema':[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                'AttributeDefinitions':[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],
                'Tags':[{'Key':k,'Value':v} for k,v in tags.items()]}
            if family=='pipeline':
                args['AttributeDefinitions'] += [{'AttributeName':a,'AttributeType':'S'} for a in ('GSI2PK','GSI2SK')]
                args['GlobalSecondaryIndexes']=[{'IndexName':'CandidateBucketIndex','KeySchema':[{'AttributeName':'GSI2PK','KeyType':'HASH'},{'AttributeName':'GSI2SK','KeyType':'RANGE'}],'Projection':{'ProjectionType':'ALL'}}]
            if family=='intelligence':
                args['AttributeDefinitions'] += [{'AttributeName':a,'AttributeType':t} for a,t in [('expiryPartition','S'),('expiresAt','N'),('GSI1PK','S'),('GSI1SK','S')]]
                args['GlobalSecondaryIndexes']=[{'IndexName':name,'KeySchema':[{'AttributeName':h,'KeyType':'HASH'},{'AttributeName':r,'KeyType':'RANGE'}],'Projection':{'ProjectionType':projection}} for name,h,r,projection in [('ExpirationIndex','expiryPartition','expiresAt','KEYS_ONLY'),('PublicationIndex','GSI1PK','GSI1SK','ALL')]]
            d.create_table(**args)
        class Ddb:
            def __getattr__(self,name):return getattr(d,name)
            def describe_table(self,**kwargs):
                value=d.describe_table(**kwargs)
                i=Q.FAMILIES.index(kwargs['TableName'].rsplit('-',1)[1])+1
                value['Table']['TableId']=f'{i:08d}-1111-4111-8111-111111111111'
                return value
        at=int(time.time())
        class Kms:
            def __init__(self):
                self.metadata={'Arn':KEY,'KeyId':KEY.rsplit('/',1)[1],'Description':PREFIX,
                    'KeyState':'Enabled','Enabled':True,'KeyManager':'CUSTOMER','Origin':'AWS_KMS',
                    'KeySpec':'HMAC_256','KeyUsage':'GENERATE_VERIFY_MAC','MultiRegion':False}
                self.tags={'Project':'trustcheckradar','Environment':'dev','Purpose':'campaign-contributor-token','PeriodId':str(at//Q.P.PERIOD_SECONDS-2)};self.calls=[]
            def describe_key(self,**kwargs):
                assert kwargs=={'KeyId':KEY};return {'KeyMetadata':deepcopy(self.metadata)}
            def list_resource_tags(self,**kwargs):
                assert kwargs=={'KeyId':KEY};return {'Tags':[{'TagKey':k,'TagValue':v} for k,v in self.tags.items()]}
            def generate_mac(self,**kwargs):
                assert kwargs['KeyId']==KEY and kwargs['MacAlgorithm']=='HMAC_SHA_256'
                self.calls.append(kwargs);return {'KeyId':KEY,'MacAlgorithm':'HMAC_SHA_256','Mac':hashlib.sha256(kwargs['Message']).digest()}
        yield Ddb(),Kms(),at


def execute(fixture,selected_case,**changes):
    d,kms,at=fixture
    args={'run_id':RUN,'tables':{f:PREFIX+'-'+f for f in Q.FAMILIES},'key_arn':KEY,'case':selected_case,'now':lambda:at}
    return Q.run(d,kms,**(args|changes))


@pytest.mark.parametrize('case',Q.CASES)
def test_composed_real_sdk_paths(fixture,case):
    before=dict(os.environ)
    result=execute(fixture,case)
    assert result['passed'] and result['accountAndWithdrawalComplete']
    assert result['earlyDeadlineTicks']>1 and result['drainTicks']>1
    assert result['laterKmsCallCount']==0 and not result['retirementEnabled']
    assert result['injectedLostAcknowledgmentCount']==int(case=='lost_ack')
    assert result['poisonRefusedSeal']==(case=='poison_recovery')
    assert PREFIX not in str(result) and KEY not in str(result)
    assert dict(os.environ)==before


def test_existing_rows_never_reset(fixture):
    d,kms,_=fixture
    d.put_item(TableName=PREFIX+'-ledger',Item=C.wire({'PK':'existing','SK':'keep'}))
    with pytest.raises(RuntimeError):execute(fixture,'complete_replay')
    assert not kms.calls and d.scan(TableName=PREFIX+'-ledger')['Count']==1


@pytest.mark.parametrize('change',[{'run_id':'bad'},{'tables':{}},{'case':'unknown'},{'key_arn':KEY.replace(Q.ACCOUNT,'111111111111')}])
def test_namespace_refused_before_sdk(fixture,change):
    d,kms,_=fixture;d.describe_table=lambda **kw:pytest.fail('No SDK permitted')
    with pytest.raises(RuntimeError):execute(fixture,'complete_replay',**change)
    assert not kms.calls


def test_wrong_key_description_never_seeds(fixture):
    d,kms,_=fixture;kms.metadata['Description']='unrelated'
    with pytest.raises(RuntimeError):execute(fixture,'complete_replay')
    assert all(d.scan(TableName=PREFIX+'-'+f)['Count']==0 for f in Q.FAMILIES)


def test_wrong_period_key_tag_never_seeds(fixture):
    d,kms,_=fixture;kms.tags['PeriodId']=str(int(kms.tags['PeriodId'])+1)
    with pytest.raises(RuntimeError):execute(fixture,'complete_replay')
    assert not kms.calls and all(d.scan(TableName=PREFIX+'-'+f)['Count']==0 for f in Q.FAMILIES)


def test_missing_qualification_tag_never_seeds(fixture):
    d,kms,_=fixture
    arn=d.describe_table(TableName=PREFIX+'-pipeline')['Table']['TableArn']
    d.untag_resource(ResourceArn=arn,TagKeys=['QualificationRunId'])
    with pytest.raises(RuntimeError):execute(fixture,'complete_replay')
    assert not kms.calls and all(d.scan(TableName=PREFIX+'-'+f)['Count']==0 for f in Q.FAMILIES)


def test_budget_cutoff_before_first_sdk(fixture):
    d,kms,_=fixture;d.describe_table=lambda **kw:pytest.fail('No SDK permitted')
    with pytest.raises(Exception):execute(fixture,'complete_replay',remaining_ms=lambda:5999)
    assert not kms.calls
