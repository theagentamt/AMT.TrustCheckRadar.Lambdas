# V1 trial, paid authority and complimentary writers

Current integration: see [snapshot integrity](v1-access-snapshot-integrity.md) for strict retained trial evidence and concurrent-read behavior. Trial retention policy has been approved (eligibility until account deletion); qualified storage/deletion and activation still require actual acceptance. Legacy Web Risk is now retired; this does not activate modern access or provide the missing verified paid/operator adapters. Earlier local evidence below is historical.

Status: isolated implementation, never a legacy entitlement fallback. Mobile
handlers support explicit trial activation and access snapshots; all gates
remain disabled by default. No live authority rows or grants are seeded.

## Mobile routes and runtime

`v1_entitlements.app.lambda_handler` implements `GET /v1/access` and
`POST /v1/access/trial`, API Gateway HTTP API v2 JWT events only. Configure a
Cognito authorizer on both routes; do not permit untrusted direct Lambda invoke.
The handler validates the existing verified authorizer context using
`shared_history.security.jwt_subject`, and reuses the users/deletion fences and
strict ACTIVE_BINDING pointer from the V1 core. Trial activation additionally
fences the current device inside its write transaction.

`contracts/v1-access/v1` contains exact request/snapshot/error schemas, 14
fixtures and Android handling rules. No account ID or purchase token appears in
these responses. The app must explicitly confirm device activation/switch using
the existing `/device-registration` contract before activating a trial. A raw
registered fingerprint is used in `x-device-binding-fingerprint`, not a hash of
it. The adapter never registers/switches a device or starts a trial on its own.

Set `V1_ENTITLEMENTS_ENABLED=true` only after review; the shared
`AUTHORITY_ENABLED` gate is independently required. Shared runtime requires all
explicit table names, Cognito issuer/client/scope, policy and operational limits,
and the exact HMAC keyring secret ARN. See `shared_check_authority/runtime.py`.
Trial activation also requires `TRIAL_AUTHORITY_RETENTION_APPROVED=true`; absence
fails closed in the writer itself. Keep it false until the owner approves
account-lifetime trial history and its deletion handling.
This handler has no provider invocation, purchase secret or operator grant
permission. Its IAM requires `GetItem`/`TransactWriteItems` on the exact existing
users/device/deletion/authority tables and `GetSecretValue` on that keyring ARN.
No Scan, Query, GSI, S3 or public paid/operator route is needed. Package the
`v1_entitlements`, `shared_check_authority` and `shared_history` modules; use
Python 3.14/ARM64. Legacy Web Risk remains unchanged.

## Transactional authority

`EntitlementWriter` uses disjoint V1 keys in the existing authority table.
`ACCESS.sources` retains independent trial/paid/complimentary evidence. Effective
precedence is complimentary, paid, then unexpired trial. Every mutation increments
ACCESS revision; periodRevision references the immutable PERIOD grantRevision.
Overlay changes never overwrite a period counter or change its identity.

Explicit trial activation atomically creates a fixed seven-day, ten-check period
and `TRIAL_HISTORY`. The history prevents restart after reinstall, device change,
expiry or an exhausted allowance. A repeated activation returns its original
clock. Research/legacy FREE/PRO balances are never consulted. Account deletion
and device changes racing activation cancel the complete transaction.

A complimentary grant requires a trusted operator context whose exact IAM role
is in the configured allowlist. The future IAM adapter must derive that context
from authenticated AWS identity, never body claims. The source supports an
optional expiry, represented by explicit null for no expiry. Grant/revoke creates
an audit row in the same transaction, recording allowlisted principal, HMAC of
session identity, reason code and revision. Revocation restores the actual
underlying period/usage, including exhausted or expired states. Mobile cannot
call this library interface. No administrative endpoint is deployed by this PR.

`refresh_for_account` updates effective authority after an overlay expires,
without extending or creating a trial or paid period. Snapshot and future
consumer admission call it; reconciliation does not need current entitlement.
Each admission still rechecks all fences, so a snapshot never authorizes work.

`AUTHORITY_OP#<HMAC>` records reconcile uncertain writes with the same mutation
identity. Conflicting mutation content cannot reuse an ID. Mutations fail closed
if another retained HMAC namespace has authority: migration must prevent double
allowance. Account deletion must remove every namespace, sources, trial history,
periods and audit rows. These records intentionally have **no invented TTL**;
authority/audit retention and deletion-bridge coverage are activation gates.

## Verified paid adapter requirements

The writer consumes only `VerifiedMonthlyDecision` returned by an injected
server verifier; mobile JSON and legacy normalized purchase outcomes are not
accepted. It checks account, exact configured product/store, monthly plan,
fresh verification, ordered source revision, immutable period identity/dates,
and non-overlap. Renewals create new periods; repeated verification preserves
used/reserved counts. Revocation does not delete old settlement counters. A
missing verifier or unknown period denies paid authority.

A concrete Google adapter remains required, rather than guessing a 30-day
interval from subscription-level `startTime`. Google exposes the current
subscription state/expiry and latest successful order through
[purchases.subscriptionsv2](https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2).
The [Orders resource](https://developers.google.com/android-publisher/api-ref/rest/v3/orders)
provides accounting-period snapshots in subscription details. This can supply
an immutable funded-period identity and interval, while current access expiry
must continue to come from subscriptionsv2. Deferral/grace can change access
expiry without creating another allowance; the current bounded writer rejects
changes to a period's interval until that distinction is implemented.

The adapter needs:

- Server-issued opaque account binding checked against Google's verified
  `obfuscatedExternalAccountId`; a unique purchase ownership ledger and explicit
  linked-token replacement migration, with no second account consuming the same
  purchase. [Google's security guidance](https://developer.android.com/google/play/billing/security)
  describes purchase verification and obfuscated account binding.
- Exact package/product/base-plan checks against a verified monthly catalog;
  subscription-state, pending/acknowledgement and refund/revocation handling.
- Fetch the latest successful order and match token/product/base plan. Discard
  buyer addresses and financial details not needed for entitlement accounting.
- Serialize trusted verification per purchase and assign a local monotonic
  source revision. Notifications trigger a fresh API read; their arrival order
  must not restore stale access. An ETag or order timestamp alone is not a global
  lifecycle sequence.
- Test renewal, canceled-but-unexpired access, grace/hold, deferral, refund,
  restore, account mismatch and linked replacement against official sandbox data
  before activating paid writes. iOS requires its own verified store adapter.

No Google Play credential, store product configuration or real receipt was
inspected in this implementation. Paid access therefore remains fail-closed.

## Validation

Python 3.14 isolated Moto tests: **57 writer/HTTP regressions passed**. They cover
trial repeat/device-change/expiry, deletion and device races, period preservation,
revocation/renewal, paid evidence rejection, operator allowlisting/audit,
uncertain writes, cross-key migration fences, complete-only original-period
settlement, unlimited complimentary admission with abuse caps, strict requests,
HTTP failure privacy, and disabled retention/feature gates. Two schema fixture
checks passed. The normal repository suite passed **652 tests and 162 subtests**
with three intentionally isolated modules skipped. These are emulator/local
results; actual AWS IAM/API behavior remains a deployment acceptance step.
