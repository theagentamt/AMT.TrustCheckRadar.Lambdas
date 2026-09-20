# Disabled Dev URL consumer and recovery candidate

This increment implements real authenticated handlers and transactional recovery,
but does not activate routes, provision a secret value, publish ZIPs or deploy.
Legacy Web Risk is unchanged. Retention/deletion/device acceptance remains a gate.
Canonical transport: `contracts/url-consumer/1.0.0-candidate.1`; it wraps unchanged
public outcome `0.2.0-candidate.1`. Nineteen fixtures were generated against the
actual Consumer service and local DynamoDB emulator.

## Packages and invocation

| Package | Handler | Runtime/timeout | Access |
|---|---|---|---|
| url_consumer.zip | app.lambda_handler | Python3.14 ARM64 /29s | Proposed JWT HTTP API prepare/submit/reconcile; currently disabled |
| url_lease_recovery.zip | app.lambda_handler | Python3.14 ARM64 /15s | Scheduled `{schemaVersion:1}` only; currently disabled |
| v1_entitlements.zip | v1_entitlements.app.lambda_handler | Python3.14 ARM64 /15s | GET /v1/access and explicit POST /v1/access/trial; currently disabled |

The existing private assessment retains schemaVersion1/operator behavior, adding
an optional trusted `executionBudgetMs` integer 1000..18000. Omission preserves
its existing32-second budget; consumers can only request a tighter budget. The
consumer requires at least14seconds remaining for external work and passes at
most18seconds, with a25-second orchestration target, bounded no-retry AWS calls,
and time reserved for settlement. Native Lambda timeout29seconds/API29seconds is
a final guard, not proof of successful settlement. Real Dev timing qualification
is required before activation.

## Exact configuration

All authority/consumer/entitlement flags default false. Never set them true merely
because code or Terraform validation passes.

Shared authority: STAGE=dev; AUTHORITY_ENABLED; USERS_TABLE_NAME;
DEVICE_BINDINGS_TABLE_NAME; DELETION_LEDGER_TABLE_NAME; AUTHORITY_TABLE_NAME;
COGNITO_ISSUER; COGNITO_APP_CLIENT_ID; COGNITO_REQUIRED_SCOPE;
AUTHORITY_HMAC_SECRET_ARN; AUTHORITY_POLICY_VERSION=owner-2026-09-20-v1;
OPERATION_VALIDITY_SECONDS; WORKER_SETTLEMENT_SECONDS; RECONCILIATION_SECONDS;
RECEIPT_RETENTION_SECONDS; COUNTER_RETENTION_SECONDS; ATTEMPT_WINDOW_SECONDS;
ATTEMPTS_PER_WINDOW; MAX_INFLIGHT. Every numeric setting is mandatory and positive;
receipt retention must cover operation+worker+reconciliation, and counter retention
must cover receipt retention/window. Proposed retention values are not defaults.

The HMAC secret ARN must be the exact Dev
`trustcheckradar/dev/v1-authority-hmac-<suffix>` resource. Loader requests
VersionStage=AWSCURRENT explicitly. Secret JSON is
`{ "activeKeyId": "k1", "keys": { "k1": "<base64 of at least32 random bytes>" } }`.
No secret value is committed, logged, output, or managed by this code's Terraform.

Consumer adds CONSUMER_ENABLED and exact qualified URL_ASSESSMENT_FUNCTION_ARN
`arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-assessment:live`.
Entitlements adds V1_ENTITLEMENTS_ENABLED and
TRIAL_AUTHORITY_RETENTION_APPROVED (both false); see v1-entitlement-writers.md.
Sweeper needs only STAGE, LEASE_SWEEP_ENABLED=false, AUTHORITY_TABLE_NAME.

