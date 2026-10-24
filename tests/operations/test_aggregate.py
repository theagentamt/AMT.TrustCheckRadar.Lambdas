"""Offline contract/reducer regressions; no service/provider acceptance claims."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from shared_operations.aggregate import day_bounds, reduce_day
from shared_operations.contract import OperationsError, empty_event, validate

DAY = date(2026, 10, 4)
START, END = day_bounds(DAY)
LC = 'lc1_' + '1' * 32


def accepted(**changes):
    return empty_event(eventType='check_accepted', eventId='op1_'+'1'*32, eventTimeEpoch=START+10,
        environment='dev', service='message_check', inputKind='pasted_text', logicalCheckRef=LC,
        lifecycleRevision=1, accountingState='pending', accessClass='trial', **changes)


def terminal(outcome='complete', **changes):
    charged = outcome == 'complete'
    reasons = {'complete': 'NONE', 'partial': 'COVERAGE_PARTIAL', 'inconclusive': 'RESULT_INCONCLUSIVE',
        'failed': 'SETTLED_FAILED', 'blocked': 'RESULT_BLOCKED', 'invalid_input': 'RESULT_INVALID',
        'unsupported': 'RESULT_UNSUPPORTED', 'unavailable': 'PROVIDER_UNAVAILABLE'}
    data = dict(accepted(), eventType='check_terminal', eventId='op1_'+'2'*32, lifecycleRevision=2,
        eventTimeEpoch=START+20, processingOutcome=outcome, accountingState='charged' if charged else 'not_charged',
        chargedChecks=int(charged), reasonCode=reasons[outcome])
    return validate(data | changes)


def attempt(*, finished=False, provider=True, **changes):
    data = dict(accepted(), eventType='attempt_finished' if finished else 'attempt_started',
        eventId='op1_'+('4' if finished else '3')*32, lifecycleRevision=None,
        accountingState=None, accessClass=None, attemptRef='at1_'+'1'*32,
        attemptKind='provider_call' if provider else 'internal_component', component='message_evaluator',
        attemptOrdinal=1, providerFamily='openai' if provider else None,
        eventTimeEpoch=START+(15 if finished else 12), attemptOutcome='succeeded' if finished else None,
        durationBucket='1_4s' if finished else None,
        providerRequestUnits=1 if finished and provider else None,
        estimatedCostMicrousd=10 if finished and provider else None)
    return validate(data | changes)


def report(events, rejections=None, **changes):
    options = dict(day=DAY, as_of=END+100, environment='dev', source_complete=True,
        provider_coverage={'openai':'complete','google_web_risk':'complete'}) | changes
    return reduce_day(events, rejections or [], **options)


def test_existing_policy_fixtures_are_unchanged_and_valid():
    root = Path(__file__).resolve().parents[2]
    source = root/'docs/SECUR4ALL-178-operations-contract'
    packet = json.loads((source/'fixtures.proposed.json').read_text())
    for row in packet['events']: assert validate(row['event']) == row['event']
    for row in packet['rejectionAggregates']: validate(row['aggregate'], rejection=True)
    for old, new in [('operations-projection.schema.proposed.json','event.schema.json'),
                     ('rejection-aggregate.schema.proposed.json','rejection.schema.json')]:
        assert (source/old).read_bytes() == (root/'src/shared_operations/contracts'/new).read_bytes()


@pytest.mark.parametrize('field,value', [('accountId','private'),('submittedText','private'),('reasonCode','private'),
    ('eventTimeEpoch',True),('eventTimeEpoch',1.0),('lifecycleRevision',True),('chargedChecks',False),
    ('logicalCheckRef','user@example.test'),('inputKind','unknown'),('eventId','op1_secret')])
def test_hostile_extra_fields_and_nonintegral_wire_values_are_rejected(field,value,capsys):
    data=accepted();data[field]=value
    with pytest.raises(OperationsError,match='^OPERATIONS_FACT_INVALID$'): validate(data)
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('outcome',['complete','partial','inconclusive','failed','blocked','invalid_input','unsupported','unavailable'])
def test_exact_replay_order_and_all_settled_outcomes(outcome):
    a,t=accepted(),terminal(outcome)
    value=report([t,a,t,a])
    assert value == report([a,t])
    row=value['services']['message_check']
    assert row['received']==1 and row[outcome]==1 and row['pending']==0
    assert row['deductedChecks']==int(outcome=='complete') and row['noDeductionChecks']==int(outcome!='complete')
    assert value['sourceComplete']


def test_pending_unknown_and_complimentary_are_not_zero_allowance_or_charged():
    pending=report([accepted()])['services']['message_check']
    assert pending['settlementUnknownChecks']==1 and pending['noDeductionChecks']==0
    a=accepted();a['accessClass']='complimentary'
    t=terminal(accessClass='complimentary',accountingState='not_charged',chargedChecks=0)
    row=report([a,t])['services']['message_check']
    assert row['complimentaryCompletedChecks']==1 and row['deductedChecks']==0 and row['noDeductionChecks']==1


def test_conflicting_identity_and_terminal_never_pick_last_writer():
    a,t=accepted(),terminal()
    contradiction=terminal('failed')
    one=report([a,t,contradiction]);two=report([contradiction,a,t])
    assert one==two and not one['sourceComplete']
    assert one['services']['message_check']['pending']==1
    assert one['reconciliationFaults']['conflictingEventIdentity']==1


def test_distinct_identity_cannot_double_count_same_lifecycle_phase():
    a=accepted();other=dict(a,eventId='op1_'+'f'*32)
    result=report([a,other])
    assert result['services']['message_check']['received']==0
    assert result['reconciliationFaults']['conflictingLifecycle']==1


def test_unknown_provider_cost_never_becomes_observed_zero_or_total_spend():
    a,s=accepted(),attempt()
    row=report([a,s])['providerAttempts']['openai']
    assert row['started']==row['unresolved']==row['costUnknownProviderAttempts']==row['requestUnitsUnknownProviderAttempts']==1
    assert row['costKnownProviderAttempts']==0 and row['knownEstimatedCostMicrousd']==0
    finish=attempt(finished=True,estimatedCostMicrousd=0,providerRequestUnits=0)
    row=report([finish,s,a])['providerAttempts']['openai']
    assert row['succeeded']==row['costKnownProviderAttempts']==1 and row['costUnknownProviderAttempts']==0


def test_attempt_finish_is_attributed_to_its_start_day_and_original_check_cohort():
    a=accepted();s=attempt(eventTimeEpoch=END-1);f=attempt(finished=True,eventTimeEpoch=END+5)
    t=terminal(eventTimeEpoch=END+10)
    value=report([f,t,s,a])
    assert value['services']['message_check']['complete']==1
    assert value['providerAttempts']['openai']['knownEstimatedCostMicrousd']==10
    next_day=report([f,t,s,a],day=date(2026,10,5),as_of=END+86400)
    assert next_day['services']['message_check']['received']==0
    assert next_day['providerAttempts']['openai']['started']==0


def test_finish_without_start_and_immutable_attempt_mismatch_disclose_incomplete():
    a,f=accepted(),attempt(finished=True)
    assert not report([a,f])['sourceComplete']
    s=attempt();f['attemptOrdinal']=2
    value=report([a,s,f]);row=value['providerAttempts']['openai']
    assert not value['sourceComplete'] and row['unresolved']==row['costUnknownProviderAttempts']==1


def test_embedded_google_attempt_does_not_create_an_extra_url_check():
    a=accepted();s=attempt(service='url_check',inputKind='message_url',component='url_assessment',providerFamily='google_web_risk')
    f=attempt(finished=True,service='url_check',inputKind='message_url',component='url_assessment',providerFamily='google_web_risk')
    value=report([a,s,f])
    assert value['sourceComplete'] and value['services']['message_check']['received']==1
    assert value['services']['url_check']['received']==0 and value['providerAttempts']['google_web_risk']['started']==1


@pytest.mark.parametrize('day,hours',[(date(2026,3,8),23),(date(2026,11,1),25),(DAY,24)])
def test_chicago_dst_half_open_day(day,hours):
    start,end=day_bounds(day);assert end-start==hours*3600


def test_rejection_snapshots_are_cumulative_and_outside_received_denominator():
    root=Path(__file__).resolve().parents[2]
    r=json.loads((root/'docs/SECUR4ALL-178-operations-contract/fixtures.proposed.json').read_text())['rejectionAggregates'][0]['aggregate']
    r=dict(r,windowStartEpoch=START,rejectedCount=2)
    latest=dict(r,rejectedCount=5)
    value=report([], [r,latest,r])
    assert value['preAdmissionRejections'][r['service']][r['reasonCode']]==5
    assert sum(s['received'] for s in value['services'].values())==0


def test_reports_have_no_check_attempt_or_customer_reference_and_empty_is_not_healthy():
    events=[accepted(),attempt(),attempt(finished=True),terminal()]
    value=report(events,source_complete=False)
    encoded=json.dumps(value)
    for event in events:
        assert event['eventId'] not in encoded and LC not in encoded
        if event['attemptRef']:assert event['attemptRef'] not in encoded
    assert not value['sourceComplete'] and value['localIntakeCoverage']=='unavailable'
    with pytest.raises(OperationsError,match='BATCH_BOUND'):report([accepted()]*10001)
    default=reduce_day([accepted()],[],day=DAY,as_of=END,environment='dev',source_complete=True)
    assert default['lifecycleSourceComplete'] and not default['sourceComplete']
    assert set(default['providerTelemetryCoverage'].values())=={'unavailable'}


@pytest.mark.parametrize('which',['early_start','late_finish','late_terminal'])
def test_impossible_lifecycle_times_cannot_certify_complete_source(which):
    a=accepted();s=attempt();f=attempt(finished=True);t=terminal()
    if which=='early_start':s['eventTimeEpoch']=a['eventTimeEpoch']-1
    elif which=='late_finish':f['eventTimeEpoch']=a['eventTimeEpoch']+7*86400
    else:t['eventTimeEpoch']=a['eventTimeEpoch']+7*86400
    value=report([a,s,f,t],as_of=END+8*86400)
    assert not value['sourceComplete'] and value['reconciliationFaults']
