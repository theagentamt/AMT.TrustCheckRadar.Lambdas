import json
from pathlib import Path
from copy import deepcopy
import pytest
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(__file__).parents[2]/'contracts/url-consumer/1.0.0-candidate.1'
SCHEMA=json.loads((ROOT/'response.schema.json').read_text())
FIXTURES=json.loads((ROOT/'fixtures.json').read_text())['fixtures']
VALIDATOR=Draft202012Validator(SCHEMA,format_checker=FormatChecker())

@pytest.mark.parametrize('fixture',FIXTURES,ids=lambda x:x['name'])
def test_canonical_transport_fixture(fixture):
    body=fixture['response'];VALIDATOR.validate(body)
    if body['outcome']:
        assert body['outcome']['checkId']==body['checkId']
        assert body['outcome']['accounting']==body['accounting']
        assert body['outcome']['access']==body['access']
    if fixture['httpStatus']>=400:
        assert body['accounting']['chargedChecks'] is None
        assert body['accounting']['requiresReconciliation'] is True


def test_prior_charge_preserved_and_reconcile_failure_unknown():
    data={x['name']:x['response'] for x in FIXTURES}
    assert data['repeat-prepare-charged']['accounting']['chargedChecks']==1
    for name in ('reconcile-auth-failure','submit-refresh-failure','invalid-reconcile','expired-submit','conflict'):
        assert data[name]['accounting']['chargedChecks'] is None
    assert data['known-threat-partial']['outcome']['verdict']=='high_risk'
    assert data['known-threat-partial']['accounting']['chargedChecks']==0


def test_pending_cannot_claim_free_or_offer_an_outcome():
    value=deepcopy(next(x['response'] for x in FIXTURES if x['name']=='pending'))
    value['accounting']={'state':'not_started','chargedChecks':0,'receiptId':None,'requiresReconciliation':False}
    assert list(VALIDATOR.iter_errors(value))