Initial reviewed Dev security settings:20 authenticated operations per60-second
window,2 inflight/account; consumer reservedConcurrency2 and sweeper1. Malformed
bodies on known authenticated routes count; no customer allowance is deducted for
rate limiting. Error responses preserve unknown accounting because the same
logical check might already have been admitted. No daily business cap is invented.
These caps do not establish full subscription economics or fleet-wide fraud
protection; monitor attempted/partial/provider costs separately.

## Recovery, retention and deletion

ADMITTED receipts bind account HMAC partition, separate client checkId, signed
operationProof, exact intent HMAC, original period identity/revision, execution
proof and lease deadline. A normalized allowlisted assessment summary may be
stored atomically with SETTLED state for response-loss recovery; no raw URL,
message, Google body, JWT, key, or request payload is stored.

Pending receipts have GSI1PK=V1_PENDING and a padded deadline GSI1SK. Existing GSI1
is sufficient; no new index is needed. They have retentionDeadlineEpoch but no
DynamoDB expiresAt until settlement, so asynchronous TTL cannot erase the receipt
before reserved counters are released. Logical reads deny expired retention.
Lease cleanup removes the pending index and sets expiresAt to the original
retention deadline. A recurring sweeper and retention/backlog alerts must be wired
before enabling any admissions; prolonged cleanup failure is a privacy/allowance
incident, not permission for indefinite retention.

Sweeper processes two pages of five leases per invocation and persists its cursor
at PK=V1#CONTROL, SK=LEASE_SWEEP_CURSOR. It advances past failed/poison items so an
old corrupt lease cannot starve later accounts. A subsequent full pass revisits
failures. Counts only are logged; cursor/partitions/proofs are never logged.

Cleanup needs no raw owner reference: it conditionally updates existing receipt,
original period, inflight, and ACCESS partition fence, charging zero only for an
expired ADMITTED lease. It never creates an account, invokes a provider, or refunds
an already-settled charge. Account deletion must first mark every retained-key
ACCESS partition DELETING, then remove all V1 rows. This deletion bridge is an
explicit activation blocker until wired and tested. The current recovery helper
respects the fence; it does not implement that bridge.

## IAM handoff

Consumer: GetItem on exact users/device/deletion/authority tables; transactional
ConditionCheckItem on identity tables and underlying PutItem/UpdateItem/DeleteItem/
ConditionCheckItem on authority, restricted with dynamodb:EnclosingOperation to
TransactWriteItems. Exact HMAC GetSecretValue AWSCURRENT; exact assessment:live
InvokeFunction; log writes. No Google secret permission. Current consumer does not
Query/Scan; cleanup is scheduled or explicit same-proof reconciliation.

Sweeper: GetItem authority; Query authority/GSI1 only with pending-partition
restriction; transaction-only UpdateItem/ConditionCheckItem authority, including
the V1#CONTROL cursor. No identity tables, Secrets Manager, Lambda invoke, or raw
network provider permission. Entitlements: same authority/identity transaction
boundary plus HMAC read, with no provider invocation. Keep these roles separate
from the existing private provider role, whose database denial remains intact.

## Validation and release

Tests cover real core+Moto transactions, writer/trial/account/device fences,
complete-only charging, no duplicate provider work, original-period renewal,
complimentary transitions, lost responses, late/duplicate recovery, pagination
past poison leases, and canonical HTTP/state fixtures. An expired/auth-failed/
malformed reconcile or failed submit never asserts not_started/zero. Repeated
prepare reads the existing receipt and preserves a prior charge.

All27ZIPs (25functions+2contract archives) are built. Three new handlers are imported
from isolated archive layouts in Python3.14 with networking blocked. Since ARM64
Linux dependency wheels cannot execute on macOS or CIx86_64, that host check uses
the same pinned host jsonschema dependency while loading packaged application and
schema resources. Actual enabled ARM64 runtime/IAM/timing smoke remains required
on the reviewed Dev deployment.

The combined diff is classified authority_manual, including the private-budget
extension and packaging. Main CI may build/retain GitHub artifacts; publisher must
skip AWS credentials and all S3 uploads. No runtime or alias is changed by merge.
