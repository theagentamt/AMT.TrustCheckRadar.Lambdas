# Private result feedback source handoff

SECUR4ALL-238 / ATCR-122 adds an authenticated private opinion on a retained,
owned governed message or URL assessment (including QR-through-URL). It does not
change the assessment, subscription, allowance, model, campaign data or research
participation. No text, links, screenshots, explanations, demographics or provider
requests enter this feature.

## Approval and immutable interface

The owner approved structured-only feedback and storage on the existing receipt
with disclosed backup retention. The exact proposal is infrastructure commit
`807dd0d82aa4c9c3b6937857fe93a40fb24cb557`,
`docs/PRIVATE-RESULT-FEEDBACK-REVIEW.md`, SHA256
`9e3485588daeae698b139cb070b4de16bb9298348135271e7d9adafd9968bee6`.
The UI-copy document at that commit has SHA256
`2d32f2662bc574cb4c21c61d7ba608fa7c262775ce9d0fd9a0d16cbad7469cae`.

Canonical `contracts/result-feedback/1.0.0-candidate.1` was independently published
at Lambda commit `84bde2021c12478fe70562c289ff8730858d9dd6`; its SHA256SUMS file
hash is `0653e9753882e04322366b1018ef2543b6d9fe5aa856f366fd5b95c96c06c735`.
It contains the closed request/response schema, eligibility matrix, synthetic
fixtures and exact approved EN/ES strings. Existing assessment contract bytes
are unchanged. The private POST route is `/v1/result-feedback`, wire limit4096
UTF-8 bytes, transport `1.0.0-feedback-candidate.1`. Deployment is separate.

The four categories are `looks_legitimate`, `looks_like_scam`, `unclear`, and
`unhelpful`. Feedback IDs are opaque and scoped to the exact owned CHECK, not
account-global unique keys or credentials. Same ID and category returns the
original accepted acknowledgment. Changing its category conflicts. Another ID
on an already-reported result returns `already_received`, without saving that
new choice or disclosing the prior category. A valid other owned result may use
the same ID independently. Timeout/transaction uncertainty is not success; the
client may explicitly retry the same immutable report identity.

## Receipt and transaction boundary

The handler authenticates Cognito context, validates the active device and
checks the deletion fence. It loads only the exact HMAC-derived account CHECK;
no global search or subscription/period reads occur. Admission-expired proofs
remain cryptographically verified within the original retained receipt horizon.
Receipt expiration, wrong ownership/reference, malformed summaries, missing
scope, technical-only errors and legacy/local/history/recovery results fail
closed. Eligible displayed processing is complete/partial for URL and
complete/partial/inconclusive for message. URL eligibility uses the same public
mapper as the app, so technical private partials mapped to blocked/invalid input
do not qualify. Noncharged assessed partial/inconclusive results remain eligible.

Both initial writes and duplicate acknowledgments transactionally fence the
account, deletion ledger, active-device pointer/binding, HMAC inventory and exact
observed settled receipt. The receipt condition binds summary, proof, payload
HMAC, client/receipt IDs, family/version, accounting, assessment time and original
expiry/index fields. SET updates only `feedback`; there is no row resurrection.
Stored feedback must have exactly its four approved fields and a received time
between assessment and current server time, before original expiration. A race
returns unconfirmed state, not an acknowledgment based on stale reads. A response
lost after commit can be confirmed by same-report retry; no automatic retry loop.

Authenticated attempts, including malformed requests, still increment the
existing bounded security-attempt counter. That metadata is separate from paid
checks: no grant, reservation, allowance, AI or reputation operation is performed.
Logs contain only fixed event, HTTP status, result state and closed reason codes.

## Data inventory, deletion and backups

The only new retained value is CHECK.feedback:
`{feedbackId, category, receivedAt, policyVersion}`. It remains account-linkable
through the existing HMAC receipt partition. No original or sanitized input,
extra outcome copy or research-use consent is stored here. Original CHECK
retentionDeadlineEpoch, expiresAt, GSI and assessment summary stay unchanged.
Logical access stops at the original seven-day deadline, even while DynamoDB TTL
is pending. The existing explicit expiry worker and account-deletion bridge
remove the whole row including feedback; local Moto tests exercise both paths.
These are source/emulator results, not deployed cleanup evidence.

The shared table's backups inherit the existing retention policy. Historical
foundation inventory recorded 35-day PITR. Root's read-only Dev audit at
2026-09-21 19:26 UTC confirmed35-day PITR, expiresAt TTL enabled and GSI1active;
regional backup/export/recovery-point lists were empty. This is a bounded Dev
audit, not proof of all historical copies or restore-quarantine qualification.
Owner approval covers that disclosed backup model, not instantaneous erasure of
all copies at seven days. Before activation, inventory current PITR, manual
snapshots, AWS Backup and extracts; rehearse isolation of restored tables and
reapply original expiry/deletion fences before permitting restored data access.
A restored row with a lost deletion fence cannot be made safe by this handler
alone. Do not point the service at an unvalidated restored table.

