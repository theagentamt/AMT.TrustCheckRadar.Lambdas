"""Candidate2 source contract fixtures and independent-client reference validation."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest
from jsonschema import Draft202012Validator, FormatChecker
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from shared_message_contract.validation_v2 import validate_summary
DIR=ROOT/'contracts/message-consumer/1.0.0-candidate.2'
FIXTURES=json.loads((DIR/'response-fixtures.json').read_text())


@pytest.mark.parametrize('item',FIXTURES,ids=lambda item:item['id'])
def test_new_contract_fixture_and_reference_agree(item):
    Draft202012Validator(json.loads((DIR/'response.schema.json').read_text()),format_checker=FormatChecker()).validate(item['body'])
    if item['body']['outcome']:
        value=item['body']['outcome'];validate_summary(value)
        spec=importlib.util.spec_from_file_location('candidate2_reference',DIR/'reference_validation.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.validate_summary(value)


def test_candidate2_checksums_requests_and_copy():
    for line in (DIR/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ');assert hashlib.sha256((DIR/name).read_bytes()).hexdigest()==digest
    for item in json.loads((DIR/'request-fixtures.json').read_text()):
        kind='reconcile' if item['route'].endswith('/reconcile') else 'prepare' if item['route'].endswith('/prepare') else 'submit'
        Draft202012Validator(json.loads((DIR/(kind+'.schema.json')).read_text())).validate(item['body'])
    messages=json.loads((DIR/'messages.json').read_text())
    assert messages['message.ai_basis']=={'en':'AI assessment','es':'Evaluación de IA'}
    assert messages['message.ai_inconclusive']['en'].endswith('No check was deducted.')


@pytest.mark.parametrize('id,mutation',[
 ('ai-warning-complete',{'verdict':'high_risk'}),
 ('ai-no-warning-complete',{'aiReasonCodes':['AI_PRETEXT']}),
 ('ai-warning-complete',{'aiReasonCodes':[]}),
 ('ai-warning-complete',{'assessmentBasis':[]}),
 ('ai-abstained',{'processingOutcome':'complete','coverage':'supported_checks_complete','limitationCodes':[]}),
 ('ai-known-link-survives-no-warning',{'verdict':'no_known_threat_detected','messageKey':'message.ai_no_warning'}),
 ('ai-warning-withheld-link',{'processingOutcome':'complete','coverage':'supported_checks_complete'}),
 ('ai-no-warning-withheld-link',{'processingOutcome':'complete','coverage':'supported_checks_complete'}),
 ('ai-provider-timeout',{'aiReasonCodes':['AI_PRETEXT']}),
 ('ai-independent-high-risk-with-ai-warning',{'assessmentBasis':['ai_assessment']}),
])
def test_billing_evidence_and_ai_ceiling_mutations_rejected(id,mutation):
    item=next(x for x in FIXTURES if x['id']==id);value=copy.deepcopy(item['body']['outcome']);value.update(mutation)
    with pytest.raises(Exception):validate_summary(value)
    assert list(Draft202012Validator(json.loads((DIR/'outcome.schema.json').read_text())).iter_errors(value))


def test_zero_charge_copy_never_allowed_on_unknown_or_charged_envelope():
    body=copy.deepcopy(next(x['body'] for x in FIXTURES if x['id']=='ai-abstained'))
    validator=Draft202012Validator(json.loads((DIR/'response.schema.json').read_text()))
    for mutation in ({'state':'unknown'}, {'accounting':body['accounting']|{'state':'charged','chargedChecks':1}}):
        invalid=copy.deepcopy(body);invalid.update(mutation)
        assert list(validator.iter_errors(invalid))


@pytest.mark.parametrize('item',json.loads((DIR/'invalid-outcome-fixtures.json').read_text()),ids=lambda item:item['id'])
def test_untrustworthy_text_never_retains_ai_finding(item):
    with pytest.raises(Exception):validate_summary(item['outcome'])
    assert list(Draft202012Validator(json.loads((DIR/'outcome.schema.json').read_text())).iter_errors(item['outcome']))
