"""Private admission contract. No verification signer or ownership-policy default."""
import base64
import hashlib
import json
import re
from decimal import Decimal
from uuid import UUID

ALGORITHM = 'RSASSA_PSS_SHA_256'
ROUTE = 'POST /support/account-deletion'
PURPOSE = 'verified-support-account-deletion-v1'
CONFIG_FIELDS = {
    'environment', 'accountId', 'region', 'functionArn', 'apiId', 'stage',
    'operatorRoleArn', 'operatorRoleId', 'kmsKeyArn', 'generation',
    'verificationPolicySha256', 'readinessSha256', 'maximumVerificationAgeSeconds',
    'cognitoPoolId', 'usersTable', 'usersTableId', 'ledgerTable', 'ledgerTableId',
    'inventoryManifestSha256', 'inventoryRevision', 'allowedSubjects',
}
RECORD_FIELDS = {
    'schemaVersion', 'purpose', 'environment', 'accountId', 'region', 'apiId', 'stage',
    'route', 'functionArn', 'operatorRoleArn', 'operatorRoleId', 'generation',
    'verificationPolicySha256', 'readinessSha256', 'cognitoPoolId', 'subject',
    'operationId', 'profileSha256', 'requestReceivedAtEpoch', 'ownershipVerifiedAtEpoch',
    'issuedAtEpoch', 'expiresAtEpoch',
}


class Unavailable(Exception):
    def __init__(self):
        super().__init__('SUPPORT_ADMISSION_UNAVAILABLE')


def need(value):
    if not value:
        raise Unavailable()


def integer(value):
    need(type(value) is int and 0 < value <= 9007199254740991)
    return value


def uuid(value, version=None):
    need(type(value) is str and str(UUID(value)) == value)
    need(version is None or UUID(value).version == version)
    return value


def sha(value):
    need(type(value) is str and re.fullmatch('[0-9a-f]{64}', value))
    return value


def unique(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result)
        result[key] = value
    return result


def parse(value):
    need(type(value) is str and len(value.encode('utf-8')) <= 8192)
    return json.loads(value, object_pairs_hook=unique,
                      parse_constant=lambda _: need(False))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def profile_hash(row):
    # DynamoDB integral Decimals have the same canonical representation as JSON integers.
    def normalized(value):
        if isinstance(value, Decimal):
            need(value.is_finite() and value == value.to_integral_value())
            return int(value)
        need(value is None or type(value) in (str, int, bool))
        return value
    need(type(row) is dict and 3 <= len(row) <= 32)
    return hashlib.sha256(canonical({k: normalized(v) for k, v in row.items()})).hexdigest()


def settings(environ):
    need(environ.get('SUPPORT_ACCOUNT_DELETION_ENABLED') == 'true')
    c = parse(environ.get('SUPPORT_ACCOUNT_DELETION_CONFIG_JSON'))
    need(type(c) is dict and set(c) == CONFIG_FIELDS and c['environment'] == 'dev')
    need(re.fullmatch('[0-9]{12}', c['accountId']) and c['region'] == 'us-east-1')
    prefix = 'arn:aws:'
    need(re.fullmatch(prefix + 'lambda:' + c['region'] + ':' + c['accountId'] +
                      ':function:trustcheckradar-dev-support-account-deletion(?::live)?', c['functionArn']))
    need(re.fullmatch('[a-z0-9]{10}', c['apiId']) and c['stage'] in ('dev', '$default'))
    need(re.fullmatch(prefix + 'iam::' + c['accountId'] + ':role/[A-Za-z0-9+=,.@_-]{1,64}', c['operatorRoleArn']))
    need(re.fullmatch('AROA[A-Z0-9]{17}', c['operatorRoleId']))
    need(re.fullmatch(prefix + 'kms:' + c['region'] + ':' + c['accountId'] + ':key/[0-9a-f-]{36}', c['kmsKeyArn']))
    uuid(c['kmsKeyArn'].split('/')[-1]); uuid(c['generation'])
    for key in ('verificationPolicySha256', 'readinessSha256', 'inventoryManifestSha256'):
        sha(c[key])
    need(integer(c['maximumVerificationAgeSeconds']) <= 300)
    integer(c['inventoryRevision'])
    need(re.fullmatch('us-east-1_[A-Za-z0-9]+', c['cognitoPoolId']))
    for key in ('usersTable', 'ledgerTable'):
        need(re.fullmatch('trustcheckradar-dev-[a-z0-9-]+', c[key]))
        uuid(c[key + 'Id'])
    need(c['usersTable'] != c['ledgerTable'])
    subjects = c['allowedSubjects']
    need(type(subjects) is list and 1 <= len(subjects) <= 10 and len(set(subjects)) == len(subjects))
    for subject in subjects:
        uuid(subject)
    return c


def envelope(event, context, c):
    need(type(event) is dict and event.get('version') == '2.0' and event.get('routeKey') == ROUTE)
    need(event.get('rawPath') == '/support/account-deletion' and event.get('rawQueryString') == '')
    need(event.get('isBase64Encoded') is False and not event.get('queryStringParameters'))
    rc = event.get('requestContext', {})
    need(rc.get('apiId') == c['apiId'] and rc.get('accountId') == c['accountId']
         and rc.get('stage') == c['stage'] and rc.get('routeKey') == ROUTE)
    need(rc.get('http', {}).get('method') == 'POST')
    need(context.invoked_function_arn == c['functionArn'])
    iam = rc.get('authorizer', {}).get('iam', {})
    arn = iam.get('userArn', '')
    role_name = c['operatorRoleArn'].split('/')[-1]
    match = re.fullmatch('arn:aws:sts::' + c['accountId'] + ':assumed-role/' +
                         re.escape(role_name) + '/([A-Za-z0-9+=,.@_-]{2,64})', arn)
    need(match and iam.get('accountId') == c['accountId'])
    need(iam.get('userId') == c['operatorRoleId'] + ':' + match[1])
    body = parse(event.get('body'))
    need(type(body) is dict and set(body) == {'record', 'signature'})
    need(event['body'] == canonical(body).decode('ascii'))
    record = body['record']
    need(type(record) is dict and set(record) == RECORD_FIELDS and type(record['schemaVersion']) is int
         and record['schemaVersion'] == 1 and record['purpose'] == PURPOSE and record['route'] == ROUTE)
    for key in ('environment', 'accountId', 'region', 'apiId', 'stage', 'functionArn',
                'operatorRoleArn', 'operatorRoleId', 'generation', 'verificationPolicySha256',
                'readinessSha256', 'cognitoPoolId'):
        need(record[key] == c[key])
    uuid(record['subject']); uuid(record['operationId'], 4); sha(record['profileSha256'])
    need(record['subject'] in c['allowedSubjects'])
    signature = base64.b64decode(body['signature'], validate=True)
    need(type(body['signature']) is str and base64.b64encode(signature).decode() == body['signature']
         and len(signature) == 384)  # Pinned RSA_3072 key; no algorithm negotiation.
    encoded = canonical(record)
    need(len(encoded) <= 4096)
    return record, signature, encoded


def clocks(record, config, now):
    integer(now)
    received, verified, issued, expires = [integer(record[k]) for k in
        ('requestReceivedAtEpoch', 'ownershipVerifiedAtEpoch', 'issuedAtEpoch', 'expiresAtEpoch')]
    need(received <= verified <= issued <= now < expires
         and now - verified <= config['maximumVerificationAgeSeconds']
         and expires - verified <= config['maximumVerificationAgeSeconds'])
