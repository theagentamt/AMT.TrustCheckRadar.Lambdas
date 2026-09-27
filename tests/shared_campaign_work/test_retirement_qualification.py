"""AWS-ready fixture on real SDK/Moto tables; KMS modeled deterministically."""
import importlib.util
import os
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone
import time
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
import boto3
from moto import mock_aws
from botocore.exceptions import ClientError
from shared_campaign_work import configuration as C
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('period_retirement',ROOT/'scripts/qualification/period_retirement.py')
Q=importlib.util.module_from_spec(spec);spec.loader.exec_module(Q)
RUN='012345abcdef'
PREFIX='amt-campaign-retirement-qual-'+RUN
KEY=f'arn:aws:kms:{Q.REGION}:{Q.ACCOUNT}:key/11111111-1111-4111-8111-111111111111'

@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.setenv('MOTO_ACCOUNT_ID',Q.ACCOUNT)
    with mock_aws():
        d=boto3.client('dynamodb',region_name=Q.REGION)
        tags={'Project':'trustcheckradar','Environment':'dev','Purpose':'campaign-retirement-qualification','QualificationRunId':RUN}
        for family in ('pipeline','outbox'):
            d.create_table(TableName=PREFIX+'-'+family,BillingMode='PAY_PER_REQUEST',
                KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],
                Tags=[{'Key':k,'Value':v} for k,v in tags.items()])
        class Ddb:
            def __getattr__(self,name):return getattr(d,name)
            def describe_table(self,**kwargs):
                value=d.describe_table(**kwargs)
                value['Table']['TableId']=('11111111-1111-4111-8111-111111111111'
                    if kwargs['TableName'].endswith('-pipeline') else '22222222-2222-4222-8222-222222222222')
                return value
        at=int(time.time());period=at//Q.P.PERIOD_SECONDS-2
        class Kms:
            def __init__(self):
                self.metadata={'Arn':KEY,'KeyId':KEY.rsplit('/',1)[1],'Description':PREFIX,
                    'KeyState':'Enabled','Enabled':True,'KeyManager':'CUSTOMER','Origin':'AWS_KMS',
                    'KeySpec':'HMAC_256','KeyUsage':'GENERATE_VERIFY_MAC','MultiRegion':False}
                self.tags={'Project':'trustcheckradar','Environment':'dev','Purpose':'campaign-contributor-token','PeriodId':str(period)}
                self.calls=[]
            def describe_key(self,**kwargs):
                assert kwargs=={'KeyId':KEY};return {'KeyMetadata':deepcopy(self.metadata)}
            def list_resource_tags(self,**kwargs):
                assert kwargs=={'KeyId':KEY};return {'Tags':[{'TagKey':k,'TagValue':v} for k,v in self.tags.items()]}
            def disable_key(self,**kwargs):
                assert kwargs=={'KeyId':KEY};self.calls.append('disable')
                self.metadata.update(KeyState='Disabled',Enabled=False)
            def schedule_key_deletion(self,**kwargs):
                assert kwargs=={'KeyId':KEY,'PendingWindowInDays':7};self.calls.append('schedule')
                self.metadata.update(KeyState='PendingDeletion',Enabled=False,
                    DeletionDate=datetime.fromtimestamp(at+7*86400,timezone.utc))
            def generate_mac(self,**kwargs):
                assert kwargs=={'KeyId':KEY,'MacAlgorithm':'HMAC_SHA_256','Message':Q.MESSAGE}
                if not self.metadata['Enabled']:
                    raise ClientError({'Error':{'Code':'DisabledException' if self.metadata['KeyState']=='Disabled' else 'KMSInvalidStateException'}},'GenerateMac')
                return {'KeyId':KEY,'MacAlgorithm':'HMAC_SHA_256','Mac':b'x'*32}
        yield Ddb(),Kms(),at


def execute(fixture,selected_case,**changes):
    d,kms,at=fixture
    args={'run_id':RUN,'pipeline_table':PREFIX+'-pipeline','outbox_table':PREFIX+'-outbox',
        'key_arn':KEY,'case':selected_case,'now':lambda:at}
    return Q.run(d,kms,**(args|changes))


@pytest.mark.parametrize('case',Q.CASES)
def test_real_sdk_fixture_cases(fixture,case):
    before=dict(os.environ)
    result=execute(fixture,case)
    assert result['passed'] and result['syntheticProof'] and not result['productionActivation']
    assert result['kmsMutationCount']==(0 if case in ('gate_closed','preproof_refusal') else 2)
    assert not result['keyDestroyedObserved']
    assert str(KEY) not in str(result) and PREFIX not in str(result)
    assert dict(os.environ)==before
    if case=='guards_and_complete_replay':assert result['guardRefusalCount']==2
    if case=='lost_ack':assert result['injectedLostAcknowledgmentCount']==2


def test_fixture_never_resets_existing_rows(fixture):
    d,kms,_=fixture
    d.put_item(TableName=PREFIX+'-pipeline',Item=C.wire({'PK':'existing','SK':'keep'}))
    with pytest.raises(RuntimeError):execute(fixture,'complete_replay')
    assert kms.calls==[]
    assert d.scan(TableName=PREFIX+'-pipeline')['Count']==1


@pytest.mark.parametrize('changes',[
    {'run_id':'nothex'},{'pipeline_table':'trustcheckradar-dev-pipeline'},
    {'outbox_table':PREFIX+'-pipeline'},{'key_arn':KEY.replace(Q.ACCOUNT,'111111111111')},
    {'case':'other'}])
def test_closed_namespace_rejects_before_sdk(fixture,changes):
    d,kms,_=fixture
    d.describe_table=lambda **kwargs:pytest.fail('SDK must not run')
    with pytest.raises(RuntimeError):execute(fixture,'complete_replay',**changes)
    assert kms.calls==[]


def test_wrong_key_description_never_seeds_or_mutates(fixture):
    d,kms,_=fixture;kms.metadata['Description']='other-owned-key'
    with pytest.raises(RuntimeError):execute(fixture,'complete_replay')
    assert d.scan(TableName=PREFIX+'-pipeline')['Count']==0 and kms.calls==[]
