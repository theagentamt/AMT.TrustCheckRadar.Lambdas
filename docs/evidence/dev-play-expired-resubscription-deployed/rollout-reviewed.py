"""Owner-approved exact PR131 Dev resubscription code overlay. Journaled CAS; no mutation retries."""
import base64
import datetime
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

ROOT = Path('/private/tmp/amt-pr131-resubscription-rollout')
PLAN = ROOT / 'plan.json'
JOURNAL = ROOT / 'journal.json'
RESULT = ROOT / 'evidence.json'
SOURCE = Path('/tmp/amt-pr131-exact-overlay')
HEAD = 'cfc1241905b5a24dc6165c1d5025837f22161459'
MERGE = 'a51e17732ce101a54162e6a3c4db1b60597a76c5'
APPROVAL = 'User Sentinel_29ab99976f2c8191bf152efc8a00f0a9 go ahead to exact prepared PR131 Handoff-only Dev deployment; parent proposal Sentinel_8f6885a147f0819194e414a0d3fd1a84; no purchase, other runtime, settings, permissions or lifecycle change'
UTILITY = Path('/tmp/amt-dev-tester-switch.py')
UTILITY_SHA = '7036e6f02153a54c95338e679720e0502eb2d61943930616f771400f09ae187c'
HISTORICAL_PLAN = Path('/tmp/amt-play-v11-enable-verifier-private/plan.json')
HISTORICAL_DIGEST = '62316c5318cebbc9b3989915572987aaed56b2bbf003450a1d7988721301f1be'
APPROVED_POST = Path('/private/tmp/amt-pr128-prepare-auth-rollout/post-metadata-private.json')
APPROVED_POST_SHA = '8f61107ab6621a8bb5d15d4c0e668688a7a482cec73943882d06bee950024a4e'
TARGETS = {
    'trustcheckradar-dev-v1-play-handoff': {
        'version': '9', 'beforeSha': '4c9f18d5c3d64c596487a35d7e1f1287cf711507ac87f7ff6756466c793bb4f1',
        'sha': 'e91716c17d9dc66f3956452e5003d75e6c6e65ffc43c2f77d89c606c81cd8206',
        'bytes': 6279910, 'artifact': '/tmp/amt-dev-play-resubscription-live9-overlay.zip'},
}

SAFE = frozenset({'MODE_REJECTED', 'PRIVATE_PATH_UNSAFE', 'SOURCE_DRIFT', 'ARTIFACT_DRIFT',
    'EXISTING_PLAN', 'EXISTING_JOURNAL_NO_REPLAY', 'PLAN_DRIFT', 'PROTECTED_DRIFT',
    'LATEST_DRIFT', 'ALIAS_DRIFT', 'CANDIDATE_ALREADY_PRESENT', 'VERSION_INVALID',
    'VERSION_NOT_READY', 'CODE_DRIFT', 'CONFIG_DRIFT', 'REPLAY_REJECTED',
    'MUTATION_SCOPE_REJECTED', 'ROLLBACK_DRIFT', 'READBACK_INCOMPLETE', 'SESSION_TOO_SHORT'})


def require(value, code):
    if not value:
        raise RuntimeError(code)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def code(sha):
    return base64.b64encode(bytes.fromhex(sha)).decode()


def owned(path, directory=False):
    s = path.lstat()
    require(not path.is_symlink() and s.st_uid == os.geteuid()
            and stat.S_IMODE(s.st_mode) == (0o700 if directory else 0o600)
            and (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)), 'PRIVATE_PATH_UNSAFE')


def private_read(path):
    owned(path)
    return json.loads(path.read_text())


