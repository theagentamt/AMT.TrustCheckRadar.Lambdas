"""New producer deadlines fit the existing period/recovery and data maxima."""
import os
import json
from decimal import Decimal
from types import SimpleNamespace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
from tests.shared_campaign_locators.test_dynamodb import (
    world,publisher,cluster,observation,EVENT,PERIOD,PINS,TOKEN,get,wire)
from shared_campaign_locators import period as P


def run(d,when,expiry):
    item=observation()|{'observedAtEpoch':when,'expiresAt':expiry}
    d.put_item(TableName='outbox',Item=wire(json.loads(json.dumps(item|{'PK':'EVENT#'+EVENT,'SK':'OBSERVATION_READY','eventType':'campaign.observation.ready'}),parse_float=Decimal)))
    assert publisher.publish_observation(item,pipeline_table_name='pipeline',users_table_name='users',deletion_ledger_table_name='ledger',
        cluster_queue_url='synthetic',hmac_key_id='arn:aws:kms:us-east-1:107827791950:key/12345678-1234-4234-8234-123456789abc',
        transient_retention_days=21,dynamodb_client=d,kms_client=SimpleNamespace(generate_mac=lambda **_:{'Mac':b'x'*32}),
        sqs_client=SimpleNamespace(send_message=lambda **_:{}),now_epoch=when,**PINS)=='published'
    body=json.dumps({'schemaVersion':1,'recordVersion':1,'environment':'dev','statisticsEventId':EVENT,'eventType':'campaign.cluster.requested'})
    assert cluster.process_message(body,environment='dev',schema_version=1,table_name='pipeline',retention_days=21,max_submissions=3,
        dynamodb=d,now_epoch=when,users_table_name='users',deletion_ledger_table_name='ledger',outbox_table_name='outbox',**PINS)=='candidate-created'


@pytest.mark.parametrize('before_end,observation_lifetime',[(60,72*3600),(4*86400,1000)])
def test_exact_group_deadlines_never_exceed_original_or_recovery_bound(world,before_end,observation_lifetime):
    d,_=world;end=(PERIOD+1)*P.PERIOD_SECONDS;when=end-before_end
    run(d,when,when+observation_lifetime)
    expected=min(when+observation_lifetime+18*86400,when+21*86400,end+P.RECOVERY_SECONDS)
    event='EVENT#'+EVENT
    for sk in ('FEATURE','DEDUPE','CLUSTERED'):
        row=get(d,'pipeline',event,sk)
        assert row['expiresAt']==expected and row['GSI3SK']==expected
    clustered=get(d,'pipeline',event,'CLUSTERED');pk='CANDIDATE#'+clustered['candidateId']
    for sk in ('SUMMARY','CONTRIB#'+TOKEN):
        row=get(d,'pipeline',pk,sk);assert row['expiresAt']==expected and row['GSI3SK']==expected
    rows=[item for item in d.scan(TableName='pipeline')['Items'] if item.get('recordType',{}).get('S')=='CAMPAIGN_CONTRIBUTOR_LOCATOR']
    assert len(rows)==2 and all(Decimal(v['targetExpiresAtEpoch']['N'])==expected for v in rows)


@pytest.mark.parametrize('period,now,bound',[(True,1,2),(PERIOD,True,2),(PERIOD,1,True)])
def test_deadline_helper_rejects_boolean_clocks(period,now,bound):
    with pytest.raises(Exception):P.transient_deadline(period,now,bound)


def test_recovery_boundary_cannot_create_even_if_registry_open():
    end=(PERIOD+1)*P.PERIOD_SECONDS+P.RECOVERY_SECONDS
    with pytest.raises(Exception):P.transient_deadline(PERIOD,end,end+86400)
