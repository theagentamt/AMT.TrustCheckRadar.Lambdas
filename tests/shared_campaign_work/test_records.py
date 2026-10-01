from copy import deepcopy
from decimal import Decimal
import pytest
from shared_campaign_work import records as R

GEN='69a43d58-54d1-4edc-9941-8a37a5ab8d79'
EVENT='7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc'
PK='EVENT#'+EVENT


def sample():return R.pair('dev',GEN,1480,1,'pipeline',PK,'FEATURE',1790000000)


def test_hash_is_framed_and_domain_separated_and_lookup_contains_no_target_keys():
    work,lookup=sample();assert R.validate_pair(work,lookup,'dev',GEN)==(work,lookup)
    assert work['SK']=='WORK#00000000000000000001'
    assert 'targetPK' not in lookup and 'targetSK' not in lookup
    assert R.lookup_key('pipeline',PK,'FEATURE')!=R.lookup_key('outbox',PK,'OBSERVATION_READY')


@pytest.mark.parametrize('field,value', [('ordinal',True),('schemaVersion',True),('periodId',True),('deadlineEpoch',True),
                                        ('generation','00000000-0000-4000-8000-000000000000'),('unknown','preserve')])
def test_pair_unknown_and_wire_type_mismatch_refused(field,value):
    work,lookup=sample();work[field]=value
    with pytest.raises(R.WorkUnavailable):R.validate_pair(work,lookup,'dev',GEN)


@pytest.mark.parametrize('pk,sk', [('EVENT#raw','FEATURE'),(PK,'OTHER'),('CANDIDATE#'+EVENT,'CONTRIB#raw'),
                                  ('PERIOD#1480','HMAC_KEY'),('PERIOD_WORK_CONTROL#1480','STATE'),
                                  ('CONTRIB#01480#'+'A'*43,'TOMBSTONE')])
def test_unknown_target_never_becomes_coverage(pk,sk):
    with pytest.raises(R.WorkUnavailable):R.lookup_key('pipeline',pk,sk)


def test_ordinal_and_clock_bounds():
    for value in (0,True,Decimal('1.5'),Decimal('NaN'),R.MAX_ORDINAL+1):
        with pytest.raises(R.WorkUnavailable):R.work_key(1480,value)
    with pytest.raises(R.WorkUnavailable):R.pair('dev',GEN,1480,1,'pipeline','CONTRIB#1481#'+'A'*43,'TOMBSTONE',1790000000)


def test_lookup_collision_or_wrong_target_is_not_adopted():
    work,lookup=sample()
    with pytest.raises(R.WorkUnavailable):R.validate_pair(work,lookup,'dev',GEN,family='pipeline',pk=PK,sk='DEDUPE')
    other=deepcopy(lookup);other['ordinal']=2
    with pytest.raises(R.WorkUnavailable):R.validate_pair(work,other,'dev',GEN)
