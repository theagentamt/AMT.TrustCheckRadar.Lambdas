#!/usr/bin/env python3
"""Read-only exact Dev diagnostic plan; never publish code or change an alias."""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID
from prepare_access_snapshot_diagnostics import BASELINE_SHA256, EXPECTED_SHA256, HANDLER, require

ACCOUNT = '107827791950'
FUNCTION = 'trustcheckradar-dev-v1-entitlements'
ALIAS = 'live'
FIELDS = ('FunctionName', 'CodeSha256', 'Role', 'Handler', 'Runtime', 'Architectures',
          'MemorySize', 'Timeout', 'Environment', 'VpcConfig', 'Layers')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def admission(environment, subject):
    base = json.loads(environment['DEV_SUBJECT_ALLOWLIST_JSON'])
    extra = json.loads(environment['DEV_ACCESS_SNAPSHOT_EXTRA_SUBJECTS_JSON'])
    require(type(base) is list and len(base) == 1 and type(extra) is list and len(extra) == 1)
    require(all(type(v) is str and str(UUID(v)) == v for v in base + extra))
    require(subject not in base and subject in extra and base != extra)
    require(environment.get('STAGE') == 'dev' and environment.get('AUTHORITY_ENABLED') == 'true'
            and environment.get('V1_ENTITLEMENTS_ENABLED') == 'true')
    return {'baseSubjectCount': 1, 'extraGetOnlySubjectCount': 1,
            'selectedTesterGetAdmitted': True, 'selectedTesterTrialAdmissionExpanded': False}


def qualify(lam, scheduler, api, subject, candidate):
    require(hashlib.sha256(candidate).hexdigest() == EXPECTED_SHA256)
    current = lam.get_function_configuration(FunctionName=FUNCTION, Qualifier=ALIAS)
    latest = lam.get_function_configuration(FunctionName=FUNCTION, Qualifier='$LATEST')
    alias = lam.get_alias(FunctionName=FUNCTION, Name=ALIAS)
    require(current['Version'] == '15' and alias['FunctionVersion'] == current['Version']
            and not alias.get('RoutingConfig', {}).get('AdditionalVersionWeights'))
    require(current['Role'] == 'arn:aws:iam::107827791950:role/trustcheckradar-dev-v1-entitlements-execution'
            and current['MemorySize'] == 256 and current['Timeout'] == 10
            and current['Runtime'] == 'python3.14' and current['Architectures'] == ['arm64'])
    require(current['Handler'] == HANDLER and current['State'] == latest['State'] == 'Active'
            and current['LastUpdateStatus'] == latest['LastUpdateStatus'] == 'Successful')
    baseline = base64.b64encode(bytes.fromhex(BASELINE_SHA256)).decode()
    require(current['CodeSha256'] == latest['CodeSha256'] == baseline)
    require(all(current.get(k) == latest.get(k) for k in FIELDS))
    require(lam.get_function_concurrency(FunctionName=FUNCTION).get('ReservedConcurrentExecutions') == 2)
    access = admission(current['Environment']['Variables'], subject)
    fingerprints = {}
    for page in lam.get_paginator('list_functions').paginate():
        for function in page.get('Functions', []):
            name = function['FunctionName']
            if not name.startswith('trustcheckradar-dev-') or name == FUNCTION:
                continue
            aliases = [item for alias_page in lam.get_paginator('list_aliases').paginate(FunctionName=name)
                       for item in alias_page.get('Aliases', [])]
            live = next((item for item in aliases if item['Name'] == ALIAS), None)
            if live is None:
                continue
            value = lam.get_function_configuration(FunctionName=name, Qualifier=ALIAS)
            require(live['FunctionVersion'] == value['Version'])
            concurrency = lam.get_function_concurrency(FunctionName=name).get('ReservedConcurrentExecutions')
            fingerprints[name] = digest({'config': {k: value.get(k) for k in FIELDS},
                'version': value['Version'], 'concurrency': concurrency, 'routing': live.get('RoutingConfig', {})})
    require(len(fingerprints) == 12)
    handoff = lam.get_function_configuration(FunctionName='trustcheckradar-dev-v1-play-handoff', Qualifier=ALIAS)['Environment']['Variables']
    require(all(handoff.get(k) == value for k, value in {
        'PLAY_HANDOFF_ENABLED': 'false', 'PLAY_LIFECYCLE_ENABLED': 'false',
        'PLAY_PREPARATION_ENABLED': 'true', 'AUTHORITY_ENABLED': 'true'}.items()))
    require(json.loads(handoff['DEV_SUBJECT_ALLOWLIST_JSON']) == [subject])
    cleanup = lam.get_function_configuration(FunctionName='trustcheckradar-dev-play-token-deletion', Qualifier=ALIAS)['Environment']['Variables']
    require(subject in json.loads(cleanup['DEV_SUBJECT_ALLOWLIST_JSON']))
    for name in ('trustcheckradar-dev-play-lifecycle-ingress', 'trustcheckradar-dev-play-lifecycle-worker'):
        env = lam.get_function_configuration(FunctionName=name, Qualifier=ALIAS)['Environment']['Variables']
        require(env.get('PLAY_LIFECYCLE_ENABLED') == 'false')
    schedule = scheduler.get_schedule(Name='trustcheckradar-dev-play-lifecycle-worker', GroupName='trustcheckradar-dev-play-lifecycle')
    require(schedule['State'] == 'DISABLED')
    schedule.pop('ResponseMetadata', None)
    routes = {}
    wanted = {'POST /device-registration', 'GET /v1/access', 'POST /v1/purchases/google-play/prepare', 'POST /v1/purchases/google-play/verify'}
    for page in api.get_paginator('get_routes').paginate(ApiId='icuak34th9'):
        for route in page.get('Items', []):
            if route['RouteKey'] in wanted:
                integration = api.get_integration(ApiId='icuak34th9', IntegrationId=route['Target'].split('/')[-1])
                integration.pop('ResponseMetadata', None)
                routes[route['RouteKey']] = digest({'route': route, 'integration': integration})
    require(set(routes) == wanted)
    private = {'functionName': FUNCTION, 'alias': ALIAS, 'beforeVersion': '15',
        'beforeCodeSha256': baseline, 'candidateSha256': EXPECTED_SHA256,
        'targetConfigurationFingerprint': digest({k: current.get(k) for k in FIELDS if k != 'CodeSha256'}),
        'latestRevisionId': latest['RevisionId'], 'aliasRevisionId': alias['RevisionId'],
        'protectedRuntimeFingerprints': fingerprints, 'protectedRouteFingerprints': routes,
        'rtdnScheduleFingerprint': digest(schedule), 'accessAdmission': access}
    public = {'observedAtUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scope': 'dev-existing-access-function-safe-diagnostics-only',
        'functionName': FUNCTION, 'alias': ALIAS, 'currentVersion': '15',
        'baselineSha256': BASELINE_SHA256, 'candidateSha256': EXPECTED_SHA256,
        'plannedUpdates': ['existing function code, Publish=true, CAS on latest revision',
            'existing unweighted live alias15 to the exact returned qualified version, CAS on alias revision'],
        'newInfrastructureOrCapacity': False, 'configurationAndAccessAdmissionChanged': False,
        'accessAdmission': access, 'protectedRuntimeCount': len(fingerprints), 'protectedRouteCount': len(routes),
        'paymentVerificationLifecycleRTDNRemainDisabled': True,
        'rollback': 'CAS live alias from the recorded diagnostic version back to existing version15; preserve configuration and both allowlists.',
        'planDigest': digest(private), 'reviewedSourcePublicationRequiredBeforeRuntimeApply': True,
        'applied': False, 'behavioralQualification': False, 'identityOrCredentialOutput': False}
    return private, public


