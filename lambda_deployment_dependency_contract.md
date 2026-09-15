# Lambda Deployment Dependency Contract

This document captures the deployment dependency contract for the Lambda functions currently used from the infrastructure repo.

## Campaign Cluster Aggregator

- Lambda path: `src/campaign_cluster_aggregator/`
- Artifact: `campaign_cluster_aggregator.zip`
- Handler: `app.lambda_handler`
- Invocation: campaign cluster SQS queue with partial batch responses
- Required configuration: `APP_ENVIRONMENT`, `CAMPAIGN_SCHEMA_VERSION`,
  `PIPELINE_TABLE_NAME`, `TRANSIENT_RETENTION_DAYS`,
  `MIN_CONTRIBUTOR_COUNT`, and `MAX_CONTRIBUTOR_SUBMISSIONS`
- Access: read/write `CampaignPipeline` and query `CandidateBucketIndex`
- Queue messages contain only the approved five-field opaque envelope.
- Similarity weights are semantic 45%, lexical 25%, tactics/taxonomy 20%, and
  bounded indicators 10%; category conflicts cannot match.
- Scores at or above 0.82 may match. Lower scores create a separate candidate;
  publication is never automatic.
- Each contributor contributes one centroid vector and at most three counted
  submissions per candidate and fixed 14-day period.
- Candidate, contribution, and event dedupe records expire within 21 days.

---

## Campaign Lifecycle and Deletion Bridge

- Artifacts: `campaign_lifecycle.zip` and `campaign_deletion_bridge.zip`
- Handlers: `app.lambda_handler`
- The lifecycle handler accepts only the scheduler operations `manage_keys`,
  `finalize_periods`, and `expire_transient` for its immutable environment.
- Period HMAC key ARNs are registered at `PK=PERIOD#<periodId>, SK=HMAC_KEY`.
  Publisher and deletion workers read that registry; no KMS alias permission is
  required.
- Keys are created as `HMAC_256`/`GENERATE_VERIFY_MAC`, tagged by project,
  environment, purpose, and period, disabled after the seven-day recovery window,
  and scheduled for deletion with the seven-day minimum.
- Threshold finalization recomputes counts from contribution records, suppresses
  cohorts below 10, persists only a non-linkable aggregate for review, and removes
  the transient candidate ledger.
- The deletion bridge derives only current/recovery-period tokens, writes a
  tombstone before deleting GSI-matched records, and recomputes affected centroids
  and counts from remaining contributions.
- Campaign withdrawal commands remain `PENDING` until deletion succeeds. The
  worker then atomically changes the matching participation epoch to `withdrawn`,
  appends a 400-day completion receipt, and marks the ledger command `COMPLETE`.
  `COMPLETE` stream modifications are ignored to prevent loops.
- The deletion worker additionally requires `USERS_TABLE_NAME`,
  `DELETION_LEDGER_TABLE_NAME`, `PARTICIPATION_ITEM_SK=CAMPAIGN_PARTICIPATION`,
  and `PARTICIPATION_AUDIT_DAYS=400` with users-table Get/Put/Update and ledger
  UpdateItem permissions.
- `expire_transient` fails explicitly until `CampaignPipeline` exposes an expiry
  query access pattern. DynamoDB TTL remains a safety net but is not represented as
  evidence of deadline-bound explicit deletion.

---

## Campaign Participation

- Lambda path: `src/campaign_participation/`
- Artifact: `campaign_participation.zip`; handler: `app.lambda_handler`
- Routes: authenticated HTTP API v2 `GET` and `PUT`
  `/v1/users/campaign-participation`
- Identity is exclusively the Cognito JWT `claims.sub`; body identity, legacy
  claims, and principal IDs are not trusted.
- Required configuration: `USERS_TABLE_NAME` or `USERS_TABLE_ARN`,
  `ENTITLEMENTS_TABLE_NAME` or `ENTITLEMENTS_TABLE_ARN`,
  `DELETION_LEDGER_TABLE_NAME` or `DELETION_LEDGER_TABLE_ARN`, `ENVIRONMENT`,
  `CAMPAIGN_PARTICIPATION_NOTICE_VERSION`,
  `CAMPAIGN_PARTICIPATION_POLICY_VERSION=policy-1`,
  `CAMPAIGN_PARTICIPATION_AUDIT_RETENTION_DAYS=400`,
  `CAMPAIGN_PARTICIPATION_DELETION_SLA_HOURS=24`,
  `FREE_MONTHLY_SCAN_LIMIT=10`,
  `PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT=15`, and `PRO_MONTHLY_SCAN_LIMIT`.
- `PUT` accepts exactly `schemaVersion=1`, `action=join|withdraw`, the configured
  notice version, and a canonical UUIDv4 `operationId`.
- Current state is `PK=USER#<sub>, SK=CAMPAIGN_PARTICIPATION`. Accepted
  transitions append a receipt at
  `SK=CAMPAIGN_CONSENT#<consentEpochId>#<timestamp>#<operationId>` with a 400-day
  TTL. A transactionally written `SK=CAMPAIGN_OPERATION#<operationId>` record
  makes retries a single strongly consistent `GetItem` rather than a filtered
  receipt query. Re-enrollment always creates a new consent epoch.
- Join/withdraw quota changes preserve usage as
  `used=max(0,oldLimit-remaining)` and `remaining=max(0,newLimit-used)`; free
  participation is 15 total scans, base free is 10, and Pro is unchanged.
- Withdrawal atomically writes the pending state, receipt, adjusted entitlement,
  and ledger command. The command key is `PK=ACCOUNT#<sub>`,
  `SK=CAMPAIGN_WITHDRAWAL#<operationId>` and its deletion deadline is exactly 24
  hours after occurrence.
- GET, PUT, and idempotent replay strongly check the authoritative active profile
  and fixed account-deletion fence. Every PUT repeats both checks inside the same
  DynamoDB transaction, so a concurrent deletion fence wins without recreating
  participation, consent-operation, or entitlement state.
- IAM requires `dynamodb:GetItem` and `dynamodb:PutItem` on the users and
  entitlements tables, `dynamodb:GetItem` on the deletion ledger, and transaction
  `ConditionCheckItem` access to exact `USER#*/PROFILE` and
  `ACCOUNT#*/ACCOUNT_DELETION` keys. Withdrawal additionally needs ledger
  `PutItem`. IAM policies may constrain transactional actions with
  `dynamodb:EnclosingOperation=TransactWriteItems`.

---

## Campaign Review

- Artifact: `campaign_review.zip`; handler: `app.lambda_handler`.
- Requires `APP_ENVIRONMENT`, `CAMPAIGN_SCHEMA_VERSION`,
  `INTELLIGENCE_TABLE_NAME`, `REVIEWER_GROUP`, and
  `MIN_CONTRIBUTOR_COUNT=10`.
- Accepts only the configured Cognito reviewer group and performs conditional,
  versioned transitions from `PENDING_REVIEW` to `CONFIRMED`, from `CONFIRMED` to
  `PUBLISHED`, and from review/confirmed/published states to suppression where
  allowed.
