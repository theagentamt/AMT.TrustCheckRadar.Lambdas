from uuid import UUID
from decimal import Decimal

CURRENT_NOTICE = 'research-consent-2026-09-21-v2'
CURRENT_POLICY = 'independent-research-v1'


def _key(account, sort='CAMPAIGN_PARTICIPATION', prefix='USER#'):
    return {'PK': {'S': prefix + account}, 'SK': {'S': sort}}


def _valid(item):
    try:
        epoch = UUID(item.get('consentEpochId', ''))
    except (ValueError, TypeError, AttributeError):
        return False
    return (item.get('campaignConsentGranted') is True and item.get('noticeVersion') == CURRENT_NOTICE
            and item.get('environment') in {'dev', 'uat', 'prod'}
            and type(item.get('accountId')) is str and 0 < len(item['accountId']) <= 128
            and epoch.version == 4 and str(epoch) == item['consentEpochId'])


def authorized(item, users, ledger, client):
    if not users or not ledger:
        raise RuntimeError('Research authority storage is not configured')
    if not _valid(item):
        return False
    participation = client.get_item(TableName=users, Key=_key(item['accountId']), ConsistentRead=True).get('Item') or {}
    deletion = client.get_item(TableName=ledger, Key=_key(item['accountId'], 'ACCOUNT_DELETION', 'ACCOUNT#'), ConsistentRead=True).get('Item')
    return (not deletion and participation.get('state') == {'S': 'enrolled'}
            and participation.get('consentEpochId') == {'S': item['consentEpochId']}
            and participation.get('environment') == {'S': item['environment']}
            and participation.get('noticeVersion') == {'S': CURRENT_NOTICE}
            and participation.get('policyVersion') == {'S': CURRENT_POLICY})


def condition_checks(item, users, ledger):
    if not _valid(item) or not users or not ledger:
        raise RuntimeError('Research consent authority is invalid')
    return [{'ConditionCheck': {
        'TableName': users, 'Key': _key(item['accountId']),
        'ConditionExpression': '#state = :enrolled AND consentEpochId = :epoch AND #env = :env AND noticeVersion = :notice AND policyVersion = :policy',
        'ExpressionAttributeNames': {'#state': 'state', '#env': 'environment'},
        'ExpressionAttributeValues': {':enrolled': {'S': 'enrolled'}, ':epoch': {'S': item['consentEpochId']},
            ':env': {'S': item['environment']}, ':notice': {'S': CURRENT_NOTICE}, ':policy': {'S': CURRENT_POLICY}}}},
        {'ConditionCheck': {'TableName': ledger, 'Key': _key(item['accountId'], 'ACCOUNT_DELETION', 'ACCOUNT#'),
                            'ConditionExpression': 'attribute_not_exists(PK)'}}]


def cluster_authority(feature, *, users, ledger, outbox, client, now_epoch):
    """Fail closed after original 72h observation expiry; never copy identity into pipeline."""
    if not outbox or not users or not ledger:
        raise RuntimeError('Research cluster authority storage is not configured')
    if feature.get('researchNoticeVersion') != CURRENT_NOTICE or feature.get('researchPolicyVersion') != CURRENT_POLICY:
        return None
    key = {'PK': {'S': 'EVENT#' + feature['statisticsEventId']}, 'SK': {'S': 'OBSERVATION_READY'}}
    raw = client.get_item(TableName=outbox, Key=key, ConsistentRead=True).get('Item')
    if not raw:
        return None
    try:
        item = {k: raw[k]['S'] for k in ('PK', 'SK', 'accountId', 'consentEpochId', 'noticeVersion', 'environment', 'eventType', 'statisticsEventId')}
        item['campaignConsentGranted'] = raw['campaignConsentGranted']['BOOL']
        item['expiresAt'] = Decimal(raw['expiresAt']['N'])
    except (KeyError, TypeError, ValueError):
        return None
    expiry = item.get('expiresAt')
    if (item.get('PK') != key['PK']['S'] or item.get('SK') != 'OBSERVATION_READY'
            or item.get('statisticsEventId') != feature['statisticsEventId']
            or item.get('environment') != feature['environment']
            or item.get('eventType') != 'campaign.observation.ready'
            or isinstance(expiry, bool) or not isinstance(expiry, (int, Decimal))
            or int(expiry) != expiry or expiry <= now_epoch
            or not authorized(item, users, ledger, client)):
        return None
    # Guard the exact owner/epoch/purpose evidence and its original deadline at commit.
    names = {'#' + k: k for k in ('accountId', 'consentEpochId', 'noticeVersion', 'environment', 'eventType', 'statisticsEventId', 'campaignConsentGranted', 'expiresAt')}
    values = {':' + k: raw[k] for k in names.values()}
    values[':now'] = {'N': str(now_epoch)}
    outbox_check = {'ConditionCheck': {'TableName': outbox, 'Key': key,
        'ConditionExpression': ' AND '.join('#' + k + ' = :' + k for k in names.values()) + ' AND #expiresAt > :now',
        'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values}}
    return [*condition_checks(item, users, ledger), outbox_check]
