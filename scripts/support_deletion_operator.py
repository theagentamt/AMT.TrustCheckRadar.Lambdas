"""Private support admission and separately authorized read-only status.

No signer, synthetic confirmation, mailbox client, password reset or direct eraser.
Default check is offline. An ambiguous POST is never automatically repeated.
"""
import argparse
import base64
import hashlib
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import support_deletion_verifier as V
C = V.C
from support_account_deletion.runtime import Table
from account_data_api.service import validate_command, _valid_component_receipt
from shared_account_finalization.service import Finalizer, COMMAND_FIELDS, REQUIRED_COMPONENTS, validate_inventory

INTENT_FIELDS = {'schemaVersion', 'caseReference', 'subject', 'operationId',
    'configurationSha256', 'capabilitySha256', 'issuedAtEpoch', 'expiresAtEpoch',
    'attemptedAtEpoch', 'stage'}


def capability(value, config):
    """Syntax/binding only. Only the server verifies the KMS signature."""
    C.need(type(value) is dict and set(value) == {'record', 'signature'})
    r = value['record']
    C.need(type(r) is dict and set(r) == C.RECORD_FIELDS
           and type(r['schemaVersion']) is int and r['schemaVersion'] == 1
           and r['purpose'] == C.PURPOSE and r['route'] == C.ROUTE)
    for field in ('environment', 'accountId', 'region', 'apiId', 'stage', 'functionArn',
                  'operatorRoleArn', 'operatorRoleId', 'generation', 'verificationPolicySha256',
                  'readinessSha256', 'cognitoPoolId'):
        C.need(r[field] == config[field])
    C.uuid(r['subject']); C.uuid(r['operationId'], 4); C.sha(r['profileSha256'])
    C.need(r['subject'] in config['allowedSubjects'])
    signature = base64.b64decode(value['signature'], validate=True)
    C.need(type(value['signature']) is str and len(signature) == 384
           and base64.b64encode(signature).decode() == value['signature'])
    C.need(len(C.canonical(r)) <= 4096)
    return r


def operator_identity(sts, config):
    who = sts.get_caller_identity()
    role = config['operatorRoleArn'].split('/')[-1]
    match = re.fullmatch('arn:aws:sts::' + config['accountId'] + ':assumed-role/' +
                        re.escape(role) + '/([A-Za-z0-9+=,.@_-]{2,64})', who.get('Arn', ''))
    C.need(match and who.get('Account') == config['accountId']
           and who.get('UserId') == config['operatorRoleId'] + ':' + match[1])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise C.Unavailable()


def http_post(session, config, body):
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    stage = '' if config['stage'] == '$default' else '/' + config['stage']
    url = 'https://' + config['apiId'] + '.execute-api.' + config['region'] + '.amazonaws.com' + stage + '/support/account-deletion'
    encoded = C.canonical(body)
    request = AWSRequest(method='POST', url=url, data=encoded, headers={'content-type': 'application/json'})
    SigV4Auth(session.get_credentials().get_frozen_credentials(), 'execute-api', config['region']).add_auth(request)
    try:
        response = build_opener(NoRedirect()).open(
            Request(url, data=encoded, headers=dict(request.headers), method='POST'), timeout=15)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(4097)
        C.need(len(raw) <= 4096)
        return response.code, C.parse(raw.decode('ascii'))