- Publication creates the sparse `STATE#PUBLISHED` index keys; emergency
  suppression removes them in the same conditional update.
- Every state change and standardized reason code is written with a conditional
  immutable audit record in the same DynamoDB transaction. Reviewer identity and
  free-form reason text are not persisted.
- Merge and split fail closed until an overlap-safe aggregate contract exists.

---

## Campaign Trends

- Artifact: `campaign_trends.zip`; handler: `app.lambda_handler`.
- Requires `APP_ENVIRONMENT`, `CAMPAIGN_SCHEMA_VERSION`,
  `INTELLIGENCE_TABLE_NAME`, `PUBLICATION_INDEX_NAME`,
  `MIN_CONTRIBUTOR_COUNT=10`, `MAXIMUM_PAGE_SIZE`, and
  `PAGINATION_TOKEN_TTL_SECS`. `PAGINATION_TOKEN_SECRET` is also required and
  must contain at least 32 UTF-8 bytes supplied through protected configuration;
  it must not be committed or shared across environments.
- Queries only `GSI1PK=STATE#PUBLISHED` through `PublicationIndex`; it has no
  transient or identity-table dependency.
- Returns count bands rather than exact counts and suppresses any item whose
  contributor or submission band is missing or invalid.
- Supports English/Spanish labels, safe unknown taxonomy IDs, category/tactic/
  channel/risk/language/trend/week filters, descending periods, bounded page
  sizes, and environment-bound expiring HMAC-authenticated pagination tokens.
- Language, tactic, and channel values are filterable only when
  `dimensionSchemaVersion=1` proves lifecycle thresholding. Legacy aggregates
  fail closed to empty dimension lists.

---

## Campaign Observation Publisher

- Lambda path: `src/campaign_observation_publisher/`
- Artifact: `campaign_observation_publisher.zip`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible, ARM64
- Invocation mode: `INSERT` records from the campaign outbox DynamoDB stream

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `APP_ENVIRONMENT` | Yes | `dev` | Cross-environment validation and key selection cannot run |
| `CAMPAIGN_SCHEMA_VERSION` | Yes | `1` | Version validation cannot run; V1 is the only supported value |
| `PIPELINE_TABLE_NAME` | Yes | `trustcheckradar-dev-campaign-pipeline` | Transient app features and dedupe state cannot be written |
| `USERS_TABLE_NAME` | Yes | `trustcheckradar-dev-users` | Withdrawal and consent-epoch state cannot be condition-checked; publication fails closed |
| `DELETION_LEDGER_TABLE_NAME` | Yes | `trustcheckradar-dev-deletion-ledger` | A delayed outbox record cannot be fenced against account deletion |
| `CLUSTER_QUEUE_URL` | Yes | `https://sqs.us-east-1.amazonaws.com/...` | Opaque clustering requests cannot be published |
| `CONTRIBUTOR_PERIOD_DAYS` | No | `14` | Defaults to 14 and rejects any other value |
| `TRANSIENT_RETENTION_DAYS` | No | `21` | Defaults to 21 and rejects values above the approved maximum |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Needs |
|---|---|
| Campaign outbox stream | `dynamodb:DescribeStream`, `dynamodb:GetRecords`, `dynamodb:GetShardIterator`, `dynamodb:ListStreams` |
| Campaign pipeline table | standalone `dynamodb:GetItem`; `dynamodb:PutItem` and `dynamodb:UpdateItem` only with `dynamodb:EnclosingOperation=TransactWriteItems`; no `DeleteItem` |
| Users participation item | `dynamodb:GetItem`, plus `dynamodb:ConditionCheckItem` constrained to `dynamodb:EnclosingOperation=TransactWriteItems` |
| Deletion-ledger account fence | `dynamodb:GetItem`, plus `dynamodb:ConditionCheckItem` constrained to `dynamodb:EnclosingOperation=TransactWriteItems` |
| Current-period KMS HMAC key | `kms:GenerateMac` with `HMAC_SHA_256` |
| Clustering queue | `sqs:SendMessage` |
| Campaign metrics namespace | `cloudwatch:PutMetricData` |

### Privacy and message contract

- The outbox input is strict, versioned, environment-bound, and requires an explicit `campaignConsentGranted` boolean plus the server-authorized `consentEpochId` and `noticeVersion` provenance.
- An opted-in outbox item contains the exact V1 `appFeatures` object:
  `schemaVersion`, `extractorVersion`, `languageId`, `taxonomyBucket`, `vector`,
  `lexicalFingerprint`, `signalIds`, `indicatorIds`, and `confidence`.
- The shared contract caps canonical compact JSON at 32 KiB; vectors at 384
  finite values in `[-1,1]`; fingerprints at 32 unique lowercase 16-character
  hex values; signal/indicator lists at 16 unique stable IDs each; and confidence
  at `[0,1]`. Booleans are not numeric values.
- Declined consent, withdrawn/stale server participation, and expired
  observations are successful no-ops. The publisher re-reads the authoritative
  participation record and fixed deletion fence, and condition-checks the same
  consent epoch plus absence of `ACCOUNT#<sub>/ACCOUNT_DELETION` in its pipeline
  transaction. Withdrawal or account deletion therefore wins over an already
  queued outbox record. A final pre-send authority read suppresses pending retries.
  After sending, the `PENDING` to `PUBLISHED` status update is another transaction
  with the same participation and deletion-fence checks plus exact existing-target
  conditions. A post-send deletion race is reported as suppressed and cannot write
  pipeline status after the fence; downstream contributor tombstones remain the
  queue-race defense.
- The account identifier is used only as input to `GenerateMac`; it is not written to the pipeline table, queue, logs, metrics, or handler response.
- Contributor tokens use `HMAC(period-key, b"campaign-contributor:v1\\0" + account-id)` and are scoped to fixed 14-day UTC periods.
- The publisher resolves the enabled KMS key ARN from the lifecycle-owned
  `PK=PERIOD#<periodId>, SK=HMAC_KEY` registry item.
- App-provided features are revalidated, stored without source text or account
  identity, and expire within 21 days; dedupe state uses the same maximum.
- Feature extraction is app-only. No Lambda or other server runtime loads an
  extractor model; the publisher sends `campaign.cluster.requested` directly to
  `CLUSTER_QUEUE_URL`.
- Clustering queue messages contain exactly `schemaVersion`, `eventType`, `environment`, `statisticsEventId`, and `recordVersion`.
- Standard-queue delivery is at least once. A `PENDING`/`PUBLISHED` dedupe record prevents completed replays while permitting recovery after a write-before-send failure.

The authoritative completed-analysis/outbox schema remains subject to the external
campaign contract approval. Its strict parser is isolated so approved field changes
can be aligned without weakening the privacy boundary.

---

## Age Attestation

