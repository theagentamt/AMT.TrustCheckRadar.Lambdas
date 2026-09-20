# Dev V1 engineering integration — manual release candidate

This source increment connects the reviewed V1 deletion helper to a real bounded worker, enforces the retained-key inventory in runtime loading and every account mutation transaction, and adds a mandatory synthetic-subject guard before consumer/access/trial AWS work. It remains an engineering integration, not general customer availability. The owner approved seven-day minimized result/usage receipts without original messages/URLs and trial eligibility retained until account deletion on 2026-09-20. Those values do not approve unrelated legacy account-deletion policies.

## Release and activation boundary

Manually build four authority packages from one pinned commit: `url_consumer.zip`, `url_lease_recovery.zip`, `v1_entitlements.zip`, and `v1_authority_deletion.zip`. Also build `url_assessment.zip` from that source: the trusted 18-second execution budget extension is present in source but was not in the previous live private assessment v1 artifact. Updating the private assessment is a separately reviewed exact-artifact change, required before a consumer call. Existing operator events without the optional budget remain supported. No legacy Web Risk artifact changes.

Python 3.14 ARM64; consumer handler `app.lambda_handler`, recovery handler `app.lambda_handler`, entitlement handler `v1_entitlements.app.lambda_handler`, deletion handler `v1_authority_deletion.app.lambda_handler`. All default flags remain false. Package validation must import all four disabled handlers without network and inspect ARM64 native dependencies; host import alone is not actual Lambda runtime qualification. The release classifier marks this mixed change `authority_manual`; broad publishing must not upload it. CI adds only local packaging validation for the fourth package; no workflow deployment is added.

The account-data API is **not deployed in Dev**, and its legacy ENTITLEMENTS cleanup/identity finalization requirements are not complete. Its source now requires the additional V1_AUTHORITY receipt in both required-components configuration and USER_PROFILE prerequisites. It still requires ENTITLEMENTS separately and all prior policy/inventory/completion approvals. Do not deploy or enable that API merely because V1 deletion tests pass. Root-controlled synthetic deletion commands qualify only the new V1 partition cleanup, not full account erasure.

## Synthetic subject restriction

`DEV_SUBJECT_ALLOWLIST_JSON` must be a nonempty JSON array of distinct canonical UUID Cognito subjects (backend maximum 10; infrastructure may impose a tighter limit). There is no wildcard, empty-list or missing-setting fallback. Consumer/access/trial call `require_engineering_subject` before secrets, DynamoDB or provider work. It validates issuer/client/access-token type/scope/expiry from API Gateway authorizer claims and matches the exact subject; request body/header identity never qualifies. Existing account/age/device/entitlement transaction checks still apply afterwards.

The worker uses the same strict allowlist from server configuration and processes only matching authoritative deletion commands. Unlisted commands are skipped without account mutation. It must independently load retained keys even when AUTHORITY_ENABLED=false or the selected account is already fenced. Lease recovery contains only HMAC references and remains limited to the newly qualified V1 data; initial inventory setup therefore requires proving there are no historical V1 rows before the first newly generated namespace is issued. No paid/complimentary grant may be fabricated for testing: use the real explicit trial activation path with the approved 7-day/10-completed-check policy.

## Verified inventory and key rotation

The independent loader retrieves only the exact Dev HMAC secret ARN at VersionStage AWSCURRENT with SDK retries disabled. It checks retained key IDs/material length and loads the independently verified `V1#CONTROL/HMAC_KEY_INVENTORY` authority row. The row's complete issued-key fingerprint map must exactly match the secret keyring; a coverage label is not proof of historical completeness. Initialization is a privileged, separately reviewed operation after verifying issuance history (or confirming a fresh namespace and no historical V1 rows). No loader auto-creates this row.

Every authority account mutation now strongly validates this inventory and adds a transaction condition on its revision, key map, type and coverage. A rotation between authorization and admission/settlement fails atomically. Missing or dropped keys fail closed. Future rotations must append/retain all old namespaces and material until reviewed migration/retirement proves they are unused; this release includes no key-retirement tool or safe recovery from lost keys. Rotation while deletion progress is pinned requires a reviewed continuation strategy, not overwriting progress.

