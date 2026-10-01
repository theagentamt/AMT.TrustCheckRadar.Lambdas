"""Supervised human email attestation. No mailbox, admission or erasure client.

The signer is a trusted human authority, not an automated email-origin detector.
All policy/configuration values are explicit reviewed inputs; offline check is default.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from support_account_deletion import contract as C
from support_account_deletion.runtime import PROFILE_FIELDS, Table
from shared_account_finalization.service import REQUIRED_COMPONENTS, validate_inventory

POLICY_FIELDS = {'schemaVersion', 'method', 'signerRoleArn', 'signerRoleId',
    'custodyReference', 'auditLocation', 'retentionAfterResolutionDays', 'roleSeparation',
    'challengeValiditySeconds', 'maximumReplyAgeSeconds', 'capabilityValiditySeconds'}
CHALLENGE_FIELDS = {'schemaVersion', 'caseReference', 'subject', 'operationId',
    'configurationSha256', 'policySha256', 'profileSha256', 'email', 'challenge',
    'requestReceivedAtEpoch', 'preparedAtEpoch', 'expiresAtEpoch'}
ATTESTATION_FIELDS = {'schemaVersion', 'caseReference', 'challengeSha256',
    'returnedChallenge', 'replyReceivedAtEpoch', 'ownershipVerifiedAtEpoch',
    'reviewedActualSharedInboxReply', 'explicitDeletionConfirmed'}


def digest(value):
    return hashlib.sha256(C.canonical(value)).hexdigest()


def policy(config, value):
    C.need(type(value) is dict and set(value) == POLICY_FIELDS)
    C.need(type(value['schemaVersion']) is int and value['schemaVersion'] == 1)
    C.need(value['method'] == 'trusted-human-shared-inbox-challenge-reply-v1')
    C.need(value['auditLocation'] == 'same-support-case'
           and type(value['retentionAfterResolutionDays']) is int
           and value['retentionAfterResolutionDays'] == 30
           and value['roleSeparation'] == 'same-owner-separate-restricted-roles')
    C.uuid(value['custodyReference'])
    C.need(re.fullmatch('arn:aws:iam::' + config['accountId'] +
        ':role/[A-Za-z0-9+=,.@_-]{1,64}', value['signerRoleArn']))
    C.need(re.fullmatch('AROA[A-Z0-9]{17}', value['signerRoleId']))
    C.need(value['signerRoleArn'] != config['operatorRoleArn']
           and value['signerRoleId'] != config['operatorRoleId'])
    C.need(C.integer(value['challengeValiditySeconds']) <= 86400)
    C.need(C.integer(value['maximumReplyAgeSeconds']) <= value['challengeValiditySeconds'])
    C.need(C.integer(value['capabilityValiditySeconds']) <= config['maximumVerificationAgeSeconds'])
    C.need(digest(value) == config['verificationPolicySha256'])
    return value


def private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        C.need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
               and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
               and info.st_size <= 8192)
        raw = os.read(fd, 8193)
        value = C.parse(raw.decode('ascii'))
        C.need(raw == C.canonical(value))
        return value
    finally:
        os.close(fd)


def exclusive(path, value):
    # Caller supplies an owner-only case directory. Failed writes remain as an
    # attempt marker; never delete it automatically and thereby permit a retry.
    parent = Path(path).parent
    info = parent.stat()
    C.need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
           and stat.S_IMODE(info.st_mode) == 0o700 and not parent.is_symlink())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(C.canonical(value)); stream.flush(); os.fsync(stream.fileno())
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        # Intent may have committed. Preserve the file, including on fsync failure.
        raise


class Verifier:
    def __init__(self, config, reviewed_policy, clients, now=lambda: int(time.time())):
        self.c = C.settings({'SUPPORT_ACCOUNT_DELETION_ENABLED': 'true',
            'SUPPORT_ACCOUNT_DELETION_CONFIG_JSON': C.canonical(config).decode()})
        self.p = policy(self.c, reviewed_policy)
        self.clients, self.now, self.previous = clients, now, 0

    def clock(self):
        value = C.integer(self.now())
        C.need(value >= self.previous)
        self.previous = value
        return value

    def identity(self):
        self.clock()
        identity = self.clients['sts'].get_caller_identity()
        role = self.p['signerRoleArn'].split('/')[-1]
        match = re.fullmatch('arn:aws:sts::' + self.c['accountId'] + ':assumed-role/' +
            re.escape(role) + '/([A-Za-z0-9+=,.@_-]{2,64})', identity.get('Arn', ''))
        C.need(match and identity.get('Account') == self.c['accountId']
               and identity.get('UserId') == self.p['signerRoleId'] + ':' + match[1])
        self.clock()

    def snapshot(self, subject):
        C.uuid(subject); C.need(subject in self.c['allowedSubjects'])
        self.identity()
        ddb = self.clients['dynamodb']
        for field in ('usersTable', 'ledgerTable'):
            self.clock()
            table = ddb.describe_table(TableName=self.c[field])['Table']
            C.need(table.get('TableId') == self.c[field + 'Id'] and table.get('TableStatus') == 'ACTIVE'
                and table.get('TableArn') == 'arn:aws:dynamodb:' + self.c['region'] + ':' +
                self.c['accountId'] + ':table/' + self.c[field])
        def read(table, pk, sk):
            self.clock()
            return Table(ddb, self.c[table]).get_item(Key={'PK': pk, 'SK': sk},
                ConsistentRead=True).get('Item')
        inventory = read('ledgerTable', 'INVENTORY#dev', 'ACCOUNT_DATA_INVENTORY')
        validate_inventory(inventory, 'dev', self.c['inventoryManifestSha256'], REQUIRED_COMPONENTS,
            expected_revision=self.c['inventoryRevision'], now_epoch=self.clock())
        C.need(read('ledgerTable', 'ACCOUNT#' + subject, 'ACCOUNT_DELETION') is None)
        profile = read('usersTable', 'USER#' + subject, 'PROFILE')
        C.need(type(profile) is dict and set(profile) <= PROFILE_FIELDS
               and profile.get('sub') == subject and profile.get('status') in ('ACTIVE', 'PENDING_AGE_GATE'))
        C.need(profile.get('PK') == 'USER#' + subject and profile.get('SK') == 'PROFILE')
        self.clock()
        user = self.clients['cognito-idp'].admin_get_user(UserPoolId=self.c['cognitoPoolId'], Username=subject)
        attrs = user.get('UserAttributes', [])
        C.need(type(attrs) is list and all(type(a) is dict and set(a) == {'Name', 'Value'} for a in attrs))
        values = {a['Name']: a['Value'] for a in attrs}
        C.need(len(values) == len(attrs) and user.get('Username') == subject
               and values.get('sub') == subject and values.get('email_verified') == 'true')
        email = values.get('email')
        C.need(type(email) is str and re.fullmatch(r'[^\s@<>]+@[^\s@<>]+', email) and len(email) <= 254)
        for field in ('email', 'given_name', 'family_name', 'phone_number'):
            C.need(values.get(field) == profile.get(field))
        self.clock()
        return C.profile_hash(profile), email

    def prepare(self, subject, case_reference, received, output):
        C.uuid(case_reference); C.integer(received)
        C.need(Path(output).name == 'challenge.json' and Path(output).parent.name == case_reference)
        C.need(received <= self.clock())
        profile, email = self.snapshot(subject)
        prepared = self.clock()
        challenge = dict(schemaVersion=1, caseReference=case_reference, subject=subject,
            operationId=str(uuid4()), configurationSha256=digest(self.c), policySha256=digest(self.p),
            profileSha256=profile, email=email, challenge=secrets.token_urlsafe(32),
            requestReceivedAtEpoch=received, preparedAtEpoch=prepared,
            expiresAtEpoch=prepared + self.p['challengeValiditySeconds'])
        exclusive(output, challenge)
        return {'schemaVersion': 1, 'stage': 'CHALLENGE_PREPARED', 'emailSent': False}

    def validate_attestation(self, challenge, attestation):
        C.need(type(challenge) is dict and set(challenge) == CHALLENGE_FIELDS
               and type(attestation) is dict and set(attestation) == ATTESTATION_FIELDS)
        for value in (challenge, attestation):
            C.need(type(value['schemaVersion']) is int and value['schemaVersion'] == 1)
        for field in ('caseReference', 'subject', 'operationId'):
            C.uuid(challenge[field], 4 if field == 'operationId' else None)
        C.need(challenge['subject'] in self.c['allowedSubjects'])
        for field in ('configurationSha256', 'policySha256', 'profileSha256'):
            C.sha(challenge[field])
        C.need(challenge['configurationSha256'] == digest(self.c) and challenge['policySha256'] == digest(self.p))
        C.need(type(challenge['challenge']) is str and re.fullmatch('[A-Za-z0-9_-]{43}', challenge['challenge']))
        C.need(attestation['caseReference'] == challenge['caseReference']
               and attestation['challengeSha256'] == digest(challenge)
               and attestation['returnedChallenge'] == challenge['challenge']
               and attestation['reviewedActualSharedInboxReply'] is True
               and attestation['explicitDeletionConfirmed'] is True)
        received, prepared, expires = [C.integer(challenge[k]) for k in
            ('requestReceivedAtEpoch', 'preparedAtEpoch', 'expiresAtEpoch')]
        reply, verified = [C.integer(attestation[k]) for k in ('replyReceivedAtEpoch', 'ownershipVerifiedAtEpoch')]
        now = self.clock()
        C.need(received <= prepared <= reply <= verified <= now < expires
               and expires - prepared == self.p['challengeValiditySeconds']
               and verified - reply <= self.p['maximumReplyAgeSeconds']
               and now - verified < self.p['capabilityValiditySeconds'])
        return now

    def sign(self, challenge, attestation, intent_path, output_path):
        self.validate_attestation(challenge, attestation)
        # All files for this operation are in one governed owner-only case directory.
        C.need(Path(intent_path).parent.resolve() == Path(output_path).parent.resolve())
        C.need(Path(intent_path).parent.name == challenge['caseReference']
               and Path(intent_path).name == challenge['operationId'] + '.sign-intent.json'
               and Path(output_path).name == challenge['operationId'] + '.capability.json')
        C.need(not Path(output_path).exists() and not Path(output_path).is_symlink())
        profile, email = self.snapshot(challenge['subject'])
        C.need(profile == challenge['profileSha256'] and email == challenge['email'])
        self.clock()
        key = self.clients['kms'].describe_key(KeyId=self.c['kmsKeyArn'])['KeyMetadata']
        C.need(key.get('Arn') == self.c['kmsKeyArn'] and key.get('KeySpec') == 'RSA_3072'
               and key.get('KeyUsage') == 'SIGN_VERIFY' and key.get('KeyState') == 'Enabled'
               and key.get('Origin') == 'AWS_KMS' and key.get('KeyManager') == 'CUSTOMER'
               and key.get('MultiRegion') is False and C.ALGORITHM in key.get('SigningAlgorithms', []))
        issued = self.validate_attestation(challenge, attestation)
        record = {k: self.c[k] for k in ('environment', 'accountId', 'region', 'apiId', 'stage',
            'functionArn', 'operatorRoleArn', 'operatorRoleId', 'generation',
            'verificationPolicySha256', 'readinessSha256', 'cognitoPoolId')}
        record.update(schemaVersion=1, purpose=C.PURPOSE, route=C.ROUTE,
            subject=challenge['subject'], operationId=challenge['operationId'],
            profileSha256=profile, requestReceivedAtEpoch=challenge['requestReceivedAtEpoch'],
            ownershipVerifiedAtEpoch=attestation['ownershipVerifiedAtEpoch'], issuedAtEpoch=issued,
            expiresAtEpoch=min(challenge['expiresAtEpoch'], attestation['ownershipVerifiedAtEpoch'] +
                self.p['capabilityValiditySeconds']))
        C.clocks(record, self.c, issued)
        intent = {'schemaVersion': 1, 'caseReference': challenge['caseReference'],
            'operationId': challenge['operationId'], 'verifier': self.p['signerRoleArn'],
            'confirmationTime': attestation['ownershipVerifiedAtEpoch'], 'outcome': 'SIGN_ATTEMPTED'}
        exclusive(intent_path, intent)
        C.clocks(record, self.c, self.clock())
        result = self.clients['kms'].sign(KeyId=self.c['kmsKeyArn'], Message=C.canonical(record),
            MessageType='RAW', SigningAlgorithm=C.ALGORITHM)
        C.need(result.get('KeyId') == self.c['kmsKeyArn'] and result.get('SigningAlgorithm') == C.ALGORITHM
               and type(result.get('Signature')) is bytes and len(result['Signature']) == 384)
        C.clocks(record, self.c, self.clock())
        # A changed account or deletion admitted while KMS ran cannot yield a new capability.
        C.need(self.snapshot(challenge['subject']) == (profile, email))
        C.clocks(record, self.c, self.clock())
        exclusive(output_path, {'record': record, 'signature': base64.b64encode(result['Signature']).decode('ascii')})
        return {'schemaVersion': 1, 'stage': 'CAPABILITY_WRITTEN', 'admissionSubmitted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('check', 'prepare', 'sign'), default='check')
    for name in ('configuration', 'policy', 'subject', 'case-reference', 'received-at',
                 'challenge', 'attestation', 'intent', 'output'):
        parser.add_argument('--' + name)
    args = parser.parse_args()
    try:
        config = C.settings({'SUPPORT_ACCOUNT_DELETION_ENABLED': 'true',
            'SUPPORT_ACCOUNT_DELETION_CONFIG_JSON': C.canonical(private_read(args.configuration)).decode()})
        reviewed = policy(config, private_read(args.policy))
        if args.mode == 'check':
            print('{"schemaVersion":1,"stage":"OFFLINE_INPUTS_VALID","policyApprovalInferred":false}')
            return 0
        import boto3
        from botocore.config import Config
        sdk = Config(retries={'total_max_attempts': 1}, connect_timeout=2, read_timeout=3)
        clients = {n: boto3.client(n, region_name=config['region'], config=sdk)
                   for n in ('sts', 'dynamodb', 'cognito-idp', 'kms')}
        verifier = Verifier(config, reviewed, clients)
        if args.mode == 'prepare':
            result = verifier.prepare(args.subject, args.case_reference, int(args.received_at), args.output)
        else:
            result = verifier.sign(private_read(args.challenge), private_read(args.attestation), args.intent, args.output)
        print(C.canonical(result).decode())
        return 0
    except Exception:
        # Never print provider exceptions, identity, mailbox, challenge or capability.
        print('{"schemaVersion":1,"stage":"SUPPORT_VERIFIER_UNCONFIRMED"}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