- Lambda path: `src/age_attestation/app.py`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation modes:
  - API/event invocation for age attestation updates
  - Cognito-trigger style invocation is also supported by the code path

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `USERS_TABLE_NAME` | Yes, unless `TABLE_NAME` is set | `trustcheckradar-dev-users` | DynamoDB profile updates cannot run |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-users` | Same as above if `USERS_TABLE_NAME` is not provided |
| `DELETION_LEDGER_TABLE_NAME` | Yes | `trustcheckradar-dev-deletion-ledger` | The profile writer fails closed because it cannot prove that account deletion has not started |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB users table | `USERS_TABLE_NAME` or `TABLE_NAME` | `dynamodb:UpdateItem` constrained to `dynamodb:EnclosingOperation=TransactWriteItems` | Name required by code |
| DynamoDB deletion ledger | `DELETION_LEDGER_TABLE_NAME` | `dynamodb:ConditionCheckItem` constrained to `dynamodb:EnclosingOperation=TransactWriteItems` | Name required by code |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: users table from `USERS_TABLE_NAME` / `TABLE_NAME`
- Primary key shape:
  - `PK = USER#<sub>`
  - `SK = PROFILE`
- Item family updated:
  - user profile item
- Attributes updated:
  - `ageVerified`
  - `ageVerifiedAt`
  - `agePolicyVersion`
  - `updatedAt`
  - `status`
- GSI usage: none
- TTL fields: none

### Runtime assumptions

- Expects authenticated caller context to resolve the Cognito `sub` for API mode.
- Writes only to an existing `ACTIVE` or `PENDING_AGE_GATE` profile whose `sub`
  matches the authenticated account. The profile update and absence of the fixed
  account-deletion fence are one DynamoDB transaction, so age attestation cannot
  reactivate a deleting account.
- Route expectation: POST-style API invocation if exposed through API Gateway.

### Compatibility aliases

- Table variable aliases accepted:
  - `USERS_TABLE_NAME`
  - `TABLE_NAME`
- Code does not require an ARN.

---

## Post Confirmation

- Lambda path: `src/post_confirmation/app.py`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: Cognito Post Confirmation trigger

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `USERS_TABLE_NAME` | Preferred | `trustcheckradar-dev-users` | New profile record cannot be created if no table identifier is provided |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-users` | Same as above if `USERS_TABLE_NAME` is not provided |
| `USERS_TABLE_ARN` | Infrastructure fallback | `arn:aws:dynamodb:us-east-1:123456789012:table/trustcheckradar-dev-users` | The table name is derived from the ARN when neither name variable is present |
| `DELETION_LEDGER_TABLE_NAME` | Preferred | `trustcheckradar-dev-deletion-ledger` | New profile creation fails closed because it cannot prove that a prior/finalized account deletion is absent |
| `DELETION_LEDGER_TABLE_ARN` | Infrastructure fallback | `arn:aws:dynamodb:us-east-1:123456789012:table/trustcheckradar-dev-deletion-ledger` | The ledger name is derived from the ARN when the name is absent |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB users table | `USERS_TABLE_NAME`, `TABLE_NAME`, or `USERS_TABLE_ARN` | `dynamodb:PutItem` constrained to `dynamodb:EnclosingOperation=TransactWriteItems` | Name or ARN required by code |
| DynamoDB deletion ledger | `DELETION_LEDGER_TABLE_NAME` or `DELETION_LEDGER_TABLE_ARN` | `dynamodb:ConditionCheckItem` constrained to `dynamodb:EnclosingOperation=TransactWriteItems` | Name or ARN required by code |
| Cognito user pool trigger event | direct event payload | invoke permission only | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: users table from `USERS_TABLE_NAME` / `TABLE_NAME`, or derived from `USERS_TABLE_ARN`
- Primary key shape:
  - `PK = USER#<sub>`
  - `SK = PROFILE`
- Item family written:
  - user profile item
- Notable attributes:
  - `sub`
  - `email`
  - `given_name`
  - `family_name`
  - `phone_number`
  - `over_18`
  - `status`
  - `ageVerified`
  - `ageVerifiedAt`
  - `agePolicyVersion`
  - `createdAt`
  - `updatedAt`
- GSI usage: none
- TTL fields: none

### Runtime assumptions

- Triggered by Cognito after signup confirmation.
- Uses one DynamoDB transaction to require absence of the fixed account-deletion
  fence and conditionally create the profile. This prevents a delayed/replayed
  Cognito trigger from recreating a profile for a deleted subject.

### Compatibility aliases

- Table variable aliases accepted:
  - `USERS_TABLE_NAME`
  - `TABLE_NAME`
  - `USERS_TABLE_ARN`
- Deletion-ledger identifiers accepted:
  - `DELETION_LEDGER_TABLE_NAME`
  - `DELETION_LEDGER_TABLE_ARN`
- The ARN fallback matches the identity-workflows Terraform stack contract.

---

## Device Registration

- Lambda path: `src/device_registration/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route, effectively POST `/device-registration`

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `DEVICE_BINDINGS_TABLE_NAME` | Yes, unless `TABLE_NAME` is set | `trustcheckradar-dev-device-bindings` | Device binding records cannot be read or written |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-device-bindings` | Same as above if `DEVICE_BINDINGS_TABLE_NAME` is not provided |
| `DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS` | No | `180` | Defaults are used for inactive cleanup horizon |
| `USERS_TABLE_NAME` | Yes | `trustcheckradar-dev-users` | Authoritative account checks cannot run |
| `DELETION_LEDGER_TABLE_NAME` | Yes | `trustcheckradar-dev-deletion-ledger` | Deletion-fence checks cannot run |
| `COGNITO_ISSUER` | Yes | Cognito issuer URL | Access-token issuer cannot be bound |
| `COGNITO_APP_CLIENT_ID` | Yes | app client id | Access-token client cannot be bound |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB device bindings table | `DEVICE_BINDINGS_TABLE_NAME` or `TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:Query` | Name required by code |
| DynamoDB users table | `USERS_TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:ConditionCheckItem` | Name required by code |
| DynamoDB deletion ledger | `DELETION_LEDGER_TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:ConditionCheckItem` | Name required by code |
| API Gateway JWT authorizer / Cognito identity | request context | invoke + authorizer context | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: device bindings table
- Primary key shape:
  - `PK = USER#<sub>`
  - `SK = DEVICE#<bindingFingerprint>`
  - authoritative pointer: `SK = ACTIVE_BINDING`
- GSI usage:
  - `GSI1PK`
  - `GSI1SK`
  - active lookup pattern uses `USER#<sub>#ACTIVE`
- TTL field:
  - `expiresAt` for inactive binding cleanup
- Item family written/read:
  - device binding items with fields such as:
    - `accountId`
    - `bindingFingerprint`
    - `platform`
    - `osVersion`
    - `status`
    - `firstSeenAt`
    - `lastSeenAt`
    - `deactivatedAt`

### Runtime assumptions

- Caller must have a verified Cognito access token; legacy/principal fallbacks
  are rejected and the token `sub` is the only account target.
- Profile and deletion-fence conditions are repeated inside the device write transaction.
- Request is expected to contain a stable device binding fingerprint generated by the client.
- Decision outcomes supported:
  - `NEW`
  - `KNOWN`
  - `SWITCH`

