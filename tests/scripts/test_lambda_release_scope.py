import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('lambda_release_scope', Path(__file__).parents[2] / 'scripts/lambda_release_scope.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_resolver_source_and_shared_packaging_change_publish_only_resolver():
    assert module.classify(['src/url_redirect_resolver/app.py', 'scripts/build_lambda_zip.sh', '.github/workflows/publish.yml', 'docs/url-resolver-python314-handoff.md']) == 'resolver'


def test_unrelated_lambda_or_shared_runtime_change_keeps_broad_scope():
    assert module.classify(['src/url_redirect_resolver/app.py', 'src/conversation_analysis/app.py']) == 'all'
    assert module.classify(['src/url_redirect_resolver/app.py', 'src/shared_entitlements/service.py']) == 'all'


def test_no_resolver_change_does_not_claim_resolver_only_scope():
    assert module.classify([]) == 'all'
    assert module.classify(['README.md']) == 'all'
    assert module.classify(['src/url_redirect_resolver_evil/app.py']) == 'all'


def test_contract_only_handoff_never_publishes_runtime_artifacts():
    assert module.classify([
        'contracts/url-assessment/v1-draft/contract-set.json',
        'tests/url_assessment_contracts/test_contract.py',
        'scripts/lambda_release_scope.py',
        'tests/scripts/test_lambda_release_scope.py',
        '.github/workflows/publish.yml',
        'docs/GITHUB_PUBLISHING.md',
    ]) == 'contracts'


def test_runtime_change_cannot_hide_behind_contract_only_scope():
    assert module.classify([
        'contracts/url-assessment/v1-draft/contract-set.json',
        'src/shared_entitlements/service.py',
    ]) == 'all'
    assert module.classify([
        'contracts/url-assessment/v1-draft/contract-set.json',
        'src/url_redirect_resolver/resolver.py',
    ]) != 'contracts'


def test_private_assessment_requires_manual_publication_without_aws_automation():
    assert module.classify(['src/url_assessment/app.py', '.github/workflows/ci.yml', '.github/workflows/publish.yml', 'scripts/build_lambda_zip.sh', 'scripts/lambda_release_scope.py', 'tests/scripts/test_lambda_release_scope.py', 'docs/url-assessment-private-dev.md']) == 'assessment_manual'
    assert module.classify(['src/url_assessment/app.py', 'src/web_risk_communication/app.py']) == 'all'


def test_unwired_authority_core_never_publishes_runtime_artifacts():
    assert module.classify(['src/shared_check_authority/core.py', 'tests/shared_check_authority/test_transactions.py', '.github/workflows/ci.yml', '.github/workflows/publish.yml', 'scripts/lambda_release_scope.py', 'tests/scripts/test_lambda_release_scope.py', 'docs/v1-check-authority.md', 'docs/GITHUB_PUBLISHING.md']) == 'authority_manual'
    assert module.classify(['src/shared_check_authority/core.py', 'src/conversation_analysis/app.py']) == 'all'
    assert module.classify(['src/shared_check_authority/core.py', 'scripts/build_lambda_zip.sh']) == 'all'
    assert module.classify(['src/shared_check_authority_evil/core.py']) == 'all'
