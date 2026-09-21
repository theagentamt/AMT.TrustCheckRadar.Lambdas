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
    assert module.classify(['scripts/build_lambda_zip.sh']) == 'all'
    assert module.classify(['src/shared_check_authority_evil/core.py']) == 'all'


def test_combined_consumer_writer_recovery_and_budget_release_is_manual():
    paths = [
        'src/url_consumer/app.py', 'src/url_lease_recovery/app.py',
        'src/shared_check_authority/core.py', 'src/shared_check_authority/entitlements.py',
        'src/v1_entitlements/app.py', 'src/url_assessment/app.py',
        'tests/shared_check_authority/test_consumer.py', 'tests/url_assessment/test_assessment.py',
        'contracts/url-consumer/1.0.0-candidate.1/response.schema.json',
        'contracts/v1-access/v1/snapshot.schema.json',
        'tests/contracts/test_v1_access_contract.py', 'tests/contracts/test_url_consumer_transport.py',
        'scripts/build_lambda_zip.sh', 'scripts/verify_v1_packages.py',
        'scripts/lambda_release_scope.py', 'tests/scripts/test_lambda_release_scope.py',
        '.github/workflows/ci.yml', '.github/workflows/publish.yml',
        'docs/v1-check-authority.md', 'docs/v1-entitlement-writers.md',
        'docs/url-consumer-dev-handoff.md', 'docs/GITHUB_PUBLISHING.md',
    ]
    assert module.classify(paths) == 'authority_manual'
    assert module.classify(paths + ['src/web_risk_communication/app.py']) == 'all'


def test_exact_reviewed_combined_diff_is_manual():
    import json
    paths=json.loads((Path(__file__).parent/'authority_release_paths.json').read_text())
    assert module.classify(paths)=='authority_manual'
    assert module.classify(paths+['src/web_risk_communication/app.py'])=='all'


def test_deletion_integration_and_parent_completion_gate_remain_manual():
    paths = ['src/v1_authority_deletion/app.py', 'src/v1_authority_deletion/service.py',
             'src/shared_check_authority/inventory.py', 'src/shared_check_authority/engineering.py',
             'src/account_data_api/config.py', 'src/account_data_api/service.py',
             'tests/account_data_api/test_service.py', 'tests/shared_check_authority/test_deletion_worker.py',
             'scripts/build_lambda_zip.sh', 'scripts/verify_v1_packages.py',
             'scripts/lambda_release_scope.py', 'tests/scripts/test_lambda_release_scope.py',
             '.github/workflows/ci.yml', 'docs/url-consumer-engineering-integration.md']
    assert module.classify(paths) == 'authority_manual'
    assert module.classify(['src/account_data_api/service.py']) == 'all'
