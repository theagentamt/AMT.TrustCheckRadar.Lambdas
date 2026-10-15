# SECUR4ALL-340 backend Dev qualification

This is a source-level runbook for the later scoped Dev qualification. It does
not record a live run, runtime activation, Android restart, provider call, or
release/UAT result.

## Immutable artifact publication

Dispatch `.github/workflows/publish.yml` from `main` with mode
`governed_history_candidate`, environment `dev`, the exact reviewed main SHA,
and the successful CI run ID for that SHA. The workflow re-runs the source
gates, builds Python 3.14 ARM64 packages for `message_consumer`,
`message_evaluator`, `url_consumer`, and `governed_history`, then publishes
immutable versioned objects. It does not update Lambda functions, aliases,
routes, IAM, data, secrets, or activation settings. Exact S3 versions and
checksums are the input to the separately reviewed infrastructure plan.

## Synthetic HTTP runner

Use one explicitly authorized, onboarded synthetic Dev account and a mode-0600
JSON file containing only `Authorization` and
`x-device-binding-fingerprint`. The runner performs no network access without
`--execute`. Choose its lifecycle explicitly:

- `disposable` permits explicit first trial activation and requires account
  deletion through the approved cleanup path after evidence is recorded.
- `dedicated_reusable` requires an existing trial, rejects
  `--activate-trial`, preserves the account, and relies on each new governed
  History receipt's fixed seven-day TTL. It never resets the trial clock or
  counters.

Disposable example:

```shell
PYTHONPATH=src python3.14 scripts/qualify_dev_governed_history.py \
  --api-base https://api-dev.andmorethings.net \
  --headers-file /private/path/dev-headers.json \
  --language en \
  --fixture-mode disposable \
  --execute --activate-trial \
  --confirm-synthetic AUTHORIZED_SYNTHETIC_DEV_ACCOUNT
```

Dedicated reusable fixture example:

```shell
PYTHONPATH=src python3.14 scripts/qualify_dev_governed_history.py \
  --api-base https://api-dev.andmorethings.net \
  --headers-file /private/path/dev-headers.json \
  --language en \
  --fixture-mode dedicated_reusable \
  --execute \
  --confirm-synthetic AUTHORIZED_SYNTHETIC_DEV_ACCOUNT
```

Run the same bounded command again with `--language es`. The runner always reads
the access snapshot first. Once the trial exists, `--activate-trial` does not
send another activation request, so the original clock and counters cannot be
reset by this runner.

Each language run makes at most 20 HTTP requests. It executes two candidate.3
rules-only checks: a deterministic complete result charged once and an
inconclusive result charged zero. It verifies exact replay, proof-only
reconciliation, stable content-free list/detail results after a fresh client
read, immutable original accounting, and the fixed seven-day result deadline.
It prints only aggregate booleans and counts; it never prints tokens, device
fingerprints, check/result identifiers, proofs, original text, or response
bodies.

The infrastructure qualification must independently prove zero AI/provider
invocations, exact artifact/configuration identity, and final rollback with
routes, allowlists, engineering gates, and trial retention inactive. Delete the
account through the approved cleanup path only when `fixture-mode=disposable`.
For `dedicated_reusable`, preserve the account and verify the new receipt
deadlines remain the original fixed seven-day deadlines; do not renew them.
The runner does not delete accounts, reset trials, or change gates.

ATCR-163 remains the client release-test work. A successful backend run does
not prove Android process restart, local cache clearing, or physical-device/UAT
behavior.

## Local validation

```shell
PYTHONPATH=src python3.14 -m pytest -q \
  tests/scripts/test_qualify_dev_governed_history.py \
  tests/scripts/test_publish_message_candidate.py \
  tests/scripts/test_lambda_release_scope.py
```