### Compatibility aliases

- Table variable aliases accepted:
  - `DEVICE_BINDINGS_TABLE_NAME`
  - `TABLE_NAME`
- Code does not require an ARN.

---

## Device Recovery

- Lambda path: `src/device_recovery/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `DEVICE_BINDINGS_TABLE_NAME` | Yes, unless `TABLE_NAME` is set | `trustcheckradar-dev-device-bindings` | Recovery logic cannot read or update bindings |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-device-bindings` | Same as above if `DEVICE_BINDINGS_TABLE_NAME` is not provided |
| `DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS` | No | `180` | Defaults are used for inactive retention handling |
| `DEVICE_RECOVERY_CONTROL_TABLE_NAME` | Consumer route | `trustcheckradar-dev-device-recovery-control` | Idempotency, rate, and audit state cannot be stored |
| `USERS_TABLE_NAME` / `DELETION_LEDGER_TABLE_NAME` | Yes | environment tables | Authority conditions cannot run |
| `COGNITO_ISSUER` / `COGNITO_APP_CLIENT_ID` | Consumer route | Cognito values | Consumer token cannot be bound |
| `DEVICE_SELF_RECOVERY_ENABLED` | Yes | `false` | Consumer route remains disabled by default |
| `DEVICE_RECOVERY_POLICY_STATUS` | Yes | `pending` | Must remain pending until approved |
| `DEVICE_RECOVERY_AUDIT_RETENTION_DAYS` | Consumer route | `90` | Any other value fails closed under the approved Dev policy |
| `DEVICE_RECOVERY_ALLOWED_PRINCIPAL_ARNS_JSON` | Operator route | JSON ARN array | AWS_IAM operator route fails closed |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB device bindings table | `DEVICE_BINDINGS_TABLE_NAME` or `TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:ConditionCheckItem`, `dynamodb:Query` | Name required by code |
| Recovery control table | `DEVICE_RECOVERY_CONTROL_TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem` | Name required by code |
| Users / deletion ledger | explicit names | `dynamodb:GetItem`, `dynamodb:ConditionCheckItem` | Names required by code |
| API Gateway | request context | AWS_IAM for `/device-recovery`; JWT for `/v1/users/device-recovery` | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: device bindings table
- Primary key shape:
  - `PK = USER#<sub>`
  - `SK = DEVICE#<bindingFingerprint>`
  - pointer `SK = ACTIVE_BINDING`
- GSI usage:
  - `GSI1PK`
  - `GSI1SK`
  - active binding lookup
- TTL field:
  - `expiresAt` on inactive records
- Item families:
  - reads existing device binding items
  - writes reactivated/deactivated binding state

### Runtime assumptions

- Operator recovery requires an exact allowlisted IAM `userArn`.
- Consumer recovery requires a recent, verified token and exact UUIDv4 request.
- All writes are atomic with pointer, profile, and deletion-fence conditions.
- Consumer replay performs strongly consistent profile/fixed-fence reads before
  returning an existing recovery receipt; a pre-issued token cannot replay device
  details after account deletion begins. The write transaction repeats both checks
  to close the read/write race.
- The consumer route stays disabled until the five compatible artifacts are pinned to one release.
- Dev recovery records use distinct approved bounds: retry receipts seven days,
  rate state 24 hours, security audit 90 days, and table PITR seven days once the
  currently absent recovery-control table is provisioned.

### Compatibility aliases

- Table variable aliases accepted:
  - `DEVICE_BINDINGS_TABLE_NAME`
  - `TABLE_NAME`
- Code does not require an ARN.

---

## Account Data API and account-deletion producer

- Artifact: `account_data_api.zip`; handler: `app.lambda_handler`.
- Disabled HTTP routes: `POST /v1/users/account-deletion` and
  `GET /v1/users/account-deletion`.
- Optional retry sources: deletion-ledger DynamoDB stream with
  `ReportBatchItemFailures` for session revocation, bounded device cleanup and
  bounded device-recovery control cleanup/minimization, bounded
  analysis-abuse cleanup/minimization, bounded campaign-outbox locator cleanup,
  and policy/prerequisite-gated profile/current-consent cleanup,
  plus the exact scheduled input
  `{"schemaVersion":1,"operation":"reconcile-session-revocation"}`.
  Failed item identifiers are DynamoDB sequence numbers; configuration and
  setup failures propagate for whole-batch retry instead of returning HTTP envelopes.
- POST accepts exactly schema 1, `DELETE_ACCOUNT`, and a canonical UUIDv4
  `operationId`. The target is only the verified Cognito access-token `sub` and
  signed `auth_time` must be no older than 300 seconds.
- The producer transaction changes `USER#<sub>/PROFILE` from `ACTIVE` to
  `DELETION_REQUESTED` and creates the fixed
  `ACCOUNT#<sub>/ACCOUNT_DELETION` fence. The profile condition requires
  matching `sub` and `ageVerified=true`.
- The fence contains exact schema/record/environment/event/account/operation,
  `status=REQUESTED`, `occurredAtEpoch`, and the 24-hour `deleteByEpoch`.
- Component receipts use `ACCOUNT_DELETION#<COMPONENT>` and are counted only
  when their operation ID, request timestamp and exact 120-day
  `retainUntilEpoch` match the fence/completion contract. The ledger TTL remains
  disabled; this field is controlled-retirement metadata only.
- Cognito session revocation occurs only after the durable fence exists.
  Direct failure never rolls the fence back; stream and bounded scheduled
  reconciliation retry it beyond stream retention.
- GET is a best-effort status route while an access token remains accepted. It
  deliberately does not require an active profile and makes no post-expiry
  availability promise.
- `completionEligible=true` means only that the configured component receipts
  exist. This Lambda never marks the product account deleted or removes the
  authoritative fence.

Required environment:

- `APP_ENVIRONMENT`, `USERS_TABLE_NAME`, `DELETION_LEDGER_TABLE_NAME`,
  `DEVICE_BINDINGS_TABLE_NAME`, `DEVICE_RECOVERY_CONTROL_TABLE_NAME`,
  `ANALYSIS_ABUSE_TABLE_NAME`, `CAMPAIGN_OUTBOX_TABLE_NAME`
- `COGNITO_ISSUER`, `COGNITO_APP_CLIENT_ID`, `COGNITO_USER_POOL_ID`
- `ACCOUNT_DELETION_ENABLED=false` by default
- `ACCOUNT_DELETION_POLICY_STATUS=pending` by default
- `ACCOUNT_DATA_INVENTORY_STATUS=pending` by default
- `ACCOUNT_DELETION_COMPLETION_STATUS=incomplete` by default
- `COGNITO_USERNAME_IS_SUB=false` by default
- `ACCOUNT_DELETION_REQUIRED_COMPONENTS_JSON`; once approved it must include
  at least `SESSION_REVOCATION`, `DEVICE_BINDINGS`, `DEVICE_RECOVERY`, `HISTORY`,
  `ANALYSIS_ABUSE`, `CAMPAIGN`, `CAMPAIGN_OUTBOX`, `ENTITLEMENTS`,
  `USER_PROFILE`, and `IDENTITY`
