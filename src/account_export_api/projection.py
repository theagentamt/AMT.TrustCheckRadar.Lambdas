"""Public field allowlists. Internal keys and capabilities never leave the service."""
from decimal import Decimal
from types import SimpleNamespace
from .cursor import require

FIELDS = {
    'analysis_requests': ('status','createdAt','updatedAt','expiresAt'),
    'scan_consumption': ('consumptionType','createdAt','expiresAt'),
    'research_observations': ('observedAtEpoch','sourceType','riskLevel','noticeVersion','signalIds','expiresAt'),
    'research_contributions': ('languageId','taxonomyBucket','signalIds','indicatorIds','confidence','submissionCount','expiresAt'),
    'profile': ('email','given_name','family_name','phone_number','over_18','status','ageVerified','ageVerifiedAt','agePolicyVersion','createdAt','updatedAt'),
    'devices': ('platform','osVersion','status','firstSeenAt','lastSeenAt','deactivatedAt'),
    'recovery': ('operation','result','status','completedAtEpoch'),
    'subscriptions': ('entitlementTier','subscriptionStatus','platform','productId','billingPeriodStartUtc','billingPeriodEndUtc','lastVerifiedAtUtc','isAccessGranted','monthlyScanLimit','remainingMonthlyScans','remainingCredits'),
    'usage': ('periodKey','usedCount'),
    'access': ('state','basis','validFromEpoch','validUntilEpoch','activationKind','activatedAtEpoch','policyVersion'),
    'allowance': ('startEpoch','endEpoch','limit','usedChecks','reservedChecks','policyVersion'),
    'trial': ('activatedAtEpoch','policyVersion'),
    'recognition': ('qualifyingChecks','awardedBadgeIds'),
    'participation': ('state','noticeVersion','policyVersion','effectiveFrom','effectiveUntil','withdrawalRequestedAt','deletionDeadlineAt'),
    'consent': ('eventType','occurredAt','noticeVersion','policyVersion','resultingState','effectiveMonthlyScanLimit'),
}
INTEGER_FIELDS = {'completedAtEpoch','monthlyScanLimit','remainingMonthlyScans','remainingCredits','usedCount','validFromEpoch','validUntilEpoch','activatedAtEpoch','startEpoch','endEpoch','limit','usedChecks','reservedChecks','qualifyingChecks','effectiveMonthlyScanLimit','observedAtEpoch','expiresAt','submissionCount'}
BOOLEAN_FIELDS = {'ageVerified','isAccessGranted'}


def plain(value):
    if isinstance(value, Decimal):
        require(value.is_finite(), 'SOURCE_UNAVAILABLE', 503)
        return int(value) if value == value.to_integral_value() else float(value)
    if type(value) is dict:
        return {k: plain(v) for k,v in value.items()}
    if type(value) is list:
        return [plain(v) for v in value]
    return value


def integer(value, minimum=0):
    value = plain(value)
    require(type(value) is int and value >= minimum, 'SOURCE_UNAVAILABLE', 503)
    return value


def text(value):
    require(type(value) is str and len(value.encode('utf-8')) <= 4096,
            'SOURCE_UNAVAILABLE', 503)
    return value


def pick(row, family):
    data = {}
    for key in FIELDS[family]:
        if key not in row:
            continue
        value = row[key]
        if value is None:
            data[key] = None
        elif key in INTEGER_FIELDS:
            data[key] = integer(value)
        elif key in BOOLEAN_FIELDS:
            require(type(value) is bool, 'SOURCE_UNAVAILABLE', 503)
            data[key] = value
        elif key in ('awardedBadgeIds','signalIds','indicatorIds'):
            require(type(value) is list and len(value) <= 100 and len(set(value)) == len(value), 'SOURCE_UNAVAILABLE', 503)
            data[key] = [text(v) for v in value]
        elif key == 'confidence':
            value = plain(value)
            require(type(value) in (int,float) and 0 <= value <= 1, 'SOURCE_UNAVAILABLE',503)
            data[key] = value
        else:
            data[key] = text(value)
    require(bool(data), 'SOURCE_UNAVAILABLE', 503)
    return data


