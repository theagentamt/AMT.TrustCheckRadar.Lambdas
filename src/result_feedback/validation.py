"""Closed result-bound feedback protocol. Client IDs never establish ownership."""
import re
VERSION='1.0.0-feedback-candidate.1'
POLICY='private-result-feedback-2026-09-21-v1'
APPROVAL_SHA='9e3485588daeae698b139cb070b4de16bb9298348135271e7d9adafd9968bee6'
CATEGORIES=('looks_legitimate','looks_like_scam','unclear','unhelpful')
ASSESSMENTS=('1.0.0-candidate.1','1.0.0-message-candidate.1','1.0.0-message-candidate.2')
ELIGIBLE=('complete','partial','inconclusive')
IDENTITY=r'[A-Za-z0-9_-]{1,64}'
PROOF=r'v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}'
RECEIPT=r'(?:[0-9a-f]{32}|expired_[0-9a-f]{32})'
REASONS=('AUTHENTICATION_REQUIRED','ACCOUNT_UNAVAILABLE','ACTIVE_DEVICE_REQUIRED','INPUT_REJECTED','CONTRACT_UNSUPPORTED','RESULT_UNAVAILABLE','FEEDBACK_ID_CONFLICT','RATE_LIMITED','SERVICE_NOT_ENABLED','SERVICE_UNAVAILABLE','FEEDBACK_ALREADY_RECEIVED')

class FeedbackError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)

def require(ok,code='INPUT_REJECTED'):
    if not ok:raise FeedbackError(code)

def matches(pattern,value):return type(value) is str and re.fullmatch(pattern,value) is not None

def request(value):
    require(type(value) is dict and set(value)=={'feedbackTransportVersion','feedbackId','result','category'})
    require(value['feedbackTransportVersion']==VERSION,'CONTRACT_UNSUPPORTED')
    require(matches(IDENTITY,value['feedbackId']) and type(value['category']) is str and value['category'] in CATEGORIES)
    ref=value['result'];require(type(ref) is dict and set(ref)=={'checkId','operationProof','receiptId','assessmentTransportVersion'})
    require(matches(IDENTITY,ref['checkId']) and matches(PROOF,ref['operationProof']) and matches(RECEIPT,ref['receiptId']))
    require(type(ref['assessmentTransportVersion']) is str and ref['assessmentTransportVersion'] in ASSESSMENTS,'CONTRACT_UNSUPPORTED')
    return value

def response(status,*,feedback_id=None,check_id=None,reason=None,retryable=False,retry_after=None,received=None,expires=None):
    value={'feedbackTransportVersion':VERSION,'feedbackId':feedback_id,'checkId':check_id,'status':status,
        'reasonCode':reason,'retryable':retryable,'retryAfterSeconds':retry_after,'receivedAt':received,'expiresAt':expires}
    require(status in ('accepted','already_received','rejected','unknown'),'SERVICE_UNAVAILABLE')
    require(feedback_id is None or matches(IDENTITY,feedback_id),'SERVICE_UNAVAILABLE')
    require(check_id is None or matches(IDENTITY,check_id),'SERVICE_UNAVAILABLE')
    require(reason is None or reason in REASONS,'SERVICE_UNAVAILABLE')
    if status in ('accepted','already_received'):
        require(feedback_id is not None and check_id is not None and type(received) is int and type(expires) is int and 0<=received<expires
            and not retryable and retry_after is None and reason==(None if status=='accepted' else 'FEEDBACK_ALREADY_RECEIVED'),'SERVICE_UNAVAILABLE')
    else:require(received is expires is None and reason is not None,'SERVICE_UNAVAILABLE')
    return value