def utility():
    require(hashlib.sha256(UTILITY.read_bytes()).hexdigest() == UTILITY_SHA, 'SOURCE_DRIFT')
    spec = importlib.util.spec_from_file_location('pr126_guard', UTILITY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def local_pins():
    require(subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip() == HEAD, 'SOURCE_DRIFT')
    require(not subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain'], text=True).strip(), 'SOURCE_DRIFT')
    subprocess.run(['git', '-C', str(SOURCE), 'merge-base', '--is-ancestor', HEAD, MERGE], check=True)
    review_path = SOURCE / 'docs/dev-play-expired-resubscription-overlay-independent-review.json'
    require(hashlib.sha256(review_path.read_bytes()).hexdigest() == 'a212b074eb1d0cbdb41a6de809d516f71d115bba787b8bba2732f153dbcff04c', 'SOURCE_DRIFT')
    review = json.loads(review_path.read_text())
    require(review['result'] == 'PASS_EXACT_ARTIFACT_AND_SOURCE_ONLY_HANDOFF', 'SOURCE_DRIFT')
    for name, sha in review['reviewedFileSha256'].items():
        require(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest() == sha, 'SOURCE_DRIFT')
    manifest = json.loads((SOURCE/'docs/dev-play-expired-resubscription-overlay.json').read_text())
    for name, sha in {**manifest['sourceAndTestDocPins'], **manifest['evidencePins']}.items():
        require(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest() == sha, 'SOURCE_DRIFT')
    for target in TARGETS.values():
        artifact = Path(target['artifact'])
        require(artifact.is_file() and not artifact.is_symlink() and artifact.stat().st_size == target['bytes']
                and hashlib.sha256(artifact.read_bytes()).hexdigest() == target['sha'], 'ARTIFACT_DRIFT')
        require(manifest['artifactSha256'] == review['artifact']['sha256'] == target['sha']
                and review['artifact']['baselineSha256'] == target['beforeSha'], 'ARTIFACT_DRIFT')




def config_digest(m, value):
    return m.digest({k: v for k, v in m.config(value).items() if k != 'CodeSha256'})


def extras(m, cs, snapshot):
    iam, lam = cs['iam'], cs['lambda']
    rows = {}
    for name in sorted(m.TARGETS):
        role = snapshot['runtime'][name]['config']['Role'].split('/')[-1]
        r = iam.get_role(RoleName=role)['Role']
        attached = []
        for page in iam.get_paginator('list_attached_role_policies').paginate(RoleName=role):
            for policy in page.get('AttachedPolicies', []):
                meta = iam.get_policy(PolicyArn=policy['PolicyArn'])['Policy']
                document = iam.get_policy_version(PolicyArn=policy['PolicyArn'], VersionId=meta['DefaultVersionId'])['PolicyVersion']['Document']
                attached.append({'arn': policy['PolicyArn'], 'version': meta['DefaultVersionId'], 'document': document})
        rows[name] = m.digest({'trust': r.get('AssumeRolePolicyDocument'),
                              'boundary': r.get('PermissionsBoundary'),
                              'attached': sorted(attached, key=lambda x: x['arn'])})
    resources = {}
    for name in ('trustcheckradar-dev-v1-play-handoff', 'trustcheckradar-dev-v1-entitlements'):
        try:
            value = lam.get_policy(FunctionName=name, Qualifier='live')['Policy']
            resources[name] = m.digest(json.loads(value))
        except lam.exceptions.ResourceNotFoundException:
            resources[name] = 'NONE'
    api = cs['apigatewayv2']
    authorizers = [a for page in api.get_paginator('get_authorizers').paginate(ApiId='icuak34th9') for a in page.get('Items', [])]
    stages = [stage for page in api.get_paginator('get_stages').paginate(ApiId='icuak34th9')
              for stage in page.get('Items', [])]
    return {'roleTrustAndManagedPolicies': rows, 'lambdaLiveResourcePolicies': resources,
            'apiAuthorizers': m.digest(sorted(authorizers, key=lambda x: x['AuthorizerId'])),
            'apiStages': m.digest(sorted(stages, key=lambda x: x['StageName']))}


def historical_guard(m, snapshot):
    require(hashlib.sha256(APPROVED_POST.read_bytes()).hexdigest() == APPROVED_POST_SHA, 'SOURCE_DRIFT')
    approved = private_read(APPROVED_POST)
    require(snapshot == approved['snapshot'], 'PROTECTED_DRIFT')


def selected_guard(session, cs, m):
    selected = private_read(Path('/tmp/amt-dev-apptester-selected-account-private.json'))
    subject = selected['subject']
    user = cs['cognito-idp'].admin_get_user(UserPoolId=selected['pool'], Username=selected['username'])
    attrs = {a['Name']: a['Value'] for a in user['UserAttributes']}
    require(user.get('Enabled') is True and user.get('UserStatus') == 'CONFIRMED'
        and attrs.get('sub') == subject and attrs.get('email_verified') == 'true'
        and attrs.get('email') == selected['email'] and user.get('Username') == selected['username'], 'PROTECTED_DRIFT')
    db = session.resource('dynamodb', config=m.Config(connect_timeout=3, read_timeout=10, retries={'total_max_attempts': 1}))
    profile = db.Table('trustcheckradar-dev-users').get_item(Key={'PK': 'USER#'+subject, 'SK': 'PROFILE'},
        ConsistentRead=True, ProjectionExpression='#s,#sub,#age', ExpressionAttributeNames={'#s':'status','#sub':'sub','#age':'ageVerified'}).get('Item', {})
    require(profile.get('status') == 'ACTIVE' and profile.get('sub') == subject and profile.get('ageVerified') is True, 'PROTECTED_DRIFT')
    tomb = db.Table('trustcheckradar-dev-deletion-ledger').get_item(Key={'PK':'ACCOUNT#'+subject,'SK':'ACCOUNT_DELETION'},
        ConsistentRead=True, ProjectionExpression='PK').get('Item')
    require(tomb is None, 'PROTECTED_DRIFT')
    snapshot = private_read(APPROVED_POST)['snapshot']
    h = snapshot['runtime']['trustcheckradar-dev-v1-play-handoff']['config']['Environment']['Variables']
    require(all(h.get(k) == v for k,v in {'PLAY_HANDOFF_ENABLED':'true', 'PLAY_PREPARATION_ENABLED':'true',
        'AUTHORITY_ENABLED':'true', 'PLAY_REQUIRE_TEST_PURCHASES':'true', 'PLAY_CATALOG_P1M_VERIFIED':'true',
        'PLAY_LIFECYCLE_ENABLED':'false'}.items()) and json.loads(h['DEV_SUBJECT_ALLOWLIST_JSON']) == [subject], 'PROTECTED_DRIFT')
    inventory = db.Table(h['PURCHASE_OWNERSHIP_TABLE_NAME']).get_item(Key={'PK':'PURCHASE#CONTROL','SK':'OWNERSHIP_INVENTORY'}, ConsistentRead=True).get('Item', {})
    require(set(inventory)=={'PK','SK','recordType','schemaVersion','revision','coverage','environment'}
        and inventory.get('coverage')=='VERIFIED_COMPLETE' and inventory.get('environment')=='dev'
        and inventory.get('recordType')=='PURCHASE_OWNERSHIP_INVENTORY' and inventory.get('schemaVersion')==1
        and type(inventory.get('revision')).__name__ in {'int','Decimal'} and inventory['revision']>=1, 'PROTECTED_DRIFT')
    return True

def build_plan(m, session, cs):
    snapshot = m.capture(cs)
    historical_guard(m, snapshot)
    approved = private_read(APPROVED_POST)
    require(extras(m, cs, snapshot) == approved['extraProtected'], 'PROTECTED_DRIFT')
    expiry = getattr(session._session.get_credentials(), '_expiry_time', None)
    require(isinstance(expiry, datetime.datetime) and expiry.tzinfo is not None
            and (expiry - datetime.datetime.now(datetime.timezone.utc)).total_seconds() >= 900, 'SESSION_TOO_SHORT')
    require(selected_guard(session, cs, m), 'PROTECTED_DRIFT')
    targets = {}
    lam = cs['lambda']
    for name, target in TARGETS.items():
        live = lam.get_function_configuration(FunctionName=name, Qualifier='live')
        latest = lam.get_function_configuration(FunctionName=name)
        alias = lam.get_alias(FunctionName=name, Name='live')
        require(live['Version'] == alias['FunctionVersion'] == target['version']
                and not alias.get('RoutingConfig', {}).get('AdditionalVersionWeights'), 'ALIAS_DRIFT')
        require(live['CodeSha256'] == code(target['beforeSha']) and m.config(live) == m.config(latest)
                and latest.get('State') == 'Active' and latest.get('LastUpdateStatus') == 'Successful', 'LATEST_DRIFT')
        versions = [v for page in lam.get_paginator('list_versions_by_function').paginate(FunctionName=name) for v in page.get('Versions', [])]
        require(not any(v.get('CodeSha256') == code(target['sha']) for v in versions), 'CANDIDATE_ALREADY_PRESENT')
        require(sorted(int(v['Version']) for v in versions if v['Version'].isdigit()) == list(range(1, 10)), 'VERSION_INVALID')
        targets[name] = {'beforeVersion': target['version'], 'beforeCodeSha256': code(target['beforeSha']),
                         'candidateCodeSha256': code(target['sha']), 'configurationDigest': config_digest(m, live),
                         'latestRevisionId': latest['RevisionId'], 'aliasRevisionId': alias['RevisionId'],
                         'existingVersions': sorted(v['Version'] for v in versions)}
    return {'source': HEAD, 'merge': MERGE, 'rolloutSourceSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'targets': targets, 'snapshot': snapshot,
            'extraProtected': extras(m, cs, snapshot)}




def fences(m, cs, p, state):
    snapshot = m.capture(cs)
    expected = p['snapshot']
    require(set(snapshot['runtime']) == set(expected['runtime']), 'PROTECTED_DRIFT')
    for name, row in snapshot['runtime'].items():
        if name not in TARGETS:
            require(row == expected['runtime'][name], 'PROTECTED_DRIFT')
            continue
        done = name in state.get('completedTargets', [])
        version = state['newVersions'][name] if done else TARGETS[name]['version']
        expected_code = code(TARGETS[name]['sha'] if done else TARGETS[name]['beforeSha'])
        require(row['version'] == version and row['config']['CodeSha256'] == expected_code, 'ALIAS_DRIFT')
        require(config_digest(m, row['config']) == p['targets'][name]['configurationDigest']
                and row['concurrency'] == expected['runtime'][name]['concurrency']
                and row['routing'] == expected['runtime'][name]['routing'], 'CONFIG_DRIFT')
    for key in ('routes', 'schedules', 'cleanupMappings', 'security'):
        require(snapshot[key] == expected[key], 'PROTECTED_DRIFT')
    require(extras(m, cs, snapshot) == p['extraProtected'], 'PROTECTED_DRIFT')
    return snapshot


def qualify(m, lam, name, version, p, sha):
    until = time.monotonic() + 55
    while True:
        value = lam.get_function_configuration(FunctionName=name, **({'Qualifier': version} if version else {}))
        require(value['CodeSha256'] == code(sha), 'CODE_DRIFT')
        require(config_digest(m, value) == p['targets'][name]['configurationDigest'], 'CONFIG_DRIFT')
        require(value.get('State') != 'Failed' and value.get('LastUpdateStatus') != 'Failed', 'VERSION_NOT_READY')
        if value.get('State') == 'Active' and value.get('LastUpdateStatus') == 'Successful':
            return value
        require(time.monotonic() < until, 'VERSION_NOT_READY')
        time.sleep(1)


def mutation(m, lam, p, state, name, operation, arguments):
    require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == p['rolloutSourceSha256'], 'SOURCE_DRIFT')
    require(name in TARGETS and arguments.get('FunctionName') == name, 'MUTATION_SCOPE_REJECTED')
    key = name + ':' + operation
    require(key not in state['attempts'], 'REPLAY_REJECTED')
    target = p['targets'][name]
    if operation == 'code_publish':
        require(set(arguments) == {'FunctionName', 'ZipFile', 'Publish', 'RevisionId'}
                and arguments['Publish'] is True and arguments['RevisionId'] == target['latestRevisionId']
                and hashlib.sha256(arguments['ZipFile']).hexdigest() == TARGETS[name]['sha'], 'MUTATION_SCOPE_REJECTED')
        call = lam.update_function_code
    elif operation == 'alias':
        require(set(arguments) == {'FunctionName', 'Name', 'FunctionVersion', 'RoutingConfig', 'RevisionId'}
                and arguments['Name'] == 'live' and arguments['FunctionVersion'] == state['newVersions'][name]
                and arguments['RoutingConfig'] == {'AdditionalVersionWeights': {}}
                and arguments['RevisionId'] == target['aliasRevisionId'], 'MUTATION_SCOPE_REJECTED')
        call = lam.update_alias
    else:
        raise RuntimeError('MUTATION_SCOPE_REJECTED')
    state['attempts'].append(key)
    state['phase'] = key + '_started'
    m.atomic(JOURNAL, state)
    print(json.dumps({'phase': state['phase']}), flush=True)
    result = call(**arguments)
    state['confirmed'].append(key)
    state['phase'] = key + '_confirmed'
    m.atomic(JOURNAL, state)
    return result


def report(m, cs, p, state):
    require(set(state.get('completedTargets', [])) == set(TARGETS), 'READBACK_INCOMPLETE')
    fences(m, cs, p, state)
    rows = []
    lam = cs['lambda']
    for name, target in TARGETS.items():
        version = state['newVersions'][name]
        live = qualify(m, lam, name, 'live', p, target['sha'])
        latest = qualify(m, lam, name, None, p, target['sha'])
        rollback = qualify(m, lam, name, target['version'], p, target['beforeSha'])
        alias = lam.get_alias(FunctionName=name, Name='live')
        require(live['Version'] == alias['FunctionVersion'] == version
                and not alias.get('RoutingConfig', {}).get('AdditionalVersionWeights'), 'ALIAS_DRIFT')
        rows.append({'functionName': name, 'rollbackVersion': rollback['Version'],
                     'rollbackCodeSha256': rollback['CodeSha256'], 'liveVersion': version,
                     'codeSha256': live['CodeSha256'], 'artifactSha256': target['sha'],
                     'latestAndLiveCodeMatch': latest['CodeSha256'] == live['CodeSha256'],
                     'configurationAdmissionAndCapacityUnchanged': True, 'unweightedAliasVerified': True})
    result = {'observedAtUtc': now(), 'sourceCommit': HEAD, 'releaseMergeCommit': MERGE,
              'ownerApprovalId': 'Sentinel_29ab99976f2c8191bf152efc8a00f0a9',
              'deployed': True, 'targets': rows, 'other12RuntimeFingerprintsUnchanged': True,
              'fourRoutesIAMManagedAndInlinePoliciesTrustLogsCapacityCleanupSchedulesAndLifecycleUnchanged': True,
              'apiAuthorizersAndStagesUnchanged': True, 'entitlementsLiveVersion18Preserved': True, 'cleanupLiveVersion17Preserved': True,
              'noNewInfrastructureConfigurationPurchaseReplayOrEndpointInvocation': True,
              'secretValuesRead': False, 'mutationRetried': False, 'rollbackExecuted': False,
              'newMigrationAndFixedPredicateLiveBehaviorNotExercised': True, 'historicalFifthTransitionPredicateRemainsUnknown': True,
              'noNewPurchaseAuthority': True, 'allFivePriorBudgetsRemainConsumed': True}
    m.atomic(RESULT, result)
    return result


def main():
    logging.disable(logging.CRITICAL)
    state = None
    journal_owned = False
    stage = 'local_pins'
    m = None
    try:
        require(len(sys.argv) in (2, 3) and sys.argv[1] in {'plan', 'apply', 'readback'}, 'MODE_REJECTED')
        mode = sys.argv[1]
        local_pins()
        m = utility()
        ROOT.mkdir(mode=0o700, exist_ok=True)
        owned(ROOT, True)
        session, cs, _ = m.clients()
        if mode == 'plan':
            require(not PLAN.exists() and not JOURNAL.exists(), 'EXISTING_PLAN')
            stage = 'fresh_preflight'
            p = build_plan(m, session, cs)
            m.exclusive(PLAN, p)
            print(json.dumps({'planDigest': m.digest(p), 'observedAtUtc': now(),
                'targets': {n: {'liveVersion': t['beforeVersion'], 'rollbackVersion': t['beforeVersion']}
                            for n, t in p['targets'].items()}, 'protectedPreflightPassed': True, 'applied': False}))
            return 0
        p = private_read(PLAN)
        require(len(sys.argv) == 3 and sys.argv[2] == m.digest(p), 'PLAN_DRIFT')
        if mode == 'apply':
            require(not JOURNAL.exists(), 'EXISTING_JOURNAL_NO_REPLAY')
            stage = 'fresh_preflight'
            require(build_plan(m, session, cs) == p, 'PLAN_DRIFT')
            state = {'phase': 'fresh_verified', 'planDigest': m.digest(p), 'approval': APPROVAL,
                     'attempts': [], 'confirmed': [], 'newVersions': {}, 'completedTargets': []}
            m.exclusive(JOURNAL, state)
            journal_owned = True
            lam = cs['lambda']
            for name, target in TARGETS.items():
                stage = name + ':pre_code'
                fences(m, cs, p, state)
                current = lam.get_function_configuration(FunctionName=name)
                require(current['RevisionId'] == p['targets'][name]['latestRevisionId']
                        and current['CodeSha256'] == code(target['beforeSha']), 'LATEST_DRIFT')
                stage = name + ':code_publish'
                response = mutation(m, lam, p, state, name, 'code_publish', {
                    'FunctionName': name, 'ZipFile': Path(target['artifact']).read_bytes(),
                    'Publish': True, 'RevisionId': current['RevisionId']})
                version = response.get('Version')
                require(type(version) is str and re.fullmatch(r'[1-9][0-9]*', version)
                        and version == '10'
                        and version not in p['targets'][name]['existingVersions']
                        and response.get('CodeSha256') == code(target['sha']), 'VERSION_INVALID')
                state['newVersions'][name] = version
                m.atomic(JOURNAL, state)
                qualify(m, lam, name, version, p, target['sha'])
                qualify(m, lam, name, None, p, target['sha'])
                stage = name + ':pre_alias'
                fences(m, cs, p, state)
                alias = lam.get_alias(FunctionName=name, Name='live')
                require(alias['RevisionId'] == p['targets'][name]['aliasRevisionId']
                        and alias['FunctionVersion'] == target['version']
                        and not alias.get('RoutingConfig', {}).get('AdditionalVersionWeights'), 'ALIAS_DRIFT')
                qualify(m, lam, name, target['version'], p, target['beforeSha'])
                qualify(m, lam, name, None, p, target['sha'])
                stage = name + ':alias'
                response = mutation(m, lam, p, state, name, 'alias', {
                    'FunctionName': name, 'Name': 'live', 'FunctionVersion': version,
                    'RoutingConfig': {'AdditionalVersionWeights': {}}, 'RevisionId': alias['RevisionId']})
                require(response.get('FunctionVersion') == version
                        and not response.get('RoutingConfig', {}).get('AdditionalVersionWeights'), 'ALIAS_DRIFT')
                state['completedTargets'].append(name)
                m.atomic(JOURNAL, state)
        else:
            state = private_read(JOURNAL)
            require(state['planDigest'] == m.digest(p), 'PLAN_DRIFT')
        stage = 'final_readback'
        result = report(m, cs, p, state)
        if mode == 'apply':
            state['phase'] = 'completed'
            m.atomic(JOURNAL, state)
        print(json.dumps(result), flush=True)
        return 0
    except Exception as error:
        raw = getattr(error, 'response', {})
        raw_error = raw.get('Error') if type(raw) is dict else None
        provider = raw_error.get('Code') if type(raw_error) is dict else None
        provider = provider if type(provider) is str else None
        local = error.args[0] if type(error) is RuntimeError and error.args and type(error.args[0]) is str else None
        result = {'failed': True, 'stage': stage, 'safeLocalCode': local if local in SAFE else None,
                  'safeProviderCode': provider if provider in {'AccessDenied', 'AccessDeniedException', 'ExpiredToken',
                    'ExpiredTokenException', 'PreconditionFailedException', 'ResourceConflictException'} else None,
                  'existingSsoRenewalNeeded': type(error).__name__ in {'TokenRetrievalError', 'UnauthorizedSSOTokenError', 'SSOTokenLoadError'},
                  'detailsSuppressed': True, 'mutationRetried': False}
        if state is not None and m is not None and journal_owned:
            state['failure'] = result
            try:
                m.atomic(JOURNAL, state)
                result['journalPersisted'] = True
            except Exception:
                result['journalPersisted'] = False
            result.update(attempts=state['attempts'], confirmed=state['confirmed'],
                          allocatedVersions=state['newVersions'], readOnlyReconciliationRequired=True)
        print(json.dumps(result), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