Account export must inventory this embedded field wherever retained governed
CHECKs are included. This increment creates no export endpoint, long-lived
extract or analytics pipeline; existing broader export acceptance remains open.
No customer-feedback-to-research reuse or aggregate retention is implicitly
approved. Server storage acceptance does not promise human support or a reply.

## Package and environment

Artifact `result_feedback.zip`; canonical handler
`result_feedback.app.lambda_handler` (root `app.lambda_handler` compatibility shim).
Python3.14 / ARM64, root infrastructure source config10seconds/256MiB/concurrency2.
It packages the shared authority/security code, both message summary validators,
canonical URL mapper/schema and feedback contract. It includes no evaluator,
provider transport or evaluation tooling. jsonschema4.25.1 is the only declared
application dependency. Lambda runtime supplies boto3.

Required gates: `STAGE=dev`, `RESULT_FEEDBACK_ENABLED=false` by default,
`RESULT_FEEDBACK_POLICY_VERSION=private-result-feedback-2026-09-21-v1`,
`RESULT_FEEDBACK_POLICY_APPROVAL_SHA256=9e3485588daeae698b139cb070b4de16bb9298348135271e7d9adafd9968bee6`.
Disabled/mismatched gates return503 before AWS or settings discovery.
Future guarded engineering activation also requires `DEV_SUBJECT_ALLOWLIST_JSON`,
`COGNITO_ISSUER`, `COGNITO_APP_CLIENT_ID`, `COGNITO_REQUIRED_SCOPE`,
`USERS_TABLE_NAME`, `DEVICE_BINDINGS_TABLE_NAME`, `DELETION_LEDGER_TABLE_NAME`,
`AUTHORITY_TABLE_NAME`, `AUTHORITY_HMAC_SECRET_ARN`, `ATTEMPT_WINDOW_SECONDS`,
`ATTEMPTS_PER_WINDOW`. HMAC uses the established verified key inventory and exact
AWSCURRENT secret version. Feedback-specific minimal settings intentionally need
no billing authority enablement, period, allowance or provider environment.

IAM: GetItem and transactional ConditionCheckItem on the four existing tables;
UpdateItem only on authority (security attempt counters and existing CHECK);
GetSecretValue only exact authority HMAC secret AWSCURRENT; own logs. No Query,
Scan, PutItem, DeleteItem, provider secret, Lambda invocation or analysis route.
SDK automatic retries are disabled. No new table/queue/provider or public access.

## Validation and remaining acceptance

Reproduce local gate with Python3.14 and isolated Moto:

```
AMT_AUTHORITY_INTEGRATION=1 python -m pytest -q tests/result_feedback tests/shared_check_authority tests/message_consumer tests/recovery_runtime tests/scripts
bash scripts/build_lambda_zip.sh --function result_feedback --output-dir /tmp/amt-result-feedback-arm64
```

Source-only isolated archive tests invoke the exact Terraform handler and shim
with its disabled minimal environment, prohibit AWS/network access, then import
packaged validators/contracts. Full dependency assembly is distinct from ARM64
execution. No deployment, live account or provider call occurs in these tests.

Root's same audit found url-lease-recovery livev4 LEASE_SWEEP_ENABLED=false
and its schedule DISABLED; v1-authority-deletion livev3 had its enable flagfalse,
schedule DISABLED and stream mapping Disabled. Do not assume emulator-tested
explicit cleanup runs in Dev or silently enable shared global workers. Feedback
activation remains blocked by cleanup/deletion qualification and approved
worker enablement. AWS authentication is no longer the audit blocker.

Required before full story completion: verified release integration, Android
real-result/foreground lifecycle and HTTP evidence, current backup/copy audit,
restore quarantine and export inventory acceptance, explicitly approved private
route/activation, and real AWS account/device/ownership/deletion/expiry/idempotency
checks. Do not represent source publication or disabled Terraform as live success.

Final local source evidence: Python3.14.7 gate above passed439 tests and21
subtests (including63 feedback cases). `bash -n` and ShellCheck passed for the
packager. Full ARM64 dependency assembly produced269 unique archive entries,
valid CRC and rpds Python3.14 AArch64 Linux extension. Artifact:
`/tmp/amt-result-feedback-arm64/result_feedback.zip`, SHA256
`4dcdb8abed2e52efbe7b3b1d882586a1b62f2a716a3cb2b1b71755944bb85a29`.
Source-only package invocation uses the host interpreter, not that native ARM
extension. Secret scan and exact source/release publication are recorded in the
PR/tracker handoff, without claiming runtime activation.