- exact policy constants: reauthentication 300 seconds, deletion SLA 24 hours,
  device/recovery/analysis-abuse/outbox deletion page sizes 100, recovery receipt 7
  days, recovery audit 90 days, recovery rate state 86400 seconds, History dedupe
  120 days, and account component receipt 120 days; reconciliation defaults 100
  items and ten pages
- `ANALYSIS_REQUEST_ID_TTL_SECONDS=900`, matching the owner-approved ordinary
  conversation-analysis `REQUEST_ID_TTL_SECONDS`; the older 86400-second
  deployment example is not an approved value
- `ANALYSIS_REQUEST_DEDUPE_POLICY_STATUS=pending` and
  `ANALYSIS_LEGACY_REQUEST_RETENTION_POLICY_STATUS=pending` and
  `ANALYSIS_CONSUMPTION_DELETION_POLICY_STATUS=pending` by default; all three must be
  explicitly set to `approved` in a coordinated activation before an
  `ANALYSIS_ABUSE` receipt. The decisions themselves are owner-approved; pending
  defaults prevent an artifact-only change from activating destructive behavior
- `ACCOUNT_DELETION_CAMPAIGN_OUTBOX_PAGE_SIZE=100`;
  `CAMPAIGN_OUTBOX_LOCATOR_COVERAGE_STATUS=pending` by default and must remain
  pending until legacy/backfill coverage is proven. An empty account-locator query
  alone is not proof that legacy event-keyed content is absent.
- `USER_PROFILE_DELETION_POLICY_STATUS=pending` by default. Even when approved,
  the worker refuses to query/delete profile state until exact receipts exist for
  session, device, recovery, History, analysis, campaign, outbox, and entitlements.

These false/pending/incomplete decisions are independent activation gates.
They must not be changed merely because the artifact exists. In particular,
`COGNITO_USERNAME_IS_SUB` requires proof from the deployed user-pool contract.

IAM:

- users table `dynamodb:UpdateItem` for the profile transaction
- deletion ledger `dynamodb:GetItem`, `dynamodb:PutItem`, and `dynamodb:Scan`
- deletion ledger `dynamodb:DeleteItem` for device-progress cleanup
- device bindings table `dynamodb:Query` and `dynamodb:DeleteItem`
- device recovery control table `dynamodb:Query`, `dynamodb:PutItem`, and
  `dynamodb:DeleteItem`, scoped to `USER#*`; `PutItem` replaces a source row with
  its exact minimal allowlist under operation/expiry conditions
- analysis-abuse table `dynamodb:Query`, `dynamodb:PutItem`, and
  `dynamodb:DeleteItem`, scoped to the four deterministic
  `ANALYSIS#REQUEST|RATE|SCAN_RATE|CONSUMPTION#<sha256(sub)>` partitions;
  request `PutItem` replaces source rows with the exact content-free allowlist
- campaign outbox table `dynamodb:Query` on `ACCOUNT#*`, `dynamodb:GetItem` on
  `EVENT#*`, and conditional `dynamodb:DeleteItem` on both key families. No scan,
  put, update, or index access is needed by account-data cleanup.
- users table `dynamodb:Query` on `USER#*` and conditional `DeleteItem` for
  `PROFILE`, `CAMPAIGN_PARTICIPATION`, and `CAMPAIGN_OPERATION#*` only when the
  profile policy and all prerequisites are approved. Exact 400-day
  `CAMPAIGN_CONSENT#*` audit rows are validated and preserved.
- `cognito-idp:AdminUserGlobalSignOut` on the configured user pool
- standard deletion-ledger stream read actions on the event-source role

Transactional IAM is granted through underlying item actions and may use
`dynamodb:EnclosingOperation=TransactWriteItems`. `dynamodb:LeadingKeys` scopes
partition keys only; the application enforces exact sort-key families.

Metrics use `AMT/TrustCheckRadar/AccountData`. HTTP/stream counters have bounded
Environment/Operation dimensions. Reconciliation reports success, scanned,
matched, revoked, already-complete, device and recovery records deleted, recovery
records minimized, analysis-abuse and campaign-outbox records deleted/minimized,
completed device/recovery/analysis-abuse/outbox/profile components, truncation,
full-pass completion, and full-pass age, plus analysis/outbox/profile
policy-blocked counts; unknown
full-pass age is omitted. Reconciliation failures emit a separate counter and
then propagate to the scheduler.

Activation remains blocked by the unverified complete inventory, final identity
deletion, any additional inventory components, overall
finalization, and fence-retention policy. Full-account export is also blocked;
paginated JSON is preferred, but the complete inventory and protected delivery
contract are not yet proven.

Conversation-analysis request creation, lease takeover, result storage, scan-rate
updates, request-rate updates, and the atomic result/consumption commit all include
the active-profile and absent-deletion-ledger conditions in the same DynamoDB
transaction. Destructive cleanup may therefore finish a partition only after the
fixed fence exists, without a late writer recreating it. `release_request` can only
delete or conditionally downgrade an existing processing row; after minimization
its condition cannot match.

The analysis-abuse worker stops before CONSUMPTION and cannot issue its component
receipt while any explicit policy status is pending. With those activation guards
approved, it deletes CONSUMPTION completely. REQUEST cleanup uses an exact
content-free allowlist: ordinary and longer-lived non-History legacy rows expire no
later than the original deletion request plus 900 seconds, while an earlier expiry
is never extended. History `COMPLETED_ERASED` rows within the separately approved
deletion-anchored 120-day ceiling retain that expiry. History account cleanup writes
the same shape and anchor, so retries cannot extend retention and either worker
order removes response, authorization, and event content.

Compatibility gate: exact component validators reject legacy receipts that lack
`retainUntilEpoch`, while conditional receipt creation cannot replace them. Before
the paired Lambda release is activated, infrastructure/operations must perform a
read-only inventory of component receipt keys. Activation requires either proof
that none exist or a separately approved migration contract. These Lambdas do not
mutate live legacy receipts.

---

## Purchase Handoff

