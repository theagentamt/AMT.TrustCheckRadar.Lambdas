# Deployed deletion acceptance operators

These operators are intended only for the exact reviewed Dev synthetic acceptance plan. They do not approve an inventory, activate a gate or create component receipts. Root owns each execution decision and deployment. Production Lambda source and artifact selection are unchanged by these scripts.

## Empty-work acceptance

`scripts/qualify_cleanup_workers.py` defaults to offline plan validation. Execution requires `--execute`, an exact `--plan-sha256`, and the plan's six function hashes, environment digests, revision/version pins and three table IDs. The helper repeats bounded strongly consistent ledger/History reads before each single invocation, refuses pending account/withdrawal/recovery work or retained History account/content rows, and uses only the six documented reconciliation/sweep events. It does not retry. Alias checks and pre/post revision checks detect drift; unqualified invocations cannot be atomically revision-bound.

Results persist after each step. Failures retain earlier successes and the attempted worker, with only a fixed category: preflight, runtime-mismatch, invoke-transport, invoke-response, function-error, response-validation or postflight. No response bodies or exception strings are retained. `allPassed` means all invocations and required response counters passed; it does not certify global coverage, historical erasure or CloudWatch ingestion. History truncation/full-pass counters remain explicit observations.

The initial actual run passed account-data, campaign and History bridge, then stopped at History lifecycle. Sanitized diagnostics identified the reserved `shard` condition attribute. PR68 fixes that expression; the original partial outcome is not an all-worker pass. The paired immutable artifacts and independently downloaded-byte verification are recorded under `docs/evidence/history-checkpoint-expression`. Runtime installation and requalification are separate.

## Completion observer

`scripts/observe_dev_account_deletion.py` is read-only. Both modes require the private synthetic journal and an explicit clean `--source-root`/`--source-commit` before loading actual finalizer validators. Capture `baseline` before the request. `observe` additionally requires the exact baseline byte hash. Each invocation has a 180-second deadline and bounded pagination.

The private baseline contains table identities plus hashed unrelated row keys/contents. Observation requires the original owned terminal operation, all twelve unexpired exact component receipts, absent campaign sidecars/control, empty selected USER partition and actual Cognito absence. Every baseline unrelated row must remain unchanged. Additional new rows are permitted, including operational cursor creation; this is not an exact whole-table-equality claim. The final ledger/profile proofs are reread. No direct deletion or identity mutation is available.

## Accepted request and token rejection

`scripts/qualify_dev_account_deletion_monitor.py` retains the reviewed administrative bootstrap/authentication boundaries and atomic private journal. Its authentication stage sets a random password only in memory, submits the original operation once and durably records acceptance before monitoring. It does not represent native signup delivery acceptance.

After acceptance, the access token remains only in process memory for at most 600 seconds/120 polls. A qualifying poll requires both an application `UNAUTHORIZED` response and Cognito rejection, in that same poll and before token expiry. Time/expiry are checked after each network operation; alternating denials and natural expiry cannot establish rejection. Pending API responses must match the original owned operation. Monitoring failure preserves accepted state and does not retry POST. Token rejection is distinct from erasure; use the independent observer for completion.

Validation: 35 focused tests pass in normal and optimized Python (17 empty-work/failure-category, 10 observer and 8 monitor); the combined run with the previously integrated bootstrap and inventory helpers passes 69 tests. Tests use synthetic mocked service responses and actual source finalizer validators where stated; they do not replace actual deployed worker, IAM, route or token acceptance. The observer/source and runtime-plan pins must be reviewed anew for any changed deployment. No passwords, JWTs, journal subject or raw account rows belong in committed evidence.
