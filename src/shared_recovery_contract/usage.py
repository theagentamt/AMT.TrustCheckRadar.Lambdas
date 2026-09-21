"""Usage-only receipt projection: no descriptions, category IDs or action IDs."""
import re
from .constants import POLICY,PLAYBOOK,OUTCOMES,require


def usage(check,state):
    return validate_usage({'schemaVersion':1,'kind':'recovery_usage','checkId':check,
        'policyVersion':POLICY,'playbookVersion':PLAYBOOK,'processingOutcome':state,
        'contentDisposition':'not_retained'})


def validate_usage(value,check=None,state=None):
    require(type(value) is dict and set(value)=={'schemaVersion','kind','checkId','policyVersion','playbookVersion','processingOutcome','contentDisposition'},'RESULT_SUMMARY_INVALID')
    require(type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['kind']=='recovery_usage'
        and value['policyVersion']==POLICY and value['playbookVersion']==PLAYBOOK
        and value['processingOutcome'] in OUTCOMES and value['contentDisposition']=='not_retained'
        and type(value['checkId']) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value['checkId'])
        and (check is None or value['checkId']==check) and (state is None or value['processingOutcome']==state),'RESULT_SUMMARY_INVALID')
    return value
