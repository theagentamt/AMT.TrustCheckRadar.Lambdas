# SECUR4ALL-341 documentation-only publication correction

Existing story: [SECUR4ALL-341](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-341).
This is a source-only implementation checkpoint. It changes no runtime, alias,
route, IAM/trust, credential source, activation gate, provider, account or data.

## Problem and resulting behavior

The documentation-only main merge `c9d95fd8cdc95b6843c5b8b0114fc7b53186d872`
was classified `all`: CI37176124102 and publication37176224174 succeeded and
copied every package to a new immutable Dev S3 prefix. That was artifact
publication, not Lambda deployment. Existing published objects remain intact.

The corrected classifier returns `docs_only` only for a nonempty range entirely
within canonical `docs/` paths. The primary publishing job accepts that exact
scope, preserves source/CI/package validation, and skips AWS environment
requirements, credential assumption, account/bucket reads and every S3 upload.
The summary explicitly states no publication/credentials/uploads and emits no
S3 location. Existing main-only CI triggers and manual candidate workflows are
unchanged; no workflow was dispatched for this checkpoint.

Mixed documentation/runtime, packaging, workflow and unknown changes preserve
the existing specialized/conservative policy. Empty diffs and root README-only
changes still classify `all`. `git diff --no-renames` includes both deletion and
addition paths for a move: moving `src/...` into `docs/...` cannot suppress the
runtime change. Renames wholly inside `docs/` remain documentation-only.

## Validation and independent review

- `AWS_EC2_METADATA_DISABLED=true PYTHONPATH=src
  /tmp/amt-dev-diagnostics-venv/bin/python -B -m pytest -q tests/scripts
  --junitxml=/tmp/amt-sec341-scripts-tests.xml`: 215 passed, one deliberately
  gated isolated SDK module skipped, 21 subtests passed. The skipped unchanged
  campaign initializer requires `AMT_AUTHORITY_INTEGRATION=1`; it is not a
  security-check bypass or a live test result.
- Independent author tests execute the actual local workflow validator and
  summary, evaluate every primary AWS/publication guard, exercise real temporary
  Git histories including cross-boundary R100 renames, and reject wrong source,
  scope/schema/base manifests. 20 independent cases passed; combined with
  classifier cases, 32 passed. The pre-fix independent baseline retained three
  expected failures and 14 passes before the later rename/order cases were added.
- `actionlint .github/workflows/*.yml`, `shellcheck scripts/*.sh`, Python compile
  of scripts/tests and `git diff --check`: passed.
- Exact-source independent review: `PASS_SOURCE_ONLY`, with no integration
  blocker. Its committed receipt pins the four implementation/test files.

These are local executable workflow/classifier and regression checks, not a
GitHub dispatch, actual main CI, AWS credential request, S3 write or deployment.
The existing inline validator assumes ordinary unoptimized workflow Python; its
assert pattern is unchanged and no optimization-hardening pass is claimed.

## Integration and remaining acceptance

Base release: `7647a910cb656cd701b1b6d7db9b769ead4e57e0`.
Audited divergent main: `8ecc79678fd1f87c575c43f7f4ffb856869db48d`.
Normal reviewed source integration is into `release-V01` under the owner's
continuous-execution instruction. It authorizes no main promotion or publisher
dispatch. The exact PR/merge/source evidence is recorded in the existing story
after remote verification.

SEC341 remains open because its criterion 5 explicitly requires reviewed main
integration and actual main CI behavior. Preserve main's independently added
entitlements/governed scopes during any later approved reconciliation; never
replace main files wholesale with this older release baseline. After authorized
promotion, obtain the exact main CI/release-scope result and verify the docs-only
publication skip without modifying historical immutable objects. No extra
pre-main check trigger, forged status or bypass is permitted.
