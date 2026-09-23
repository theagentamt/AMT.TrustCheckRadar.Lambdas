# Play lifecycle runtime contract (closed candidate)

All new handlers are Python 3.14/arm64, `app.lambda_handler`, disabled before AWS/provider access. Proposed artifacts are `play_lifecycle_ingress.zip`, `play_lifecycle_worker.zip`, and `play_token_deletion.zip`. Existing `v1_play_handoff.zip`, account deletion/export and shared accounting packages must be coordinated; this document is not activation approval.

## Dedicated store and encryption

Use one dedicated DynamoDB table with PK/SK strings, `expiresAt` TTL and GSI1 (`GSI1PK`, `GSI1SK`) **KEYS_ONLY**. No stream, PITR, on-demand backups or AWS Backup selection. Worker always strongly reads the base item after index lookup. Table resource policy alone does not prove backup exclusion; audit backup selections and authorized operator/service roles.

- Token: `V1#<retainedKeyId>#<accountHmac> / PLAY_TOKEN#<tokenSha256>`. Schema 2 adds wrapped data key and bounded retry/ack metadata; plaintext token is never a DynamoDB attribute. `expiresAt` is exactly latest verified access end plus 604800. Due index `V1_PLAY_RECONCILE / <nextAttemptAtEpoch>#<PK>#<SK>`.
- Reverse account binding: `PLAY_BINDING#<existing Android obfuscatedAccountHash> / ACCOUNT`.
- Account deletion locator: `V1#<retainedKeyId>#<accountHmac> / PLAY_BINDING`.
- Binding pair is account-lifetime billing identity metadata. It has no raw token, no post-account-deletion retention and no invented token expiry. It is created only by an authenticated active-account/current-device prepare request and erased with account data. Unknown mappings never authorize guessed ownership.

KMS uses only static encryption context `{purpose: google-play-reconciliation, environment: dev}` so CloudTrail does not receive account/token identifiers in encryption context. Use `GenerateDataKey(KeySpec=AES_256)` and `Decrypt` on exact configured key ARNs. AES-GCM authenticated plaintext envelope carries exact account partition, token digest and token; decrypt validates all three against the row. Direct KMS Encrypt cannot hold the maximum 4096-byte token plus binding envelope. No global CloudTrail suppression is proposed.

## Runtime environment

Common runtime authority configuration is unchanged (`STAGE`, `AUTHORITY_ENABLED`, exact table names, HMAC secret ARN, Cognito settings and approved authority horizons). New exact fields:

- `PLAY_LIFECYCLE_ENABLED=false`, `PLAY_TOKEN_CLEANUP_ENABLED=false`, `PLAY_PREPARATION_ENABLED=false` by default.
- `PLAY_TOKEN_TABLE_NAME`; `PLAY_TOKEN_KMS_KEY_ARN`; `PLAY_TOKEN_READABLE_KMS_KEYS_JSON` (exact 1–4 key ARNs, includes active).
- `PLAY_LIFECYCLE_WORKER_PRINCIPAL_ARN` (the exact configured worker identity, never request-body supplied).
- `DEV_SUBJECT_ALLOWLIST_JSON`: exact selected Dev subjects; no wildcard.
- Existing fixed `GOOGLE_PLAY_PACKAGE_NAME`, `GOOGLE_PLAY_PRODUCT_ID`, `GOOGLE_PLAY_BASE_PLAN_ID`, `GOOGLE_PLAY_BILLING_PERIOD=P1M`, `PLAY_CATALOG_P1M_VERIFIED`, `PLAY_REQUIRE_TEST_PURCHASES=true`, exact service-account secret ARN.
- Ingress only: `PLAY_PUBSUB_AUDIENCE`, `PLAY_PUBSUB_SUBSCRIPTION`, `PLAY_PUBSUB_SERVICE_ACCOUNT_EMAIL`, `PLAY_PUBSUB_SERVICE_ACCOUNT_SUBJECT`. Signature verification and exact issuer/audience/email/subject precede provider or persistence.
- Deletion only: `DELETION_LEDGER_STREAM_ARN`, approved `DELETION_RECEIPT_RETENTION_SECONDS`, retained HMAC keys and subject allowlist; independent of active paid access.

Ingress is synchronous authenticated Pub/Sub delivery, no AWS queue/raw-token DLQ. Google subscription retention is owner-approved 600 seconds, no retained acknowledged messages, no topic retention and no DLQ. Google pending copies cannot be selectively erased on account deletion; handlers refuse account-fenced changes. No event timestamp grants/revokes access. Scheduled worker input is exactly `{"schemaVersion":1,"operation":"reconcile-play-lifecycle"}`. Delete reconciliation input is exactly `{"schemaVersion":1,"operation":"reconcile-play-token-deletion"}`; stream input must match the exact configured deletion ledger ARN. No raw-token event is stored in logs or durable request audit.

## IAM boundary

- Runtime provider client: exact two existing secrets, AWSCURRENT only; no SSM/S3 or Lambda provider invocation. KMS exact configured keys GenerateDataKey/Decrypt with static context.
- Token store: strongly consistent GetItem; bounded Query on account partition and GSI1 `V1_PLAY_RECONCILE`; transactional Put/Update/Delete/ConditionCheck on `V1#*#*` token/account locator and `PLAY_BINDING#*` reverse locator only. No runtime approval-marker writes. Dedicated cleanup role needs no decrypt/provider secret.
- Existing authority/ownership store: existing scoped GetItem and transactional authority/period/audit/usage/owner/locator actions, with profile/deletion/inventory conditions. Strict shortening may require conditional Delete and bounded account Query for pending releases; final implementation/tests pin those actions before activation.
- Deletion worker: exact ledger GetItem/Scan/stream reads and transactional command conditions/component receipt; cleanup all retained account namespaces before PLAY_TOKENS receipt. Identity finalizer must require that new component under reviewed inventory revision.
- Export: token-table GetItem/Query only; expose minimized verification metadata and scope exclusions, never raw token/ciphertext/wrapped key. Public contract versioning must preserve old immutable fixtures.

