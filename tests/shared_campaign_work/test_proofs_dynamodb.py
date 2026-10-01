"""Retired-prefix and sealed proof never infer absence from missing keys."""
import pytest
from tests.shared_campaign_work.test_retirement_dynamodb import sealed,retire
from tests.shared_campaign_work.test_transactions_dynamodb import world,PERIOD
from shared_campaign_work import configuration as C,records as R
from shared_campaign_work.proofs import Proofs,advance_prefix


def test_sealed_proof_needs_exact_control_and_resource_binding(sealed):
    client,kms,put,get,registry,marker,control,now=sealed
    proof=Proofs(client,now=lambda:now)
    assert proof.sealed(PERIOD) and len(proof.guards)==4
    put(control|{'pendingCount':1,'nextOrdinal':2})
    with pytest.raises(R.WorkUnavailable):Proofs(client,now=lambda:now).sealed(PERIOD)


def test_missing_key_is_never_retired_absence(sealed):
    client,kms,put,get,registry,marker,control,now=sealed
    client.delete_item(TableName='pipeline',Key=C.wire({'PK':registry['PK'],'SK':'HMAC_KEY'}))
    assert Proofs(client,now=lambda:now).sealed(PERIOD) is False
    with pytest.raises(R.WorkUnavailable):advance_prefix(client,now=lambda:now)


def test_only_contiguous_retired_proof_advances_prefix_without_mutating_marker(sealed):
    client,kms,put,get,registry,marker,control,now=sealed
    with pytest.raises(R.WorkUnavailable):advance_prefix(client,now=lambda:now)
    retire(sealed)
    assert advance_prefix(client,now=lambda:now)=={'retiredThroughPeriodId':PERIOD}
    proof=Proofs(client,now=lambda:now)
    assert proof.minimum==PERIOD+1 and proof.sealed(PERIOD)
    assert get({'PK':marker['PK'],'SK':marker['SK']})==marker
    with pytest.raises(R.WorkUnavailable):advance_prefix(client,now=lambda:now)
    assert get({'PK':'PERIOD_RETIRED_PREFIX#dev','SK':'STATE'})['retiredThroughPeriodId']==PERIOD


def test_stale_restored_resource_id_cannot_reuse_seal(sealed,monkeypatch):
    client,kms,put,get,registry,marker,control,now=sealed
    monkeypatch.setenv('CAMPAIGN_PERIOD_WORK_PIPELINE_TABLE_ID','33333333-3333-4333-8333-333333333333')
    with pytest.raises(R.WorkUnavailable):Proofs(client,now=lambda:now)
