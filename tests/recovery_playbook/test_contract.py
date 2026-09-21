import copy
import hashlib
import importlib.util
import itertools
import json
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT=Path(__file__).resolve().parents[2];DIR=ROOT/'contracts/recovery-playbook/1.0'
spec=importlib.util.spec_from_file_location('detailed_recovery',DIR/'reference_selector.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
CASES=m.read('fixtures.json')['cases'];BUNDLE=m.read('bundle.json')


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id'])
def test_actual_detailed_default_matches_bilingual_combination(case):
    result=m.select(case['input'],signed_in=True,today=date(2026,9,21))
    assert result['state']=='available'
    assert {k:result[k] for k in case['expectedPlan']}==case['expectedPlan']
    Draft202012Validator(m.read('selection-output.schema.json')).validate(result)
    assert result['accounting']=='not_an_analysis_check' and result['clarification']=='unavailable'


def test_approved_text_is_exact_and_self_contained():
    basics=m.basics.read('recovery-basics-approved.json')['content']
    mapping={'sent_payment':'payment','credentials_or_mfa':'password','financial_or_identity':'identity','clicked_link':'link','software_or_remote_access':'device'}
    assert len(BUNDLE['actions'])==9 and len(BUNDLE['limits'])==4
    for item in BUNDLE['actions']+BUNDLE['limits']:
        for language in ('en','es'):
            assert item['text'][language] in basics[language]['recovery_'+mapping[item['exposureId']]+'_body']
    for language,content in BUNDLE['common'].items():
        assert all(value==basics[language][key] for key,value in content.items())
    assert BUNDLE['officialLinks']==m.basics.read('official-links.json')
    assert BUNDLE['headings']=={'now':{'en':'Do now','es':'Qué hacer ahora'},'follow_up':{'en':'Follow-up','es':'Seguimiento'}}
    assert BUNDLE['conditionalNotes']=={'link_device_conditional':{'en':'If you downloaded something suspicious, also use the device guidance.','es':'Si descargó algo sospechoso, consulte también las indicaciones para el dispositivo.'}}
    assert BUNDLE['approvalEvidence']['reviewSha256']=='173132b8d5a16d3d5a8ccdbc7355a631f8384634945772993200a3559c89da72'
    assert BUNDLE['approvedOn']=='2026-09-21' and m.basics.read('recovery-basics-approved.json')['approvedOn']=='2026-09-20'


def test_all_combination_permutations_priorities_limits_and_conditional_note():
    for case in CASES[:32]:
        value=case['input'];expected=m.plan(value)
        for permutation in itertools.permutations(value['exposures']):
            assert m.plan(value|{'exposures':list(permutation)})==expected
        combined=expected['nowActionIds']+expected['followUpActionIds']
        assert len(combined)==len(set(combined))
        assert len(expected['limitIds'])==len(set(expected['limitIds']))
    assert m.plan(CASES[0]['input'])['conditionalNoteKey']=='link_device_conditional'
    assert m.plan(CASES[0]['input']|{'exposures':['clicked_link','software_or_remote_access']})['conditionalNoteKey'] is None
    assert m.plan(CASES[0]['input']|{'exposures':['software_or_remote_access'],'unsure':True})['conditionalNoteKey']=='link_device_conditional'


@pytest.mark.parametrize('today,state',[(date(2026,9,20),'review_due'),(date(2026,9,21),'available'),(date(2027,3,20),'available'),(date(2027,3,21),'review_due')])
def test_new_bundle_has_own_review_clock(today,state):
    assert m.select(CASES[0]['input'],signed_in=True,today=today)['state']==state


def test_independent_withdrawal_combinations_and_missing_corrupt_fallback():
    args={'signed_in':True,'today':date(2027,3,20)};answers=CASES[0]['input']
    withdrawn=m.basics.read('recovery-basics-approved.json')|{'approval':'Withdrawn'}
    # Valid complete detailed bundle survives withdrawal of the older bundle.
    detailed=m.select(answers,**args,fallback_content=withdrawn)
    assert detailed['state']=='available' and detailed['activeBundleVersion']==m.VERSION
    for missing in ({},BUNDLE|{'approval':'Withdrawn'},BUNDLE|{'bundleVersion':'future'}):
        fallback=m.select(answers,**args,bundle=missing)
        assert fallback['state']=='fallback_basics' and fallback['fallbackPlan']['state']=='review_due'
        assert fallback['nowActionIds']==[] and fallback['conditionalNoteKey'] is None
        unavailable=m.select(answers,**args,bundle=missing,fallback_content=withdrawn)
        assert unavailable['state']=='limited_help' and unavailable['fallbackPlan'] is None
        for result in (fallback,unavailable):Draft202012Validator(m.read('selection-output.schema.json')).validate(result)
    changed=copy.deepcopy(BUNDLE);changed['common']['en']['recovery_intro_body']='invented'
    assert m.select(answers,**args,bundle=changed)['state']=='fallback_basics'
    signed_out=m.select(answers,signed_in=False,today=date(2026,9,21))
    assert signed_out['state']=='sign_in_required' and signed_out['fallbackPlan'] is None


def test_packet_integrity():
    for line in (DIR/'SHA256SUMS').read_text().splitlines():
        checksum,name=line.split('  ')
        assert hashlib.sha256((DIR/name).read_bytes()).hexdigest()==checksum
    Draft202012Validator.check_schema(m.read('selection-output.schema.json'))


@pytest.mark.parametrize('case',m.read('availability-fixtures.json')['cases'],ids=lambda c:c['id'])
def test_shared_native_availability_cases(case):
    supplied={'approved':BUNDLE,'withdrawn':BUNDLE|{'approval':'Withdrawn'},'missing':{},'incompatible':BUNDLE|{'bundleVersion':'future'}}[case['detailedState']]
    old=m.basics.read('recovery-basics-approved.json')
    if case['basicsState']=='withdrawn':old=old|{'approval':'Withdrawn'}
    result=m.select(CASES[0]['input']|{'language':case['language']},signed_in=case['signedIn'],today=date.fromisoformat(case['today']),bundle=supplied,fallback_content=old)
    observed={key:result[key] for key in ('state','activeBundleVersion','reviewDue')}
    observed['fallbackState']=result['fallbackPlan']['state'] if result['fallbackPlan'] else None
    assert observed==case['expected']
    Draft202012Validator(m.read('selection-output.schema.json')).validate(result)