def security_baseline(iam, logs):
    role = 'trustcheckradar-dev-v1-entitlements-execution'
    policies = {}
    names = [name for page in iam.get_paginator('list_role_policies').paginate(RoleName=role)
             for name in page.get('PolicyNames', [])]
    for name in names:
        policies[name] = iam.get_role_policy(RoleName=role, PolicyName=name)['PolicyDocument']
    attached = [item for page in iam.get_paginator('list_attached_role_policies').paginate(RoleName=role)
                for item in page.get('AttachedPolicies', [])]
    group_name = '/aws/lambda/' + FUNCTION
    groups = [item for page in logs.get_paginator('describe_log_groups').paginate(logGroupNamePrefix=group_name)
              for item in page.get('logGroups', [])]
    matches = [row for row in groups if row['logGroupName'] == group_name]
    require(len(matches) == 1)
    group = matches[0]
    stable_group = {k: group.get(k) for k in ('logGroupName', 'retentionInDays', 'kmsKeyId', 'logGroupClass')}
    return {'executionPolicyFingerprint': digest({'inline': policies, 'attached': attached}),
            'logGroupFingerprint': digest(stable_group)}, group.get('retentionInDays')


def save_exclusive(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--selected-subject-file', type=Path, required=True)
    parser.add_argument('--private-plan', type=Path, required=True)
    parser.add_argument('--public-evidence', type=Path, required=True)
    args = parser.parse_args()
    try:
        import boto3
        from botocore.config import Config
        subject = json.loads(args.selected_subject_file.read_text())['subject']
        require(type(subject) is str and str(UUID(subject)) == subject)
        session = boto3.Session(profile_name='trustcheckradar', region_name='us-east-1')
        config = Config(connect_timeout=3, read_timeout=5, retries={'total_max_attempts': 1})
        require(session.client('sts', config=config).get_caller_identity()['Account'] == ACCOUNT)
        private, public = qualify(session.client('lambda', config=config), session.client('scheduler', config=config),
            session.client('apigatewayv2', config=config), subject, args.candidate.read_bytes())
        security, retention = security_baseline(session.client('iam', config=config), session.client('logs', config=config))
        private.update(security)
        public.update(existingLogRetentionDays=retention, iamAndLogRetentionChangesPlanned=False, planDigest=digest(private))
        save_exclusive(args.private_plan, private)
        save_exclusive(args.public_evidence, public)
        print(json.dumps(public))
    except Exception:
        raise SystemExit('Read-only diagnostic plan rejected; no private values printed. No runtime changed.') from None


if __name__ == '__main__':
    main()
