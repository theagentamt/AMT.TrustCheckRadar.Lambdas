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
