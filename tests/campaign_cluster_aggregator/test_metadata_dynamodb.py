"""Real producer storage boundaries for reconstructable contributor metadata."""
import os,uuid,base64
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated SDK tests',allow_module_level=True)
from tests.shared_campaign_locators.test_dynamodb import (
    world,publish,aggregate,get,key,wire,EVENT,TOKEN,PERIOD,locator_for_target)


def summary(d):
    clustered=get(d,'pipeline','EVENT#'+EVENT,'CLUSTERED')
    pk='CANDIDATE#'+clustered['candidateId']
    return pk,get(d,'pipeline',pk,'SUMMARY')


def test_new_and_repeat_contributions_preserve_original_reconstructable_features(world):
    d,put=world;publish(d);aggregate(d);pk,first=summary(d)
    original=get(d,'pipeline',pk,'CONTRIB#'+TOKEN)
    assert original['metadataSchemaVersion']==1
    assert original['lexicalFingerprint']==first['lexicalFingerprint']==['0123456789abcdef']
    assert original['signalIds']==first['signalIds'] and original['indicatorIds']==first['indicatorIds']
    event=str(uuid.uuid4());publish(d,event)
    feature=get(d,'pipeline','EVENT#'+event,'FEATURE');put(feature|{'indicatorIds':['payment.crypto','new_signal']})
    assert aggregate(d,event)=='counted-repeat'
    later=get(d,'pipeline',pk,'CONTRIB#'+TOKEN)
    for field in ('metadataSchemaVersion','lexicalFingerprint','signalIds','indicatorIds','expiresAt'):
        assert later[field]==original[field]
    assert get(d,'pipeline',pk,'SUMMARY')['indicatorIds']==first['indicatorIds']


def test_new_contributor_summary_and_later_repair_have_same_indicator_union(world):
    d,put=world;publish(d);aggregate(d);pk,first=summary(d)
    event=str(uuid.uuid4());publish(d,event)
    token=base64.urlsafe_b64encode(b'y'*32).decode().rstrip('=')
    feature=get(d,'pipeline','EVENT#'+event,'FEATURE')
    feature.update(contributorToken=token,GSI1PK=f'CONTRIB#{PERIOD}#{token}',indicatorIds=['payment.crypto','new_signal'])
    put(feature);put(locator_for_target(feature,'dev'))
    assert aggregate(d,event)=='matched'
    result=get(d,'pipeline',pk,'SUMMARY')
    assert result['indicatorIds']==['new_signal','payment.crypto']
    assert get(d,'pipeline',pk,'CONTRIB#'+token)['lexicalFingerprint']==feature['lexicalFingerprint']


@pytest.mark.parametrize('target',['SUMMARY','CONTRIB'])
def test_legacy_metadata_cannot_be_blessed_by_new_arrival(world,target):
    d,put=world;publish(d);aggregate(d);pk,before=summary(d)
    sk='SUMMARY' if target=='SUMMARY' else 'CONTRIB#'+TOKEN
    original=get(d,'pipeline',pk,sk);original.pop('metadataSchemaVersion');put(original)
    event=str(uuid.uuid4());publish(d,event)
    with pytest.raises(ValueError):aggregate(d,event)
    assert get(d,'pipeline',pk,sk)==original
    assert get(d,'pipeline','EVENT#'+event,'CLUSTERED') is None


def test_changed_contributor_metadata_between_read_and_write_is_not_counted(world):
    d,put=world;publish(d);aggregate(d);pk,before=summary(d)
    event=str(uuid.uuid4());publish(d,event)
    class Race:
        def __getattr__(self,name):return getattr(d,name)
        def transact_write_items(self,**kwargs):
            item=get(d,'pipeline',pk,'CONTRIB#'+TOKEN);put(item|{'indicatorIds':['different']})
            return d.transact_write_items(**kwargs)
    with pytest.raises(d.exceptions.TransactionCanceledException):aggregate(Race(),event)
    assert get(d,'pipeline',pk,'SUMMARY')==before
    assert get(d,'pipeline','EVENT#'+event,'CLUSTERED') is None
    assert get(d,'pipeline',pk,'CONTRIB#'+TOKEN)['submissionCount']==1
