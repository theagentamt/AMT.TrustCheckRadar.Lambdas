# Sprint 7 History and Recognition Lambda Contract

Status: **implementation candidate complete; all activation flags remain disabled pending integration acceptance**

This contract covers Lambda runtime integration for `SECUR4ALL-224`,
`SECUR4ALL-225`, and `SECUR4ALL-226`. It does not authorize deployment or
claim that any story is complete.

## Artifacts and entry points

| Artifact | Handler | Purpose |
|---|---|---|
| `conversation_analysis.zip` | `app.lambda_handler` | Existing analysis plus synchronous History acceptance/completion and durable replay protection. |
| `history_read_api.zip` | `app.lambda_handler` | Authenticated History list/detail and recognition progress reads. |
| `history_mutation_api.zip` | `app.lambda_handler` | Authenticated bootstrap, delete-one, clear-History, History-only account-data deletion, and reset-progress mutations. |
| `history_lifecycle.zip` | `app.lambda_handler` | Scheduled expiration and durable erasure-job processing. |
| `history_account_deletion_bridge.zip` | `app.lambda_handler` | Authoritative deletion-ledger stream bridge that immediately fences History and starts full History cleanup. |
| `history-contracts-1.0.0.zip` | n/a | Canonical routes, limits, errors, badge IDs, and EN/ES localization contract. |

Only `history_account_deletion_bridge` requires a DynamoDB stream. It consumes
the deletion-ledger `NEW_IMAGE` for the fixed account fence described below.
No queue, projector, campaign table, or paid-model dependency is required.

## Physical resources and indexes

- `trustcheckradar-{env}-history-content`
- `trustcheckradar-{env}-history-control`
- `ExpirationIndex` on both tables: `expiryBucket` (S), `expiresAt` (N),
  `KEYS_ONLY`
- `PendingLifecycleIndex` on the control table: `lifecycleBucket` (S),
  `lifecycleAt` (N), `KEYS_ONLY`
- Numeric DynamoDB TTL attribute: `expiresAt`
- AWS-owned DynamoDB encryption at rest

Content keys are
`PK=USER#<sub>#HISTORY#<historyGeneration>` and
`SK=COMPLETE#<13-digit-completedAtEpochMs>#<requestId>`. Reads query the base
table strongly and newest-first. The control table uses:

- `PK=USER#<sub>`, `SK=STATE`
- `PK=USER#<sub>`, `SK=REQUEST#<requestId>`
- `PK=USER#<sub>`, `SK=MUTATION#<operationId>`
- `PK=USER#<sub>`, `SK=PROGRESS#<recognitionGeneration>`
- `PK=USER#<sub>`, `SK=COMPLETION#<requestId>`
- `PK=USER#<sub>`, `SK=ERASURE#<operationId>`
- `PK=CURSOR#<sha256(randomHandle)>`, `SK=CURSOR`
- `PK=LIFECYCLE#<environment>`, `SK=EXPIRATION#<HISTORY|CONTROL>`
- `PK=LIFECYCLE#<environment>`, `SK=RECONCILIATION#<HISTORY|CONTROL>`

The four lifecycle checkpoint records support three bounded expiration lanes.
Every invocation re-queries all 16 current-hour shards, so a record that becomes
due later in the hour is not skipped. The monotonic `EXPIRATION` checkpoints
drain closed-hour backlog from the configured start hour, and a bucket is not
advanced until its index query has no continuation. The rolling
`RECONCILIATION` checkpoints repeatedly revisit recent closed-hour shards to
recover records that were backdated or temporarily absent from the eventually
consistent GSI. Application writers must not create expirations outside that
window. Pending completion records are rescheduled after observation, moving
them behind other due work instead of permanently occupying the first index
page.

Each active `REQUEST` locator is also a durable retention work record. It keeps
its longer deduplication `expiresAt`, while `lifecycleAt` equals the History
content expiry and `lifecycleBucket=PENDING#<shard>`. The pending lifecycle lane
can therefore remove the replay response and content-free the locator even when
native DynamoDB TTL already removed the History row and its expiration-index
evidence. Content-index processing deletes the History row first, but it does
not clear this independent work marker until replay redaction succeeds. A replay
write failure leaves the locator discoverable and retryable. Delete-one and
generation erasure remove the marker only as part of their corresponding
redaction/erasure path.