- Lambda path: `src/purchase_handoff/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route, effectively POST `/purchase-handoff`

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `ENTITLEMENTS_TABLE_NAME` | Yes, unless `TABLE_NAME` is set | `trustcheckradar-dev-purchase-entitlements` | Entitlement persistence fails |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-purchase-entitlements` | Same as above if `ENTITLEMENTS_TABLE_NAME` is not provided |
| `USERS_TABLE_NAME` | Yes | `trustcheckradar-dev-users` | Active profile authority cannot be proven and purchase handoff fails closed |
| `DELETION_LEDGER_TABLE_NAME` | Yes | `trustcheckradar-dev-deletion-ledger` | Fixed account-deletion fencing cannot be proven and purchase handoff fails closed |
| `PURCHASE_VERIFICATION_MODE` | No, but required for live verification behavior | `google_play` | If set to `stub`, Google Play is not called |
| `GOOGLE_PLAY_SECRET_NAME` | Required for live Google Play verification | `trustcheckradar/dev/google-play-service-account` | Live verification cannot fetch service account credentials |
| `GOOGLE_PLAY_PACKAGE_NAME` | Required for live Google Play verification | `com.andmorethings.trustcheckradar` | Android Publisher API calls cannot be scoped correctly |
| `GOOGLE_PLAY_PRO_PRODUCT_ID` | No | `trustcheck_radar_pro_monthly` | Defaults may be used, but product mapping may be wrong |
| `FREE_MONTHLY_SCAN_LIMIT` | No | `10` | Default free quota logic may be wrong if code assumes configured policy |
| `PRO_MONTHLY_SCAN_LIMIT` | No | `100` | Default pro quota logic may be wrong if code assumes configured policy |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB purchase entitlements table | `ENTITLEMENTS_TABLE_NAME` or `TABLE_NAME` | `dynamodb:GetItem`, transactional `dynamodb:PutItem` | Name required by code |
| DynamoDB users profile | `USERS_TABLE_NAME` | `dynamodb:GetItem`, transaction `dynamodb:ConditionCheckItem` on exact `USER#*/PROFILE` | Name required by code |
| DynamoDB deletion fence | `DELETION_LEDGER_TABLE_NAME` | `dynamodb:GetItem`, transaction `dynamodb:ConditionCheckItem` on exact `ACCOUNT#*/ACCOUNT_DELETION` | Name required by code |
| Secrets Manager Google Play service-account secret | `GOOGLE_PLAY_SECRET_NAME` | `secretsmanager:GetSecretValue` | Name required by code |
| Google Android Publisher API | runtime outbound call | outbound HTTPS | No AWS identifier |
| API Gateway JWT authorizer / Cognito identity | request context | invoke + authorizer context | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: purchase entitlements table
- Primary key shapes:
  - Entitlement item:
    - `PK = USER#<sub>`
    - `SK = ENTITLEMENT#google_play#trustcheck_radar_pro_monthly`
  - Idempotency item:
    - `PK = TOKEN#<sha256(purchaseToken)>`
    - `SK = IDEMPOTENCY`
- Compatibility read shape:
  - `SK = ENTITLEMENT`
- GSI usage: none in the code path described here
- TTL fields:
  - none required by the purchase handoff logic itself
- Item families:
  - entitlement state
  - purchase token idempotency records

### Runtime assumptions

- POST-style API request with authenticated Cognito user context.
- For live verification, the code expects a full Google service-account JSON stored in Secrets Manager.
- This Lambda is the verification point against Google Play; downstream entitlement consumers trust DynamoDB state.
- Accepted/rejected verification state is committed in one transaction with the
  active-profile and absent-deletion-fence checks. Replay reads are also blocked
  after the fence. This closes late writes but does not approve token retention,
  legacy token discovery, or an `ENTITLEMENTS` deletion receipt.

### Compatibility aliases

- Table variable aliases accepted:
  - `ENTITLEMENTS_TABLE_NAME`
  - `TABLE_NAME`
- Secret variable expected by code:
  - `GOOGLE_PLAY_SECRET_NAME`
- Important: code expects secret **name**, not secret ARN.

---

## Entitlement Snapshot

- Lambda path: `src/entitlement_snapshot/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route, effectively GET `/entitlements/snapshot`

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `ENTITLEMENTS_TABLE_NAME` | Yes, unless `USERS_TABLE_NAME` or `TABLE_NAME` is set | `trustcheckradar-dev-purchase-entitlements` | Snapshot cannot read entitlement state |
| `USERS_TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-purchase-entitlements` | Used only if `ENTITLEMENTS_TABLE_NAME` is not set |
| `TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-purchase-entitlements` | Used only if stronger names are not set |
| `ENTITLEMENT_PLATFORM` | No | `google_play` | Defaults are used for key resolution |
| `ENTITLEMENT_PRODUCT_ID` | No | `trustcheck_radar_pro_monthly` | Defaults are used for key resolution |
| `ENTITLEMENT_USAGE_PERIOD_MODE` | No | `billing_cycle` | Defaults are used in usage response logic |
| `FREE_MONTHLY_SCAN_LIMIT` | No | `10` | Free-tier remaining count may be incorrect |
| `PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT` | No | `15` | Enrolled free-tier remaining count may be incorrect |
| `PRO_MONTHLY_SCAN_LIMIT` | No | `100` | Pro-tier remaining count may be incorrect |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB purchase entitlements table | `ENTITLEMENTS_TABLE_NAME` or fallback aliases | `dynamodb:GetItem` | Name required by code |
| API Gateway JWT authorizer / Cognito identity | request context | invoke + authorizer context | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: purchase entitlements table
- Primary key shapes:
  - entitlement item:
    - `PK = USER#<sub>`
    - `SK = ENTITLEMENT#google_play#trustcheck_radar_pro_monthly`
  - usage item:
    - `PK = USER#<sub>`
    - `SK = USAGE#<periodKey>`
- Compatibility read shape:
  - `SK = ENTITLEMENT`
- GSI usage: none
- TTL fields: none required by snapshot read path
- Item families read:
  - entitlement item
  - usage counter item

### Runtime assumptions

- Read-only entitlement/status endpoint.
- Authenticated caller required.
- Returns current entitlement state, usage counters, and restore guidance.

### Compatibility aliases

- Table variable aliases accepted:
  - `ENTITLEMENTS_TABLE_NAME`
  - `USERS_TABLE_NAME`
  - `TABLE_NAME`
- Code does not require a table ARN.

---

## Conversation Analysis

