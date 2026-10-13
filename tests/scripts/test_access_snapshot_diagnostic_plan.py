import base64
import importlib.util
from pathlib import Path
import sys
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('safe_plan', ROOT / 'scripts/plan_access_snapshot_diagnostics.py')
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)
package_spec = importlib.util.spec_from_file_location('safe_package', ROOT / 'scripts/prepare_access_snapshot_diagnostics.py')
package = importlib.util.module_from_spec(package_spec)
package_spec.loader.exec_module(package)
PRESERVED = str(UUID(int=1))
SELECTED = str(UUID(int=2))


def candidate():
    return package.build((ROOT / 'tests/fixtures/dev-access-reader-8ecc.zip').read_bytes(),
                         (ROOT / 'src/v1_entitlements/diagnostics.py').read_bytes())


class Pages:
    def __init__(self, value):
        self.value = value

    def paginate(self, **kwargs):
        return [self.value]


class Lambda:
    def __init__(self):
        self.extra = [SELECTED]
        self.base = [PRESERVED]
        self.latest_hash = None
        self.weighted = False
        self.verify_enabled = False

    def get_function_configuration(self, FunctionName, Qualifier):
        import json
        env = {'STAGE': 'dev', 'AUTHORITY_ENABLED': 'true', 'V1_ENTITLEMENTS_ENABLED': 'true',
               'DEV_SUBJECT_ALLOWLIST_JSON': json.dumps(self.base),
               'DEV_ACCESS_SNAPSHOT_EXTRA_SUBJECTS_JSON': json.dumps(self.extra)}
        if FunctionName.endswith('v1-play-handoff'):
            env = {'AUTHORITY_ENABLED': 'true', 'PLAY_HANDOFF_ENABLED': 'true' if self.verify_enabled else 'false',
                   'PLAY_LIFECYCLE_ENABLED': 'false', 'PLAY_PREPARATION_ENABLED': 'true',
                   'DEV_SUBJECT_ALLOWLIST_JSON': json.dumps([SELECTED])}
        elif FunctionName.endswith('play-token-deletion'):
            env = {'DEV_SUBJECT_ALLOWLIST_JSON': json.dumps([PRESERVED, SELECTED])}
        elif 'play-lifecycle-' in FunctionName:
            env = {'PLAY_LIFECYCLE_ENABLED': 'false'}
        code = base64.b64encode(bytes.fromhex(package.BASELINE_SHA256)).decode()
        if Qualifier == '$LATEST' and self.latest_hash:
            code = self.latest_hash
        return {'FunctionName': FunctionName, 'Version': '$LATEST' if Qualifier == '$LATEST' else '15',
                'CodeSha256': code, 'Handler': plan.HANDLER, 'State': 'Active',
                'LastUpdateStatus': 'Successful', 'Environment': {'Variables': env},
                'Role': 'arn:aws:iam::107827791950:role/trustcheckradar-dev-v1-entitlements-execution', 'RevisionId': 'synthetic-private-revision',
                'Runtime': 'python3.14', 'Architectures': ['arm64'], 'MemorySize': 256, 'Timeout': 10}

    def get_alias(self, **kwargs):
        return {'FunctionVersion': '15', 'RevisionId': 'synthetic-private-alias',
                'RoutingConfig': {'AdditionalVersionWeights': {'14': 0.1}} if self.weighted else {}}

    def get_function_concurrency(self, **kwargs):
        return {'ReservedConcurrentExecutions': 2}

    def get_paginator(self, name):
        if name == 'list_functions':
            return Pages({'Functions': [{'FunctionName': 'trustcheckradar-dev-protected-' + str(i)} for i in range(12)]})
        assert name == 'list_aliases'
        return Pages({'Aliases': [{'Name': 'live', 'FunctionVersion': '15'}]})


class Scheduler:
    def get_schedule(self, **kwargs):
        return {'State': 'DISABLED', 'ResponseMetadata': {'RequestId': 'changing-private-id'}}


class API:
    def __init__(self):
        self.serial = 0

    def get_paginator(self, name):
        assert name == 'get_routes'
        return Pages({'Items': [{'RouteKey': key, 'Target': 'integrations/example'} for key in (
            'POST /device-registration', 'GET /v1/access', 'POST /v1/purchases/google-play/prepare',
            'POST /v1/purchases/google-play/verify')]})

    def get_integration(self, **kwargs):
        self.serial += 1
        return {'IntegrationUri': 'synthetic-existing-route', 'ResponseMetadata': {'RequestId': str(self.serial)}}


def test_plan_preserves_get_only_admission_and_private_revisions():
    api = API()
    private, public = plan.qualify(Lambda(), Scheduler(), api, SELECTED, candidate())
    assert private['beforeVersion'] == public['currentVersion'] == '15'
    assert public['accessAdmission']['selectedTesterGetAdmitted'] is True
    assert public['accessAdmission']['selectedTesterTrialAdmissionExpanded'] is False
    assert public['newInfrastructureOrCapacity'] is False
    assert public['configurationAndAccessAdmissionChanged'] is False
    assert public['applied'] is False
    assert 'latestRevisionId' not in public and 'aliasRevisionId' not in public
    _, repeated = plan.qualify(Lambda(), Scheduler(), api, SELECTED, candidate())
    assert repeated['planDigest'] == public['planDigest']


@pytest.mark.parametrize('field,value', [('extra', []), ('base', [PRESERVED, SELECTED]),
                                       ('latest_hash', 'unrelated-candidate'), ('weighted', True),
                                       ('verify_enabled', True)])
def test_unqualified_or_broader_runtime_state_rejected(field, value):
    lam = Lambda()
    setattr(lam, field, value)
    with pytest.raises(ValueError):
        plan.qualify(lam, Scheduler(), API(), SELECTED, candidate())