A restore that can reintroduce records older than the reconciliation window is
not ready to serve immediately. The restore runbook must first set both tables'
`EXPIRATION` and `RECONCILIATION` checkpoints to the earliest restored expiry,
run lifecycle sweeps until the expiration checkpoint lag is zero and the
reconciliation window has completed, and only then admit reads or analysis
traffic. This is an activation gate; DynamoDB TTL is not accepted as the purge
mechanism.

Cursor values returned to clients are random URL-safe handles. DynamoDB keys,
subjects, assessments, and other payload data are not encoded in the handle.
Cursor records contain an HMAC subject binding and the server-side last sort
key; the HMAC secret is read from Secrets Manager.

`POST /v1/users/history/bootstrap` creates `STATE` and `PROGRESS#0` together and
is idempotent for an existing valid active state. Its transaction conditionally
checks the authoritative `USER#<sub>/PROFILE` record (`status=ACTIVE`,
`ageVerified=true`, matching `sub`) and absence of
`ACCOUNT#<sub>/ACCOUNT_DELETION`. Missing/ineligible/deleted users fail closed.

## Version 1 API routes

These routes exist behind disabled gates. They are not ready to expose until the
route, cursor, error, and field-bound contract is approved.

- `GET /v1/users/history`
- `GET /v1/users/history/{requestId}`
- `GET /v1/users/history/export`
- `GET /v1/users/progress`
- `POST /v1/users/history/bootstrap`
- `DELETE /v1/users/history/{requestId}`
- `DELETE /v1/users/history`
- `POST /v1/users/progress/reset`
- `DELETE /v1/users/history/account` (History/recognition data only)

Every API call requires a verified, unexpired Cognito access token with the
configured issuer/client, `token_use=access`, scope
`aws.cognito.signin.user.admin`, and the existing active device-binding
fingerprint. Identity comes only from `sub`; client-supplied `userId`,
`accountId`, and `sub` targeting is rejected. Each API call also strongly reads
the authoritative profile and fixed deletion fence, so a pre-issued token or a
delayed/missing stream delivery cannot restore access. Responses use
`Cache-Control: private, no-store` and
`Pragma: no-cache`. Mutation bodies are exact V1 objects containing a canonical
UUIDv4 `operationId`.

`DELETE /v1/users/history/account` is deliberately not a full account-deletion
endpoint and must not be wired or described as satisfying `SECUR4ALL-200`. Full
account deletion is the fixed deletion-ledger item
`PK=ACCOUNT#<sub>, SK=ACCOUNT_DELETION`, with the exact V1
`account.deletion.requested` fields documented in the canonical contract. The
bridge atomically changes History state to `DELETING` and creates a resumable job
covering generations zero through the captured maximum. Lifecycle completion
sets `DELETED` and writes `ACCOUNT_DELETION#HISTORY` as the component receipt;
it never marks the overall account-deletion command complete.

The only lifecycle invocation shape is:

```json
{"schemaVersion":1,"operation":"sweep"}
```

The EventBridge schedule/input role must be environment-scoped. Account-erasure
job creation remains an explicit `SECUR4ALL-200` integration gate; the scheduled
handler does not accept arbitrary account IDs from event payloads.

## Runtime environment

Approved constants default in code and may be omitted to stay below Lambda's
4-KB environment-variable limit: schema 1, History 90 days, purge SLA 24 hours,
dedup/tombstones 120 days, mutation receipts 7 days, page sizes 20/50, response
262144 bytes, cursor TTL 900 seconds, summary 4096 bytes, list length 20,
signal/action 1024 bytes, required Cognito scope, badge IDs/thresholds/keys, and
recognition qualification policy. Supplying an override with a different value
fails closed.

Minimal `conversation_analysis` History additions (in addition to its existing
analysis/entitlement/device settings):