## Deletion worker infrastructure contract

Suggested timeout 30 seconds, reserved concurrency 1. Environment:

- `STAGE=dev`, `V1_AUTHORITY_DELETION_ENABLED=false` initially.
- `AUTHORITY_TABLE_NAME`, `DELETION_LEDGER_TABLE_NAME` — existing exact table names.
- `AUTHORITY_HMAC_SECRET_ARN` — exact existing Dev secret ARN, AWSCURRENT only.
- `DEV_SUBJECT_ALLOWLIST_JSON` — exact approved synthetic subjects.
- `DELETION_LEDGER_STREAM_ARN` — exact deletion table stream ARN.
- `DELETION_RECEIPT_RETENTION_SECONDS=10368000` — existing 120-day component-receipt compatibility, distinct from seven-day V1 result retention.

Stream: INSERT/MODIFY, filter `NewImage.SK.S=ACCOUNT_DELETION` and `NewImage.eventType.S=account.deletion.requested`, batch size 5, ReportBatchItemFailures. The worker verifies the exact stream ARN and command against the authoritative strongly read ledger. Partial deletion returns the record sequence for retry. A bounded stream retry policy is acceptable only with mandatory scheduled reconciliation of the durable ledger.

One-minute schedule payload: `{"schemaVersion":1,"operation":"reconcile-v1-authority-deletion"}`. Reconciliation scans at most two pages of 25 evaluated rows, processes at most five commands, and resumes from the last evaluated/processed key. Failed/poison commands advance the cursor and are retried on later full passes. Cursor is in the deletion ledger, `PK=V1#CONTROL`, `SK=DELETION_RECONCILIATION_CURSOR`, with CAS revision and full-pass timestamps. No URLs, credentials or provider payloads are persisted. It starts a command only with at least 10 seconds remaining; the helper checks a five-second safety margin between pages and before completion, returning pending safely. Per command, at most two pages of 20 data rows are deleted; all ACCESS rows are fenced before deletion and removed last.

IAM needs exact-table authority GetItem/Query and transaction-scoped UpdateItem/DeleteItem/ConditionCheckItem; deletion-ledger GetItem/Scan and transaction-scoped PutItem/UpdateItem/DeleteItem/ConditionCheckItem, including ACCOUNT#* and V1#CONTROL keys. Grant exact AWSCURRENT secret retrieval and exact deletion stream DescribeStream/GetRecords/GetShardIterator/ListStreams as required by mapping. No provider invoke, user/Cognito administration or arbitrary table grants. Initialization of verified inventory is a separate operator capability, not a worker write.

Logs contain only fixed event/reason and integer counts. Successful stream: event `v1_authority_deletion`, examined/completed/pending/failed/deleted/overdue/skipped and reason OK or BATCH_FAILED. Scheduled events additionally contain pages/fullPassCompleted/fullPassAgeSeconds. Fatal failures log event, failed=1, reason DELETION_UNAVAILABLE, then raise a fixed error. Already completed receipts do not repeatedly increment overdue. Pending is batch work, not total backlog; alert on failure, overdue, missing heartbeat and excessive full-pass age. Full-pass timestamps survive pauses; a heartbeat without progress cannot hide a stalled sweep.

## Validation and remaining qualification

Tests use synthetic identities/credentials and Moto, including real authority mutations and component transactions; no live records or secret values. Covered: strict independent loader, pre-AWS allowlist, wrong/expired JWTs, inventory race, stream source rejection, bounded partial retry, scheduled catch-up/poison progress, cursor CAS, full-pass age, overdue suppression for complete receipts, disabled no-network handlers, parent completion blocking for either missing V1_AUTHORITY or legacy ENTITLEMENTS, and all prior deletion races.

Before engineering route enablement, root must review exact artifacts/IAM/schedules, provision verified fresh inventory, configure the sole synthetic subject, verify registered active device and account/age state, run the real explicit trial path, and test complete/partial/error accounting, same-proof retry/reconciliation, lease recovery and synthetic deletion. Results logically expire after seven days; DynamoDB TTL physical removal is asynchronous and must not be described as exact physical deletion at that instant. Immediate account cleanup uses explicit deletion transactions. Customer access remains blocked pending full legacy account deletion and other product readiness gates.

