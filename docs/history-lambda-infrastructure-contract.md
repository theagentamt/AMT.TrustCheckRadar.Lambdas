# Sprint 7 History and Recognition Lambda Contract

Status: **implementation is disabled; activation decisions are pending**

This contract covers Lambda runtime integration for `SECUR4ALL-224`,
`SECUR4ALL-225`, and `SECUR4ALL-226`. It does not authorize deployment or
claim that any story is complete.

## Artifacts and entry points

| Artifact | Handler | Purpose |
|---|---|---|
| `conversation_analysis.zip` | `app.lambda_handler` | Existing analysis plus synchronous History acceptance/completion and durable replay protection. |
| `history_read_api.zip` | `app.lambda_handler` | Authenticated History list/detail and recognition progress reads. |
| `history_mutation_api.zip` | `app.lambda_handler` | Authenticated delete-one, clear-History, and reset-progress mutations. |
| `history_lifecycle.zip` | `app.lambda_handler` | Scheduled expiration and durable erasure-job processing. |

No DynamoDB stream, queue, projector, or campaign dependency is required.

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

Cursor values returned to clients are random URL-safe handles. DynamoDB keys,
subjects, assessments, and other payload data are not encoded in the handle.
Cursor records contain an HMAC subject binding and the server-side last sort
key; the HMAC secret is read from Secrets Manager.

The account lifecycle owner must create `STATE` with `accountStatus=ACTIVE`,
both generations at zero, and `acceptedSequence=0`. Recognition activation also
requires `PROGRESS#0`. Missing state fails closed and never appears as an empty
History or zero progress response.

## Draft API routes

These routes exist behind disabled gates. They are not ready to expose until the
route, cursor, error, and field-bound contract is approved.

- `GET /v1/users/history`
- `GET /v1/users/history/{requestId}`
- `GET /v1/users/progress`
- `DELETE /v1/users/history/{requestId}`
- `DELETE /v1/users/history`
- `POST /v1/users/progress/reset`

Every API call requires a verified Cognito JWT `sub` and the existing active
device-binding fingerprint. Responses use `Cache-Control: private, no-store` and
`Pragma: no-cache`. Mutation bodies are exact V1 objects containing a canonical
UUIDv4 `operationId`.

The only lifecycle invocation shape is:

```json
{"schemaVersion":1,"operation":"sweep"}
```

The EventBridge schedule/input role must be environment-scoped. Account-erasure
job creation remains an explicit `SECUR4ALL-200` integration gate; the scheduled
handler does not accept arbitrary account IDs from event payloads.

## Runtime environment

Common:

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
- `HISTORY_DEDUP_RETENTION_DAYS`
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
- `HISTORY_LIFECYCLE_LOOKBACK_HOURS`
- `HISTORY_LIFECYCLE_MAX_ITEMS_PER_SWEEP`
- `HISTORY_ERASURE_BATCH_SIZE` (maximum 25)
- `HISTORY_COMPLETION_STUCK_SECONDS`

`HISTORY_WRITES_ENABLED=true` is rejected unless durable replay is also enabled.
Turning off new writes does not abandon already accepted work: an analysis with
a persisted History authorization finishes by writing content or a content-free
tombstone, preserving at-most-once charging.

## IAM contract

Use exact environment table/index/secret ARNs. Suggested `dynamodb:LeadingKeys`
patterns are defense in depth; tenant isolation remains enforced by the verified
JWT-derived key in each handler because the shared Lambda execution role has no
per-request IAM principal tag.

`conversation_analysis` needs:

- `dynamodb:GetItem` on History control/content (`USER#*`)
- `dynamodb:TransactWriteItems` on History control/content plus its existing
  abuse/entitlement/campaign transaction resources

`history_read_api` needs:

- content `GetItem`, `Query` with `USER#*#HISTORY#*`
- control `GetItem` with `USER#*` or `CURSOR#*`
- control `PutItem` only with `CURSOR#*`
- device-binding GSI `Query` with `USER#*#ACTIVE`
- `secretsmanager:GetSecretValue` only for `HISTORY_CURSOR_SECRET_NAME`

`history_mutation_api` needs:

- control `GetItem` with `USER#*`
- device-binding GSI `Query` with `USER#*#ACTIVE`
- `dynamodb:TransactWriteItems` on History content/control and the analysis
  abuse table; transaction leading keys are `USER#*` and
  `ANALYSIS#REQUEST#*`

`history_lifecycle` needs:

- `Query` on both base tables and their exact indexes
- control `GetItem`, `UpdateItem`, `DeleteItem`
- content `DeleteItem`
- base-table leading keys `USER#*` and `CURSOR#*`
- index leading keys `HISTORY#*`, `CONTROL#*`, and `PENDING#*`

No artifact requires SQS, SNS, DynamoDB Streams, S3 data access, or campaign
table access.

## Metrics and alarms

The lifecycle artifact emits Embedded Metric Format values under
`AMT/TrustCheckRadar/History`, dimensioned only by bounded `Environment`:

- `ExpiredContentRecords`
- `ExpiredControlRecords`
- `CompletedErasureJobs`
- `OverdueErasureJobs`
- `ObservedPendingCompletions`
- `StuckPendingCompletions`
- `OldestPendingCompletionAgeSeconds`
- `OldestPendingErasureAgeSeconds`
- `LifecycleWorksetTruncated`
- `LifecycleSweepSuccess`
- `LifecycleSweepFailure`
- `HistoryReadSuccess`, `HistoryReadFailure`
- `HistoryMutationSuccess`, `HistoryMutationFailure`
- `HistoryAuthorizationFailure`

Count metrics use `Count`; age metrics use `Seconds`. They are emitted once per
lifecycle invocation. The infrastructure schedule should run every five minutes.
`LifecycleSweepSuccess=1` is the success heartbeat, including a successful empty
check. A missing heartbeat is missing data, not zero backlog, and its alarm must
treat missing data as breaching after two expected periods. Age values are zero
only after a successful non-truncated empty check. When the approved work cap is
reached, `LifecycleWorksetTruncated=1`; an unobserved age is omitted instead of
reported as zero.

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

- final numeric control/dedup/tombstone and backup/PITR decisions
- final route, paging, cursor, response, and error schemas
- stable badge IDs, localization keys, and qualification definition
- decision whether identical content under a new request ID counts
- account-state bootstrap and `SECUR4ALL-200` deletion-fence/backup-erasure hook
- authenticated Dev integration and 24-hour erasure evidence
