"""Approved local detailed selection; no provider, persistence or billing."""
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

ROOT=Path(__file__).parent
BASICS_DIR=ROOT.parents[1]/'recovery-selection'/'1.0.0'
spec=importlib.util.spec_from_file_location('approved_basic_selector',BASICS_DIR/'reference_selector.py')
basics=importlib.util.module_from_spec(spec);spec.loader.exec_module(basics)
VERSION='recovery-playbook-1.0'


def read(name):return json.loads((ROOT/name).read_text())


def plan(answers):
    basics.validate_answers(answers)
    bundle=read('bundle.json');selected=set(answers['exposures'])
    all_fallback=answers['unsure'] or not selected
    if all_fallback:selected=set(bundle['exposureOrder'])
    note=all_fallback or ('clicked_link' in selected and 'software_or_remote_access' not in selected)
    if 'clicked_link' in selected:selected.add('software_or_remote_access')
    actions=sorted((a for a in bundle['actions'] if a['exposureId'] in selected),
                   key=lambda a:(a['phase']!='now',a['rank']))
    category_order=list(dict.fromkeys(a['exposureId'] for a in actions))
    limits=sorted((v for v in bundle['limits'] if v['exposureId'] in selected),
                  key=lambda v:category_order.index(v['exposureId']))
    return {'nowActionIds':[a['id'] for a in actions if a['phase']=='now'],
            'followUpActionIds':[a['id'] for a in actions if a['phase']=='follow_up'],
            'limitIds':[v['id'] for v in limits],
            'conditionalNoteKey':'link_device_conditional' if note else None}


def select(answers,*,signed_in,today,bundle=None,fallback_content=None):
    basics.validate_answers(answers)
    if type(signed_in) is not bool or type(today) is not date:
        raise basics.ContractError('INVALID_LOCAL_CONTEXT')
    supplied=read('bundle.json') if bundle is None else bundle
    result={'bundleVersion':VERSION,'activeBundleVersion':None,'state':'limited_help','reason':None,
            'nowActionIds':[],'followUpActionIds':[],'limitIds':[],'conditionalNoteKey':None,
            'officialLinkIds':[],'reviewDue':False,'fallbackPlan':None,
            'accounting':'not_an_analysis_check','clarification':'unavailable'}
    if not signed_in:
        result.update(state='sign_in_required',reason='sign_in_required');return result
    if supplied!=read('bundle.json'):
        fallback=basics.select(answers,signed_in=signed_in,today=today,content=fallback_content)
        if fallback['state'] in ('available','review_due'):
            result.update(state='fallback_basics',reason='detailed_unavailable',fallbackPlan=fallback,
                          activeBundleVersion='recovery-basics-1.0',reviewDue=fallback['reviewDue'])
        else:result['reason']='content_unavailable'
        return result
    approved_on=date.fromisoformat(supplied['approvedOn'])
    due=today<approved_on or today>approved_on+timedelta(days=180)
    result.update(plan(answers),state='review_due' if due else 'available',activeBundleVersion=VERSION,
                  reviewDue=due,officialLinkIds=['recovery_guidance','identity_help','fraud_report'])
    return result