def submit(config, body, case_reference, directory, session, sts, *, now=lambda: int(time.time()), http=http_post):
    C.uuid(case_reference); directory = Path(directory)
    C.need(directory.name == case_reference)
    record = capability(body, config)
    C.clocks(record, config, now())
    operator_identity(sts, config)
    C.clocks(record, config, now())
    op = record['operationId']
    intent = {'schemaVersion': 1, 'caseReference': case_reference, 'subject': record['subject'],
        'operationId': op, 'configurationSha256': V.digest(config), 'capabilitySha256': V.digest(body),
        'issuedAtEpoch': record['issuedAtEpoch'], 'expiresAtEpoch': record['expiresAtEpoch'],
        'attemptedAtEpoch': now(), 'stage': 'POST_ATTEMPTED'}
    V.exclusive(directory / (op + '.admission-intent.json'), intent)
    # Even a local failure after this point requires read-only reconciliation.
    C.clocks(record, config, now())
    status, response = http(session, config, body)
    C.need(type(response) is dict and type(response.get('schemaVersion')) is int
           and response.get('completionVerified') is False
           and status == 202 and response == {'schemaVersion': 1, 'operationId': op,
        'admission': 'ACCEPTED', 'completionVerified': False})
    V.exclusive(directory / (op + '.admission-accepted.json'),
        {'schemaVersion': 1, 'caseReference': case_reference, 'operationId': op,
         'configurationSha256': V.digest(config), 'admission': 'ACCEPTED', 'completionVerified': False})
    return {'schemaVersion': 1, 'admission': 'ACCEPTED', 'completionVerified': False,
            'repeatAdmission': False}


def intent(value, config):
    C.need(type(value) is dict and set(value) == INTENT_FIELDS
           and type(value['schemaVersion']) is int and value['schemaVersion'] == 1
           and value['stage'] == 'POST_ATTEMPTED')
    C.uuid(value['caseReference']); C.uuid(value['subject']); C.uuid(value['operationId'], 4)
    C.sha(value['capabilitySha256'])
    C.need(value['configurationSha256'] == V.digest(config) and value['subject'] in config['allowedSubjects'])
    issued, attempted, expires = [C.integer(value[k]) for k in ('issuedAtEpoch', 'attemptedAtEpoch', 'expiresAtEpoch')]
    C.need(issued <= attempted < expires and expires - issued <= config['maximumVerificationAgeSeconds'])
    return value


