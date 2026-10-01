import hashlib
import importlib.util
import itertools
import json
from datetime import date
from pathlib import Path

import pytest

DIR=Path(__file__).resolve().parents[2]/'contracts/recovery-selection/1.0.0'
spec=importlib.util.spec_from_file_location('approved_recovery',DIR/'reference_selector.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
CASES=m.read('fixtures.json')['cases']


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id'])
def test_actual_approved_default_matches_every_bilingual_fixture(case):
    result=m.select(case['input'],signed_in=True,today=date(2026,9,21))
    assert result['state']==case['shippedPolicyState']=='available'
    assert result['actionIds']==case['proposedActionIds']
    assert result['clarification']=='unavailable' and result['accounting']=='not_an_analysis_check'


def test_exact_provenance_copy_and_immutable_predecessor():
    old=DIR.parent/'1.0.0-candidate.1'
    for directory in (DIR,old):
        for row in (directory/'SHA256SUMS').read_text().splitlines():
            digest,name=row.split('  ')
            assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==digest
    policy=m.read('selection-policy.json');oldpolicy=json.loads((old/'selection-policy.json').read_text())
    assert policy['uiDraft']==oldpolicy['uiDraft'] and len(policy['uiDraft'])==7
    assert policy['selectionApprovedOn']=='2026-09-21'
    assert '7-1315' in policy['approvalRecord']
    assert policy['approvalEvidence']['reviewSha256']=='158b44a11d6f96637993fc5765618a10a7703f7c2da83c74fc603969d9dc45f4'
    assert (DIR/'recovery-basics-approved.json').read_bytes()==(old/'recovery-basics-approved.json').read_bytes()
    assert m.read('recovery-basics-approved.json')['approvedOn']=='2026-09-20'


def test_actual_default_session_withdrawal_review_due_and_unknown_fields():
    value=CASES[0]['input']
    assert m.select(value,signed_in=False,today=date(2026,9,21))['state']=='sign_in_required'
    assert m.select(value,signed_in=True,today=date(2027,3,20))['state']=='review_due'
    for content in ({},m.read('recovery-basics-approved.json')|{'approval':'Withdrawn'},m.read('recovery-basics-approved.json')|{'bundleVersion':'future'}):
        assert m.select(value,signed_in=True,today=date(2026,9,21),content=content)['state']=='limited_help'
    with pytest.raises(m.ContractError):m.select(value|{'privateIncident':'private-marker'},signed_in=True,today=date(2026,9,21))