- `APP_ENVIRONMENT`, `COGNITO_ISSUER`, `COGNITO_APP_CLIENT_ID`
- `USERS_TABLE_NAME`, `DELETION_LEDGER_TABLE_NAME`
- `HISTORY_CONTENT_TABLE_NAME`, `HISTORY_CONTROL_TABLE_NAME`
- `HISTORY_WRITES_ENABLED`, `HISTORY_DURABLE_REPLAY_ENABLED`
- `HISTORY_PITR_POLICY_APPROVED`, `HISTORY_CONTROL_RETENTION_POLICY_APPROVED`

Minimal `history_read_api`:

- the preceding identity, authority, History-table, and approval values
- `DEVICE_BINDINGS_TABLE_NAME`, `HISTORY_CURSOR_SECRET_NAME`
- `HISTORY_API_CONTRACT_STATUS=approved`, `HISTORY_READS_ENABLED`

Minimal `history_mutation_api`:

- the read identity/authority/History/device values except cursor secret
- `ANALYSIS_ABUSE_TABLE_NAME`, `HISTORY_API_CONTRACT_STATUS=approved`
- `HISTORY_MUTATIONS_ENABLED`
- when reset is exposed: `RECOGNITION_ENABLED` and
  `HISTORY_RECOGNITION_CONTRACT_STATUS=approved`

Minimal `history_lifecycle`:

- `APP_ENVIRONMENT`, `HISTORY_CONTENT_TABLE_NAME`,
  `HISTORY_CONTROL_TABLE_NAME`, `ANALYSIS_ABUSE_TABLE_NAME`,
  `DELETION_LEDGER_TABLE_NAME`
- `HISTORY_PITR_POLICY_APPROVED`, `HISTORY_CONTROL_RETENTION_POLICY_APPROVED`,
  `HISTORY_LIFECYCLE_ENABLED`
- `HISTORY_LIFECYCLE_START_EPOCH_HOUR`,
  `HISTORY_LIFECYCLE_MAX_ITEMS_PER_SWEEP`,
  `HISTORY_LIFECYCLE_MAX_BUCKET_QUERIES_PER_SWEEP`,
  `HISTORY_EXPIRATION_RECONCILIATION_HOURS`, `HISTORY_ERASURE_BATCH_SIZE`,
  `HISTORY_COMPLETION_STUCK_SECONDS`, `HISTORY_COMPLETION_RECHECK_SECONDS`

Minimal `history_account_deletion_bridge`:

- `APP_ENVIRONMENT`, `HISTORY_CONTROL_TABLE_NAME`,
  `DELETION_LEDGER_TABLE_NAME`, `HISTORY_ACCOUNT_DELETION_ENABLED`

Optional explicit common values:

- `APP_ENVIRONMENT`
- `HISTORY_CONTENT_TABLE_NAME`
- `HISTORY_CONTROL_TABLE_NAME`
- `DEVICE_BINDINGS_TABLE_NAME`
- `ANALYSIS_ABUSE_TABLE_NAME`
- `HISTORY_SCHEMA_VERSION=1`
- `HISTORY_RETENTION_DAYS=90`
- `HISTORY_ERASURE_SLA_HOURS=24`
- `HISTORY_EXPIRATION_INDEX_NAME=ExpirationIndex`
- `HISTORY_LIFECYCLE_INDEX_NAME=PendingLifecycleIndex`

Independent feature gates, all default `false`:

- `HISTORY_READS_ENABLED`
- `HISTORY_WRITES_ENABLED`
- `HISTORY_DURABLE_REPLAY_ENABLED`
- `HISTORY_MUTATIONS_ENABLED`
- `RECOGNITION_ENABLED`
- `HISTORY_LIFECYCLE_ENABLED`

`HISTORY_DURABLE_REPLAY_ENABLED` is a safety latch, not a routine kill switch.
Once History writes have accepted traffic it must remain enabled through the
longest approved durable-locator retention window. Likewise lifecycle processing
must not be disabled while erasure or completion jobs remain pending.

Approval/configuration gates:

- `HISTORY_API_CONTRACT_STATUS`
- `HISTORY_RECOGNITION_CONTRACT_STATUS`
- `HISTORY_PITR_POLICY_APPROVED`
- `HISTORY_CONTROL_RETENTION_POLICY_APPROVED`
- `HISTORY_DEDUP_RETENTION_DAYS` (whole days; must cover
  `HISTORY_RETENTION_DAYS * 86400 + HISTORY_ERASURE_SLA_HOURS * 3600`)
- `HISTORY_MUTATION_RETENTION_DAYS`
- `HISTORY_CURSOR_SECRET_NAME`
- `HISTORY_CURSOR_TTL_SECONDS`
- `HISTORY_DEFAULT_PAGE_SIZE`
- `HISTORY_MAX_PAGE_SIZE`
- `HISTORY_MAX_RESPONSE_BYTES`
- `HISTORY_MAX_SUMMARY_BYTES`
- `HISTORY_MAX_LIST_ITEMS`
- `HISTORY_MAX_TEXT_FIELD_BYTES`
- `HISTORY_BADGE_CATALOG_JSON`
- `HISTORY_NEW_ID_RECOGNITION_POLICY`
- `HISTORY_BADGE_QUALIFICATION_POLICY`
- `HISTORY_LIFECYCLE_START_EPOCH_HOUR` (Unix seconds, UTC-hour aligned)
- `HISTORY_LIFECYCLE_MAX_ITEMS_PER_SWEEP`
- `HISTORY_LIFECYCLE_MAX_BUCKET_QUERIES_PER_SWEEP`
- `HISTORY_EXPIRATION_RECONCILIATION_HOURS` (maximum 168)
- `HISTORY_ERASURE_BATCH_SIZE` (maximum 25)
- `HISTORY_COMPLETION_STUCK_SECONDS`
- `HISTORY_COMPLETION_RECHECK_SECONDS`

`HISTORY_WRITES_ENABLED=true` is rejected unless durable replay is also enabled.
Turning off new writes does not abandon already accepted work: an analysis with
a persisted History authorization finishes by writing content or a content-free
tombstone, preserving at-most-once charging.

Write, mutation, and lifecycle validation reject a durable-locator retention
window that ends before the content-retention deadline plus the erasure SLA.
With the fixed 90-day content and 24-hour cleanup contracts, 91 whole days is
the minimum valid boundary, not a selected deployment default. The approved
duration must still be supplied explicitly. Longer outage and restore coverage
remains a separate backup/PITR policy and activation gate.

## IAM contract

Use exact environment table/index/secret ARNs. Suggested `dynamodb:LeadingKeys`
patterns are defense in depth; tenant isolation remains enforced by the verified
JWT-derived key in each handler because the shared Lambda execution role has no
per-request IAM principal tag.

`conversation_analysis` needs:

- `dynamodb:GetItem` on History control/content (`USER#*`)
- transaction member permissions `dynamodb:PutItem`, `dynamodb:UpdateItem`, and
  `dynamodb:ConditionCheckItem` on the exact History content/control resources,
  each constrained by
  `dynamodb:EnclosingOperation = TransactWriteItems`; retain the equivalent
  underlying member permissions for its existing abuse/entitlement/campaign
  transaction resources

`history_read_api` needs:

- content `GetItem`, `Query` with `USER#*#HISTORY#*`
- control `GetItem` with `USER#*` or `CURSOR#*`
- control `PutItem` only with `CURSOR#*`
- users `GetItem` only with `USER#*`
- deletion ledger `GetItem` only with `ACCOUNT#*`
- device-binding GSI `Query` with `USER#*#ACTIVE`
- `secretsmanager:GetSecretValue` only for `HISTORY_CURSOR_SECRET_NAME`

`history_mutation_api` needs:

- control `GetItem` with `USER#*`
- device-binding GSI `Query` with `USER#*#ACTIVE`
- users and deletion-ledger `GetItem` plus transaction
  `dynamodb:ConditionCheckItem` for the exact profile/deletion-fence keys