def status(config, policy, attempt, clients, *, now=lambda: int(time.time())):
    attempt = intent(attempt, config)
    # Status uses the separately restricted verifier role, never operator DDB access.
    verifier = V.Verifier(config, policy, clients, now=now)
    verifier.identity()
    ddb = clients['dynamodb']
    for field in ('usersTable', 'ledgerTable'):
        table = ddb.describe_table(TableName=config[field])['Table']
        C.need(table.get('TableId') == config[field + 'Id'] and table.get('TableStatus') == 'ACTIVE'
               and table.get('TableArn') == 'arn:aws:dynamodb:' + config['region'] + ':' +
               config['accountId'] + ':table/' + config[field])
    ledger = Table(ddb, config['ledgerTable'])
    def read(sk):
        return ledger.get_item(Key={'PK': 'ACCOUNT#' + attempt['subject'], 'SK': sk}, ConsistentRead=True).get('Item')
    inventory = ledger.get_item(Key={'PK': 'INVENTORY#dev', 'SK': 'ACCOUNT_DATA_INVENTORY'}, ConsistentRead=True).get('Item')
    observed = verifier.clock()
    validate_inventory(inventory, 'dev', config['inventoryManifestSha256'], REQUIRED_COMPONENTS,
                       expected_revision=config['inventoryRevision'], now_epoch=observed)
    command = read('ACCOUNT_DELETION')
    base_result = {'schemaVersion': 1, 'completionVerified': False, 'repeatAdmission': False,
                   'observedAtEpoch': observed, 'validComponentReceipts': 0}
    if command is None:
        return base_result | {'status': 'ADMISSION_UNCONFIRMED'}
    C.need(command.get('accountId') == attempt['subject'] and command.get('operationId') == attempt['operationId'])
    original = {k: value for k, value in command.items() if k in COMMAND_FIELDS}
    original.update(eventType='account.deletion.requested', status='REQUESTED')
    validate_command(original, 'dev')
    C.need(attempt['issuedAtEpoch'] <= original['occurredAtEpoch'] < attempt['expiresAtEpoch']
           and original['occurredAtEpoch'] <= observed)
    if command.get('status') == 'REQUESTED':
        validate_command(command, 'dev')
    else:
        C.need(Finalizer._completed(command, original, observed))
    valid = 0
    receipts = []
    for component in REQUIRED_COMPONENTS:
        receipt = read('ACCOUNT_DELETION#' + component)
        receipts.append((component, receipt))
        valid += bool(_valid_component_receipt(receipt, original, component)
                      and original['occurredAtEpoch'] <= receipt['occurredAtEpoch'] <= observed < receipt['retainUntilEpoch'])
    result = base_result | {'status': 'INCOMPLETE', 'validComponentReceipts': valid,
                           'originalOperationMatched': True,
                           'deadlineOverdue': observed > original['deleteByEpoch']}
    if command.get('status') != 'COMPLETE' or valid != len(REQUIRED_COMPONENTS):
        return result
    profile = Table(ddb, config['usersTable']).get_item(
        Key={'PK': 'USER#' + attempt['subject'], 'SK': 'PROFILE'}, ConsistentRead=True).get('Item')
    C.need(profile is None)
    cognito = clients['cognito-idp']
    try:
        cognito.admin_get_user(UserPoolId=config['cognitoPoolId'], Username=attempt['subject'])
    except cognito.exceptions.UserNotFoundException:
        pass
    else:
        raise C.Unavailable()
    C.need(read('ACCOUNT_DELETION') == command)
    C.need(ledger.get_item(Key={'PK': 'INVENTORY#dev', 'SK': 'ACCOUNT_DATA_INVENTORY'},
                          ConsistentRead=True).get('Item') == inventory)
    fresh = verifier.clock()
    for component, receipt in receipts:
        C.need(_valid_component_receipt(receipt, original, component)
               and original['occurredAtEpoch'] <= receipt['occurredAtEpoch'] <= fresh < receipt['retainUntilEpoch'])
    C.need(Finalizer._completed(command, original, fresh))
    verifier.identity()
    # The twelve validated receipts cover their owned components. No independent
    # device-table read, backup wipe, or fresh receipt is implied by this observer.
    return result | {'status': 'COMPLETE', 'completionVerified': True,
                     'profileAbsent': True, 'cognitoAbsent': True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('check', 'submit', 'status'), default='check')
    for field in ('configuration', 'capability', 'case-reference', 'case-directory', 'policy', 'intent'):
        p.add_argument('--' + field)
    p.add_argument('--poll-seconds', type=int, default=0)
    a = p.parse_args()
    config = C.settings({'SUPPORT_ACCOUNT_DELETION_ENABLED': 'true',
        'SUPPORT_ACCOUNT_DELETION_CONFIG_JSON': C.canonical(V.private_read(a.configuration)).decode()})
    C.need(type(a.poll_seconds) is int and 0 <= a.poll_seconds <= 1800)
    if a.mode in ('check', 'submit'):
        body = V.private_read(a.capability); record = capability(body, config); C.clocks(record, config, int(time.time()))
        if a.mode == 'check':
            print('{"schemaVersion":1,"offlineSyntaxValid":true,"signatureVerified":false,"awsCalls":0}')
            return
    import boto3
    from botocore.config import Config
    sdk = Config(retries={'total_max_attempts': 1}, connect_timeout=2, read_timeout=5)
    session = boto3.Session(region_name=config['region'])
    if a.mode == 'submit':
        C.need(a.poll_seconds == 0)
        result = submit(config, body, a.case_reference, a.case_directory, session, session.client('sts', config=sdk))
        print(C.canonical(result).decode()); return
    policy = V.private_read(a.policy); attempt = V.private_read(a.intent)
    clients = {n: session.client(n, config=sdk) for n in ('sts', 'dynamodb', 'cognito-idp')}
    try:
        deadline = time.monotonic() + a.poll_seconds
        while True:
            result = status(config, policy, attempt, clients)
            print(C.canonical(result).decode(), flush=True)
            if result['completionVerified'] or time.monotonic() >= deadline:
                return
            if deadline - time.monotonic() < 60:
                return
            time.sleep(60)
    finally:
        for client in clients.values(): client.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"schemaVersion":1,"status":"UNCONFIRMED","repeatAdmission":false}')
        raise SystemExit(2) from None