- Lambda path: `src/conversation_analysis/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route, effectively POST analysis endpoint

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `ANALYSIS_ABUSE_TABLE_NAME` | Yes | `trustcheckradar-dev-analysis-abuse-control` | Request dedupe/rate controls fail closed |
| `DEVICE_BINDINGS_TABLE_NAME` | Yes | `trustcheckradar-dev-device-bindings` | Device binding validation fails and request is rejected/unavailable |
| `ENTITLEMENTS_TABLE_NAME` | Yes for entitlement enforcement, unless fallback aliases are set | `trustcheckradar-dev-purchase-entitlements` | Monthly/credit entitlement gating fails |
| `USERS_TABLE_NAME` | Yes for server-authoritative campaign publishing | `trustcheckradar-dev-users` | Participation state cannot be read or condition-checked during an outbox write |
| `DELETION_LEDGER_TABLE_NAME` | Yes | `trustcheckradar-dev-deletion-ledger` | Transaction-time account-deletion fencing fails closed |
| `RATE_LIMIT_WINDOW_SECONDS` | No | `60` | Default request throttling windows are used |
| `RATE_LIMIT_MAX_REQUESTS` | No | `10` | Default request throttling caps are used |
| `REQUEST_ID_TTL_SECONDS` | No; policy approval still required before account-deletion activation | `900` source default | Exact Dev value and longer-lived legacy-row treatment remain unapproved |
| `SCAN_RATE_LIMIT_WINDOW_SECONDS` | No | `3600` | Default scan abuse window is used |
| `SCAN_RATE_LIMIT_MAX_REQUESTS` | No | `20` | Default scan abuse cap is used |
| `FREE_MONTHLY_SCAN_LIMIT` | No | `10` | Free-tier quota math may be wrong |
| `PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT` | No | `15` | Server-authorized participating free quota may be wrong |
| `PRO_MONTHLY_SCAN_LIMIT` | No | `100` | Pro-tier quota math may be wrong |
| `APP_ENVIRONMENT` | Required when campaign publishing is configured | `dev` | Opted-in campaign outbox records cannot be environment-bound |
| `CAMPAIGN_OUTBOX_TABLE_NAME` | Required for opted-in campaign publishing | `trustcheckradar-dev-campaign-outbox` | Explicit opt-in fails closed instead of publishing |
| `CAMPAIGN_SCHEMA_VERSION` | No | `1` | Defaults to the V1 campaign record contract |
| `OBSERVATION_RETENTION_HOURS` | No | `72` | Campaign outbox records default to the maximum 72-hour retention |
| `OPENAI_SECRET_NAME` | Required for real model calls | `trustcheckradar/dev/openai` | OpenAI API key cannot be loaded |
| `OPENAI_SECRET_FIELD` | No | `apiKey` | Defaults are used when omitted |
| `OPENAI_RESPONSES_ENDPOINT` | No | `https://api.openai.com/v1/responses` | Defaults are used when omitted |
| `OPENAI_MODEL` | No | `gpt-4.1-mini` | Defaults are used when omitted |
| `OPENAI_TIMEOUT_SECONDS` | No | `20` | Defaults are used when omitted |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB abuse-control table | `ANALYSIS_ABUSE_TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:DeleteItem` | Exact name required by code |
| DynamoDB device bindings table | `DEVICE_BINDINGS_TABLE_NAME` | `dynamodb:Query` | Name required by code |
| DynamoDB entitlements table | `ENTITLEMENTS_TABLE_NAME` or fallback aliases | `dynamodb:GetItem`, `dynamodb:PutItem` | Name required by code |
| DynamoDB users participation item | `USERS_TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:ConditionCheckItem` constrained to `dynamodb:EnclosingOperation=TransactWriteItems` | Name required for server-authorized campaign publishing |
| DynamoDB deletion ledger | `DELETION_LEDGER_TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:ConditionCheckItem` constrained to `dynamodb:EnclosingOperation=TransactWriteItems` | Fixed account-deletion fence |
| Campaign outbox table | `CAMPAIGN_OUTBOX_TABLE_NAME` | `dynamodb:PutItem` through the existing completion transaction | Name required only for explicitly opted-in submissions |
| Secrets Manager OpenAI secret | `OPENAI_SECRET_NAME` | `secretsmanager:GetSecretValue` | Name required by code |
| OpenAI Responses API | runtime outbound call | outbound HTTPS | No AWS identifier |
| API Gateway JWT authorizer / Cognito identity | request context | invoke + authorizer context | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Abuse-control table item families:
  - request idempotency / dedupe:
    - `PK = ANALYSIS#REQUEST#<sha256(identity)>`
    - `SK = <requestId>`
    - Stores the random campaign `statisticsEventId` only while an opted-in result is awaiting atomic commit, allowing retries to reuse the same event identity.
  - request rate limiting:
    - `PK = ANALYSIS#RATE#<sha256(identity)>`
    - `SK = <windowStart>`
  - scan abuse cap:
    - `PK = ANALYSIS#SCAN_RATE#<sha256(accountId)>`
    - `SK = <windowStart>`
  - scan consumption idempotency:
    - `PK = ANALYSIS#CONSUMPTION#<sha256(accountId)>`
    - `SK = <requestId>`
- TTL fields:
  - `ttl`
  - `expiresAt`
- Entitlements table item families read/written:
  - entitlement item
  - usage/remaining scan metadata such as:
    - `remainingMonthlyScans`
    - `remainingCredits`
    - `lastScanAt`
    - `lastScanRequestId`
    - `lastScanConsumptionType`
- Device binding table usage:
  - query active binding by user

### Runtime assumptions

- Expects authenticated request.
- Every abuse-control write and result/consumption transaction condition-checks
  the active profile and absence of the fixed account-deletion fence.
- Expects `Authorization: Bearer <id-token-or-jwt>`.
- Expects device-binding header:
  - `X-Device-Binding-Fingerprint`
- Expects JSON payload for analysis request.
- This Lambda is linked to a real OpenAI call when `OPENAI_SECRET_NAME` is configured and verification mode is not stubbed away in code.

### Compatibility aliases

- Abuse storage requires the canonical `ANALYSIS_ABUSE_TABLE_NAME`; aliases are
  intentionally not accepted because the users/profile table is a distinct
  authority and must never become the abuse-store fallback.
- Entitlements table aliases accepted:
  - `ENTITLEMENTS_TABLE_NAME`
  - fallback aliases in shared entitlement config
- Device binding table expects the specific name:
  - `DEVICE_BINDINGS_TABLE_NAME`
- Secret variable expected by code:
  - `OPENAI_SECRET_NAME`
- Important: code expects secret **name**, not ARN.

---

## Web Risk Communication

- Lambda path: `src/web_risk_communication/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: API/event invocation with URL input

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `WEB_RISK_TABLE_NAME` | Yes, unless fallback aliases are set | `trustcheckradar-dev-web-risk-cache` | Cache reads/writes fail |
| `TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-web-risk-cache` | Used only if primary name is missing |
| `USERS_TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-web-risk-cache` | Used only if stronger names are missing |
| `WEB_RISK_SECRET_NAME` | Yes | `trustcheckradar/dev/web-risk-api-key` | Google Web Risk API key cannot be loaded |
| `WEB_RISK_SECRET_FIELD` | No | `apiKey` | Defaults are used when omitted |
| `WEB_RISK_ENDPOINT` | No | `https://webrisk.googleapis.com/v1eap1:evaluateUri` | Defaults are used when omitted |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB web-risk cache table | `WEB_RISK_TABLE_NAME` or fallback aliases | `dynamodb:GetItem`, `dynamodb:PutItem` | Name required by code |
| Secrets Manager Web Risk secret | `WEB_RISK_SECRET_NAME` | `secretsmanager:GetSecretValue` | Name required by code |
| Google Web Risk API | runtime outbound call | outbound HTTPS | No AWS identifier |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: web-risk cache table
- Primary key shapes:
  - full URL cache item:
    - `PK = WEBRISK#FULL_URL#<sha256(normalizedUrl)>`
    - `SK = RESULT`
  - domain cache item:
    - `PK = WEBRISK#DOMAIN#<sha256(domain)>`
    - `SK = RESULT`
- TTL fields:
  - `ttl`
  - `expiresAt`
- GSI usage: none
- Item families:
  - cached full-url assessment
  - cached domain assessment

New cache writes retain the SHA-256-bound key and bounded threat assessment but do
not persist or replay the normalized URL/domain in an item attribute. Legacy rows
may still contain `uri`; the Lambda suppresses that field on cache hits. Because
the legacy key has no subject index, cleanup/migration remains a separately gated
retention task and TTL expiry is not evidence of physical deletion. See
`docs/account-data-inventory.md`.

