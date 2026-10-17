"""Independent malformed aggregate and immutable revision probes, offline only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from operations_reporting.reducer import aggregate_day, day_bounds
from operations_reporting.revisions import RevisionFault, plan_revision, validate_summary


DAY = '2026-10-03'
START, END = day_bounds(DAY)
NOW = END + 60
ROWS = json.loads(Path(__file__).with_name('fixtures.json').read_text())
EVENTS = [r['event'] for r in ROWS['events']]
REJECTIONS = [r['aggregate'] for r in ROWS['rejectionAggregates']]


def snapshot(now=NOW):
    return aggregate_day(deepcopy(EVENTS), deepcopy(REJECTIONS), local_day=DAY,
                         environment='dev', as_of_epoch=now)


def initial():
    return plan_revision(None, snapshot(), generated_at_epoch=NOW)['record']


def rehash(record):
    value = {k:v for k,v in record.items() if k != 'recordSha256'}
    record['recordSha256'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def test_impossible_completion_in_empty_complimentary_population_is_invalid():
    a = deepcopy(next(e for e in EVENTS if e['eventType']=='check_accepted' and e['accessClass']=='trial'))
    t = deepcopy(next(e for e in EVENTS if e['eventType']=='check_terminal' and e['logicalCheckRef']==a['logicalCheckRef']))
    report = aggregate_day([a,t], [], local_day=DAY, environment='dev', as_of_epoch=NOW)
    report['byAccessClass']['trial'].update(complete=0, deductedChecks=0, noDeductionChecks=1)
    report['byAccessClass']['complimentary']['complete'] = 1
    with pytest.raises(RevisionFault, match='^INVALID_SUMMARY$'):
        validate_summary(report)


def test_failure_categories_cannot_all_be_relabelled_none_reason():
    report = snapshot()
    report['terminalReasons'] = {'NONE':sum(report['settled'].values())}
    with pytest.raises(RevisionFault, match='^INVALID_SUMMARY$'):
        validate_summary(report)


@pytest.mark.parametrize('delta', [{'/accepted':-123}, {}, {'/accepted':11}])
def test_first_revision_delta_must_equal_exact_nonzero_report_projection(delta):
    record = initial()
    record['numericDelta'] = delta
    rehash(record)
    with pytest.raises(RevisionFault, match='^INVALID_PREVIOUS_REVISION$'):
        plan_revision(record, snapshot(), generated_at_epoch=NOW)


def test_summary_extensions_cannot_hide_at_any_aggregate_depth(capsys):
    for path in [('settled',),('internalAttempts',),('byAccessClass','paid'),('outcomeRates','complete')]:
        report=snapshot();target=report
        for p in path: target=target[p]
        target['victimId']='private-sentinel'
        with pytest.raises(RevisionFault, match='^INVALID_SUMMARY$') as error:
            validate_summary(report)
        assert error.value.args==('INVALID_SUMMARY',)
    assert capsys.readouterr()==('', '')


def test_true_is_never_a_numeric_count_or_known_cost():
    for section,key in [('internalAttempts','started'),('reconciliationGaps','finishWithoutStart')]:
        report=snapshot();report[section][key]=True
        with pytest.raises(RevisionFault, match='^INVALID_SUMMARY$'):
            validate_summary(report)


def test_previous_object_is_not_mutated_even_when_replay_or_cross_scope_rejected():
    old=initial();before=deepcopy(old)
    assert plan_revision(old,snapshot(NOW+30),generated_at_epoch=NOW+30)['decision']=='NO_CHANGE'
    changed=snapshot();changed['environment']='uat'
    with pytest.raises(RevisionFault, match='^INVALID_PREVIOUS_REVISION$'):
        plan_revision(old,changed,generated_at_epoch=NOW+30)
    assert old==before


def test_equal_concurrent_plans_expose_cas_without_claiming_serialization():
    left=plan_revision(None,snapshot(),generated_at_epoch=NOW)
    right=plan_revision(None,snapshot(),generated_at_epoch=NOW)
    assert left==right and left['expectedPreviousSha256'] is None
    assert left['record']['report']['deliveryPerformed'] is False
    assert left['record']['report']['producerCoverage']=='not_qualified'


def test_fixed_expiry_is_independent_of_replay_generation_time():
    old=initial();old_expiry=old['expiresAtEpoch']
    for seconds in [1,3600,86400,2*86400]:
        result=plan_revision(old,snapshot(NOW+seconds),generated_at_epoch=NOW+seconds)
        assert result['decision']=='NO_CHANGE'
        assert result['record']['expiresAtEpoch']==old_expiry
        assert result['record']['generatedAtEpoch']==NOW


@pytest.mark.parametrize('outcome,reasons', [
    ('complete',['NONE']),
    ('partial',['COVERAGE_PARTIAL','PROVIDER_UNAVAILABLE','PROVIDER_AUTHORIZATION_FAILED','PROVIDER_RATE_LIMITED','PROVIDER_RESPONSE_INVALID','TIME_BUDGET_EXHAUSTED']),
    ('inconclusive',['RESULT_INCONCLUSIVE']),
    ('failed',['SETTLED_FAILED','OPERATION_EXPIRED','SERVICE_UNAVAILABLE']),
    ('blocked',['RESULT_BLOCKED']),
    ('invalid_input',['RESULT_INVALID']),
    ('unsupported',['RESULT_UNSUPPORTED']),
    ('unavailable',['PROVIDER_UNAVAILABLE','PROVIDER_AUTHORIZATION_FAILED','PROVIDER_RATE_LIMITED','PROVIDER_RESPONSE_INVALID','TIME_BUDGET_EXHAUSTED','SERVICE_DISABLED','SERVICE_UNAVAILABLE']),
])
def test_reason_feasibility_fix_preserves_every_valid_schema_family(outcome,reasons):
    a=deepcopy(next(e for e in EVENTS if e['eventType']=='check_accepted' and e['accessClass']=='trial'))
    t=deepcopy(next(e for e in EVENTS if e['eventType']=='check_terminal' and e['logicalCheckRef']==a['logicalCheckRef']))
    for reason in reasons:
        t.update(processingOutcome=outcome,reasonCode=reason,
                 accountingState='charged' if outcome=='complete' else 'not_charged',
                 chargedChecks=1 if outcome=='complete' else 0)
        report=aggregate_day([a,t],[],local_day=DAY,environment='dev',as_of_epoch=NOW)
        assert validate_summary(report)==report


def test_dense_zero_reason_keys_canonicalize_without_fabricating_corrections():
    old=initial();later=snapshot(NOW+86400)
    unused=next(k for k in ['SERVICE_DISABLED','PROVIDER_RATE_LIMITED','PROVIDER_AUTHORIZATION_FAILED','PROVIDER_RESPONSE_INVALID'] if k not in later['terminalReasons'])
    later['terminalReasons'][unused]=0
    later['requestRejectionsByService']['message_check']['AUTHENTICATION']=0
    result=plan_revision(old,later,generated_at_epoch=NOW+86400)
    assert result['decision']=='NO_CHANGE' and result['record']==old