## Explicit expiry cleanup

The recovery schedule now handles two queues with independent durable cursors: V1_PENDING leases and V1_EXPIRING minimized records. Preparations and attempt-window counters enter V1_EXPIRING when created; a check receipt enters it only after settlement. Its sort key is `<zero-padded expiresAt>#<HMAC partition>#<row SK>`. Expired pending leases settle zero first; cleanup never drops a reservation-bearing receipt merely because its retention deadline passed.

`Expiry` strongly reads the row and validates exact record type, state and expiry before a conditioned delete. It checks the verified inventory revision/key namespace and the ACCESS deletion fence. Orphan preparation/attempt rows legitimately created before an ACCESS grant can be removed with a conditional absent-ACCESS guard. Stale GSI rows, already deleted rows and expiry extensions are harmless no-ops; malformed inventory remains a failure. No provider call, secret retrieval or raw account lookup is needed.

The scheduled handler limits each queue to two pages of five rows, leaves checkpoint time, divides available runtime so one queue cannot starve the other, and persists LEASE_SWEEP_CURSOR and EXPIRY_SWEEP_CURSOR under authority-table V1#CONTROL. It reads each queue head separately so a poison oldest row remains visible while the fair cursor advances. Logs add expiredDeleted, leaseExamined, expiryExamined, leaseOldestOverdueSeconds, expiryOldestOverdueSeconds and combined oldestOverdueSeconds, all integers without identifiers. Infrastructure must grant exact GSI leading keys V1_PENDING and V1_EXPIRING, authority control reads, and transaction-scoped DeleteItem for expiry. Alarm on failures and overdue age. TTL remains fallback; physical cleanup is performed explicitly by the bounded schedule, and overdue records are observable rather than hidden by logical read expiry.

## Live HTTP smoke

The Lambda-owned `scripts/url_consumer_engineering_smoke.py` requires the real synthetic Cognito access token and registered binding fingerprint from the infrastructure owner. It creates no identity or fabricated grant, follows no redirects, performs no automatic retries, and prints only sanitized check names/outcome/counters. It explicitly activates the real trial, submits only the chosen benign example.com URL, reconciles without a URL, repeats the same proof and verifies one allowance delta. HTTP qualification must be paired with root's invocation/log/ledger inspection to prove provider invocation count and privacy; the public DTO deliberately does not expose internal call counts.

```sh
python scripts/url_consumer_engineering_smoke.py \
  --base-url '<observed Dev HTTPS API base>' \
  --fingerprint '<registered synthetic binding>' \
  --token-file /private/tmp/amt-v1-engineering-access-token \
  --receipt-file /private/tmp/amt-v1-engineering-receipt.json \
  --activate-trial --public-https
```

The token file must be a regular non-symlink owned by the current user with mode0600. Alternatively supply AMT_ENGINEERING_ACCESS_TOKEN in the process environment. Never place the bearer token on the command line. The receipt file must not already exist; it is created mode0600 before submit and contains only checkId/operationProof/expiry for controlled follow-up. Remove it with the synthetic fixture cleanup. No password or full identity metadata file is read by this harness.

## Local release qualification

At the integration freeze: 223 isolated DynamoDB/authority/consumer/deletion/expiry tests pass; the normal suite passes 692 tests plus 162 subtests (eight intentional isolated-module skips). Python compilation, shellcheck, actionlint and whitespace checks pass. The complete changed-file set classifies as authority_manual. Runtime/API qualification is recorded separately after infrastructure activates only the explicit synthetic subject. No local test result alone is evidence of live deployment.

The process-death preparation case is also closed: consumer admission requires an OPEN matching preparation; expired URL-free reconciliation can atomically close a retained never-admitted preparation and return the narrow canonical not_started/zero shape. Concurrent late admission versus closure, inventory/deletion races, duplicate reconciliation and mismatched identities are covered. Internal library callers without a client identity cannot use this closure path; consumer adapters always supply the bound client identity. See the canonical transport README and fixture `expired-unadmitted-closed`.