def receipt(row, now):
    require(row.get('recordType') == 'V1_CHECK_RECEIPT', 'SOURCE_UNAVAILABLE', 503)
    expiry = integer(row.get('retentionDeadlineEpoch'), 1)
    if expiry <= now or row.get('state') != 'SETTLED':
        return None
    assessed = integer(row.get('assessmentEpoch'))
    require(assessed <= now and assessed < expiry, 'SOURCE_UNAVAILABLE', 503)
    outcome = row.get('processingOutcome')
    require(outcome in ('complete','partial','inconclusive','failed','blocked','invalid_input','unsupported','unavailable'), 'SOURCE_UNAVAILABLE', 503)
    charged = integer(row.get('chargedChecks'))
    require(charged in (0,1) and (charged == 0 or outcome == 'complete'), 'SOURCE_UNAVAILABLE', 503)
    summary = plain(row.get('resultSummary'))
    require(summary is not None or outcome not in ('complete','partial','inconclusive'), 'SOURCE_UNAVAILABLE',503)
    if summary is not None:
        if row.get('recoveryTransportVersion'):
            from shared_recovery_contract.usage import validate_usage
            validate_usage(summary, row.get('clientCheckId'), outcome)
        elif row.get('projectionScope') == 'sanitized_message':
            from shared_message_contract import VERSION as V1, validate_summary as v1
            from shared_message_contract.validation_v2 import VERSION as V2, validate_summary as v2
            version = row.get('messageTransportVersion', V1)
            require(version in (V1,V2), 'SOURCE_UNAVAILABLE', 503)
            (v2 if version == V2 else v1)(summary, row.get('clientCheckId'), outcome)
        else:
            from shared_check_authority.summary import validate_summary
            require(row.get('projectionScope') in ('full_url','origin_only'), 'SOURCE_UNAVAILABLE', 503)
            validate_summary(summary, row.get('clientCheckId'), outcome)
        summary = {k:v for k,v in summary.items() if k != 'checkId'}
    result = {'state':'SETTLED','processingOutcome':outcome,'chargedChecks':charged,
              'assessmentEpoch':assessed,'expiresAt':expiry,'resultSummary':summary}
    feedback = row.get('feedback')
    if feedback is not None:
        require(summary is not None and outcome in ('complete','partial','inconclusive'), 'SOURCE_UNAVAILABLE',503)
        require(type(feedback) is dict and set(feedback) == {'feedbackId','category','receivedAt','policyVersion'}
                and feedback.get('category') in ('looks_legitimate','looks_like_scam','unclear','unhelpful')
                and feedback.get('policyVersion') == 'private-result-feedback-2026-09-21-v1', 'SOURCE_UNAVAILABLE', 503)
        received = integer(feedback.get('receivedAt'))
        require(assessed <= received <= now and received < expiry, 'SOURCE_UNAVAILABLE', 503)
        result['feedback'] = {'category':feedback['category'],'receivedAt':received}
    return result


def project(row, family, now):
    require(type(row) is dict, 'SOURCE_UNAVAILABLE', 503)
    if 'expiresAt' in row and integer(row['expiresAt']) <= now:
        return None
    if family == 'receipts':
        return receipt(row, now)
    if family == 'history':
        from shared_history.contracts import public_history_item
        return plain(public_history_item(row, SimpleNamespace(max_summary_bytes=4096, max_list_items=20, max_text_field_bytes=1024)))
    if family == 'access':
        result = pick(row, family)
        sources = row.get('sources')
        require(type(sources) is dict and set(sources) <= {'trial','paid','complimentary'}, 'SOURCE_UNAVAILABLE', 503)
        result['sources'] = {name:pick(source, 'access') for name,source in sources.items()}
        return result
    return pick(row, family)