Runtime implementation and coordinated IAM/artifacts require independent review before deployment. No mobile hardware is required to qualify backend contracts, synthetic provider races or runtime composition.

## Fair progress and operational signals

The scheduled worker advances a durable, CAS-protected cursor before processing each strong-read token. Up to10 pages of5 index keys are inspected per invocation and only one provider reconciliation is attempted. A malformed row with canonical index keys records failure without permanently starving later keys. Noncanonical key material is never copied into a checkpoint; it raises an alert and requires controlled inventory repair before activation can claim full sweep coverage. Cursor position PK=PLAY#CONTROL/SK=LIFECYCLE_CURSOR is in the dedicated no-backup table, contains only last GSI keys/revision/pass clocks and expiresAt=now+300, and is logically discarded at expiry. It is pseudonymous, not anonymous; the five-minute operational retention exception remains owner-review pending. PLAY_CHECKPOINT_POLICY_APPROVED defaults false. Account erasure removes an own-account cursor atomically before the PLAY_TOKENS receipt. Cursor advancement conditions target existence, preventing recreation after target erasure. Use a one-minute schedule; a five-minute schedule would expire progress before continuation. Logical expiry/TTL does not prove immediate physical removal. No GSI2 is required.

Deletion catch-up uses the existing ledger scan semantics under separate PK=PLAY#CONTROL/SK=TOKEN_DELETION_CURSOR; only transaction Update cursor/revision/scanStartedAtEpoch/lastFullPassAtEpoch. Token deletion visits at most four retained namespaces, one page per namespace and returns after the first nonempty mutation. Its max_pages argument is interface compatibility, not a smaller four-namespace proof budget. Final empty proof precedes the receipt. PLAY_TOKENS is now required before USER_PROFILE and IDENTITY, invalidating old inventory manifests until reviewed migration acceptance.

All logs have fixed event names and integer counters only. Namespace recommendation TrustCheckRadar/PlayLifecycle, dimensions Environment and Component only. Worker event play_lifecycle_worker: heartbeat,examined,reconciled,ackPending,expiredDeleted,failed,unresolved,exhausted,oldestDueSeconds. Ingress event play_lifecycle_ingress: heartbeat,accepted,testNotification,rejected,failed,unresolved. Deletion event play_token_deletion: heartbeat,examined,completed,pending,failed,deleted,overdue,skipped and scheduled fullPassAgeSeconds. Disabled handlers emit no heartbeat. Alarm failed/unresolved/exhausted and cleanup lag independently from native Lambda Errors. After eight transient failures, attempts remain hourly with exhausted alert until verified deadline; acknowledgment recovery never waits until paid-period expiry.

## Contracts and remaining qualification

Preparation candidate.1 is a separate POST /v1/purchases/google-play/prepare route, current authenticated device only. No provider or purchase token is required to establish mapping. Initial background recovery requires that exact pair, fresh Google account binding, inventory/ownership proof and atomic paid funding; it never guesses an account. Unknown new head when a paid source already exists remains reconciliation-required (not automatic replacement/restore). Existing head renewal and grace use fresh funded order and preserve counters. Unknown or long/expired predecessor lineage remains explicitly unsupported.

Account export candidate.3 adds play_verification metadata under ACCOUNT_EXPORT_PLAY_TOKENS_ENABLED; original candidate.2 files stay unchanged. The optional adapter excludes raw token, digest, ciphertext, wrapped key, binding hash and KMS identity. Old clients must explicitly integrate candidate.3 before activation. Preparation mapping is account-lifetime, while token metadata is omitted after logical expiry. Retained keys are inventoried; no fallback silently treats a lost key as empty.

Source tests and disabled package imports do not qualify live Google authentication, Pub/Sub retry delivery, real test purchase/ack, KMS IAM, deletion/export inventories, scheduler deadlines or physical erasure. Coordinate all shared-accounting packages and exclude stale writers before activation. The strong due-shortening proof is bounded at800 CHECK rows and16 matching pending receipts; larger/unknown inventories remain unchanged pending lease recovery/reconciliation. No full SECUR4ALL-125 completion or production readiness is claimed from this closed increment.

## Scoped publication command

After source review and release integration, build all13 functions listed in scripts/publish_play_lifecycle.py with scripts/build_lambda_zip.sh, without --skip-dependencies, Python3.14/arm64, into an otherwise empty directory. Generate SHA256SUMS for exactly those13 ZIPs. The authoritative list is3new handlers, v1_play_handoff, url_consumer, url_lease_recovery, v1_entitlements, v1_authority_deletion, message_consumer, recovery_consumer, result_feedback, account_export_api, account_data_api. No campaign or Android artifact is in scope.

`python scripts/publish_play_lifecycle.py --dist-dir <reviewed-directory> --source-sha <full-reviewed-source-SHA> --output <manifest.json>` validates all bytes without AWS. Add `--publish --bucket trustcheckradar-dev-107827791950-artifacts --region us-east-1` only for authorized Dev publication. It performs conditional S3 puts at releases/<sourceSHA>/<function>.zip, then verifies exact version/checksum/size. It never changes Lambda, IAM, DynamoDB or gates. Existing research9 Actions workflow is not the correct scope. Rebuilding dependencies later can change bytes and must fail immutable-key reuse; reuse exact qualified bytes instead of overwriting.