### Runtime assumptions

- Input event contains a URL string or JSON body with URL.
- Lambda normalizes and strips unsafe URL components before hashing/evaluation.
- Code does not need an S3 artifact env var at runtime.

### Compatibility aliases

- Table variable aliases accepted:
  - `WEB_RISK_TABLE_NAME`
  - `TABLE_NAME`
  - `USERS_TABLE_NAME`
- Secret variable expected by code:
  - `WEB_RISK_SECRET_NAME`
- Important: code expects secret **name**, not ARN.

---

## Shared Entitlements Module

- Module path: `src/shared_entitlements/`
- This is not a standalone Lambda, but it defines shared table/env expectations used by multiple Lambdas.

### Environment aliases used by shared entitlement code

- `ENTITLEMENTS_TABLE_NAME`
- `USERS_TABLE_NAME`
- `TABLE_NAME`
- `FREE_MONTHLY_SCAN_LIMIT`
- `PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT`
- `PRO_MONTHLY_SCAN_LIMIT`

### Shared storage assumptions

- Default entitlement fallback item:
  - `PK = USER#<sub>`
  - `SK = ENTITLEMENT`
- Google Play specific entitlement item:
  - `PK = USER#<sub>`
  - `SK = ENTITLEMENT#google_play#trustcheck_radar_pro_monthly`

---

# Terraform gaps to fix

## High-confidence gaps

1. Provide table names, not only ARNs.
- Several Lambdas read DynamoDB tables by environment variable name only.
- If Terraform currently exports only ARNs, the runtime still needs the matching `*_TABLE_NAME` env vars.
- Affected Lambdas:
  - `age_attestation`
  - `post_confirmation`
  - `device_registration`
  - `device_recovery`
  - `purchase_handoff`
  - `entitlement_snapshot`
  - `conversation_analysis`
  - `web_risk_communication`

2. Provide secret names, not only ARNs, where code expects names.
- The current code paths expect secret names for these integrations:
  - `GOOGLE_PLAY_SECRET_NAME`
  - `OPENAI_SECRET_NAME`
  - `WEB_RISK_SECRET_NAME`
- If Terraform currently injects only ARNs such as `GOOGLE_PLAY_SECRET_ARN`, the code will not resolve the secret unless compatibility support is added in code.

3. Ensure `DEVICE_BINDINGS_TABLE_NAME` is present for `conversation_analysis`.
- This is a hard dependency for device binding validation.
- If missing, the Lambda returns service-unavailable style failures during the device validation stage.

4. Ensure `ENTITLEMENTS_TABLE_NAME` is present for all entitlement-aware Lambdas.
- Affected Lambdas:
  - `purchase_handoff`
  - `entitlement_snapshot`
  - `conversation_analysis`
- Fallback aliases exist in some code paths, but relying on them is brittle and makes Terraform less explicit.

5. Ensure `ANALYSIS_ABUSE_TABLE_NAME` is present for `conversation_analysis`.
- This is required for dedupe, rate limiting, and abuse controls.

## IAM permissions Terraform must provide

### Age Attestation
- `dynamodb:UpdateItem`

### Post Confirmation
- `dynamodb:PutItem`

### Device Registration
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `dynamodb:UpdateItem`
- `dynamodb:ConditionCheckItem`
- `dynamodb:Query`

### Device Recovery
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `dynamodb:UpdateItem`
- `dynamodb:ConditionCheckItem`
- `dynamodb:Query`

### Account Data API
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `dynamodb:UpdateItem`
- `dynamodb:Scan`
- `cognito-idp:AdminUserGlobalSignOut`

### Purchase Handoff
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `secretsmanager:GetSecretValue`
- outbound internet/NAT access if Lambda is inside a VPC and still needs Google access

### Entitlement Snapshot
- `dynamodb:GetItem`

### Conversation Analysis
- `dynamodb:GetItem`
- `dynamodb:ConditionCheckItem` for participation state, active profile, and the
  fixed deletion-ledger fence
- `dynamodb:PutItem`
- `dynamodb:UpdateItem`
- `dynamodb:DeleteItem`
- `dynamodb:Query`
- `secretsmanager:GetSecretValue`
- outbound internet/NAT access if Lambda is inside a VPC and still needs OpenAI access

### Web Risk Communication
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `secretsmanager:GetSecretValue`
- outbound internet/NAT access if Lambda is inside a VPC and still needs Google Web Risk access

### Common to all Lambda execution roles
- `logs:CreateLogGroup`
- `logs:CreateLogStream`
- `logs:PutLogEvents`

## App/backend outputs Terraform should expose clearly

- API route URL for each protected endpoint
- Cognito authorizer / issuer details expected by the client
- Device binding header requirement for conversation analysis:
  - `X-Device-Binding-Fingerprint`
- Exact table names for:
  - users
  - device bindings
  - purchase entitlements
  - analysis abuse control
  - web risk cache
- Exact secret names for:
  - OpenAI API key secret
  - Google Play service-account secret
  - Google Web Risk API key secret
- Config values that are part of behavior, not just infrastructure:
  - `FREE_MONTHLY_SCAN_LIMIT`
  - `PRO_MONTHLY_SCAN_LIMIT`
  - `SCAN_RATE_LIMIT_WINDOW_SECONDS`
  - `SCAN_RATE_LIMIT_MAX_REQUESTS`
  - `RATE_LIMIT_WINDOW_SECONDS`
  - `RATE_LIMIT_MAX_REQUESTS`
  - `REQUEST_ID_TTL_SECONDS`
  - `DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS`
  - `ENTITLEMENT_PLATFORM`
  - `ENTITLEMENT_PRODUCT_ID`
  - `ENTITLEMENT_USAGE_PERIOD_MODE`

## Nice-to-fix clarity gaps

1. Standardize on `*_TABLE_NAME` and `*_SECRET_NAME` everywhere.
- Today the codebase still carries compatibility aliases in a few places.
- Terraform will be easier to reason about if each Lambda gets a single canonical env var contract.

2. Decide whether code should support secret ARN aliases.
- Right now several Lambdas clearly expect secret names.
- If infra prefers passing ARNs, add explicit code support instead of depending on naming assumptions.

3. Publish runtime contract per endpoint alongside infra outputs.
- Especially for:
  - request headers
  - request body shape
  - authenticated identity requirements
  - expected HTTP method

## Sprint 7 History and recognition addendum

The disabled runtime contract for `conversation_analysis.zip`,
`history_read_api.zip`, `history_mutation_api.zip`, and `history_lifecycle.zip` is
maintained in [docs/history-lambda-infrastructure-contract.md](docs/history-lambda-infrastructure-contract.md).
That addendum is authoritative for the two-table key/index contract, activation
gates, exact environment names, IAM actions and leading-key restrictions,
scheduled event shape, metrics, and remaining decision blockers. None of those
features may be activated merely because the ZIP artifacts exist.