- transaction member permissions `dynamodb:PutItem`, `dynamodb:UpdateItem`, and
  `dynamodb:DeleteItem` on the exact History content/control and analysis-abuse
  resources, each constrained by
  `dynamodb:EnclosingOperation = TransactWriteItems`; transaction leading keys
  are `USER#*` and `ANALYSIS#REQUEST#*`

`history_lifecycle` needs:

- `Query` on both base tables and their exact indexes
- control `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`
- content `DeleteItem`
- analysis-abuse `Query`, `UpdateItem` on `ANALYSIS#REQUEST#*`
- deletion-ledger `GetItem`, `PutItem` only for
  `ACCOUNT#*/ACCOUNT_DELETION#HISTORY`
- base-table leading keys `USER#*`, `CURSOR#*`, and
  `LIFECYCLE#<environment>`
- index leading keys `HISTORY#*`, `CONTROL#*`, and `PENDING#*`

`history_account_deletion_bridge` needs deletion-ledger stream read permissions,
control `GetItem`, transaction `UpdateItem`/`PutItem`, and transaction
`ConditionCheckItem` on the exact source deletion-ledger item. No artifact
requires SQS, SNS, S3 data access, or campaign table access.

## Metrics and alarms

The lifecycle artifact emits Embedded Metric Format values under
`AMT/TrustCheckRadar/History`, dimensioned only by bounded `Environment`:

- `ExpiredContentRecords`
- `ExpiredControlRecords`
- `CompletedErasureJobs`
- `CompletedRetentionPurges`
- `OverdueErasureJobs`
- `ObservedPendingCompletions`
- `StuckPendingCompletions`
- `OldestPendingCompletionAgeSeconds`
- `OldestPendingErasureAgeSeconds`
- `LifecycleWorksetTruncated`
- `LifecycleSweepSuccess`
- `LifecycleSweepFailure`
- `ExpirationBucketQueries`
- `RedactedReplayRecords`
- `ExpirationCheckpointLagSeconds`
- `HistoryReadSuccess`, `HistoryReadFailure`
- `HistoryMutationSuccess`, `HistoryMutationFailure`
- `HistoryAuthorizationFailure`

Count metrics, including bucket queries and replay redactions, use `Count`;
pending ages and checkpoint lag use `Seconds`. They are emitted once per
lifecycle invocation. The infrastructure schedule should run every five minutes.
`LifecycleSweepSuccess=1` is the success heartbeat, including a successful empty
check. A missing heartbeat is missing data, not zero backlog, and its alarm must
treat missing data as breaching after two expected periods. Age values are zero
only after a successful non-truncated empty check. When the approved work cap is
reached, `LifecycleWorksetTruncated=1`; an unobserved age is omitted instead of
reported as zero.

History/content expiration, clear-History erasure, and account-deletion erasure
explicitly remove the content-bearing `response` attribute from the matching
analysis-abuse replay partition. An erasure job uses bounded, resumable
`HISTORY` and `REPLAY` stages. Active request locators independently schedule
normal retention purge at `contentExpiresAt`, and remain present under their
longer deduplication retention after becoming content-free. This is required
even when table TTL is enabled: asynchronous DynamoDB TTL is supplementary
cleanup and is never treated as proof of the 24-hour physical purge.

Alarm on missing success heartbeat, `LifecycleSweepFailure > 0`,
`LifecycleWorksetTruncated > 0`, `StuckPendingCompletions > 0`,
`OverdueErasureJobs > 0`, Lambda `Errors`, `Throttles`, and transaction
cancellation/error rates on analysis and mutation handlers. API authorization
failures use the same namespace with `Count` and only the bounded dimensions
`Environment` plus `Operation` (`list`, `detail`, `progress`, `delete-one`,
`clear`, or `reset`). Logs contain operation/error codes and bounded counts only;
they do not contain account IDs, request IDs, assessments, cursor handles, or
input content.

## Remaining activation blockers

- infrastructure wiring of the new routes, exact IAM, cursor-secret container,
  deletion-ledger stream filter, and component receipt
- authenticated Dev integration, race tests against deployed AWS resources, and
  measured 24-hour physical-erasure evidence
- explicit infrastructure acceptance before enabling any feature flag
